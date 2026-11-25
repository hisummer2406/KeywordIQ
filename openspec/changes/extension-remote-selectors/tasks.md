# 任务

## 1. 后端

- [x] 1.1 `config.py` 新增 `COMPETITOR_COLLECTOR_SELECTORS_FILE`（默认空），`.env.example` 同步
- [x] 1.2 选择器文件读取与校验：按 mtime 缓存，只认约定键与长度上限，不合法整体忽略并告警（design 决策 1）
- [x] 1.3 `CollectorConfigResponse` 新增 `selectors`、`selectors_version`，端点返回
- [x] 1.4 单元测试：合法文件、改文件不重启生效、未知键、超长、未配置、文件不存在

## 2. 插件

- [x] 2.1 `fetchCollectorConfig` 解析并校验覆盖形状；`ConfigResult` 带上覆盖与版本
- [x] 2.2 运行状态快照 `selectors`、`selectorsVersion`，`AUTO_SCAN` 携带覆盖（design 决策 3）
- [x] 2.3 解析函数接受覆盖：下发值在前、内置在后；拦截文案纯文本合并；非法 CSS 跳过（design 决策 2）
- [x] 2.4 设置页显示选择器版本；人工面板不变；版本升到 0.3.3，README 写明用法
- [x] 2.5 单元测试：形状校验、下发值优先、写错时回落、拦截文案合并、快照跨恢复不变、人工路径不受影响

## 3. 验证

- [x] 3.1 后端定向 pytest；插件 typecheck、test、build；`openspec validate --strict --all`；`git diff --check`
- [ ] 3.2 真实 Chrome：本地后端配置选择器文件，改版本号后下一轮运行记录显示新版本（随 #205 试跑一起做）

2026-11-26 Claude：后端定向 pytest（选择器 + collector-config + conventions）32 项通过，ruff 通过；插件 typecheck 通过，test 17 个文件 194 项通过（新增 20 项），build 成功；`openspec validate --strict --all` 51 项通过；`git diff --check` 通过。3.2 未做。
