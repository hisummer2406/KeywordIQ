from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.site_registry import UnknownSiteError, normalize_site_code

# 周期类型：与 import_batch_model 的 PERIOD_DAY/PERIOD_WEEK 对齐。
Period = Literal["DAY", "WEEK"]


class ImportRequest(BaseModel):
    """V2 导入请求。站点必填、必须合法；周期决定写日报还是周报事实表。"""

    site: str = Field(..., description="站点码，如 US/GB/DE，必须已在站点清单中登记")
    period: Period = Field(..., description="报告周期：DAY（日报）或 WEEK（周报）")

    @field_validator("site")
    @classmethod
    def _validate_site(cls, value: str) -> str:
        """站点码校验并规整。非法站点直接报错，绝不回退默认——写错归属查不到。"""
        try:
            return normalize_site_code(value)
        except UnknownSiteError as exc:
            raise ValueError(str(exc)) from exc


class UploadCredentialRequest(BaseModel):
    """请求直传 OSS 的临时凭证。report_date 由文件名解析；period 缺省时也从文件名推断。"""

    site: str = Field(..., description="站点码，如 US/GB/DE，必须已登记")
    filename: str = Field(..., description="原始文件名，须形如 ..._2026_07_18.csv 以解析报告日期")
    period: Period | None = Field(None, description="DAY/WEEK；缺省时从文件名推断")
    object_key: str | None = Field(
        None,
        description=(
            "续签既有上传的凭证时传入本次上传的 object key；"
            "传了即为续签——只签发凭证、不新建批次记录、不生成新 key。缺省为新上传。"
        ),
    )

    @field_validator("site")
    @classmethod
    def _validate_site(cls, value: str) -> str:
        try:
            return normalize_site_code(value)
        except UnknownSiteError as exc:
            raise ValueError(str(exc)) from exc


class OSSImportRequest(BaseModel):
    """前端完成直传后，凭 object key 触发从 OSS 拉取导入。"""

    site: str = Field(..., description="站点码，须与凭证一致")
    filename: str = Field(..., description="原始文件名，用于解析报告日期与溯源")
    object_key: str = Field(..., description="直传返回的 OSS object key")
    period: Period | None = Field(None, description="DAY/WEEK；缺省时从文件名推断")

    @field_validator("site")
    @classmethod
    def _validate_site(cls, value: str) -> str:
        try:
            return normalize_site_code(value)
        except UnknownSiteError as exc:
            raise ValueError(str(exc)) from exc


class FilenameInspectRequest(BaseModel):
    """上传前解析文件名，取得归属三元组供界面回显。

    站点在这里**不是必填**——推断不出正是这个端点要告诉前端的结果之一。
    """

    filename: str = Field(..., description="待上传的原始文件名")


class ImportBatchRow(BaseModel):
    """导入历史列表的一行。

    **不含 object_key。** 它含站点、期次、日期与原始文件名，属于内部细节，按错误契约
    的同一条理由不序列化出去。界面真正需要的是「这一行还能不能重导」，那是一个布尔值
    ——`source_retained`，它直接表达含义，而不是让前端自己从一个字符串是否为空去推断。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    site: str
    period_type: Period
    report_date: date
    file_name: str
    status: str
    total_rows: int = Field(..., description="源文件数据行数")
    written_rows: int = Field(..., description="实际写入事实表的行数")
    processing_seconds: int
    error_message: str = Field("", description="失败原因，或成功时的备注（跳过行数等）")
    created_at: datetime
    completed_at: datetime | None = None
    source_retained: bool = Field(
        ..., description="原始文件是否仍可据以重导（本地退路导入的批次为 false）"
    )
    stats_status: str | None = Field(
        None, description="查询统计状态 PENDING/READY/FAILED；null 为不适用（旧批次或未成功导入）"
    )

    @classmethod
    def from_batch(cls, batch) -> "ImportBatchRow":
        """由 ORM 对象构造。`source_retained` 由 object_key 是否非空派生。"""
        return cls(
            id=batch.id,
            site=batch.site,
            period_type=batch.period_type,
            report_date=batch.report_date,
            file_name=batch.file_name,
            status=batch.status,
            total_rows=batch.total_rows,
            written_rows=batch.written_rows,
            processing_seconds=batch.processing_seconds,
            error_message=batch.error_message,
            created_at=batch.created_at,
            completed_at=batch.completed_at,
            source_retained=bool(batch.object_key),
            stats_status=batch.stats_status,
        )
