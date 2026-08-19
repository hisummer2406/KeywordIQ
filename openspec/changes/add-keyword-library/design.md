## Context

分析查询屏是一次性的：每次进去摆条件、查一次、看完离开。用户长期关心的那几十个词
（自己在推的、竞品的）没有任何地方能记下来——`dim_keyword` 是导入产物，不接受用户写入，
仓库里也没有任何「用户自建清单」的概念。

已有的地基决定了本设计的大部分形状：

- `dim_keyword` 是 `(site, keyword)` 唯一，**关键词天生按站点隔离**；
- 搜索响应的每一行已经带 `keyword_id`（`search_schemas.py`），前端勾选后可直接回传 id，
  不必按文本反查；
- 保留清理只 `DROP` 事实表的月度分区，`dim_keyword` 与其它维度表不参与——所以指向
  维度表的外键永不悬空；
- 工具箱下已有一屏（持续增长市场）走 `toolbox:view`，crud 出语句 / service 异步执行的
  分层已经成型，照抄即可。

用户已定的五条：独立盯盘页（不做分析屏筛选联动）、分组按站点、个人私有、只从分析屏加词、
工具箱二级菜单复用 `toolbox:view`。

## Goals / Non-Goals

**Goals:**

- 用户能把分析屏里看到的词攒成清单，之后打开一屏就看到这批词今天的排名与升降。
- 词掉榜、某期无数据时行仍在且状态明确——盯盘的价值有一半在这里。
- 与分析屏读到的是同一套口径与同一份列投影，不产生第二份指标定义。
- 新增的两张表与既有导入/查询/保留三条路径完全解耦，不改任何写入路径。

**Non-Goals:**

- 不做「只看词库里的词」这个分析屏筛选维度（用户明确选了独立盯盘页）。
- 不做分组共享、转让、团队可见性。
- 不做跨站点分组（同一批词盯三个站点就建三个组）。
- 不做词库屏的手工录入 / 粘贴导入。
- 不新增权限点。
- 不做条目的备注、标签、优先级等清单内的二级组织。

## Decisions

### 1. 两张表，条目只存 `keyword_id`

```
keyword_group        group_id PK | user_id → user_center | site | name | created_at | updated_at
                     UNIQUE (user_id, site, name)
keyword_group_item   group_id → keyword_group | keyword_id → dim_keyword | added_at
                     PRIMARY KEY (group_id, keyword_id)
```

条目**不存关键词文本、也不存站点码**：`keyword_id` 已唯一确定 `(site, keyword)`。

**备选：条目存关键词文本**（不引用维度表）。被否，因为那会让同一个词有两份事实来源，
而两份必然分叉——本仓库已经因此吃过亏（`sites` 参考表、`GB`/`UK` 两种写法）。存文本
唯一能换来的好处是「维度行消失时仍留痕」，但那件事不会发生：保留清理只碰事实分区。

复合主键 `(group_id, keyword_id)` 顺带把去重做掉了——重复加词是 `ON CONFLICT DO NOTHING`，
天然幂等，不需要先查后写。

### 2. 分组绑定站点，站点不可改

**备选：分组跨站点**（只存词的文本，盯盘屏顶上单独选站点）。被否有两个理由：一是它与
决策 1 直接冲突（跨站点意味着不能存 `keyword_id`）；二是界面后果——英文词在 DE 站
多半没有行，表格会出现大片空行，而「这个站点根本没这个词」和「有这个词但掉榜了」在
一张表里必须分辨，等于给一个次要场景加一整套状态语义。

站点在建组时定死。允许改站点等于把整组内容换掉，那不如新建一个组。

### 3. 私有性靠 where 条件，不靠可见性字段

每条分组/条目查询都带 `user_id = <当前用户>`；取不到自己的分组一律 404（不是 403——
403 会泄露「这个 id 确实存在」）。

**备选：加 `is_public` 列做「私有但可标为公开」**。被否：多一列就多一套可见性判定，
此后每个端点都要跨这道判定，而需求方明确选了纯私有。真需要共享时再加，那时它是加法。

用户被删除时分组连带删除（外键 `ON DELETE CASCADE`）。无主分组既查不到也删不掉，
留着只有坏处。

### 4. 盯盘查询：由条目表驱动的一次 LEFT JOIN

```sql
FROM keyword_group_item i
JOIN dim_keyword k       ON k.keyword_id = i.keyword_id
LEFT JOIN fact_{daily|weekly} f
       ON f.site = :site AND f.<date_col> = :on AND f.keyword_id = i.keyword_id
LEFT JOIN fact_dropped d
       ON d.site = :site AND d.period_type = :period AND d.anchor_date = :on
      AND d.keyword_id = i.keyword_id
WHERE i.group_id = :gid
```

行集合是条目，事实表只补数。状态由两个 LEFT JOIN 的命中情况决定：`f` 命中 = 在榜，
`f` 空而 `d` 命中 = 掉榜，都空 = 无数据。

**备选：两步取数**（先分页取条目，再按 ≤100 个 id 批量取事实行，Python 里拼），也就是
分析屏「先分页再批量补附加」的那套。被否于一点：**排序**。盯盘的默认排序是当期排名，
而排名在事实表里，两步法在第一步就没有排序依据。

单查询的代价是要依赖规划器选对驱动顺序，而这正是决策 6 的上限在兜底：一个组至多 500 条，
最坏情况也就是 500 行的嵌套循环，两侧都走现成索引——`idx_fact_{daily,weekly}_keyword_history`
是 `(site, keyword_id, 日期列)`，`fact_dropped` 的主键是 `(site, period_type, anchor_date,
keyword_id)`，都恰好覆盖这两个 JOIN。

走势与历史排名仍走既有的两步：分页后拿 ≤100 个 `keyword_id` 调 `fetch_trends`，
`shape_trends` / `shape_trend_dates` **必须传 `on`/`view`**（漏传会画出天 8 点 / 周 13 点，
且没有任何地方会报错——导出路径踩过一次）。

排序里的三段序（在榜 → 掉榜 → 无数据）由一个状态序表达式承担，**不随升降序翻转**，
与分析屏「掉榜行恒排在在榜行之后」同一约定。

### 5. 列投影抽成共用函数，不复制第二份

`analysis_crud._base_query` 里那段三十来列的投影抽成 `fact_row_columns(fact)`，
`_base_query` 与词库盯盘各自引用。

**备选：词库侧照抄一份**。被否：将来加一列指标要改两处，漏改的那处不报错，表现是
同一个词在两屏显示不同的值——这类分叉在本仓库有前科（列注释漂移曾盖住一条真差异）。

抽取本身不改变分析屏的 SQL，回归由既有的查询集成测试覆盖。

### 6. 上限 20 组 / 站点 / 用户、500 词 / 组，超限拒绝不截断

超限返回 422 并说明当前数与上限，**整批不写**。静默截断的清单与用户以为存进去的不是
同一份，而他无从察觉——与导出行数上限同一条纪律。

500 这个数同时是决策 4 的性能保证（见上）。两个上限都进 `config`，`.env.example` 同步。

### 7. 代码落位：`app/table/toolbox/library_*.py`，路由 `/api/toolbox/library`

与菜单位置和权限点保持一致，与 `growth_*` 并列。分层照旧：model / crud（只出语句）/
service（async 执行 + 业务判定 + 抛 `APIError` 子类）/ api（瘦控制器，不做 try/except 转译）/
schemas（Pydantic v2）。

端点：分组 CRUD 四个、条目批量增删两个、`POST /watch`、`GET /scope`。全部挂
`toolbox:view` + `get_current_user`。

### 8. Alembic 迁移

两张都是**普通表**，不分区、无物化视图，因此可以 `--autogenerate`（分区表那条手写规则
在这里不适用）。步骤：

1. 两个模型登记进 `migration_scope.py` 的 `V2_TABLES` ——**漏登的表 autogenerate
   看不见也不报错**，对模型改动一律回答「没有差异」。
2. `uv run alembic revision --autogenerate -m "词库分组与条目"`，人工核对生成的 DDL
   带上了 `schema='analysis'`（schema 由 `database.Base` 的 metadata 统一提供，模型里
   不硬编码）。
3. 列注释必须与模型里的文本**一字不差**，否则 `test_v2_integration.py::
   test_models_match_migrated_schema` 会红——它要求 autogenerate 对迁移到 head 的库
   产出空结果。跨表复用的 `site` 注释取 `column_comments.SITE_COLUMN_COMMENT`。

回滚是 `downgrade` 里 `DROP TABLE` 两张，无数据迁移、无 backfill；表是新的，回滚不影响
任何既有数据。

### 9. 前端

- **新屏** `frontend/src/pages/Toolbox/KeywordLibrary/`：左侧分组列表（站点切换 + 增删改），
  右侧 ProTable。进屏自动查（判据见 spec：盯盘的空表只有一种含义，与增长榜屏相反）。
- **分析屏**：ProTable 加 `rowSelection` + 批量操作条。选中状态**不进 `stateCache`**，
  因此**不动 `CACHE_VERSION`**。
- 复选框列会改变表格的列宽分配，`scroll.x` 要同步加宽，改完**实测**而不是推算
  （本仓库在像素对齐上连错过三轮，两次都是靠 `getBoundingClientRect` 才定位）。

## Risks / Trade-offs

- **[同一批词想盯三个站点要建三个组]** → 接受。这是决策 2 的直接后果，替代方案的代价
  （大片空行 + 两种"没有"要分辨）更贵。若日后真有跨站点对比需求，那是一个独立的
  「跨站点对比」功能，不是把分组放宽。
- **[分析屏加复选框列挤压既有列宽]** → 改完用实测数核对列宽与 `scroll.x`，不靠推算。
- **[LEFT JOIN 的驱动顺序被规划器选反]** → 500 条上限使最坏情况仍然廉价；集成测试
  用满员分组（500 条）跑一次盯盘查询，防止将来放宽上限时无声退化。
- **[两个上限是拍出来的]** → 都进 config 可调；上调 500 前必须重测决策 4 的查询。
- **[抽取共用列投影动了分析屏的热路径]** → 纯重构、不改语义，由既有查询集成测试兜底；
  抽取与新功能分成两个提交，出问题时能单独回退。

## Migration Plan

1. 建表迁移（决策 8），`alembic upgrade head`。表是空的，无 backfill。
2. 后端上线（新端点不影响既有端点）。
3. 前端上线（新菜单项 + 分析屏多选）。
4. 回滚：`alembic downgrade -1` 删两张表；前后端各自回退上一版本。无数据损失风险
   ——除了用户自己刚建的清单。

按 V2 的既有约定，本变更不单独发布，随 V2 整体上线。

## Open Questions

无。五个待定项（用途、站点归属、归属人、入库方式、权限点）已由需求方逐条定下，
issue #37 里剩下的那条「词从 `dim_keyword` 消失怎么办」在架构上不成立——维度表不参与
保留清理。
