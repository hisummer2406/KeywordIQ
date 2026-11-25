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

2026-11-24 Claude（首轮本地试跑后）：首轮 20 家全部跑完、无拦截，但 13 家第 1 页为 0、其余多数只有 1–3 个商品。用户人工确认原因是 Chrome 的 Amazon 配送地址不是美国：三种链接都 No results，改成美国邮编 10001 后整页都有商品。插件新增配送地址检查（不符整轮停止，design 决策 4），版本 0.3.1；新增 6 项测试，typecheck 通过，test 15 个文件 145 项全部通过。首轮在错误配送地址下写入的试点库数据已清空。

## 2A. 阶段 1 采集质量（2026-11-25，#205「当前接手范围」）

- [x] 2A.1 配送位置按目标邮编校验：设置里按站点存目标邮编（默认 US 10001，设置页可改美国站）；每页核对，确认不符记 `wrong_delivery`，读不到／只剩 Deliver to／无目标／无法判断记 `delivery_unconfirmed`，都在当前店停止整轮，已提交的店保留（design 决策 4「配送位置校验」）
- [x] 2A.2 分页终止分类：自然到达末页 `coverage: complete`；达到 N 页仍有下一页 `coverage: limited` 照常合并上报；第 1 页空 `empty_page`、前页有下一页而后页无有效 ASIN `missing_page`，均不上报（Requirement「每轮按关注清单逐店采集按最新排序的前 N 页」）
- [x] 2A.3 运行记录与设置页：每店记站点、页数、有效 ASIN 数、覆盖情况、停止／失败原因；汇总区分已处理、有效（含覆盖有限）、失败；显示配送核对的站点、目标与实际文字
- [x] 2A.4 回归测试：正确邮编、China、Berlin 10115 Germany、空地址、只剩 Deliver to、无目标邮编、翻页时漂移、第二家不符保留第一家、达到上限覆盖有限、恰好到末页、异常缺页、恢复时同一请求体与覆盖标记
- [x] 2A.5 版本升到 0.3.2；README 试点约束写明专用 Profile 手动设 US / 10001

2026-11-25 Claude：见 PR #207 本轮提交说明中的验证结果（typecheck / test / build / openspec / diff check）。真实 Chrome 中选择器与配送校验尚未验证，归 3.4。

## 3. 开跑前检查（人工，代码合并后）

- [ ] 3.1 宿主机网络出口与卖家后台分开；专用 Chrome Profile 手动把 Amazon 配送位置设为 `US / 10001`，设置页美国站目标邮编为 10001
- [ ] 3.2 本地开发后端就绪，试点账号关注 10–20 家真实店铺（含小店和多页店），为试点账号签发长有效期令牌并通过「手动填写令牌」填入
- [ ] 3.3 插件 API 地址指向本地后端，开启自动采集，先「立即运行一次」观察 2–3 家店
- [ ] 3.4 真实页面确认：带 `i=merchant-items` 的按最新排序链接仍被 content script 识别，解析到的条目与页面一致；页头配送选择器能读到 `New York 10001`，把配送改成 China 时整轮停止；多页店的覆盖有限／自然到末页标记与页面一致

## 4. 试跑与结论

- [ ] 4.1 连续 5 天运行，按 #205 门槛统计：可用率 ≥ 90%、验证码或访问拒绝 0 次、人工介入每周不超过 1 次；每天记录分母、有效店数、覆盖有限店数、失败／停止原因、人工介入，并抽查假新发现（样本与比例，门槛待定）
- [ ] 4.2 结果补到 #205，与 #203 的 Keepa 实测对比后决定阶段 2
