# 任务

## 1. 实现（extension/）

- [x] 1.1 自动采集设置的存取与默认值：开关、每日时刻、页数 N、店铺数上限（Requirement「自动采集默认关闭，只在设置页开启」）
- [x] 1.2 纯函数：店铺页 URL 构造、店间间隔、页面拦截识别、下一步决策（design 决策 2、4）
- [x] 1.3 读取关注清单客户端（`POST /api/competitor/watches` 分页读取）
- [x] 1.4 后台状态机：alarms、tabs 事件、持久化与恢复、复用 `handleCapture` 上报（design 决策 3、6；Requirement「运行进度可恢复，上报保持幂等」）
- [x] 1.5 content script：响应解析请求、识别拦截页、自动采集标签页不渲染人工面板（design 决策 7）
- [x] 1.6 设置页：自动采集区块、立即运行、停止、最近一轮汇总、导出 JSON（Requirement「每轮运行结果可查看与导出」）
- [x] 1.7 manifest 新增 `alarms` 权限，manifest 与 package 版本升到 0.3.0；README 写明试点用法、升级需重新授权、试点只连本地后端

## 2. 验证

- [x] 2.1 单元测试覆盖 1.1–1.4 的纯逻辑与状态机关键路径（拦截停止、空结果不上报、恢复时重发同一 request_id、多页合并只上报一次）
- [x] 2.2 `pnpm run typecheck && pnpm run test`
- [x] 2.3 `openspec validate --strict --all`

2026-11-24 Claude：`pnpm run typecheck` 通过；`pnpm run test` 15 个文件 135 项全部通过（新增 auto-plan 17、auto-runner 11、watch-client 3）；`pnpm run build` 成功；`openspec validate --strict --all` 50 项通过；`git diff --check` 通过。未在真实 Chrome 和 Amazon 页面上运行，content script 的 AUTO_SCAN 应答与拦截页识别只在 3.3、3.4 人工验证。

2026-11-24 Claude（PR #206 Codex 两条 P2）：翻页时按 `docId` 忽略旧文档的回答；新增运行期间看门狗 alarm 补建丢失的唤醒源；拿不到 content script 回答改为交给页面超时判断（design 决策 3）。新增 4 项测试，`pnpm run typecheck` 通过，`pnpm run test` 15 个文件 139 项全部通过。

## 3. 开跑前检查（人工，代码合并后）

- [ ] 3.1 宿主机网络出口与卖家后台分开
- [ ] 3.2 本地开发后端就绪，试点账号关注 10–20 家真实店铺（含小店和多页店），为试点账号签发长有效期令牌并通过「手动填写令牌」填入
- [ ] 3.3 插件 API 地址指向本地后端，开启自动采集，先「立即运行一次」观察 2–3 家店
- [ ] 3.4 真实页面确认：带 `i=merchant-items` 的按最新排序链接仍被 content script 识别，解析到的条目与页面一致

## 4. 试跑与结论

- [ ] 4.1 连续 5 天运行，按 #205 门槛统计：可用率 ≥ 90%、验证码或访问拒绝 0 次、人工介入每周不超过 1 次，并抽查假新发现比例
- [ ] 4.2 结果补到 #205，与 #203 的 Keepa 实测对比后决定阶段 2
