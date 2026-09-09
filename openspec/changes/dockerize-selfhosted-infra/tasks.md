## 1. Docker 编排脚手架

- [x] 1.1 编写应用 `Dockerfile`（基于 `uv` 安装依赖、构建产物，复用 `.python-version`/`uv.lock`）
- [x] 1.2 编写 `docker-compose.yml`：`app` 服务（构建自 1.1 的 Dockerfile，`ports: ["127.0.0.1:8000:8000"]`，`restart: unless-stopped`）与 `db` 服务（`postgres:15.8` 锁定小版本镜像，bind mount 数据目录，`restart: unless-stopped`，不对外暴露端口）
- [x] 1.3 本地起 `docker compose up`，确认 `app` 能通过服务名 `db` 连上 `db:5432`（先用空库验证网络连通，不涉及真实数据）——`/health` 200，日志里能看到 `SELECT 1` 走通两个引擎。
  **过程中发现并修复一个与 Docker 无关的预先存在的真实 bug**：`httpx` 被错放在
  `pyproject.toml` 的 `dev` 分组，但 `app/auth/dingtalk_client.py`、
  `app/table/competitor/store_service.py`/`store_fetcher.py` 在运行时导入它——本地
  开发一直带 dev 分组装依赖，从未暴露；`uv sync --no-dev`（Dockerfile 的标准做法）
  第一次真实装出一个启动即 `ModuleNotFoundError` 的镜像。已移到主依赖，`uv lock`
  重新生成，全量测试仍 584 passed。

## 2. 自建 PostgreSQL 的 schema 建库

- [x] 2.1 在全新空的 `db` 容器上执行 `alembic upgrade head`（经 `docker compose exec app`），
  24 个迁移全部跑通，`fact_daily`/`fact_weekly`/`fact_dropped`/`fact_growth` 四张分区表
  确认建出（`pg_class.relkind='p'`）。**无法直接 diff 阿里云 RDS 的实际 schema**（本会话
  没有生产库访问权限）——已核对的是"迁移链路自洽、能从空库重建出完整 schema"，不是
  "与生产 RDS 逐字段比对"，后者需要有生产访问权限的人补跑一次
  `pg_dump --schema-only` 对比。
- [x] 2.2 迁移链路本身没有发现缺口。**过程中发现并修复了与本变更无关的 CLAUDE.md
  文档漂移**：`category_stats`/`brand_stats` 早在 2026-08-05（迁移 `f5a6b7c8d9e0`）就
  从物化视图改造成按日期分片的普通表，但 CLAUDE.md 三处仍写着"物化视图"、且引用了
  已不存在的函数名 `_refresh_category_stats`（实际是 `_rebuild_filter_stats`）——已更正。
  另外发现 `scripts/seed_demo_data.py`、`scripts/generate_synthetic_data.py` 两个开发
  工具脚本引用了已在同一次迁移中删除的 `dim_product.brand`/`.category` 列，运行即报
  `UndefinedColumn`——**未在本次范围内修复**（与容器化/自建 PG 无关，是另一笔独立的
  维护债，记在这里避免下次又要重新发现一遍）。

## 3. 配置与连接池重新核算

- [x] 3.1 `.env.example`：`DATABASE_URL`/`DATABASE_URL_ASYNC` 新增指向 `db` 的注释示例
  （保持与其余环境一样"注释、按需取用"的约定，不强改成唯一活跃值），连接池改为目标值
  （同步 `2+3`，异步 `8+8`），新增自建 PG 容器专用变量段
  （`POSTGRES_DB`/`POSTGRES_PASSWORD`/`PGDATA_HOST_PATH`/`PG_*`）。**PR #84 Codex 第五轮
  复审发现一个 bug**：指向 `db` 的 `DATABASE_URL` 示例把 `${POSTGRES_PASSWORD}` 直接拼进
  DSN 字符串，这是 docker-compose 的纯变量替换、不是 URL 编码替换——密码若含 `@`/`/`/
  `:`/`#`/`%`/`?` 等保留字符，替换出来的字符串会被 SQLAlchemy 解析成错误的
  host/database（`config.py` 的 `DATABASE_URL` 是裸 `str`，不做任何校验），且这个失败
  只会在应用启动连接时才暴露。没有引入一套额外的编码变量（那需要与 `POSTGRES_PASSWORD`
  手动保持同步，容易漏改）：改为在 `DATABASE_URL`/`POSTGRES_PASSWORD` 两处都加注释，
  要求密码只用字母数字生成（如 `openssl rand -hex 24`），从根上避开转义问题。
- [x] 3.2 `db` 容器的 `max_connections`/`shared_buffers`/`effective_cache_size`/
  `work_mem` 通过 compose 的 `command:` 从 `.env` 变量注入，起点值见 `.env.example`
- [x] 3.3 `uv run pytest -m conventions` 通过（15 passed）——曾在改连接池默认值时先红
  过一次（CLAUDE.md 公示值与 config.py 默认值不一致），随 3.4 一并改完后转绿
- [x] 3.4 CLAUDE.md 更新：「Performance Optimizations」连接池算式、「Key Configuration」
  示例值、「Production Environment」表格与容量含义，均已反映同机部署与新池值；
  一并修正了 2.2 发现的 `category_stats` 文档漂移

## 4. 数据迁移

- [x] 4.1 `scripts/migrate_to_selfhosted_pg.py`：确认源库无
  `PROCESSING`/`QUEUED`/`UPLOADING` 批次 → 切源库为只读并断开其余会话
  （`_quiesce_source`，PR #84 Codex 审核指出原检查与 dump 之间仍有写入窗口后补上，
  见下）→ 容器内 `pg_dump --data-only`（排除迁移自带种子表，见下）→ 补齐目标分区 →
  `pg_restore --data-only`。**本地演练过程中发现并修复两个真实 bug，不是靠代码审查
  发现的**：
  (a) `pg_restore` 不显式给 `--username postgres` 会用 `docker compose exec` 的默认
  执行用户（root）连接，报 `role "root" does not exist"`；
  (b) `permission`/`role`/`role_permission`/`alembic_version` 四张表由 Alembic 迁移
  在建库时直接写入固定种子数据，`--data-only` 原样搬一遍会主键冲突——已在 `pg_dump`
  阶段用 `--exclude-table` 排除。**PR #84 Codex 第三轮复审发现第三个 bug**：补分区那步
  `docker compose run --rm app python scripts/ensure_target_partitions_for_migration.py`
  发生在 design.md 步骤 5（切 `.env`）之前，此刻 `.env` 的 `DATABASE_URL` 仍指向 RDS 源库，
  而该脚本原先用全局 `database.SessionFactory`（读的正是这个 `.env`）建分区——结果分区
  被重复建在源库上（RDS 早就有，白跑），自建目标库依旧没有叶子分区，紧接着的
  `pg_restore --data-only` 对每一行事实数据都报 `relation ... does not exist`。改为
  `ensure_target_partitions_for_migration.py` 自建一个独立的 `sqlalchemy.create_engine`，
  按新增的 `--db-name`/`--target-url` 参数显式拼 compose 内部网络的 `db` 服务地址，与
  `.env` 当前指向谁无关；`migrate_to_selfhosted_pg.py` 调用处同步传入 `--db-name`。
  **PR #84 Codex 第四轮复审发现第四个 bug**：拼这个 DSN 时用的是 f-string 直接把
  `POSTGRES_PASSWORD` 塞进 `postgresql://postgres:{password}@db/{db_name}`，若生产密码
  含 `@`/`/`/`#`/`%`/`:` 等 DSN 保留字符会被解析成错误的 host/database，而这一步发生在
  源库已被切为只读之后，DSN 解析失败会让迁移卡在"RDS 只读、自建库还没数据"的半途。
  改用 `sqlalchemy.engine.URL.create` 构造，不再手拼字符串。
  **PR #84 Codex 第五轮复审发现第五、六个 bug**：(a) 模块文档字符串里前提第 1 条写的
  `docker compose exec app alembic upgrade head`——此刻切换还没发生，`.env` 仍指向 RDS，
  `app` 容器读的是同一份 `.env`，`exec` 进一个尚未按新形态起过的容器会找不到容器，
  即便临时起一个也是在 RDS（本来就是 head）上空跑，目标自建库根本没建表，紧接着的
  `pg_restore` 会因为 schema 不存在而失败。已改为 `docker compose run --rm` 一次性容器 +
  `-e` 显式覆盖两个 DSN 指向 `db` 服务，design.md Migration Plan 步骤 1 同步更新指向这段
  说明。(b) `--db-name` 默认值同样硬编码 `"amazon_search"`，操作者改过 `POSTGRES_DB`
  后，本脚本会在 RDS 已切只读、`ensure_target_partitions_for_migration.py` 与
  `pg_restore` 都对着不存在的库跑之后才失败；脚本结尾打印的核对命令提示同样没带
  `--db-name`。已抽出 `scripts/_selfhosted_pg_env.py` 的 `default_db_name()`（读
  `.env` 的 `POSTGRES_DB`，取不到回退 `"amazon_search"`）给 `backup_selfhosted_pg.py`
  （5.4 已经这么修过一次）、本脚本与 4.2 的核对脚本三处共用，不再各自复制；本脚本结尾
  打印的核对命令也带上与本次迁移一致的 `--db-name`。**修完这三处后主动排查同类写法**
  （grep `default="amazon_search"`），发现 `ensure_target_partitions_for_migration.py`
  的 `--db-name` 同样硬编码——当前唯一调用方（本脚本）总是显式传 `--db-name`，这条路径
  不受影响，但该脚本文档明确写着"一般不需要手动跑"（暗示确实存在手动单独重跑的场景，
  例如迁移中途只有补分区这一步失败），手动跑时会命中同一个坑。**修法与前三处不同**：
  该脚本要求必须在 `app` 容器内跑（模块 docstring 已经说明），容器里没有 `.env`
  文件（`.dockerignore` 排除），`POSTGRES_DB` 是 compose `env_file: .env` 直接注入的
  环境变量——用 `os.environ.get("POSTGRES_DB", ...)`，不能照抄 `_selfhosted_pg_env.
  default_db_name()`（那是读宿主机 `.env` 文件，容器里找不到文件会静默回退到错误默认值，
  是同一类 bug 的另一种写法）。

  **PR #84 Codex 第七轮复审在 `ensure_target_partitions_for_migration.py` 发现第七个
  bug**：`_months_needed`（现改名 `_source_leaf_partitions`）按
  `SELECT DISTINCT site, date_trunc('month', ...) FROM analysis.{table}` 反推月份，
  只看**有行**的月份。`fact_dropped`/`fact_growth` 的叶子分区在 `import_report` 里
  先建、后计算掉榜/增长行，即便某一期结果为空也照常提交——源库因此可能存在"分区已建、
  一行没有"的空叶子；`pg_dump --data-only` 仍会为这个空叶子发一条 COPY 归档项，行驱动
  的写法看不到它，目标库没建这个分区，`pg_restore` 就会在 RDS 已切只读之后才报
  `relation "analysis.fact_dropped_2026_09" does not exist`。改为直接读源库的分区
  目录（`pg_partition_tree`）枚举叶子分区、从命名约定解析出 (site, 月份)，不再从行
  数据反推；副作用是不必再全表扫描取值，只查目录，比原来更快。新增
  `test/test_ensure_target_partitions_for_migration_integration.py`（`TEST_DATABASE_URL`
  下）：先建一个零行的叶子分区，验证新函数能识别到它——已验证过用旧的行驱动逻辑跑
  同一断言会得到空集合（真实复现过这个 bug，不是猜测）。

  **PR #84 Codex 第八轮复审在本脚本（`migrate_to_selfhosted_pg.py`）文档字符串发现
  第八个 bug**：前提第 1 条给出的示例命令
  `docker compose run --rm -e DATABASE_URL="postgresql://postgres:${POSTGRES_PASSWORD}@db/${POSTGRES_DB}" ...`
  里的 `${POSTGRES_PASSWORD}`/`${POSTGRES_DB}` 是**宿主机 shell**在调用
  `docker compose` 之前就展开的普通命令行参数——docker compose 只把 `.env` 经
  `env_file:` 注入*容器*，从不导出到调用它的宿主机 shell。操作者若照字面把这条命令
  粘进宿主机终端，两个 `${...}` 会被展开成空字符串，拼出
  `postgresql://postgres:@db/`，本该初始化目标库的 alembic 命令因此连不上库，迁移在
  这一步就卡住。改为 `docker compose run --rm app sh -c '...'`，整段命令用单引号包住
  让宿主机 shell 原样传递这两段文本，展开改在容器内的 `sh` 里发生，读到的是 `app`
  服务经 `env_file: .env` 已经注入好的变量。
- [x] 4.2 `scripts/verify_selfhosted_migration.py`：比对 `fact_daily`/`fact_weekly`/
  `fact_dropped`/`fact_growth`/`dim_keyword`/`dim_product`/`site_data_stats` 的行数与
  最新期次。**演练中发现并修复一个 bug**：源库（psycopg2）里空表的 `MAX()` 是 Python
  `None`，目标库（`psql -tAc`）里同样的空值渲染成空字符串，两者直接比较恒不相等——表现
  是"两边明明都是 0 行，核对却报不一致"。已统一按 `None` 归一化。**PR #84 Codex 复审
  发现第二个 bug**：`_TABLES_WITH_LATEST_COL` 的注释写明覆盖 `fact_dropped`/
  `fact_growth`（两张表用 `anchor_date` 判最新期），但元组本身没有真的把这两张表列
  进去——若迁移中途出错、这两张表少落了数据，核对仍会全绿放行切换。已补上，并新增
  `test/test_verify_selfhosted_migration_unit.py` 断言核对清单与
  `partitioning.PARTITIONED_TABLES` 对齐，往后新增分区事实表却忘了同步进核对脚本会
  直接测试失败，不必等到真实迁移演练才发现。**PR #84 Codex 第五轮复审发现第三个
  bug**：核对清单当时只有这七张表，`user_center`、词库两张表、`import_batch`、全部
  `competitor_*` 共享表与关注表都不在其中——这些表若在 `pg_restore` 时因故少落了数据，
  核对仍会全绿放行切换 `.env`，实际后果是用户登不进去、收藏的词库/关注清单凭空消失。
  已扩到 `migration_scope.V2_TABLES` 减去种子表后的全部 24 张持久表（新增的 17 张只做
  行数核对，没有像事实表那样统一的"期次"列可比）；`--db-name` 默认值同样改用
  `_selfhosted_pg_env.default_db_name()`。新增
  `test_核对清单覆盖全部迁移搬迁的持久表`，与 `V2_TABLES` 这份 alembic 也在用的权威清单
  对齐，而不是另写一份期望表名——新模型登记进 `V2_TABLES` 后若忘了同步进核对脚本，这条
  测试直接红。

  **PR #84 Codex 第七轮复审发现第九个 bug**：`category_stats`/`brand_stats`（迁移
  `f5a6b7c8d9e0` 已从物化视图改成按 (站点,周期,日期) 分片的普通表）仍不在核对清单
  里——它们没有 ORM 模型，全靠原生 SQL 写入（`app/table/analysis/filter_stats.py`），
  因此不登记进 `migration_scope.V2_TABLES`（那份清单只管 alembic autogenerate 能看见
  的对象），从 `V2_TABLES` 派生清单自然漏掉它们。但它们仍是 `analysis` schema 下的
  普通表，`pg_dump --data-only --schema=analysis` 按整个 schema 搬迁、不看
  `V2_TABLES`，同样会把它们搬过去；`analysis_crud.py` 靠它们提供类目/品牌下拉，数据
  没搬完整时下拉会显示不全或缺项，核对却仍会全绿放行切换。表名直接从
  `filter_stats._FILTER_STATS`（该模块实际写入的表）取，不再手写一份新清单；
  `test_核对清单覆盖全部迁移搬迁的持久表` 的预期集合同步改为
  `(V2_TABLES - 种子表) ∪ _FILTER_STATS 的表名`。
- [x] 4.3 本地演练（不是"跳过、直接标记完成"）：起一个独立的 `postgres:15.8` 容器
  模拟 RDS，跑 `alembic upgrade head` + 少量手写数据（`scripts/seed_demo_data.py` 与
  `scripts/generate_synthetic_data.py` 两个现成的生成器都因 2.2 提到的 schema 漂移
  跑不通，改用针对当前 schema 手写的最小数据集）；对真实的 `docker-compose.yml` `db`
  服务完整跑一遍 4.1 + 4.2，`verify_selfhosted_migration.py` 最终报告全部
  `[OK]`（`fact_daily`/`fact_weekly`/`dim_keyword`/`dim_product`/`site_data_stats`
  行数与最新期次一致）。新发现的一个真实拓扑限制：`_check_no_active_import` 的
  host 侧预检与容器内执行的 `pg_dump`/`pg_restore` 命令要求源库地址在两种网络视角下
  都可达——生产环境下 RDS 天然满足（同一 VPC 域名解析一致），本地演练环境不满足
  （host 与容器看到"同一台机器"的方式不同），已在脚本注释里写明这不是需要修的缺陷。
  **`_quiesce_source` 补上后单独用一个一次性 `postgres:15.8` 容器实测**（不是套进
  4.3 那套 docker-compose 演练重跑）：确认切只读后旧连接被 `pg_terminate_backend`
  断开、新连接写入被拒但读取仍正常；顺带跑出打印在函数说明里的那两个回滚指令陷阱
  （`ALTER DATABASE` 在已继承只读默认值的会话里直接跑会被自己挡下；`SET` 与
  `ALTER DATABASE` 写进同一条用分号连起来的命令时共享同一个隐式事务、`SET` 不生效）
  ——最终确认的回滚指令（`PGOPTIONS='-c default_transaction_read_only=off' psql ...`）
  逐字符跑通。

  **PR #84 Codex 第九轮复审发现第十一个 bug**：`_quiesce_source` 切只读、
  `pg_terminate_backend` 断开旧会话，但"首次检查通过"到"断开连接"之间仍有一段非零
  窗口。若有个导入恰好在这段窗口内开始，它的批次状态在 `_execute_locked` 里先于事实
  写入单独提交为 `PROCESSING`（`app/table/upload/import_service.py:144`），随后连接
  被切断，事实写入那段跟着回滚，但 `PROCESSING` 那次提交已经落地；该批次自己的失败
  处理原本想把状态改成 `FAILED`，但此刻源库已被设成持久只读，这次写入同样失败——
  批次因此永久卡在 `PROCESSING`，且会连同其余数据一起被 `pg_dump --data-only` 原样
  搬到目标库，没有任何环节会报错。已给 `_check_no_active_import` 加
  `after_quiesce: bool` 参数：`_quiesce_source` 之后再调用一次，命中时给出与首次
  检查不同的提示（首次说"等它跑完"，这次说明连接已断、源库已只读、不会自己恢复，
  需要人工核实）。新增
  `test/test_migrate_to_selfhosted_pg_integration.py`（`TEST_DATABASE_URL` 下）：
  已验证过用改动前的单参数版本跑同一测试文件会报
  `TypeError: unexpected keyword argument 'after_quiesce'`（2 个用例失败），改完
  3 个全过。
- [ ] 4.4 **未执行**——需要生产阿里云 RDS 的访问权限与实际低峰时段窗口，本会话不具备，
  留给有生产访问权限的人执行。跑法见 `migrate_to_selfhosted_pg.py` 与
  `verify_selfhosted_migration.py` 的用法说明。

## 5. 备份自动化

- [x] 5.1 `scripts/backup_selfhosted_pg.py`：容器内 `pg_dump`（全量，非 `--data-only`——
  备份要能在自建库整个消失时独立重建，不能依赖恢复时还记得先跑迁移）→
  `docker compose cp` 取出 → 上传到 OSS `backups/postgres/` 前缀。**已用真实的开发环境
  OSS 桶（`keywordiq-dev`）实际跑通**，不是只读代码；测试产物用完已从桶里删除。
  **PR #84 Codex 复审发现一个 bug**：上传用的是 `bucket.put_object_from_file`（单次
  PutObject），OSS 单个 PutObject 的对象大小上限是 5 GiB，而满窗数据约 50 GB
  （design.md 决策 1），全量 dump 迟早超过这个上限，届时当天备份直接失败。改用
  `oss2.resumable_upload`——按文件大小自动在简单上传与分片上传间切换，单个分片仍在
  5 GiB 以内，不受这个上限约束。本轮未用超过 5 GiB 的 dump 重新实测（本地演练库量级
  远够不到这个阈值，构造一个真实 5+ GiB 的库超出本轮修复范围），改动本身是 oss2 SDK
  推荐的标准大文件上传路径。
- [x] 5.2 恢复步骤写进了 `backup_selfhosted_pg.py` 的模块文档字符串（验证过的具体命令，
  含一个真实踩过的坑：`pg_restore --create` 会尝试 DROP/CREATE 自己当前连接的数据库，
  报错但被当作可忽略继续跑完——最终数据没问题，但生产恢复时不该让人对着一堆报错怀疑
  恢复是否成功，故文档里明确写"不要加 --create"，用 `--clean --if-exists` 即可）。
  **PR #84 Codex 第十轮复审发现第十二个 bug**：文档字符串里的 `pg_restore`/`psql` 核对
  命令把库名硬编码成 `--dbname amazon_search`——`--db-name` 这个 CLI 参数本身早已在
  第四轮修复时改为读 `POSTGRES_DB`（见 5.4 段），但这段供人工照抄的恢复演练命令是纯
  文本，不会跟着那次修复自动更新。操作者一旦把 `POSTGRES_DB` 改成别的值，在一个全新
  容器上照抄这两条命令会连到一个不存在的库而失败。改用
  `docker compose exec -T db sh -c '... --dbname "$POSTGRES_DB" ...'`——`db` 服务的
  `environment:` 本就注入了 `POSTGRES_DB`（`docker-compose.yml`），让展开发生在容器内
  读到的正是这个值。已用真实 `postgres:15.8` 容器验证：库名设为非默认值
  `custom_dbname` 时，旧的硬编码命令报
  `FATAL: database "amazon_search" does not exist`（退出码 1），改用 `sh -c` 包装后
  恢复与核对查询均成功（核对查询返回预期行数）。纯文档字符串改动，没有可挂的 pytest
  路径，验证只能靠这次真实容器操作。
- [x] 5.3 实际跑了一次"备份 → 恢复到全新空库 → 核对"演练：备份产出真实上传到 OSS
  又下载下来、恢复进一个从未跑过迁移的全新 `db` 容器、核对 `permission`（6 行）、
  `role`（3 行）、`alembic_version`（1 行）均与备份时刻一致。
- [ ] 5.4 **未配置**——往生产机器上装 cron 是生产环境操作，本会话不具备执行环境，
  安装本身仍留给有生产访问权限的人执行。**PR #84 Codex 第三轮复审指出**：仓库此前只有
  "挂 cron 执行"这句散文，没有任何具体的 crontab 行、时间点或安装命令——照着文档走
  到最后一步，仍要操作者自己发明这一步怎么做。已在 README.md「生产部署」一节补上可
  直接复制的 `crontab -e` 行（每天 03:00，`uv` 用绝对路径规避 cron 默认 `PATH` 找不到
  它的问题）。保留策略仍按 OSS 生命周期规则管理，不在这条 cron 里重新实现。
  **PR #84 Codex 第四轮复审指出**：`--db-name` 默认值原先硬编码 `"amazon_search"`，
  README 文档化的 cron 行没有显式传这个参数——一旦操作者按 `.env.example` 的说明把
  `POSTGRES_DB` 改成别的库名，每次定时备份都会对着一个不存在的库跑并失败。`POSTGRES_DB`
  按 `.env.example` 的约定"不经 config.py"（不进 `Settings`），因此没有直接读
  `settings.POSTGRES_DB` 的选项，改为用 `python-dotenv` 的 `dotenv_values(".env")`
  单独读这一项，取不到时回退 `"amazon_search"`。

## 6. 切换与回滚准备

**PR #84 Codex 第三轮复审发现**：`specs/containerized-deployment/spec.md` 的「迁移窗口内
可回滚」一条仍写着"观察窗口内 SHALL 能够仅通过修改配置回滚到 RDS"，与 design.md Migration
Plan 步骤 6 早已更正的结论矛盾——观察窗口内自建库一旦有新写入（导入或用户写入），仅改
`.env` 回滚会让服务从落后这部分写入的数据重新起步，必须先把这部分写入同步/重放回 RDS
或明确接受丢失。已重写该 Requirement 与 Scenario，拆成"尚无新写入"与"已有新写入"两种
场景，前者才允许纯配置回滚，后者要求先处理新写入，措辞与 design.md 对齐。

**PR #84 Codex 第四轮复审发现**：即便是"尚无新写入"这个最简单的场景，上一轮改完的
Scenario 仍然遗漏了一步——`migrate_to_selfhosted_pg.py` 的 `_quiesce_source` 在迁移
开始时已把 RDS 持久切为拒绝新写事务（`ALTER DATABASE ... SET`），这个状态不会因为
`.env` 改回指向 RDS 而自动解除。按上一轮的文字操作，回滚后应用配置层面确实指回了 RDS，
实际连上去却只能读不能写，且这个失败模式在应用日志里长得像数据库故障，不像"回滚没做
完"。已在两个 Scenario 里都补上"解除 RDS 的 `default_transaction_read_only`"这一步，
并在 `_quiesce_source` 的 docstring 与运行时打印里把还原命令的适用场景从"迁移中途放弃"
扩展到同样覆盖"迁移完成后在观察窗口内回滚"，design.md Migration Plan 步骤 6 同步说明。

- [ ] 6.1 **未执行**——切换生产 `.env` 是生产环境操作，本会话不具备执行环境
- [ ] 6.2 **未执行**——同上，依赖 6.1
- [ ] 6.3 **未执行**——需要真实的日报/周报导入周期与生产资源监控，本会话不具备
- [ ] 6.4 **未执行**——释放生产 RDS 实例是不可逆的基础设施操作，必须由有权限、且已
  完成前面几步验证的人在确认无误后执行，不应由实现变更的会话代为决定

## 7. 旧部署路径退役

- [x] 7.1 `etc/keywordiq.service` 最初只是主仓库工作树里一份未提交的文件，本 worktree
  开出时不含它；合并本分支进 `v2` 前发现 `v2` 上又有一次独立提交（`1cff6e2`）把它连同
  CLAUDE.md 的 systemd 说明真正提交了进去，与本变更的 merge 因此在 CLAUDE.md 冲突。
  按 design.md 决策 8（依赖 Docker 原生 `restart` 策略，不需要额外 systemd unit，经用户
  确认）：合并时移除了 `etc/keywordiq.service`、保留本分支的 CLAUDE.md 版本，并同步
  更新了 README.md 里引用该文件的安装步骤（另一处遗漏，随手一并改掉）。
- [x] 7.2 `deploy.sh` 重写：`uv sync`/裸 `alembic`/`systemctl restart` 替换为
  `docker compose build` → `docker compose run --rm app alembic upgrade head`
  （`db` 不对宿主机暴露端口，迁移必须在容器内跑）→ `docker compose up -d`；nginx
  reload 步骤不变（host nginx 仍然只反代前端不变）。**PR #84 Codex 复审发现一个
  bug**：旧路线（host nginx + systemd 裸 uvicorn）的 `keywordiq.service` 同样绑
  `127.0.0.1:8000`，仓库里删掉 `etc/keywordiq.service`（7.1）只影响下次全新安装，不会
  停掉宿主机上已安装、正在跑的 unit——首次由旧路线切到本容器化路线时，`docker compose
  up -d` 会因端口已被占用而起不来。已在 `deploy.sh` 里补上：`docker compose up -d`
  前用 `systemctl is-active --quiet keywordiq.service` 判断，在跑就先
  `stop`/`disable`；用 is-active 而非"unit 是否存在"判断是为了天然幂等——停过一次后
  is-active 恒为假，后续每次 `deploy.sh` 都会跳过，不需要额外记状态。design.md
  Migration Plan 步骤 5 同步更新，说明这一步现在自动发生。**PR #84 Codex 第六轮复审
  发现两个 bug**：(a) `disable` 之前套在 `is-active` 判断里——unit 若被手工停过、
  处于 failed，或部署这一刻恰好还在 activating，`is-active` 为假，但只要它仍是
  enabled，宿主机下次重启时 systemd 照样把它拉起来，回头跟新容器抢
  `127.0.0.1:8000`。已把 `stop`（按 `is-active`）与 `disable`（按 `is-enabled`）拆成
  两个独立判断，各自天然幂等。(b) 健康检查 15 次重试仍不通过时脚本仍 `exit 0`——nginx
  已经 reload 完毕，此时可能正把流量转给一个还没就绪的后端，`--yes`/CI 等无人值守调用
  会把这次部署记成成功。已把失败分支改成 `exit 1`。
- [x] 7.3 CLAUDE.md「Development Commands」更新反映 Docker 化命令；`etc/keywordiq-nginx.conf`
  顶部注释更新 + 追加一段注释掉的 Netdata Basic Auth 反代示例（呼应第 8 组）
- [x] 7.4 `uv run pytest test/` 全量：584 passed, 260 skipped（跳过的是需要真实
  `keywordiq_test` 集成库的部分，与本次改动无关，本机未接入集成库时属预期）

## 8. 可视化监控

- [x] 8.1 `docker-compose.yml` 新增 `netdata` 服务，只读挂载 Docker socket 与
  `/proc`/`/sys`/`/etc/os-release`，只绑 `127.0.0.1:19999`

  **PR #84 Codex 第十一轮复审发现第十三个 bug**：只挂 `/proc`/`/sys` 拿到的是宿主机
  挂载点的*元数据*（挂载表），diskspace 采集器要报出真实磁盘用量还得对这些挂载点本身
  发起 `statvfs()`——这个调用作用于容器自己的挂载命名空间，看到的是容器 rootfs（很小
  的 overlay 层），不是宿主机真实磁盘。上文「扩容触发条件：磁盘使用率超过 70%」这条
  运维阈值因此永远等不到正确的数字触发。用真实容器实测复现：本机 `df -h /` 报
  460Gi 总量、20Gi 可用；同样配置（只挂 `/proc`/`/sys`）的 netdata 容器
  `disk_space./` 图表报出的却是约 58 GiB 总量（35.96 可用 + 19.41 已用 + 2.996
  预留）——是 Docker Desktop 那个 Linux VM 自己的虚拟磁盘大小，与宿主机实际磁盘完全
  对不上。

  修复：加 `- /:/host/root:ro,rslave` 挂载（宿主机官方推荐的 diskspace 采集器接入
  方式，`rslave` 保证宿主机后续新增挂载点也能被看见）+ `cap_add: SYS_ADMIN`（进宿主机
  挂载命名空间需要的权限，`SYS_PTRACE` 不够）。**本地验证有硬性上限**：macOS 上
  `docker run -v /:/host/root:ro,rslave ...` 直接报
  `path / is mounted on / but it is not a shared or slave mount`——Docker Desktop
  的 Linux VM 自身挂载传播模式不支持这个标志，这是 Mac 开发机与生产 Alibaba Cloud
  Linux ECS（真实原生 Linux 内核）之间的环境差异，不是这条修复本身的问题（与 8.4
  "这是本地开发机数字，不等于生产 x86 ECS 实际值"同一类局限）。去掉 `rslave` 只挂
  `:ro` 在本机测出的数字（约 0.77 GiB）同样对不上宿主机真实磁盘——这进一步说明本机
  macOS 环境结构性地测不出这条修复在生产 Linux 主机上的真实效果，只能确认
  **缺陷本身确实存在**（未挂载时数字明显错误），修复后的真实数字需要在生产 ECS 上
  用 `docker compose up -d netdata` 后核对面板磁盘图表与 `df -h` 是否一致。
  `docker compose --env-file ... config` 确认 YAML 语法通过。
- [x] 8.2 `docker/postgres-initdb/01_netdata_monitor_role.sh`：容器首次初始化时建
  `netdata` 角色并 `GRANT pg_monitor`；`docker/netdata/go.d/postgres.conf` 提供采集
  配置。**已实测验证**：Netdata 的 PostgreSQL 采集任务实际连上、成功采集（API 返回
  67 个 `postgres_*` 图表），不是只装了角色没验证能不能用

  **PR #84 Codex 第八轮复审发现第十个 bug**：`GRANT CONNECT ON DATABASE ${POSTGRES_DB}`
  把 `POSTGRES_DB` 直接以 bash 变量插进 heredoc，未加标识符引号——若库名含 `-` 之类
  需要引号的字符（如 `keyword-iq`），拼出的是 `GRANT CONNECT ON DATABASE keyword-iq`，
  PostgreSQL 会把它解析成 `keyword - iq` 减法表达式而语法错误；脚本又跑在
  `ON_ERROR_STOP=1` 下，会让容器首次初始化直接中止。改用 psql 变量
  （`-v dbname="$POSTGRES_DB"`）+ `:"dbname"` 标识符引号语法，由 psql 负责按标识符
  规则转义。**已用真实 `postgres:15.8` 容器验证**：起一个名为 `keyword-iq-test` 的库，
  旧写法 `GRANT CONNECT ON DATABASE keyword-iq-test ...` 实测报
  `ERROR: syntax error at or near "-"`；改用 `:"dbname"` 后同一条 GRANT 成功执行。
- [x] 8.3 `docker-compose.yml` 里 `db`/`netdata` 均只绑 `127.0.0.1`；
  `etc/keywordiq-nginx.conf` 追加注释掉的 Basic Auth 反代示例作为 SSH 隧道之外的另一
  个可选路径
- [x] 8.4 实测 Netdata 自身资源占用：本地 Docker Desktop（Apple Silicon）快照约
  136 MB 内存、瞬时 CPU 6.76%（周期性采集，非持续），落在设计文档预估的 100-200MB
  区间内。**这是本地开发机的数字，不等于生产 x86 ECS 的实际值**——生产部署后应该
  用 `docker stats` 重新量一次，不要直接拿这个数字当生产依据
- [ ] 8.5 **未执行**——需要真实的生产周报导入周期与部署环境，本会话不具备

## 9. Celery 结论记录

- [ ] 9.1 **未执行**——issue #83 目前没有可勾选的"验收清单"结构，建议在 PR 描述里
  引用 design.md 决策 7 的结论作为对该验收项的回应，而不是去改 issue 正文
- [x] 9.2 已在 issue #81 下留言，说明本变更对其"不建议引入 Celery"结论的重新核对与
  维持原结论（Docker 化削弱了运维成本这条理由，但根因是代码写法、且单机没有跨机分布式
  前提这两条依然成立）
