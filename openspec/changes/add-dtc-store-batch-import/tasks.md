## 0. 开工前置

- [x] 0.1 按 `start-change` 认领并建仓库外 worktree `~/.claude-worktrees/add-dtc-store-batch-import`
      与分支 `change/add-dtc-store-batch-import`（从含本变更文档的提交开出）；
      补 `.env` 与 `frontend/node_modules`
- [x] 0.2 `keywordiq_test` 可用（活动连接 0）。基线：后端 **735 passed, 1 skipped**、
      前端 **38 files / 359 tests**

## 1. 后端

- [x] 1.1 `store_schemas.py`：`StoreBatchAddRequest`（`domains: list[str]`、`group_id?`，
      条数上限在 schema 层校验）+ `StoreBatchAddResponse`（`added` / `already_watched` /
      `rejected`，**每条都回指用户粘的那一行原文**，不只回规范化后的域名）
- [x] 1.2 `store_service.add_stores_batch()`：逐行规范化（复用 `normalize_store_domain`，
      **不另写一份**）→ 按规范化后的域名去重 → 查已关注 → 并发探测（`asyncio.Semaphore(8)`，
      **共用同一个 `AsyncClient`**）→ 写站点行与关注关系
- [x] 1.3 三类拒绝原因复用单条路径已有的常量（`STORE_NOT_SHOPIFY` / `STORE_UNREACHABLE` /
      `REJECT_MALFORMED_DOMAIN` / `REJECT_AMAZON_DOMAIN`），**不合并成一句「N 条失败」**
- [x] 1.4 配额：沿用 `COMPETITOR_MAX_WATCH_PER_USER`，**超限整批拒绝不截断**（422）
- [x] 1.5 `store_api.py` 加 `POST /stores/batch`，挂既有的 `require_permission(COMPETITOR_VIEW)`
- [x] 1.6 集成测试：三类结果同批出现且各带不同原因、全部被拒时请求仍 200、
      同批重复域名只处理一次、超条数上限整批拒绝且一条都不写、批量与单条对同一个带路径
      带查询串的链接得到相同结果、批量里的亚马逊链接给出与单条相同的去处提示

## 2. 前端

- [x] 2.1 `services/competitor.ts` 加 `addStoresBatch`
- [x] 2.2 既有「添加独立站」弹窗加「批量粘贴」tab（多行 textarea），**不新开屏**
- [x] 2.3 提交后就地展示三段结果（已加入 / 已关注 / 失败附原因）；
      加载态明确（探测可能十几秒，要说清正在做什么）
- [x] 2.4 前端单测：三段结果都渲染、失败段逐条显示原因而不是只显示条数、加载态

## 3. 收尾

- [x] 3.1 CLAUDE.md 竞对监控一节补一句批量入口与「为什么不是两步流」
- [x] 3.2 `ruff` + `pytest -m conventions` + 后端全量（带 `TEST_DATABASE_URL`）+
      前端 `lint`/`tsc`/`test` 全绿
- [x] 3.3 人工验收（一次性刮擦库，用完即删）。8 条真实输入 5.5 秒跑完：3 个真 Shopify
      正确加入（含 blessrich 的 **GBP**）、同站重复去重、4 条各带不同拒绝原因。

      **抓到一个真缺陷并修复**：`baidu.com` 被判成「无法访问该站点」——它访问得通，
      只是不是 Shopify。根因是 `/meta.json` 返回 404 抛的是 `HTTPStatusError`，被
      `except httpx.HTTPError` 一网打尽归进了「打不开」。用户会对着一个好好的站点反复
      重试。**单条路径有同样的问题**，一并修了：`HTTPStatusError` 归「不是 Shopify」
      （服务器答复了、只是没有 meta），只有超时/DNS/连接被拒才算「无法访问」。
      两条入口共用同一分类，分叉会让同一站点在两处得到不同理由。已补 2 个回归用例，
      并复验通过
- [ ] 3.4 按路径提交、开 PR（`--base v2`，带变更 id、`Closes #62`、spec delta 文件清单）
