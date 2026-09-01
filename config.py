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
    # start.sh 固定 4 个 worker。两个引擎服务的负载性质完全不同，因此**分开配置**——
    # 早先共用一组取值，使算式被系统性低估一半（文档长期写着 (池+溢出)×workers）。
    #
    # 同步引擎：导入的 COPY（psycopg2 原始游标，asyncpg 无等价接口）、导出的服务端
    # 游标、Alembic 迁移。都是低并发长事务。
    DB_POOL_SIZE: int = 20  # 连接池大小
    DB_MAX_OVERFLOW: int = 10  # 最大溢出连接
    # 异步引擎：面向请求的查询与鉴权路径。
    DB_ASYNC_POOL_SIZE: int = 20
    DB_ASYNC_MAX_OVERFLOW: int = 10
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

    model_config = ConfigDict(env_file=".env", extra="ignore")


# 创建设置实例
settings = Settings()
