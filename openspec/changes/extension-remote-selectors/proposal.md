## Why

#205 阶段 1 试跑最大的风险是插件里的 DOM 选择器（配送地址、下一页、商品卡片、拦截页）在真实 Amazon 页面上对不上。现在选择器写死在插件代码里，改一次就要重新构建、重新加载插件；无人值守的宿主机上，一次选择器失效会让当天自动采集全部停止，直到有人来换插件。

MV3 禁止加载远程代码，但允许读取远程数据。选择器只是数据，可以由服务端下发：服务端改一个配置文件，插件下一轮自动采集就用上新值，不用重装。

## What Changes

- 服务端新增配置 `COMPETITOR_COLLECTOR_SELECTORS_FILE`，指向一个 JSON 文件（版本号 + 各类选择器列表）。文件修改后无需重启即生效；文件缺失或不合法时不下发覆盖，并记录告警。
- `GET /api/competitor/collector-config` 在原有 `max_report_items` 之外返回 `selectors` 与 `selectors_version`，没有覆盖时两者为 `null`。
- 插件自动采集在每轮开始时读取覆盖并快照进运行状态；解析页面时先用服务端下发的选择器，再回落内置默认值。只接受 CSS 选择器和纯文本，不接受正则或代码；不合法的选择器跳过。
- 运行记录和设置页显示本轮使用的选择器版本（无覆盖时显示「内置」）。
- 人工采集面板仍只用内置选择器，行为不变。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `competitor-asin-ingest`：采集端配置端点新增下发页面选择器覆盖。
- `competitor-browser-collector`：自动采集使用服务端下发的选择器覆盖，并记录版本。

## Impact

- 后端：`config.py` 新增一项配置；`app/table/competitor/` 新增选择器文件读取与校验，`asin_schemas.CollectorConfigResponse` 新增两个可空字段；`.env.example` 同步。
- 插件：`collector-config.ts`、`messages.ts`、`auto-runner.ts`、`auto-plan.ts`、`parse-storefront.ts`、`content-script.ts`、`auto-options.ts`；版本升到 0.3.3。
- 不涉及数据库、迁移、日报或周报导入路径；不改变上报、基线和新发现的任何判定。
