## ADDED Requirements

### Requirement: V2 主线切换期间暂停飞书发布

主线迁到 `main` 时 SHALL 暂停受理飞书发布。恢复飞书发布前，MUST 另行对齐允许分支、
精确 SHA、同一运行的 main push CI 证据和已批准的发布执行路径。

#### Scenario: 旧 v2 引用已经归档

- **WHEN** 主仓已不提供 `refs/heads/v2`，飞书收到新的发布请求或继续执行先前已确认的请求
- **THEN** 旧分支校验 MUST 失败并阻止部署
