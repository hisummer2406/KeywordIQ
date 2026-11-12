> 进度记录：2026-11-12 Claude（Fable 5.2）认领 `change/add-opportunity-signal-leads`，worktree `~/.claude-worktrees/add-opportunity-signal-leads`（基于已含 PR #187 的 v2 `fa0556b`）。用户授权「继续其他专家的开发，根据你的判断」；合并与发布另行授权。

## 1. 后端

- [x] 1.1 `leads_service.py`：调用 `growth_service.search` / `get_scope`，按当页 keyword_id 标注未归档档案（design 决策 1、2；spec「线索列出…」「线索标出…」）
- [x] 1.2 `leads_api.py`：`GET /opportunity/leads/growth`，只挂 `opportunity:view`，接入 `api_router`（决策 3）
- [x] 1.3 真库集成测试 `test_opportunity_leads_integration.py` 5 项：榜单与口径透传、缺期回传、分页、档案归属（未归档计入、已归档不计、跨站点不计）、非法站点与无数据日期 422、空站点返回空、路由权限

## 2. 前端

- [x] 2.1 `services/opportunityLeads.ts`；`Leads/LeadsView.tsx`（站点、锚定日、口径与缺期说明、勾选至多 20 个、建档、并入同站点未归档档案）；左栏「线索」（spec「从线索建档或并入…」）
- [x] 2.2 新建档案页支持带入站点与关键词（决策 4）
- [x] 2.3 `experts.ts` 与专家页按决策 5 更新；新建任务页只列任务型专家；`experts.test.ts`

## 3. 验证与交付

- [x] 3.1 `uv run ruff check .` 通过；`pytest test/test_opportunity_leads_integration.py`（一次性真库）5 项通过；全量 `pytest` 由提交门禁运行；`openspec validate --strict --all` 通过
- [x] 3.2 前端 `pnpm tsc`、`pnpm lint` 通过；`pnpm test` 61 个文件 643 项通过
- [x] 3.3 演示库（生产只读导出的 US 11-10 持续增长榜 8 个词）经 HTTP 走通：线索列表已标出此前建档的 3 个直发梳词 → 用 3 条 brine 线索新建档案 → 把 1 条线索并入已有档案 → 再查线索，归属随之更新；非法站点 422
- [ ] 3.4 PR 回 v2；浏览器验收清单交给用户（实现方没有做浏览器实测）
