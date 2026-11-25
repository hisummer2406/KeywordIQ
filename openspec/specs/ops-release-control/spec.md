# ops-release-control Specification

## Purpose
TBD - created by archiving change add-feishu-ops-bot. Update Purpose after archive.
## Requirements
### Requirement: 重启与发布需人工确认
应用重启与版本发布 SHALL 先展示操作确认卡片，MUST 由配置的所有者本人确认后才执行；
未经确认 MUST NOT 执行。手动备份不受此约束（见 `ops-backup`）。

#### Scenario: 未确认不执行
- **WHEN** 一个重启或发布任务处于待确认状态
- **THEN** 系统不执行任何实际操作，直到收到有效确认

#### Scenario: 重启确认卡片展示影响
- **WHEN** 系统生成重启确认卡片
- **THEN** 卡片展示目标项目/服务、当前任务与预期影响

### Requirement: 重启动作范围限定
系统 SHALL 只允许预定义的 v2 应用重启（`docker compose restart app`/`nginx reload`
等固定动作），MUST NOT 允许任意容器或宿主机操作。

#### Scenario: 请求非预定义的重启目标
- **WHEN** 用户请求重启一个未在预定义列表中的服务或容器
- **THEN** 系统拒绝该请求

### Requirement: 发布仅接受确切 SHA 且 CI 全部通过
发布 SHALL 只接受允许分支历史上的完整 commit SHA，且该 SHA 上全部必需 CI 检查结论均为
成功；CI 等待中、失败、跳过必需检查或无法核实 MUST 一律阻止发布。

#### Scenario: CI 仍在运行
- **WHEN** 目标 SHA 的某个必需 CI job 状态为进行中
- **THEN** 系统拒绝生成发布确认卡片，提示等待 CI 完成

#### Scenario: CI 失败
- **WHEN** 目标 SHA 的某个必需 CI job 结论为失败
- **THEN** 系统拒绝发布

#### Scenario: 无法核实 CI 状态
- **WHEN** 查询 CI 状态的请求本身失败或找不到某个必需 job 的记录
- **THEN** 系统按"不放行"处理，不将无法核实等同于通过

#### Scenario: SHA 不在允许分支历史上
- **WHEN** 目标 SHA 无法在允许分支的提交历史中找到
- **THEN** 系统拒绝发布

### Requirement: 发布确认卡片展示完整发布计划
发布确认卡片 SHALL 展示当前 SHA、目标 SHA、变更摘要、CI 结果、是否涉及数据库迁移及
预期影响。

#### Scenario: 发布包含数据库迁移
- **WHEN** 目标 SHA 对应代码的 alembic heads 与当前生产库版本不一致
- **THEN** 确认卡片明确标注"本次发布包含数据库迁移"

### Requirement: 确认绑定与有效期
每个待确认操作 SHALL 绑定具体动作、目标与确切 SHA，并设置有效期；确认过期、重复点击
或确认期间目标发生变化 MUST NOT 触发额外的一次操作。执行前 MUST 重新核实前置条件。

#### Scenario: 确认已过期
- **WHEN** 用户点击的确认按钮对应的有效期已过
- **THEN** 系统拒绝执行，提示需要重新发起

#### Scenario: 确认期间分支推进
- **WHEN** 用户确认发布到 SHA A 之后，`v2` 分支在执行前又产生了新的提交
- **THEN** 系统仍然只发布已确认的 SHA A，不会因为分支推进而改为发布新的头部提交

#### Scenario: 执行前重新核实
- **WHEN** 一个已确认的发布任务开始真正执行
- **THEN** 系统重新核实 CI 状态、SHA 归属与导入空闲状态，任一前置条件此时不再满足则
  中止执行并报告原因，不依赖确认时刻的旧快照

### Requirement: 导入运行期间阻止重启与发布
系统 SHALL 在导入批次处于进行中状态时阻止生成重启/发布确认卡片；确认与执行之间新出现
的导入认领竞态 MUST 通过共享的协调锁解决，不得依赖"检查一次就当作安全"的判断；无法
获取导入状态时 MUST NOT 视为空闲。

#### Scenario: 导入进行中拒绝发起
- **WHEN** `import_batch` 存在进行中的批次
- **THEN** 系统拒绝生成重启/发布确认卡片

#### Scenario: 确认期间导入抢先开始
- **WHEN** 确认卡片生成时导入是空闲的，但在执行前的瞬间有新的导入抢先获得了执行锁
- **THEN** 系统在执行前的锁争用中落败，中止本次重启/发布并报告"导入进行中"，不强行
  抢占也不静默重试

#### Scenario: 无法获取导入状态
- **WHEN** 查询导入状态的请求本身失败
- **THEN** 系统不将其视为空闲，按阻止处理

### Requirement: 重启与发布互斥且不重复启动
系统 SHALL 保证重启与发布任务互斥，同一时刻只允许一个执行；重复的确认消息或服务重启
MUST NOT 导致同一操作被重新启动。

#### Scenario: 重启进行中收到发布确认
- **WHEN** 一个重启任务正在执行，此时收到一个已确认的发布请求
- **THEN** 系统拒绝立即执行该发布，提示当前有其他运维动作在进行

### Requirement: 失败报告阶段与实际状态
执行失败 SHALL 报告具体失败所处阶段（前置检查/构建部署/迁移/健康验证）与当时的实际
状态，MUST NOT 笼统报告为"失败"而不说明处于哪个阶段。

#### Scenario: 健康检查阶段失败
- **WHEN** 发布已完成部署但健康检查未通过
- **THEN** 系统报告"部署已完成，健康验证未通过"，并说明当前应用容器已切换到新版本这一
  实际状态，不误报为整体成功

### Requirement: 回滚的数据库兼容性边界
应用版本回滚 SHALL 验证目标版本与当前数据库结构的兼容性；系统 MUST NOT 自动执行数据库
逆向迁移。

#### Scenario: 回滚目标落后于当前数据库版本
- **WHEN** 回滚目标 SHA 对应代码所期望的 alembic 版本早于当前生产库已应用的版本
- **THEN** 系统拒绝自动执行，说明需要人工评估处理，不尝试自动降级数据库

### Requirement: 复用现有部署脚本
重启与发布 SHALL 通过调用现有 `deploy.sh` 完成前置检查、构建部署、必要迁移、健康与
版本验证，不重写这套部署逻辑；系统 MUST NOT 新增一条与 `deploy.sh` 平行的部署路径。

#### Scenario: 发布执行调用 deploy.sh
- **WHEN** 一个已确认的发布任务开始执行
- **THEN** 系统以非交互方式调用 `deploy.sh`，并按发布计划中已展示的迁移决定传递对应参数

### Requirement: 不新增基于告警的自动重启或发布
系统 MUST NOT 因巡检告警而自动触发重启或发布；这类动作只能由人工确认路径发起。

#### Scenario: 告警触发后不自动重启
- **WHEN** 巡检检测到服务不可用并发出告警
- **THEN** 系统不自动执行任何重启动作，仅发出告警等待人工判断

### Requirement: V2 主线切换期间暂停飞书发布

主线迁到 `main` 时 SHALL 暂停受理飞书发布。恢复飞书发布前，MUST 另行对齐允许分支、
精确 SHA、同一运行的 main push CI 证据和已批准的发布执行路径。

#### Scenario: 旧 v2 引用已经归档

- **WHEN** 主仓已不提供 `refs/heads/v2`，飞书收到新的发布请求或继续执行先前已确认的请求
- **THEN** 旧分支校验 MUST 失败并阻止部署

