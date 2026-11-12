## RENAMED Requirements

- FROM: `### Requirement: 著录与法律状态按需批量获取，成功结果不重复计费`
- TO: `### Requirement: 专利详情按需获取，成功结果不重复计费`

## MODIFIED Requirements

### Requirement: 专利详情按需获取，成功结果不重复计费

用户 SHALL 能对已出现在本核查单中的候选请求补充四种详情：著录（P011）、法律状态（P013）、全部附图（P042）、公报 PDF（P020），并选择要哪几种。只含著录和法律状态时一次最多 20 件；含附图或公报时一次最多 10 件。提交前界面 SHALL 说明会产生哪些调用：P011、P013 各一次批量调用；P042、P020 按件各调用一次，附图较多时 P042 会翻页多次。金额未知时不编造金额。

任务 SHALL 只对尚无成功结果的（候选，种类）发起调用；已成功获取的结果直接复用，页面重绘或再次打开 MUST NOT 重新调用。某一项失败 SHALL 只标记这一项，已获得的证据保留。P042 翻页中途失败时，该件附图 SHALL 按失败那次调用定性（`failed` 或 `unknown`），MUST NOT 把不完整的附图集记为成功。请求中不属于本核查单的候选 ID SHALL 被拒绝。

详情任务处理多件时 SHALL 在执行期间续租，MUST NOT 因为正常耗时超过单次租约而被回收为 `unknown`。

#### Scenario: 部分失败

- **WHEN** P011 成功、P013 超时
- **THEN** 著录显示为已获取，法律状态显示「结果未知」，可以再次显式请求法律状态

#### Scenario: 篡改候选 ID

- **WHEN** 请求中包含其他核查单的候选 ID
- **THEN** 返回 404，不创建任务

#### Scenario: 附图翻页

- **WHEN** 某件专利附图总数为 26，P042 每页取 20
- **THEN** 该件产生两次 P042 调用并都留痕，附图记为成功，共 26 张

#### Scenario: 含附图时选了 12 件

- **WHEN** 用户勾选 12 件候选并选择全部附图
- **THEN** 返回 422，不创建任务

#### Scenario: 获取 10 件附图耗时超过租约

- **WHEN** 详情任务处理 10 件附图，总耗时超过 `RISK_TASK_LEASE_SECONDS`，但每件之间都在续租
- **THEN** 任务正常完成，不被回收为 `unknown`

### Requirement: 供应商返回内容安全展示

供应商返回的标题、权利人等文本 SHALL 作为纯文本渲染，MUST NOT 当作 HTML 执行。附图和外链 SHALL 只接受 `https` 协议，其他协议（如 `javascript:`）SHALL 不渲染。检索命中图 SHALL 显示取证时间，超过 9 小时的 SHALL 提示「图片链接可能已过期」。系统 MUST NOT 镜像检索结果中的命中图；只有用户显式获取的全部附图与公报 PDF 按「附图与公报存档」要求存档。

#### Scenario: 标题含 HTML

- **WHEN** 候选标题为 `<img src=x onerror=alert(1)>`
- **THEN** 页面原样显示这段文字，不执行脚本

## ADDED Requirements

### Requirement: 附图与公报存档到对象存储，经鉴权端点读取

全部附图和公报 PDF 获取成功后，任务 SHALL 在同一任务内下载每个文件，并写入 OSS 的 `RISK_EVIDENCE_OSS_PREFIX` 前缀，记录对象位置、实际格式、字节数、sha256 和存档时间。存档文件 SHALL 只能通过与核查单同样做归属校验的端点读取（他人包括超管均返回 404），MUST NOT 向前端暴露 OSS 地址或对象 key。

OSS 未启用、下载失败或写入失败时，该文件 SHALL 记为「未存档」，保留签名链接、链接过期时间和失败分类。此时详情状态 MUST NOT 因此变为失败，系统 MUST NOT 自动重新调用 P042 或 P020。界面 SHALL 对未存档的文件标明「未存档，链接在某时间前有效」；过期后不再显示链接。

#### Scenario: 存档成功后链接过期

- **WHEN** 附图在 11-11 获取并存档，用户在 11-30 打开详情
- **THEN** 附图从存档读取正常显示，不产生任何外部调用

#### Scenario: OSS 未启用

- **WHEN** `OSS_ENABLED=false` 时获取公报 PDF
- **THEN** 详情为成功，PDF 标为「未存档」，在链接过期前可以直接打开

#### Scenario: 他人读取存档文件

- **WHEN** 用户 B 请求用户 A 核查单中候选的附图文件
- **THEN** 返回 404，不读取 OSS
