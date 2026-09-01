## Why

`dtc-store-monitoring` 落地时确认了一件事：独立站此前没有好办法收藏，所以运营手上没有
现成清单——但**量级不是几百家，只有一次性的初始装载，之后遇到一个加一个**（issue #62）。

因此长期主路径就是既有的单条添加表单，它已经够用；缺的只是初始装载那一次的批量入口。
逐个粘贴几十次可行但烦人，且中途看不到「这批里哪些能用」。

**本变更既不影响日报路径也不影响周报路径**——它不读写 `fact_daily` / `fact_weekly` /
`dim_*`，只在竞对监控这个数据域内新增一个端点与一个界面 tab。

## What Changes

- 新增 `POST /api/competitor/stores/batch`：一次提交多个域名，**一步完成**，返回逐条结果
  （成功 / 已关注 / 拒绝附原因）
- **明确不引入预览 + 确认两步流**。卖家侧那套是为两个这里不存在的条件设计的：
  （1）2657 条来自书签文件，用户不可能预先知道里面有什么；（2）`replace_group` 是破坏性的。
  这里是用户自己粘的几十个域名，而且加站点**完全可逆且无损**——取关是一次点击，
  已抓到的数据还留着
- 探测并发但有上限；单次条数有上限且**超限拒绝不截断**
- 界面：既有「添加独立站」弹窗加一个「批量粘贴」tab，不新开屏

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `dtc-store-monitoring`: 新增批量加入的行为要求——逐条附原因、并发与条数上限、
  与单条路径共用同一套规范化与拒绝理由

## Impact

**修改代码**

- `app/table/competitor/store_schemas.py`：批量请求与逐条结果 DTO
- `app/table/competitor/store_service.py`：`add_stores_batch()`
- `app/table/competitor/store_api.py`：一个端点
- `frontend/src/services/competitor.ts` + `pages/Competitor/StoreTab.tsx`：弹窗加 tab

**不新增**：表、迁移、配置项（条数上限沿用既有的
`COMPETITOR_MAX_WATCH_PER_USER` 语义，并发上限是实现常量）、权限点、依赖。

**不受影响**：单条添加路径、拉取与写入层、Amazon 卖家侧、ABA 导入与查询。
