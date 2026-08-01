## ADDED Requirements

### Requirement: 表格文件先直传对象存储

上传 SHALL 采用前端直传对象存储（OSS）：后端 SHALL 签发受限的临时凭证（STS 或签名 URL），
前端凭该凭证把文件直接写入 OSS，文件字节 MUST NOT 经过应用服务器落盘。临时凭证 SHALL 限定
到本次上传所需的最小权限（指定 bucket、object key 前缀、写操作、短有效期）。上传完成后前端
SHALL 把 OSS object key 回传给后端以触发导入。

#### Scenario: 签发受限的直传凭证

- **WHEN** 前端请求上传一个日报文件
- **THEN** 后端返回一份临时凭证，其权限仅限于向约定的 bucket 与 object key 前缀写入，且有效期为短时

#### Scenario: 文件字节不经应用服务器

- **WHEN** 前端使用临时凭证上传一个约 1.5GB 的周报文件
- **THEN** 文件直接写入 OSS，应用服务器不落盘该文件、不承载其字节流

#### Scenario: 回传 object key 触发导入

- **WHEN** 前端完成直传并把 object key 回传给后端
- **THEN** 后端据该 key 登记一次导入，进入后续的从 OSS 拉取导入流程

### Requirement: 导入以 OSS 对象为数据源

导入 SHALL 以 OSS object 为唯一数据源：导入任务 SHALL 凭 object key 从 OSS 流式读取文件内容
并喂给数据库的 `COPY`，MUST NOT 要求先把整个文件下载并整体读入内存。若 object key 指向的对象
不存在或不可读，导入 SHALL 失败并给出明确错误，MUST NOT 写入部分数据。

#### Scenario: 从 OSS 流式导入

- **WHEN** 导入任务处理一个已直传到 OSS 的周报 object
- **THEN** 任务从 OSS 流式读取并通过 `COPY` 写入，导入过程内存占用有界

#### Scenario: 对象缺失时拒绝导入

- **WHEN** 触发导入时 object key 指向的对象在 OSS 中不存在
- **THEN** 该次导入失败并返回明确错误，不产生任何事实数据行

### Requirement: OSS 对象即原始归档

直传到 OSS 的原始文件 SHALL 作为原始报表的持久归档，导入 MUST NOT 依赖导入成功后再把文件
移动到另一归档位置。原始对象 SHALL 在导入完成后继续保留于 OSS，使原始报表可被后续的重导、
数据挖掘与 AI 训练重新处理。导入批次记录 SHALL 携带该对象的 key/位置以供溯源与重导。

#### Scenario: 原始文件在入口即持久化

- **WHEN** 前端完成直传但随后导入进程崩溃
- **THEN** 原始文件仍完整保留在 OSS 中，可在恢复后据 object key 重新导入

#### Scenario: 批次记录可溯源到 OSS 对象

- **WHEN** 一次导入完成
- **THEN** 对应批次记录中包含该原始文件的 OSS object key/位置，可据此重新拉取原始文件

#### Scenario: 不再执行导入后本地归档

- **WHEN** 一次导入成功完成
- **THEN** 系统不把原始文件移动到本地归档目录，OSS 对象即为原始留档
