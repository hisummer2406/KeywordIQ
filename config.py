from typing import ClassVar

from pydantic import ConfigDict, field_validator, model_validator
from pydantic_settings import BaseSettings

from app.site_registry import UnknownSiteError, normalize_site_code, normalize_site_codes


class Settings(BaseSettings):
    # 应用配置
    APP_NAME: str
    VERSION: str
    DEBUG: bool

    # 数据库配置: postgresql://[username]:[password]@[host]:[port]/[database_name]?[参数1]=[值1]&[参数2]=[值2]
    DATABASE_URL: str
    DATABASE_URL_ASYNC: str

    # 数据库Schema
    DATABASE_SCHEMA: str

    # 站点配置
    # ENABLED_SITES 决定哪些站点可以导入与查询，也决定事实表要维护哪些分区。
    # 逗号分隔书写（如 US,GB,DE），取值必须是 app.site_registry.SITE_CATALOG 中
    # 已登记的 ISO alpha-2 站点码。解析后的列表请用 settings.enabled_site_codes。
    #
    # 这里声明为 str 而非 list[str]，是因为 pydantic-settings 2.1 会在校验器执行
    # 之前先尝试把复杂类型按 JSON 解析，逗号分隔的写法会直接报解析错误。
    ENABLED_SITES: str = "US"
    # 热窗口：库中只保留最近多少个自然月的数据，更早的月度分区由保留清理自动移出。
    # 默认 3（当前月 + 前两个月）。清理逻辑读取本值计算保留下界，见
    # app.table.analysis.retention_service。
    HOT_WINDOW_MONTHS: int = 3

    # 查询默认站点：分析查询未传站点参数时采用它（site-scoped-query）。
    # 取值必须是已登记站点码；留空时 default_site 回退为 enabled_site_codes 的第一个。
    # 绝不返回全站点混合数据。
    DEFAULT_SITE: str = ""

    # 导出行数上限：单次导出最多多少行。超出即拒绝（413），不静默截断——截断的 CSV 会被
    # 当成完整数据使用。上限的存在是为了护住浏览器内存：导出走「fetch 取 blob 再触发下载」，
    # 整个响应会先进浏览器内存（design 决策 7 的中小导出路径）。超阈值的大导出改走后端
    # 一次性下载令牌，该路径尚未实现，故此处以拒绝代替。
    EXPORT_ROW_CAP: int = 50000

    # 慢查询定位：两层独立阈值，触发不同的排查动作，不共用一个配置。
    # 见 openspec/changes/add-request-diagnostics-logging/design.md 决策 D。
    #
    # 第一层——通用 SQL 语句级（database.py 的 before/after_cursor_execute 事件监听，
    # 同时挂在同步与异步引擎上）：单条 SQL 语句本身慢（索引缺失、锁等待）。
    SLOW_QUERY_THRESHOLD_SECONDS: float = 1.0
    # 第二层——/api/analysis/search 专属结构化诊断：整个调用慢（可能是多个本身不慢的
    # 阶段叠加，或应用层整形耗时占比异常），超阈值才输出已解析的生效作用域/筛选摘要/
    # 各阶段耗时，不新增查询次数。
    ANALYSIS_SLOW_QUERY_THRESHOLD_MS: int = 1000

    # 交互查询的执行预算（#148，async-request-execution）。写进异步引擎的 statement_timeout，
    # 由数据库在超时后取消语句、释放连接——DB_POOL_TIMEOUT 只管「等连接」，管不了「执行」，
    # 没有它，一条坏计划会无限期占着连接，异步池耗尽后其他页面一起失去响应。导入/导出/迁移
    # 走同步引擎，不受影响。实测 ANALYZE 后最慢的分析查询约 1.3 s，30 s 留足余量。
    INTERACTIVE_STATEMENT_TIMEOUT_MS: int = 30000
    # 分析查询每个 worker 同时执行的上限与排队等待上限。异步池每 worker 8+8，分析查询
    # 最多占 4 条，余下留给鉴权/作用域/词库等轻量请求；等不到的返回 503，不无限排队。
    ANALYSIS_MAX_CONCURRENT_SEARCHES: int = 4
    ANALYSIS_SEARCH_QUEUE_TIMEOUT_SECONDS: float = 5.0
    # /api/analysis/search 的完整请求预算（秒，含排队）。每条语句的 statement_timeout 收紧到
    # 剩余预算，到点由数据库取消——只在应用层放弃等待的话，服务端语句会照跑到结束。
    # 低于 INTERACTIVE_STATEMENT_TIMEOUT_MS 留出余量；预算口径见 #148 design「性能预算」。
    ANALYSIS_REQUEST_TIMEOUT_SECONDS: float = 25.0
    # 客户端断开后让数据库停下（#154，async-request-execution）。取消协程只放弃等待，服务端语句
    # 照跑；SQLAlchemy 在取消时作废连接，数据库要靠这个间隔轮询套接字才发现客户端已断。实测
    # 1000 ms 时 0.5–0.65 s 停止（design D2「机制验证结果」）。0 关闭轮询；PG14+、Linux 有效。
    INTERACTIVE_CLIENT_CHECK_INTERVAL_MS: int = 1000
    # /api/analysis/search 监听客户端断开并取消查询；关掉则断开的查询跑到完整请求预算为止。
    ANALYSIS_CANCEL_ON_DISCONNECT_ENABLED: bool = True

    # 持续增长市场（工具箱）的判定口径。三项都在**日报导入收尾时**参与算定，命中结果写进
    # analysis.fact_growth；查询侧只读，不重算。
    #
    # **改这三个值不会影响已算定的期次**——旧榜单仍是旧口径算出来的。要让历史期次跟上，
    # 必须跑 scripts/backfill_growth.py 重算（保留窗口内的日报事实数据都还在，可重算）。
    # 这是「只存命中行」换来的代价，见 openspec/changes/toolbox-growth-market/design.md 决策 1。
    #
    # 窗口 7 期产生 6 个日环比，不是 7 个；要 7 个环比就得取 8 期数据。
    GROWTH_WINDOW_DAYS: int = 7           # 窗口期数（含锚定日），仅日报
    GROWTH_MIN_RANK_GAIN: int = 10000     # 累计净增名次下限（不含），rank(d-6) - rank(d)
    GROWTH_MIN_DAILY_RATE: float = 0.10   # 每个日环比的下限（含），0.10 = 10%

    # 词库（工具箱 · 我的词库）的规模上限。两个都是**拒绝式**上限：超限返回 422 且整批
    # 不写，不静默截断——截断后的清单与用户以为存进去的不是同一份，而他无从察觉。
    #
    # 每组条目数还有第二重身份：**它是盯盘查询的规模保证**。那条查询由条目表驱动、
    # LEFT JOIN 事实表与掉榜表，最坏情况是上限这么多行的嵌套循环（两侧都走现成索引：
    # idx_fact_{daily,weekly}_keyword_history 与 fact_dropped 的主键）。上调它就是在
    # 放大那条查询的最坏代价，改之前先重测。
    LIBRARY_MAX_GROUPS_PER_SITE: int = 20   # 每用户每站点的分组数上限
    LIBRARY_MAX_ITEMS_PER_GROUP: int = 500  # 每分组的关键词条数上限

    # 竞对监控（关注清单）的规模上限，同样是**拒绝式**：超限在预览阶段即返回 422，
    # 不进入可确认状态，不静默截断。
    COMPETITOR_MAX_WATCH_PER_USER: int = 3000    # 每用户的关注总数上限
    COMPETITOR_MAX_IMPORT_BATCH: int = 5000      # 单次预览可解析的条数上限
    # 预览结果的暂存时长。够运营看完预览再点确认，又不至于让废弃的预览行长期占位——
    # 过期行由 import_queue.py 的轮询循环顺带清理，不新开定时器。
    COMPETITOR_PREVIEW_TTL_SECONDS: int = 600    # 10 分钟
    # 采集端单次上报的条目数上限。一页店铺搜索结果约 48~60 个品，留三倍余量。
    # **超限整批拒绝（422），不截断**——截断后的记录与"这家店只有这些品"在数据上完全一致，
    # 事后无法分辨；而超限本身恰恰说明采集端手上的条目比它送出的多。
    COMPETITOR_MAX_REPORT_ITEMS: int = 200       # 单次 ASIN 上报的条目数上限

    # 独立站监控的拉取参数。拉取挂在 import_queue.py 已有的轮询循环上，按站点行的
    # last_fetch_at 状态列认领到期站点，不新开定时器。
    DTC_FETCH_INTERVAL_HOURS: int = 24   # 同一站点两次拉取的最小间隔
    # 翻页上限。**撞上它必须判为不完整、不做下架判定**——否则某站商品数超过上限时，
    # 超出的部分会被安静地判成下架。实测最大的站 1241 个商品、5 页，离上限很远。
    DTC_MAX_PAGES: int = 40              # 40 页 × 250 = 一万个商品
    # 畅销榜只存前 N 名：第 300 名与第 800 名的差别没有信息量，存全量会让名次表膨胀
    # 5~10 倍。实测每页 20 个商品，取前 100 名要翻 5 页。
    DTC_RANK_TOP_N: int = 100
    DTC_REQUEST_DELAY_MS: int = 400      # 同一站点两次请求的最小间隔（毫秒）

    # 文件上传配置
    UPLOAD_DIR: str = "uploads"
    MAX_FILE_SIZE: int = 3 * 1024 * 1024 * 1024  # 3GB for large files

    # 排队批次接手轮询间隔（秒）。导入串行，抢不到锁的批次留在 QUEUED，由
    # app.table.upload.import_queue 的轮询任务在锁释放后接手。
    #
    # 一轮只是一条带索引的 SELECT，间隔小些不心疼；但它决定的是「前一个导入结束后
    # 多久才轮到下一个」，30 秒是延迟与空转之间的折中。设 0 关闭接手（此时排队批次
    # 不会自动开始，需要人工重导——只在排障时这么做）。
    IMPORT_QUEUE_POLL_SECONDS: int = 30

    # 导入后刷新查询统计（ANALYZE 本次写入的叶子分区）的执行上限（秒）。实测单期 <1 s；
    # 超时不回滚数据，批次标记查询统计待更新，由同一个导入轮询在空闲时重试。
    IMPORT_ANALYZE_TIMEOUT_SECONDS: int = 300

    # 对象存储（OSS）配置：表格文件先前端直传 OSS，再由后端凭 object key 从 OSS 拉取导入。
    # 服务端凭证（AK/SK 或 RAM 角色）只在这里持有，前端只拿短时 STS 临时凭证。
    # OSS_ENABLED=False 时上传/导入走本地文件退路（本地开发与 CI 默认关闭）。
    OSS_ENABLED: bool = False
    OSS_ENDPOINT: str = ""          # 如 https://oss-cn-hangzhou.aliyuncs.com
    OSS_REGION: str = ""            # 如 cn-hangzhou
    OSS_BUCKET: str = ""
    OSS_KEY_PREFIX: str = "imports"  # object key 前缀，凭证据此限权，防前端越权
    OSS_ACCESS_KEY_ID: str = ""      # 服务端凭证：用于签发 STS 与从 OSS 读对象
    OSS_ACCESS_KEY_SECRET: str = ""
    OSS_STS_ROLE_ARN: str = ""       # 前端直传所扮演的 RAM 角色
    OSS_STS_DURATION_SECONDS: int = 3600  # 临时凭证有效期（秒），保持短时

    # Admin配置密钥：
    # python -c "import secrets; print(secrets.token_urlsafe(32))"
    ADMIN_SECRET_KEY: str

    # 钉钉单点登录（直连钉钉开放平台的企业内部应用，OAuth2 授权码流程）。
    #
    # **这套不是 OIDC，尽管授权参数里的 scope 叫 openid。** 钉钉换回来的是 accessToken，
    # 没有 id_token、没有 JWKS、没有 discovery 端点，因此接不上任何 OIDC 依赖方库。
    # 而且 /v1.0/contact/users/me 只给个人身份（unionId、nick），拿企业内 userid 与在职
    # 状态还要**另一套**企业 accessToken 去调通讯录接口——所以这里是两套凭证共用一组 AppKey。
    #
    # 关闭时 SSO 端点一律 404、登录页不显示钉钉按钮，本地开发与 CI 都跑在这个状态。
    # （路由始终注册、由端点自己判定——配置是启动期读入的，条件注册并不会让开关
    #   变得可热切换，只会让这段行为测不到。）
    SSO_ENABLED: bool = False
    DINGTALK_APP_KEY: str = ""       # 企业内部应用的 AppKey，同时作为授权 URL 的 client_id
    DINGTALK_APP_SECRET: str = ""    # 只在服务端持有，绝不出现在前端产物、响应体或日志里
    SSO_REDIRECT_URI: str = ""       # 指向 SPA 的 /sso/callback，须与钉钉后台登记的重定向 URL 一致
    # 首次扫码建档时赋予的角色码。**不从钉钉部门/职位映射**——那会让「谁能导入数据」
    # 取决于通讯录怎么编排。默认 analyst：能查询导出，不能导入。
    #
    # 这里只校验非空；它是否真的存在于 role 表要连库，由启动期检查负责（见 main.py）。
    SSO_DEFAULT_ROLE_CODE: str = "analyst"

    @model_validator(mode="after")
    def _validate_sso_credentials(self) -> "Settings":
        """开启 SSO 却缺凭证 → 启动即失败。

        宁可起不来，也不要带着一个「配了但不生效」的 SSO 开关跑起来：那种状态下管理员
        以为单点登录已经开了，员工扫完码回来却看到一个说不清的错误，而配置文件看上去
        完全正常。这与 user:manage 那个「勾了也不生效」的权限点是同一类问题。
        """
        if not self.SSO_ENABLED:
            return self

        missing = [
            name
            for name in ("DINGTALK_APP_KEY", "DINGTALK_APP_SECRET", "SSO_REDIRECT_URI")
            if not getattr(self, name).strip()
        ]
        if missing:
            raise ValueError(
                f"SSO_ENABLED=true 时以下配置项不能为空：{'、'.join(missing)}"
            )
        if not self.SSO_DEFAULT_ROLE_CODE.strip():
            raise ValueError("SSO_ENABLED=true 时 SSO_DEFAULT_ROLE_CODE 不能为空")
        return self

    # 数据库连接池配置。
    #
    # database.py 在模块级创建**两个**引擎，各自持有独立的池，故全局上限是：
    #
    #     (同步池 + 同步溢出) × workers + (异步池 + 异步溢出) × workers
    #
    # start.sh / Dockerfile 固定 4 个 worker。两个引擎服务的负载性质完全不同，因此
    # **分开配置**——早先共用一组取值，使算式被系统性低估一半（文档长期写着
    # (池+溢出)×workers）。
    #
    # 自 dockerize-selfhosted-infra 变更起，PostgreSQL 与应用同机部署（同一组
    # 4 vCPU/8 GiB），取值直接采用此前记录的目标值而非"数据库在独立 RDS"时期的
    # 20/10——PostgreSQL 进程本身现在也要在这组核心上跑，池子越大只会把
    # "快速可见的失败（pool_timeout）"换成"整体缓慢劣化"。
    #
    # 同步引擎：导入的 COPY（psycopg2 原始游标，asyncpg 无等价接口）、导出的服务端
    # 游标、Alembic 迁移。都是低并发长事务。
    DB_POOL_SIZE: int = 2  # 连接池大小
    DB_MAX_OVERFLOW: int = 3  # 最大溢出连接
    # 异步引擎：面向请求的查询与鉴权路径。
    DB_ASYNC_POOL_SIZE: int = 8
    DB_ASYNC_MAX_OVERFLOW: int = 8
    # 超时与回收两个引擎共用——它们与负载性质无关，只与网络和 RDS 空闲策略有关。
    DB_POOL_TIMEOUT: int = 30  # 连接超时时间（秒）
    DB_POOL_RECYCLE: int = 1800  # 连接回收时间
    # 注：pool_pre_ping 在 database.py 里硬编码为 True，刻意不做成配置项——它与
    # connect_args 的 TCP keepalive 是一对，用来识破被静默丢弃的半开连接，关掉会让
    # worker 在导入期被挂死的 checkout 占光。同理 echo 跟随 DEBUG，不单独开关。

    # anyio 默认线程限流器的上限（每 worker）。main.py 在 lifespan 里应用它。
    #
    # **刻意不从连接池推导。** 走这个限流器的有三类工作，只有第一类与连接数相关：
    #
    #   1. 同步 def 端点（导出）与健康检查探活 —— 每条线程占一条同步连接
    #   2. bcrypt 哈希/校验（每次登录 ~247 ms 纯 CPU）—— 不占任何连接
    #   3. 同步 BackgroundTask（run_import）—— 一条线程占住整场导入，可达数十分钟
    #
    # 曾写成 DB_POOL_SIZE + DB_MAX_OVERFLOW。按 async-query-path 任务 7.3 把同步池
    # 降到 2+3 之后，限流器会跟着变成 5：一个导入在跑就只剩 4 条线程给全部登录。
    # 两者的正确取值毫无关系，故拆开。
    #
    # 取值 32：4 vCPU 上 bcrypt 是 CPU 密集，线程再多也只是排队；留出的余量是给
    # 导入这类"占着线程但不烧 CPU"的长任务的。
    THREAD_POOL_LIMIT: int = 32

    # 自建部署（dockerize-selfhosted-infra）下 docker-compose.yml `db` 服务的实际
    # 主机名与给 Netdata 监控角色/人工 psql/迁移与备份脚本预留的连接余量——
    # `_validate_pool_within_self_hosted_pg_cap` 用。
    SELF_HOSTED_DB_HOST: ClassVar[str] = "db"
    POOL_WORKERS: ClassVar[int] = 4  # start.sh / Dockerfile 固定的 uvicorn worker 数
    PG_CONNECTION_RESERVE: ClassVar[int] = 10

    @model_validator(mode="after")
    def _validate_pool_within_self_hosted_pg_cap(self) -> "Settings":
        """自建部署下，连接池总量不得超过自建 PostgreSQL 的 max_connections。

        独立 RDS（max_connections=400）时期，池子开得再大也顶不到硬顶，最多是把
        "快速可见的失败（pool_timeout）"换成"整体缓慢劣化"。dockerize-selfhosted-infra
        把数据库搬到同机 docker-compose 的 `db` 服务后，硬顶从 400 骤降到
        `PG_MAX_CONNECTIONS`（`docker-compose.yml` 默认 120）——按 design.md
        「Migration Plan」步骤 5，既有生产环境升级时只被要求切换
        `DATABASE_URL`/`DATABASE_URL_ASYNC` 指向这个新库，若 `.env` 里旧的
        `DB_POOL_SIZE=20`/`DB_MAX_OVERFLOW=10`/`DB_ASYNC_POOL_SIZE=20`/
        `DB_ASYNC_MAX_OVERFLOW=10`（独立 RDS 时期的取值）原样留着、盖掉这里的新类
        默认值 2/3/8/8，(20+10+20+10)×4=240 远超自建 Postgres 的 120 连接上限——
        异步查询池单独就能在健康检查、导入、管理连接之前吃光全部配额，表现是
        `too many clients already`，而不是连接池排队超时那种慢性劣化（Codex 审核
        PR #84 第十五轮）。在启动期把这笔账算一遍，超出安全余量直接拒绝启动，
        好过等生产真把连接吃光了才发现。

        **池子总量校验只在 `DATABASE_URL` 的 host 恰好是 `db`（docker-compose.yml
        里自建 `db` 服务的内部网络主机名，只有这条自建部署路径会用这个值）时才
        启用**——本地开发直连 `localhost`、还留在 RDS 路线的部署直连
        `*.rds.aliyuncs.com`，都不受自建 Postgres 这个连接数硬顶约束，套用这条校验
        反而会把根本不受影响的部署方式一并挡在启动之外：本仓库自己的本地开发
        `.env` 就还留着旧的 `DB_POOL_SIZE=20`/`DB_MAX_OVERFLOW=10`（指向
        `localhost` 直连的本地 Postgres），若不按 host 收窄，这条校验会连本地
        `uv run pytest`/`uv run uvicorn` 都启动不了。**但两个 URL 必须指向同一个
        数据库这一条，不分部署形态、无条件校验**——分开配置 `DATABASE_URL`/
        `DATABASE_URL_ASYNC` 是为了让同步/异步两个引擎各自有独立的池子大小，不是
        为了让它们指向不同的库；本仓库任何一份 `.env.example` 场景（本地/自建/RDS）
        两者都写的是同一个 host 与库名。

        `PG_MAX_CONNECTIONS` 是 docker-compose.yml 专用变量，不进 `Settings`
        （与 `POSTGRES_DB` 同一条既有约定），这里只读一次原始值做校验，不新增字段。
        取不到时按 `docker-compose.yml` 里 `${PG_MAX_CONNECTIONS:-120}` 的同一个
        默认值算——这正是"既有 `.env` 从没写过这一项"的那个场景，恰恰是本条校验
        要拦住的：`db` 服务的实际 `max_connections` 同样会落到这个默认值，两边算的
        是同一个数。

        **必须先查 `os.environ`，查不到再回退读 `.env` 文件，两步都不能省。**
        这条校验会在两种截然不同的进程里跑：`app` 容器内（生产常态，`.env` 文件
        本身不在容器文件系统里——`ensure_target_partitions_for_migration.py` 的
        `--db-name` 默认值同样因为这个原因改读 `os.environ` 而不是
        `_selfhosted_pg_env.default_db_name()`），此时 `PG_MAX_CONNECTIONS` 由
        compose 的 `env_file: .env` 注入成了真正的环境变量，只能从 `os.environ`
        读到；以及宿主机 cron（`backup_selfhosted_pg.py` 明确要求"用 host cron
        调用本脚本"，不在容器里跑），此时宿主机进程的 `os.environ` 通常没有
        `PG_MAX_CONNECTIONS`（除非 cron 环境单独 export 过），但 `.env` 文件就在
        当前工作目录。pydantic-settings 读 `.env` 只会填充 `Settings` 已声明的
        字段，不会把未声明的 key 导出到 `os.environ`——若 `.env` 把
        `PG_MAX_CONNECTIONS` 调高、池子四项也跟着调大到超过默认 120 的安全余量但
        仍在新上限之内，只用 `os.environ` 读会在宿主机上读到"没设置"而回退到
        120，按错误的旧上限误判超限，`import config` 直接抛错——`backup_selfhosted_pg.py`
        因此永远跑不到 `main()`，每次 cron 触发的备份都会静默失败，而这份 `.env`
        配置在容器里其实完全合法（Codex 审核 PR #84 第十八轮）。回退读的是
        `_selfhosted_pg_env.default_db_name()` 同款 `python-dotenv`，不是重新发明
        一套解析。
        """
        import os

        from sqlalchemy.engine import make_url

        try:
            sync_url = make_url(self.DATABASE_URL)
            async_url = make_url(self.DATABASE_URL_ASYNC)
        except Exception:
            return self

        # 只看 DATABASE_URL 曾经放过一个真实缺口：切库若只改了 DATABASE_URL_ASYNC
        # （请求/鉴权走的异步引擎），DATABASE_URL（导入/导出/迁移走的同步引擎）仍留
        # 在旧库，这条校验会因为只查同步 host 直接在下面 return self，之后既不做
        # 哨兵值/总量校验，应用还会在两个库上静默分裂——异步查询与鉴权读写新库，
        # 同步的导入/导出/迁移仍读写旧库。`/health` 也发现不了这个问题：它对两个
        # 引擎各自单独 `SELECT 1`，两个库分别都健康就都探活通过，看不出两边是不是
        # 同一个库（Codex 审核 PR #84 第二十三轮）。两个 URL 本就该指向同一个数据库
        # ——分开配置的是池子大小，不是目标库，本仓库任何一份 .env 示例（本地/自建/
        # RDS）都没有让两者指向不同库的场景，故在任何部署形态下都无条件拒绝不一致，
        # 不只在自建部署下才查。
        sync_target = (sync_url.host, sync_url.port, sync_url.database)
        async_target = (async_url.host, async_url.port, async_url.database)
        if sync_target != async_target:
            raise ValueError(
                "DATABASE_URL 与 DATABASE_URL_ASYNC 指向了不同的数据库："
                f"{sync_url.host}:{sync_url.port}/{sync_url.database} 对 "
                f"{async_url.host}:{async_url.port}/{async_url.database}。"
                "同步引擎（导入/导出/迁移）与异步引擎（请求/鉴权）必须指向同一个库，"
                "否则应用会在两份数据上静默分裂，且 /health 各自探活各自的库看不出"
                "这一点。切库时两个 URL 必须一起改。"
            )

        host = sync_url.host
        if host != self.SELF_HOSTED_DB_HOST:
            return self

        # SQLAlchemy `QueuePool` 把 `pool_size<=0` 与 `max_overflow=-1` 当作"无界"
        # 的哨兵值（分别表示不限制常驻连接数、不限制溢出连接数），不是"0 个"或
        # "-1 个"。下面的算术按字面数值求和，这类哨兵值会被当成 0 或负数参与加总，
        # 让实际会撑爆 `PG_MAX_CONNECTIONS` 的配置从这条校验里蒙混过去——必须先
        # 拒绝这些取值，再进入总量算术（Codex 审核 PR #84 第二十一轮）。
        if (
            self.DB_POOL_SIZE <= 0
            or self.DB_ASYNC_POOL_SIZE <= 0
            or self.DB_MAX_OVERFLOW < 0
            or self.DB_ASYNC_MAX_OVERFLOW < 0
        ):
            raise ValueError(
                "自建部署下 DB_POOL_SIZE/DB_ASYNC_POOL_SIZE 必须为正数、"
                "DB_MAX_OVERFLOW/DB_ASYNC_MAX_OVERFLOW 必须非负——SQLAlchemy 把 "
                "pool_size<=0 或 max_overflow=-1 当作不限连接数的哨兵值，不是字面的 "
                "0 或负数，套用这几个值会让连接池实际不受限，绕开本条校验想要保证的 "
                "PG_MAX_CONNECTIONS 安全上限。"
            )

        raw = os.environ.get("PG_MAX_CONNECTIONS")
        if raw is None:
            from dotenv import dotenv_values

            raw = dotenv_values(".env").get("PG_MAX_CONNECTIONS")
        pg_max_connections = int(raw or "120")
        total = (
            self.DB_POOL_SIZE + self.DB_MAX_OVERFLOW
            + self.DB_ASYNC_POOL_SIZE + self.DB_ASYNC_MAX_OVERFLOW
        ) * self.POOL_WORKERS
        safe_cap = pg_max_connections - self.PG_CONNECTION_RESERVE
        if total > safe_cap:
            raise ValueError(
                f"连接池配置总量 {total}（(DB_POOL_SIZE+DB_MAX_OVERFLOW+"
                f"DB_ASYNC_POOL_SIZE+DB_ASYNC_MAX_OVERFLOW)×{self.POOL_WORKERS} workers）"
                f"超过自建 PostgreSQL 的安全上限 {safe_cap}"
                f"（PG_MAX_CONNECTIONS={pg_max_connections} 减去为 Netdata 监控角色/"
                "人工管理连接/迁移与备份脚本预留的余量）。多半是从独立 RDS 配置"
                "（20/10/20/10）升级到自建部署时忘了同步调小这四项——按 .env.example "
                "的新起点值（2/3/8/8）改小，或提高 PG_MAX_CONNECTIONS 并确认自建机器"
                "有余量承受。"
            )
        return self

    @field_validator("ENABLED_SITES")
    @classmethod
    def _validate_enabled_sites(cls, value: str) -> str:
        """站点码必须已在 site_registry 中登记，否则启动即失败。

        宁可启动不起来，也不要带着一个拼错的站点码跑起来——那会让数据写到一个
        永远查不到的归属下。规整后按字母序回写，保证取值唯一且有序。
        """
        raw = [item for item in (part.strip() for part in value.split(",")) if item]
        if not raw:
            raise ValueError("ENABLED_SITES 不能为空，至少要启用一个站点")
        try:
            return ",".join(normalize_site_codes(raw))
        except UnknownSiteError as exc:
            raise ValueError(str(exc)) from exc

    @property
    def enabled_site_codes(self) -> list[str]:
        """解析后的启用站点列表。取值已在校验阶段规整并排序。"""
        return [code for code in self.ENABLED_SITES.split(",") if code]

    @field_validator("DEFAULT_SITE")
    @classmethod
    def _validate_default_site(cls, value: str) -> str:
        """默认站点若配置，必须是已登记站点码；留空合法（运行时回退到首个启用站点）。"""
        if not value or not value.strip():
            return ""
        try:
            return normalize_site_code(value)
        except UnknownSiteError as exc:
            raise ValueError(str(exc)) from exc

    # 直传中的批次多久算「不可能再传完」。**刻意不是一个独立配置项**：
    # 直传的每个请求都由 STS 临时凭证签名，凭证一过期后续分片一律被拒，这次上传就再也
    # 完成不了——判据本来就是凭证有效期，不是「多久算久」。开两个旋钮迟早互相矛盾，而
    # 两个方向都糟：短于凭证有效期会把正在好好传的 1.5GB 周报判死，远长于它则让用户
    # 多等几十分钟才看到失败。
    UPLOAD_STALE_GRACE_SECONDS: ClassVar[int] = 300  # 时钟偏差 + 最后一个分片在途

    @property
    def upload_stale_seconds(self) -> int:
        """超过这个时长仍停在 UPLOADING 的批次，交给回收判定（见 import_service）。"""
        return self.OSS_STS_DURATION_SECONDS + self.UPLOAD_STALE_GRACE_SECONDS

    @property
    def default_site(self) -> str:
        """查询默认站点：配置了且在启用列表内则用它，否则回退首个启用站点。"""
        enabled = self.enabled_site_codes
        if self.DEFAULT_SITE and self.DEFAULT_SITE in enabled:
            return self.DEFAULT_SITE
        return enabled[0]

    @field_validator("SLOW_QUERY_THRESHOLD_SECONDS")
    @classmethod
    def _validate_slow_query_threshold(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("SLOW_QUERY_THRESHOLD_SECONDS 必须为正数（配成 0 或负数会让每条 SQL 都被记为慢查询）")
        return value

    @field_validator("ANALYSIS_SLOW_QUERY_THRESHOLD_MS")
    @classmethod
    def _validate_analysis_slow_query_threshold(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("ANALYSIS_SLOW_QUERY_THRESHOLD_MS 必须为正数（配成 0 或负数会让每次查询都输出诊断日志）")
        return value

    @field_validator(
        "INTERACTIVE_STATEMENT_TIMEOUT_MS",
        "ANALYSIS_MAX_CONCURRENT_SEARCHES",
        "ANALYSIS_SEARCH_QUEUE_TIMEOUT_SECONDS",
        "ANALYSIS_REQUEST_TIMEOUT_SECONDS",
        "IMPORT_ANALYZE_TIMEOUT_SECONDS",
    )
    @classmethod
    def _validate_query_budgets(cls, value):
        # PostgreSQL 的 statement_timeout=0 表示「不限」，并发上限 0 表示「永远排不上」——
        # 两种都与字面意思相反，且只会在出事时才被发现。
        if value <= 0:
            raise ValueError("查询预算/并发上限必须为正数（0 在 PostgreSQL 里是「不限时」，与本意相反）")
        return value

    @field_validator("HOT_WINDOW_MONTHS")
    @classmethod
    def _validate_hot_window(cls, value: int) -> int:
        if value < 1:
            raise ValueError("HOT_WINDOW_MONTHS 至少为 1")
        return value

    @field_validator("GROWTH_WINDOW_DAYS")
    @classmethod
    def _validate_growth_window(cls, value: int) -> int:
        # 至少 2 期才产生得出一个日环比；1 期时「持续增长」无从谈起。
        if value < 2:
            raise ValueError("GROWTH_WINDOW_DAYS 至少为 2（少于两期算不出日环比）")
        return value

    @field_validator("LIBRARY_MAX_GROUPS_PER_SITE", "LIBRARY_MAX_ITEMS_PER_GROUP")
    @classmethod
    def _validate_library_caps(cls, value: int) -> int:
        # 0 或负数会让「加入词库」这个功能整体不可用，而失败信息是「超出上限」——
        # 与真的攒满了长得一样，看不出是配置写错。
        if value < 1:
            raise ValueError("词库上限至少为 1（配成 0 会让加入词库永远失败，且看不出是配置问题）")
        return value

    @field_validator(
        "COMPETITOR_MAX_WATCH_PER_USER",
        "COMPETITOR_MAX_IMPORT_BATCH",
        "COMPETITOR_MAX_REPORT_ITEMS",
    )
    @classmethod
    def _validate_competitor_caps(cls, value: int) -> int:
        if value < 1:
            raise ValueError("竞对监控上限至少为 1（配成 0 会让添加卖家永远失败，且看不出是配置问题）")
        return value

    @field_validator("COMPETITOR_PREVIEW_TTL_SECONDS")
    @classmethod
    def _validate_competitor_preview_ttl(cls, value: int) -> int:
        if value < 1:
            raise ValueError("COMPETITOR_PREVIEW_TTL_SECONDS 至少为 1（配成 0 会让预览还没显示就过期）")
        return value

    @field_validator("DTC_FETCH_INTERVAL_HOURS", "DTC_MAX_PAGES", "DTC_RANK_TOP_N")
    @classmethod
    def _validate_dtc_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("DTC_* 拉取参数至少为 1（配成 0 会让拉取永不进行或永远判不完整）")
        return value

    @field_validator("DTC_REQUEST_DELAY_MS")
    @classmethod
    def _validate_dtc_delay(cls, value: int) -> int:
        if value < 0:
            raise ValueError("DTC_REQUEST_DELAY_MS 不能为负")
        return value

    @field_validator("GROWTH_MIN_RANK_GAIN")
    @classmethod
    def _validate_growth_min_gain(cls, value: int) -> int:
        if value < 0:
            raise ValueError("GROWTH_MIN_RANK_GAIN 不能为负（它是名次前进量，负数意味着把下跌也算作增长）")
        return value

    @field_validator("GROWTH_MIN_DAILY_RATE")
    @classmethod
    def _validate_growth_min_rate(cls, value: float) -> float:
        # 上界取 1：日环比 = (前期 − 本期)/前期，排名为正整数时它恒 < 1，配成 ≥1 会让榜单
        # 永远为空，而那种「配置写错」的表现与「今天没有增长词」完全一样，看不出来。
        if not 0 <= value < 1:
            raise ValueError("GROWTH_MIN_DAILY_RATE 必须在 [0, 1) 内（0.10 表示 10%）")
        return value

    # ---- 飞书运维机器人（ops-bot，见 openspec/changes/add-feishu-ops-bot）----
    #
    # ops-bot 是与 app 完全独立的进程/容器，这些配置只被它读取——app 进程不使用它们，
    # 因此这里只声明字段与最小的数值合法性校验，不做"启用时必填"这类强校验（那类校验
    # 放 ops_bot 自己的启动路径，不能让 app 因为运维机器人没配好而起不来）。
    OPS_DATABASE_URL: str = ""        # ops_bot_role 的连接串（同步），见迁移里的角色授权
    OPS_DATABASE_URL_ASYNC: str = ""  # ops_bot_role 的连接串（异步）
    # 注：ops_bot_role 的密码 OPS_DB_ROLE_PASSWORD **不进 Settings**——它只有一个读取方
    # （建角色的那条迁移），与 PG_MAX_CONNECTIONS/POSTGRES_DB 同一条既有约定（"只服务单一
    # 用途的部署期变量不必路由进 pydantic Settings"），迁移里直接读 os.environ。

    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""              # 只在服务端持有，绝不出现在日志或响应体里
    FEISHU_VERIFICATION_TOKEN: str = ""       # 校验事件订阅回调确实来自飞书
    FEISHU_ENCRYPT_KEY: str = ""              # 事件订阅启用加密时使用，可留空
    FEISHU_OWNER_OPEN_ID: str = ""            # 唯一授权操作人，见 ops-bot-platform 鉴权
    FEISHU_OPS_CHAT_ID: str = ""              # 通知与交互入口的运维群

    # 巡检/告警/备份阈值——初始默认值按"先观测后设阈"的保守估计给出（design.md D3），
    # 部署后须按 ops_import_baseline 记录的真实导入耗时校准。
    OPS_ALERT_SILENCE_MINUTES: int = 30
    OPS_IMPORT_DAY_TIMEOUT_MINUTES: int = 60
    OPS_IMPORT_WEEK_TIMEOUT_MINUTES: int = 240
    OPS_ERROR_RATE_THRESHOLD: int = 20        # 近 5 分钟 ERROR/5xx 计数阈值
    OPS_BACKUP_RETENTION_DAYS: int = 30
    # ops-bot 探测业务 app 的本机地址（`/health`、`/health/pool`）。V2 的 app 容器映射在
    # 127.0.0.1:8001；8000 是同机的 V1，不要改回去。
    OPS_APP_BASE_URL: str = "http://127.0.0.1:8001"

    # 重启/发布确认流程（design.md D5）。GH_TOKEN 最小权限 checks:read+contents:read，
    # 发布前核实目标 SHA 的必需 CI 是否全部成功；不配置时发布路径直接拒绝（不放行）。
    GH_TOKEN: str = ""
    GH_REPO: str = "hisummer2406/KeywordIQ"   # owner/repo，查 CI 状态用
    OPS_CONFIRM_TTL_MINUTES: int = 10         # 重启/发布确认卡片的有效期
    OPS_MAINTENANCE_WINDOW_MINUTES: int = 5   # 发布/重启执行期间的维护窗口

    @field_validator(
        "OPS_ALERT_SILENCE_MINUTES",
        "OPS_IMPORT_DAY_TIMEOUT_MINUTES",
        "OPS_IMPORT_WEEK_TIMEOUT_MINUTES",
        "OPS_ERROR_RATE_THRESHOLD",
        "OPS_BACKUP_RETENTION_DAYS",
        "OPS_CONFIRM_TTL_MINUTES",
        "OPS_MAINTENANCE_WINDOW_MINUTES",
    )
    @classmethod
    def _validate_ops_bot_thresholds(cls, value: int) -> int:
        if value < 1:
            raise ValueError("ops-bot 的阈值/时长类配置至少为 1（配成 0 或负数会让对应判据失效）")
        return value

    model_config = ConfigDict(env_file=".env", extra="ignore")


# 创建设置实例
settings = Settings()
