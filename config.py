from pydantic import ConfigDict, field_validator
from pydantic_settings import BaseSettings

from app.site_registry import UnknownSiteError, normalize_site_codes


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

    # 文件上传配置
    UPLOAD_DIR: str = "uploads"
    MAX_FILE_SIZE: int = 3 * 1024 * 1024 * 1024  # 3GB for large files

    # 原始文件归档目录：导入成功后把原始 CSV 移到这里，不删除。
    # 当前是本地目录（为将来接 OSS 预留可替换的 archive_raw_file 接口）。
    # 注意：本地归档会累积占盘，接 OSS 前需要清理/转储策略。
    IMPORT_ARCHIVE_DIR: str = "archive/raw"

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
    OSS_STS_ENDPOINT: str = "https://sts.aliyuncs.com"
    OSS_STS_ROLE_ARN: str = ""       # 前端直传所扮演的 RAM 角色
    OSS_STS_DURATION_SECONDS: int = 3600  # 临时凭证有效期（秒），保持短时

    # 批处理配置
    BATCH_SIZE: int
    MINIBATCH_SIZE: int

    # Admin配置密钥：
    # python -c "import secrets; print(secrets.token_urlsafe(32))"
    ADMIN_SECRET_KEY: str

    # 多核处理配置
    MAX_WORKERS: int
    MULTIPROCESSING_THRESHOLD_MB: float
    FILE_SPLIT_LINES: int

    # 数据库连接优化配置
    DB_POOL_SIZE: int = 20  # 连接池大小
    DB_MAX_OVERFLOW: int = 10  # 最大溢出连接
    DB_POOL_TIMEOUT: int = 30  # 连接超时时间（秒）
    DB_POOL_RECYCLE: int = 1800  # 连接回收时间
    DB_POOL_PRE_PING: bool = True  # 启用连接预检

    # 查询优化配置
    DB_ECHO: bool = False  # 生产环境关闭SQL日志
    DB_QUERY_TIMEOUT: int = 30  # 30秒查询超时

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

    @field_validator("HOT_WINDOW_MONTHS")
    @classmethod
    def _validate_hot_window(cls, value: int) -> int:
        if value < 1:
            raise ValueError("HOT_WINDOW_MONTHS 至少为 1")
        return value

    model_config = ConfigDict(env_file=".env", extra="ignore")


# 创建设置实例
settings = Settings()
