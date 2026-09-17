# ops-bot-platform Specification

## Purpose
TBD - created by archiving change add-feishu-ops-bot. Update Purpose after archive.
## Requirements
### Requirement: 独立服务运行形态
`ops-bot` SHALL 作为与业务 `app` 完全独立的进程/容器运行，不依附业务 FastAPI worker 的
生命周期；`ops-bot` 崩溃、重启或被停止 MUST NOT 影响 `app` 的可用性，反之亦然。

#### Scenario: 业务 app 重启不影响机器人可用性
- **WHEN** `app` 容器被重启（发布或人工操作）
- **THEN** `ops-bot` 容器保持运行，飞书状态查询与告警照常工作

#### Scenario: 机器人崩溃不影响业务服务
- **WHEN** `ops-bot` 进程异常退出
- **THEN** `app` 容器与业务 API 不受影响，`docker-compose.yml` 的 `restart: unless-stopped`
  策略负责拉起 `ops-bot`

### Requirement: 飞书身份与会话鉴权
所有飞书事件与卡片回调 SHALL 先通过来源校验（Verification Token / Encrypt Key），再校验
发送者 `open_id` 等于配置的 `FEISHU_OWNER_OPEN_ID` 且所在会话 `chat_id` 等于配置的
`FEISHU_OPS_CHAT_ID`；两项校验任一失败 MUST 拒绝执行任何查询或操作。鉴权 MUST NOT 依赖
昵称、群管理员身份或"群里只有一个人"这类非确定性判据。

#### Scenario: 非授权用户发送指令
- **WHEN** 一条消息或卡片回调的 `open_id` 不等于 `FEISHU_OWNER_OPEN_ID`
- **THEN** 系统拒绝执行，不返回任何状态或操作结果

#### Scenario: 授权用户在非指定会话发送指令
- **WHEN** `FEISHU_OWNER_OPEN_ID` 匹配，但消息所在 `chat_id` 不等于 `FEISHU_OPS_CHAT_ID`
- **THEN** 系统拒绝执行

#### Scenario: 伪造或未签名的回调请求
- **WHEN** 收到的 webhook 请求未通过 Verification Token / 签名校验
- **THEN** 系统在进入鉴权与业务逻辑之前直接以非 2xx 拒绝该请求

### Requirement: 任务与审计持久化
`ops-bot` 的每个后台任务、每次去重判定、每条告警状态 SHALL 落库到自建 PostgreSQL 的
独立 `ops` schema；服务重启后 MUST 能恢复查询任务列表并核实未完成任务的真实状态，不得
丢失或凭空假设。

#### Scenario: 服务重启后查询未完成任务
- **WHEN** `ops-bot` 进程在一个任务处于 `RUNNING` 状态时被重启
- **THEN** 重启后可通过飞书查询到该任务，其状态可被核实为"执行结果未知，需人工确认"而非
  被静默标记为成功或丢弃

#### Scenario: 审计记录不含敏感明文
- **WHEN** 任意任务写入审计记录
- **THEN** 记录中不包含密码、令牌、密钥或完整请求体等敏感内容

### Requirement: 最小权限数据库访问
`ops-bot` SHALL 使用独立的数据库角色连接，该角色对 `ops` schema 拥有完整权限、对
`analysis.import_batch` 与 `analysis.site_data_stats` 仅有只读权限，对其余业务表
MUST NOT 拥有任何权限，且 MUST NOT 与业务应用共用同一数据库凭证。

#### Scenario: 尝试写入业务表被数据库拒绝
- **WHEN** `ops-bot` 的数据库角色尝试对 `analysis` schema 下业务表执行写操作
- **THEN** 数据库层面拒绝该操作（权限不足），不依赖应用代码自觉不写

### Requirement: 回调及时应答与后台执行
飞书事件/卡片回调 SHALL 在飞书重试超时窗口内完成签名校验、幂等判定与初始响应；实际的
巡检、备份、重启或发布逻辑 MUST 在后台任务中执行，先返回任务编号，再通过更新卡片或主动
消息回报最终结果。

#### Scenario: 长任务不阻塞回调响应
- **WHEN** 用户点击"确认发布"
- **THEN** 系统在数秒内回应"任务已受理"并给出任务编号，发布执行在后台进行，完成后再通过
  卡片更新或新消息告知结果

### Requirement: 事件与回调幂等
系统 SHALL 对飞书事件与卡片回调按事件唯一标识去重；消息重投、回调重放或服务重启
MUST NOT 导致同一操作被重复执行。

#### Scenario: 飞书重放同一事件
- **WHEN** 飞书因未在窗口内收到确认而重放同一个事件 ID
- **THEN** 系统识别为已处理过的事件，不重新创建任务或重复执行动作

#### Scenario: 卡片按钮被重复点击
- **WHEN** 同一个确认按钮被连续快速点击两次
- **THEN** 只有一次点击真正推进任务状态并触发执行，另一次收到"已处理"的提示

### Requirement: 受限执行器
系统 SHALL 只允许执行预定义的、参数强类型的动作集合（应用重启、nginx reload、备份、
发布）；MUST NOT 将聊天文本拼接为 shell 命令，MUST NOT 提供任意命令执行能力。

#### Scenario: 用户尝试输入任意命令
- **WHEN** 用户发送的消息不匹配任何预定义动作
- **THEN** 系统不执行任何 shell 操作，仅按已支持的指令集给出可用操作提示

