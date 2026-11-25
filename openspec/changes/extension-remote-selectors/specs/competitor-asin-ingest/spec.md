## ADDED Requirements

### Requirement: 服务端下发采集端页面选择器覆盖

采集端配置端点 SHALL 在单批上限之外返回可选的页面选择器覆盖 `selectors` 及其版本 `selectors_version`。覆盖来自部署方配置的 JSON 文件；未配置、文件缺失或内容不合法时，两者 SHALL 为 `null`，且 MUST NOT 影响单批上限的返回。文件修改后 SHALL 无需重启即在后续请求中生效。

覆盖只包含 CSS 选择器与纯文本，MUST NOT 包含可执行代码或正则表达式。只接受约定的键（`result_card`、`next_page`、`seller_name`、`delivery`、`blocked_selector`、`blocked_text`），每个键为有长度上限的字符串列表；出现未知键或任一字段不合法时，整份覆盖视为无效并记录告警，MUST NOT 部分生效。

#### Scenario: 配置了合法的选择器文件

- **WHEN** 部署方把选择器文件配置为合法内容，版本为 `2026-11-26a`
- **THEN** 端点返回单批上限，以及与文件一致的 `selectors` 和 `selectors_version="2026-11-26a"`

#### Scenario: 修改文件后无需重启

- **WHEN** 部署方把文件里的版本改为 `2026-11-26b` 并保存
- **THEN** 下一次请求返回的 `selectors_version` 为 `2026-11-26b`

#### Scenario: 文件不合法

- **WHEN** 选择器文件含未知键，或某个列表超过长度上限
- **THEN** 端点返回 `selectors: null`、`selectors_version: null`，单批上限照常返回，服务端记录告警

#### Scenario: 未配置

- **WHEN** 部署方没有配置选择器文件
- **THEN** 端点返回 `selectors: null`、`selectors_version: null`
