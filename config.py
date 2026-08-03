from pydantic import ConfigDict, field_validator
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

    # 文件上传配置
    UPLOAD_DIR: str = "uploads"
    MAX_FILE_SIZE: int = 3 * 1024 * 1024 * 1024  # 3GB for large files

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

    model_config = ConfigDict(env_file=".env", extra="ignore")


# 创建设置实例
settings = Settings()
