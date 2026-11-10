## Why

「风险核查」菜单下的外观专利和审核待办一直是占位页（#24）。运营上新前要人工判断外观专利风险，现在靠 `docs/河南国曼侵权审核表(1).xlsx` 这类 Excel 手填：检索没有留存证据，意见不绑定产品版本和市场，换图后旧结论还被沿用。用户已选定智慧芽 Data REST API（与网页版账号、官方 MCP 是三个独立付费项目，本期只用 REST API），需要一个能走通的闭环：配置 Key → 上传产品图检索外观专利 → 阅读候选并按需补充著录和法律状态 → 按市场填写人工意见 → 回看历史证据。

本变更**不涉及**日报或周报路径：核查是独立数据域，不读写 ABA 事实表、分区、物化视图，不参与保留清理，不取导入咨询锁。

## What Changes

- 新增智慧芽供应商配置（仅 `is_super` 可管理）：API Key 用服务端主密钥加密落库，从不回显明文，停用或清除时留下不含密钥的审计记录。「已保存」与「已验证」分开显示，验证是一个明确说明会产生一次 P011 调用的按钮。
- 新增外观专利核查单：产品名称与版本快照、销售市场（来自 `site_registry`）、明确选择的检索法域、一张 JPG/PNG 原图（以实际内容校验，不超过 4 MB）。提交后生成持久化任务，由后台轮询原子认领，按 P010 上传 → P060 检索执行。刷新或关闭页面后可以恢复，轮询页面不会再次调用智慧芽。
- 证据留存：每页检索结果的请求参数、实际范围（已读取 N / 来源总数 M）与原始响应；候选专利；用户显式请求的 P011 著录和 P013 法律状态；每一次供应商调用的结果与「计费未知」标记。
- 失败语义：无配置、已停用、Key 无效、无接口权限、余额或次数不足、过期、限流、超时、上游 5xx/HTML、响应结构异常分别归类；超时、中断、无法解析的调用记为「未知」，不自动重发；所有失败都不归为「无命中」，也不清除应用登录态。
- 新增按市场的人工意见：追加保存、绑定核查单版本与证据引用；条件通过必须填写条件、证据和复核日期。无命中不会自动变成低风险或可上架。换图、换版本或增加市场要新建核查轮次并关联上一轮。
- 新增权限点 `risk:view`、`risk:search`、`risk:review`，迁移种子只授予 admin 角色。所有记录、图片、证据和意见都按创建人私有，越权返回 404（超管也不例外）。
- 前端：`/ip/patent` 替换为核查表单，`/ip/review` 替换为核查列表，新增详情页 `/ip/review/:id` 和超管配置页 `/ip/settings`。商标和风险案例保持占位。「风险核查」菜单从占位守卫改为 `risk:view`。

## Capabilities

### New Capabilities
- `patent-risk-provider`：智慧芽供应商配置、凭证加密、固定源站客户端、接口契约、错误分类与调用留痕。
- `patent-risk-check`：核查单创建、原图、持久化任务与认领、检索分页证据、按需详情、幂等、私有性与权限。
- `patent-risk-review`：按市场的人工意见、核查轮次、列表与详情页交互。

### Modified Capabilities
- `top-nav-structure`：「风险核查」菜单由「不设权限点的占位容器」改为由 `risk:view` 把守，外观专利和审核待办接入真实端点，`/ip` 重定向到审核待办，新增超管可见的「接口配置」。

## Impact

- 后端：新增 `app/table/risk/`（api/service/crud/model/schemas/provider client），挂到 `app/api_router.py`；`main.py` 启动一个独立的风险任务轮询。
- 数据库：新增 `risk_*` 普通表，登记 `migration_scope.V2_TABLES`；手写迁移同时写入权限种子。
- 依赖：新增运行依赖 `cryptography`（Fernet/MultiFernet）。
- 配置：新增 `RISK_CREDENTIAL_KEYS`（主密钥，可逗号分隔轮换）、`RISK_TASK_POLL_SECONDS`、`RISK_PROVIDER_TIMEOUT_SECONDS`，同步 `.env.example`。生产主密钥由用户写入 ECS `.env`，本变更不自动设置。
- 前端：重写 `frontend/src/pages/IP/DesignPatent`、`IP/Review`，新增 `IP/ReviewDetail`、`IP/Settings`、`services/risk`；`access.ts`、`routes.ts` 改用真实权限。
- 外部：只有用户提交、翻页、补充详情、验证时调用 `https://connect.zhihuiya.com`；真实 Key 的接口权益、单价、QPS 与展示授权待联调核实。
- Issue：Refs #24。#25（商标）与内部案例、旧表导入、团队协作不在本期。
