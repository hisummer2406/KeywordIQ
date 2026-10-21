---
name: local-db
description: 准备本地一次性 PostgreSQL 测试库并运行真实数据库回归；用于迁移、分区、锁验证或排查 pytest 跳过数据库用例。
---

# 本地测试库与真实回归

在仓库根目录执行。输入为测试目标、拟跑用例及已核实的本地连接配置；输出为目标身份、迁移版本、
命令/退出码、passed/failed/skipped 和未执行项。不要输出密码或完整连接串。

## 先确认目标与占用

1. 从当前配置、容器端口和连接方式核实目标；不猜 pgbase 名称、端口或默认密码。
   测试仅用本地一次性库。不能指向生产/开发业务库，也不能把转发到生产的回环端口当作本地实例。
2. 用只读查询核对 `current_database()`、`current_user`、服务端地址/端口和迁移版本。
   这些返回值需与本地实例证据共同判断；数据库名包含 test 不是充分证明。
3. 运行测试前确认没有其他会话正在使用同一库；测试夹具可能 TRUNCATE 业务表或执行分区 DDL。
   有其他使用者就停止该库的写入验证并报告，不杀连接、不抢共享资源；未经协调不改用另一库绕过约定。
4. 仅在目标确认无误后，准备下列变量。KI_TEST_SYNC_URL 和 KI_TEST_ASYNC_URL 是本次选定目标的
   同步/异步连接串，由受信本地配置读取，不在聊天或日志打印。确认三个变量指向同一目标。

```bash
: "${KI_TEST_SYNC_URL:?先确认并设置同步测试库连接}"
: "${KI_TEST_ASYNC_URL:?先确认并设置异步测试库连接}"
export DATABASE_URL="$KI_TEST_SYNC_URL"
export DATABASE_URL_ASYNC="$KI_TEST_ASYNC_URL"
export TEST_DATABASE_URL="$KI_TEST_SYNC_URL"
uv run alembic current
uv run alembic upgrade head
# 按本次影响面选文件；需要全量时才用 test/
uv run pytest test/ -q -ra
```

命令前确保变量已赋值且非空；不要复制生产 .env 到测试运行环境。缺少非连接配置时参考当前 CI 的
测试配置，不将生产凭证或业务配置整份继承。失败应先读错误，不自动反复重建数据库。

## 确需创建或重建时

确认实例可用后，目标库不存在可创建本地一次性测试库；不要因名称相似删除其他库。
重建只在用户已授权该次重建、普通迁移无法解决且没有使用者时进行；已有授权不重复询问。
删除前从管理员连接到维护库（不是目标库）再次查活动连接：

```sql
SELECT pid, usename, application_name, state
FROM pg_stat_activity
WHERE datname = :verified_test_database
  AND pid <> pg_backend_pid();
```

`:verified_test_database` 是驱动绑定参数，不直接粘贴给 psql；用所选客户端的绑定语法。
返回任何连接先停；没有连接也需确认无其他会话在准备使用。按已核实目标执行创建/重建，
不使用强制断开/强制删除，不默认执行 dropdb。完成后重新迁移并复核身份，再跑测试。

## 判断结果

- 退出码0不足以证明数据库回归通过。列出实际收集的数据库用例与每个 skip 原因；目标用例被跳过则未验证。
- 不固定 passed/skip 数量或耗时；不要把历史“只有1个skip”作为门槛。区分预期排除与环境导致跳过。
- 迁移变更在一次性库核对 upgrade、降一步再升回和模型差异；分区/视图手写迁移先离线核对DDL。
- 锁相关偶发失败核对引擎/连接生命周期与测试夹具，不先提高超时或强行释放别人的锁。
- 本Skill只验证指定测试目标；测试库通过不等于生产可发布或真实体量导入已通过。
