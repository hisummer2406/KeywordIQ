# AGENTS.md

**本仓库的开发约定全部写在 [`CLAUDE.md`](./CLAUDE.md) 里，那才是你要读的文件。**
它不是 Claude 专用的，名字只是历史沿革——约 1000 行，涵盖数据模型、各功能域的约定、
以及每条决策「不这么做会怎样」。这里的陷阱绝大多数**错了不会报错**：测试全绿、日志干净、
界面看起来正常，只有数据是错的。动手前先读它。

本文件**刻意只写差异**：`CLAUDE.md` 里有少数几句是对着 Claude Code 这个运行环境写的，
在别的智能体里是**假的**。除下面这几条外，`CLAUDE.md` 的每一句都照单适用。

不复制它的内容是有意的。两份副本必然分叉，而这正是 `CLAUDE.md` 反复警告的那类缺陷
（曾有一张 `sites` 参考表与 `site_registry.py` 存同样的字段，从建表到删除没有任何代码写过
它，真正的害处是给站点元数据开了第二个事实来源）。

---

## 1. 提交门禁不保护你，得自己跑

`CLAUDE.md` 的「开发 harness（提交门禁）」一节说 `git commit` 之前会自动跑验证、不通过就
拒绝提交。**那道门禁挂在 Claude Code 的 `PreToolUse` hook 上**
（`.claude/settings.json` → `.claude/hooks/pre_commit.py`），其他智能体既不受它约束，
**也不会被它保护**——这是唯一一处失效方向不安全的差异：你会以为自己被检查过了。

提交前按改动范围自己跑：

| 改了什么 | 跑什么 |
|---|---|
| `app/` `scripts/` `test/` `alembic/` 或顶层 `.py` | `uv run ruff check . && uv run pytest test/ -q` |
| `frontend/` | `cd frontend && pnpm lint && pnpm tsc && pnpm test` |
| 只有文档 / `openspec/` | 不用跑 |

那一节里的其余内容（跑什么、为什么、基线棘轮怎么用）对你同样成立，照读。

## 2. 项目 skill 是普通 markdown，直接读

`CLAUDE.md` 末尾列的三个 skill（`start-change` / `local-db` / `verify-import`）是 Claude Code
的 skill 机制。你没有那个机制，但**文件本身就是 markdown**，按需直接读：

```
.claude/skills/start-change/SKILL.md    # 开一个变更：认领 → 建 worktree → 收工
.claude/skills/local-db/SKILL.md        # 起测试库、跑含集成测试的全量回归
.claude/skills/verify-import/SKILL.md   # 用真实体量 CSV 跑端到端导入并逐项断言
```

`start-change` 讲的东西 `CLAUDE.md` 的「并行会话」一节已经覆盖了要点，但**动手写代码前
读一遍那个文件**——这个仓库经常同时有多个会话在干活，默认假设不是「只有我一个人」。

`openspec` 是 CLI（`openspec list` / `status` / `validate` / `archive`），不是 skill，你能直接用。
`.claude/skills/openspec-*` 那几个只是把 CLI 的用法包了一层，读不读都行。

## 3. `AskUserQuestion` 就是「问用户」

`CLAUDE.md` 的「流程重量」一节提到 `AskUserQuestion` 工具和「一个调用最多带 4 个问题」。
那是 Claude Code 的工具名。对你而言这条约定的实质是：**澄清问题一次问完，不要串行问四轮**
——每轮都是一次完整的人机往返。

---

## Code Review Rules

> 本节是 Codex 自动审核的运行时配置：Codex 审核只读 AGENTS.md，不读 CLAUDE.md。
> 内容是 CLAUDE.md 对应条目的审核视角浓缩；CLAUDE.md 相关约定变更时**须同步更新本节**，
> 防止两份副本分叉。只标记结构性、数据正确性问题；格式与 lint 归 CI 管，不要报。

### 数据写入纪律

- **事实表（`fact_daily` / `fact_weekly` / `fact_dropped` / `fact_growth`）只追加，不得原地 UPDATE。**
  排名类字段全部在写入时算定，这是按月归档与死元组归零的前提。唯一例外是同一期重复导入
  （先删该期切片再重写）。对事实表的 UPDATE 一律标记。
- **维度/价格数据仅在取值真变时写入。** `dim_product` 的 UPSERT 带
  `WHERE ... IS DISTINCT FROM EXCLUDED.*` 不是冗余；`competitor_store_price` 仅在
  price/compare_at_price/available 变化时追加。删掉这类守卫造成约 30 倍写放大，且测试照样绿。
- **站点码唯一事实来源是 `app/site_registry.py`**（英国是 `GB` 不是 `UK`）。别处硬编码站点
  列表、为站点元数据建库表，都是开第二事实来源。非法/缺失站点码必须报错（422），
  不得回退 `DEFAULT_SITE`。

### 分区与迁移

- **写事实表前必须调用 `app.table.analysis.partitioning.ensure_fact_partitions`**（幂等，无条件调）。
  新增分区表必须登记进 `PARTITIONED_TABLES`；竞对监控的普通表（`competitor_*`、
  `user_store_watch` 等）**不得登记**——登记即被按月保留清理整月 DROP，而价格与名次历史
  越久越值钱（三个月窗口是为八亿行 ABA 事实表定的）。
- **分区表与物化视图的迁移必须手写。** autogenerate 会把事实表产成普通表并静默丢掉
  `PARTITION BY`。新增模型必须登记进 `migration_scope.py` 的 `V2_TABLES`，漏登的表
  autogenerate 静默忽略、不报错。
- **列注释参与 autogenerate 比对**，模型注释与库不一致会产生常驻假差异，掩盖真差异
  （曾盖住 `user_center` 时间戳丢时区）。

### 查询与分页

- **分页排序必须带完整次级键**（如上新流的 `source, source_key, site, product_key`）。
  `published_at` 精度有限、同一时刻多条是常态，无次级键时同一行会在两页各出现一次——
  小数据量下测不出来，须对 `ORDER BY` 子句做结构性断言。
- **`fact_weekly` 的分区列是 `report_date_week`，不是 `report_date`。**
  照日报列名写周报查询直接 `UndefinedColumn`。

### 竞对监控

- **下架判定只在 `catalog_complete` 为真时做**（Shopify：翻页自然终止 + 取到的商品数不少于
  `/meta.json` 声称的 `published_products_count`）。被动 ASIN 上报是推模型，
  "这次没见到"≠"已下架"，不得据此判定下架。
- **新品判定看 `product_id` 在不在库里，不看 `published_at`**——商户批量重新发布会让日期
  整体跳变，据此判定会伪造一批假上新。
- **零条有效 ASIN 的上报整批拒绝（422），不得落定空基线**——否则下一次正常上报会把该卖家
  全部存量商品误判成新发现，界面表现只是数字突然涌出一批，不报错。

---

## 维护

`CLAUDE.md` 是唯一事实来源。**新增约定写进 `CLAUDE.md`，不要写进这里**；
只有当某条约定确实只对某一种运行环境成立时，才在本文件加一条差异说明。
（「Code Review Rules」一节属后者：它是 Codex 审核运行时的专属配置，随 CLAUDE.md
对应条目同步维护。）
