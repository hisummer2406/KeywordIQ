from pydantic import BaseModel, Field, field_validator
from typing import Literal

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
