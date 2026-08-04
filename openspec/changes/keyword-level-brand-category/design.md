# 品牌与类目归位到关键词级 设计

## Context

源报表每行是一个关键词，其中三组字段各自独立：

| 列 | 含义 |
|---|---|
| 3–5 | 点击量第 1/2/3 的**品牌** |
| 6–8 | 点击量最高/第二/第 3 的**分类** |
| 9–20 | 三个**商品**槽位，各含 ASIN / 标题 / 点击份额 / 转化贡献占比 |

**商品槽位里没有品牌，也没有类目**（已核对真实 CSV 表头）。而 `importer.py` 现在把品牌#N
与类目#N 硬配给商品#N 写进 `dim_product`：

```sql
SELECT NULLIF(c8,'')  AS asin, ..., COALESCE(c2,'') AS brand, COALESCE(c5,'') AS category  -- 槽位 1
SELECT NULLIF(c12,'') AS asin, ..., COALESCE(c3,'') AS brand, COALESCE(c6,'') AS category  -- 槽位 2
SELECT NULLIF(c16,'') AS asin, ..., COALESCE(c4,'') AS brand, COALESCE(c7,'') AS category  -- 槽位 3
```

所以 `dim_product.brand` 的真实含义是「某个关键词下与该商品同名次的那个品牌」——一个凭空
造出来的属性。再经 `DISTINCT ON (asin)`（无次级排序）任选一个胜出，最终值不确定。
proposal 的实测数字（品牌冲突率 11.7%、类目 3.4%、标题 0.0%）正是这个机制的体现：标题是
真属性所以零冲突，另两个不是。

现状约束：

- `dim_product` 上有 4 个 GIN trgm 索引（title / brand / category / asin），后两个将随列一起消失
- `category_stats` 物化视图建在 `dim_product.category` 上，必须重建
- `fact_daily` / `fact_weekly` 是**月度 RANGE + 站点 LIST 双层分区**，迁移必须手写
- 保留窗口 3 个月（`HOT_WINDOW_MONTHS`），满窗约 2 亿行 / 50 GB
- RDS 2C4G，`max_connections=400`

## Goals / Non-Goals

**Goals**

- 品牌与类目成为**关键词级**的事实字段，名次由列位置承载，与商品槽位同一套语义
- 筛选口径与展示口径一致（前三任一命中），消除 `show-top3-products` 里那条界面标注的存在前提
- `dim_product` 收敛为 ASIN + 标题，只保留有数据来源的属性

**Non-Goals**

- 不做品牌/类目的历史版本（SCD Type 2）。维度表只存名称，名次在事实表上，天然按期次留痕
- 不回填历史分区的新列（见决策 3）
- 不改动商品三槽位的既有语义与字段
- 不引入新的能力域

## Decisions

### 决策 1：品牌与类目各建维度表，事实表存引用而非文本

品牌 13.5 万/站/天、类目 104。两者都建维度表，但类型按基数分开：品牌 `integer`、
类目 `smallint`。合计 6 列约 18 字节/行，相对现有约 140 字节行宽 +13%，满窗增量约 3.6 GB。

**备选：事实表直接存文本。** 否决——品牌名平均 20 字节，6 列文本在 2 亿行上是数十 GB，
且筛选要在事实表上做字符串比较，而事实表是本系统唯一的大表，任何加宽都直接换成 IO。

**备选：类目用 PostgreSQL enum。** 否决——类目集合会随亚马逊调整而变，enum 加值要 DDL，
而 DDL 在分区表上要逐分区锁。维度表加一行不需要锁。

### 决策 2：一次到位删列，部署走停机窗口

按 proposal：`dim_product.brand` / `.category` 与其两个 GIN 索引在本变更一次删除，不留死列。

**这带来一个必须正视的部署风险。** `deploy.sh` 的顺序是 `uv sync → migrate → build →
publish → reload nginx`，**迁移先于新代码生效**。删列迁移一跑完，仍在运行的旧 worker 就
面对一个没有 `brand` 列的 schema，此后每个查询请求 500，直到 nginx reload 完成——窗口是
构建 SPA 的时长，不是几秒。

已评估的备选是「分两次发布、本次保留死列」：可回滚、无窗口，代价是库里有两列垃圾。
**已否决**，取一次到位。故必须补上部署侧的应对，否则这个风险只是被搁置而非解决：

- 本变更的发布**必须停机进行**：`migrate` 之前停掉 uvicorn，`publish` 之后再起。
  低峰执行，实际不可用时长 ≈ 构建时长。
- 停机也让回滚变简单：回滚 = 反向迁移 + 代码回退，期间本来就没有流量。
- 部署步骤写进 tasks，不依赖执行者记得——「记得先停应用」是最容易在深夜漏掉的一步。

反向迁移（`downgrade`）必须能真正跑通并恢复两列与两个 GIN 索引，本地验证过才算数。
列里的数据回不来（本就是错的），但 schema 要能回到旧代码可运行的形状。

**Alembic 处理**（对 `analysis` schema）：

- 两张维度表可由 autogenerate 产出，但要核对 schema 限定
- **事实表加列必须手写**：autogenerate 看不见分区，会把分区表当普通表处理
- `ALTER TABLE ... ADD COLUMN <type> NULL`（无默认值）在 PG 11+ 是纯元数据操作，不重写表；
  在父表上执行会自动传播到全部子分区。2 亿行下这是秒级操作，**前提是不给 DEFAULT**
- `category_stats` 依赖 `dim_product.category`，必须 `DROP MATERIALIZED VIEW` 后按新定义重建
- 迁移写完用 `alembic upgrade head --sql` 离线核对，确认 DDL 里分区结构未被破坏，
  再本地跑一遍 `downgrade -1 && upgrade head`

### 决策 3：历史数据不回填，按批次重导

新列对历史分区是 NULL，且**无法从现存数据推导**——品牌#N 已经被 `DISTINCT ON` 揉进
`dim_product` 且丢了名次与关键词的对应。唯一的真值来源是 OSS 上的原始对象
（`import_batch.object_key`）。

故提供按批次重导脚本，而不是写一个注定不准的回填 SQL。热窗口只有 3 个月，满窗约 90 个
日报 + 13 个周报批次。

**过渡期的不一致**是本变更最难解释的风险：重导完成前，同一个品牌筛选在已重导日期上有结果、
在未重导日期上返回空。缓解见 Risks。

### 决策 4：筛选口径改为「前三任一命中」，索引方案**待实测**

`品牌 = Anker` 的语义从「第一名商品的品牌是 Anker」变为「这个词的前三品牌里有 Anker」。
展示与筛选由此对齐。

三列 OR 现有索引服务不了，两个候选：

- **A**：`b1` / `b2` / `b3` 三个独立 btree，靠计划器 BitmapOr 合并
- **B**：在 `ARRAY[b1,b2,b3]` 表达式上建 GIN，用 `&&` 与多选参数直接求交

B 对类目多选（现有 `categories` 参数是列表）在表达上更自然，但 GIN 在 2 亿行上的体积与
导入后的建索引时间都显著高于 btree。

**不在此处选定。** proposal 已把这条列为已知风险，理由是 V1 有过投机建索引、实测 0 命中的
先例。决策方式写进 tasks：先在真库上用 `EXPLAIN (ANALYZE, BUFFERS)` 对两个方案各测一轮，
以实测数据落定，测量任务阻塞索引任务。

### 决策 5：品牌筛选从子串改为精确 + 下拉，与类目对称

现在品牌走 `DimProduct.brand ILIKE '%x%'`（GIN trgm）。维度化之后，子串筛选要对三个引用列
各做一次「先在 `dim_brand` 上 trgm 找出 id 集合，再在事实表上 IN」，三路 BitmapOr，复杂度
与不确定性都高。

改为精确匹配 + 下拉选项（选项来源与 `category_stats` 同构，新增 `brand_stats`），与类目的
交互完全对称。**这是一次用户可感知的能力收窄**：原先能搜「anker」匹配到「Anker Direct」，
之后只能从下拉里选确切的品牌名——**已确认接受**。

下拉必须带计数（如 `Anker (12,304)`），否则用户面对「Anker / Anker Direct / Ankermann」
三个相近选项时无从判断该选哪个；子串搜索时代这个判断是由结果集替他做的。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| **过渡期同一筛选在不同日期行为不同**（未重导的日期返回空） | 重导脚本按 `import_batch` 顺序推进并记录进度；未重导日期在界面上明确提示「该日期数据待重建」，**不静默返回空**——静默是最难排查的形态 |
| 事实表加 6 列，满窗 +3.6 GB | 扩容触发条件不变（磁盘 >70% 或缓存命中率 <95%）。3.6 GB 相对 50 GB 满窗未触及阈值 |
| 「前三任一命中」的执行计划未知，可能退化为全分区扫描 | 索引方案实测后再定（决策 4）；测量任务阻塞索引任务，不许凭直觉建 |
| GIN 方案下导入后建索引时间拉长，可能超过导入窗口 | 实测时一并测建索引耗时，作为选型输入 |
| **删列迁移跑完到新代码生效之间，旧 worker 面对缺列的 schema 会持续 500**（窗口 ≈ SPA 构建时长，不是几秒） | 本变更**停机发布**：migrate 前停 uvicorn、publish 后再起，低峰执行。步骤写进 tasks，不靠执行者记得 |
| 一次到位后若需回滚，schema 必须能回到旧代码可运行的形状 | `downgrade` 必须真正恢复两列与两个 GIN 索引，并在本地 `downgrade -1 && upgrade head` 验证通过才算数（数据回不来，本就是错的） |
| 重导期间与常规导入争抢咨询锁 | 重导走同一条 `pg_try_advisory_lock` 串行化路径，拿不到锁置 QUEUED，天然不冲突；脚本在低峰跑 |

**连接池 / 内存影响**：不新增连接使用模式，池配置不变。导入侧两个新维度的 upsert 在
`_import_staging` 表内以 SQL 完成，不进应用内存；事实表多 6 列由 `COPY` 一并装载，
仍是流式、内存有界。

## Migration Plan

**发布必须停机**（决策 2）。低峰执行，不可用时长 ≈ SPA 构建时长。

1. 建 `dim_brand` / `dim_category`（autogenerate 可产，核对 schema 限定）
2. 手写迁移：事实表父表 `ADD COLUMN ... NULL`（无 DEFAULT，元数据操作），传播到子分区
3. `DROP` 并按关键词级类目重建 `category_stats`；新增 `brand_stats`
4. 删 `dim_product.brand` / `.category` 与两个 GIN 索引；`downgrade` 要能把它们全部恢复
5. `alembic upgrade head --sql` 离线核对 → 本地 `downgrade -1 && upgrade head` 验证
6. **停 uvicorn** → `deploy.sh` → **起 uvicorn**
7. 按批次重导历史（脚本，低峰，可中断续跑）

**回滚**：停机窗口内回滚 = `downgrade` + 代码回退，期间无流量，故不存在半新半旧的状态。
窗口结束后回滚同理，但要先停机——不能在有流量时反向迁移。

## Open Questions

- 索引方案 A/B 由实测落定（决策 4），实测数据回填到本文件。
- 重导脚本的推进节奏（一次几个批次、是否限速）待观察单批次实际耗时后定。
