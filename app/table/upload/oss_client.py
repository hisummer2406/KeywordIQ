"""OSS 客户端封装：签发直传临时凭证、生成 object key、从 OSS 流式读对象。

设计要点：
- **服务端凭证只在此持有**；前端只拿短时 STS 临时凭证，且权限被 RAM 策略限死到
  指定 bucket 与 object key 前缀、仅写操作、短有效期。
- **可注入/可替换**：真实的 STS/OSS 客户端在默认工厂里惰性构造（``oss2`` 与阿里云 STS
  SDK 只在真正连云时才 import），核心逻辑（key 生成、策略构造、响应整形）全部可用桩单测，
  不依赖真实 OSS，也不要求安装云 SDK 即可跑单元测试。
- object key 前缀由服务端生成并写进凭证策略，前端无法越权到其他前缀。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date

from app.common.errors import (
    CODE_UPLOAD_OBJECT_NOT_FOUND,
    CODE_UPLOAD_OSS_NOT_READY,
    NotFoundError,
    UnprocessableError,
)
from app.site_registry import normalize_site_code
from app.table.upload.import_source import basename
from config import settings


class OSSNotConfigured(UnprocessableError):
    """OSS 未启用或配置缺失 → 422。

    ``message`` 只说「对象存储未就绪」，具体缺哪个配置项进日志——配置项名与 bucket、
    role ARN 属内部信息，不该出现在响应体里。
    """

    code = CODE_UPLOAD_OSS_NOT_READY
    message = "对象存储未就绪，请联系管理员"


class OSSObjectNotFound(NotFoundError):
    """object key 指向的对象在 OSS 中不存在 → 404。"""

    code = CODE_UPLOAD_OBJECT_NOT_FOUND
    message = "上传的文件在对象存储中不存在，请重新上传"


def _period_segment(period: str) -> str:
    p = period.strip().upper()
    if p not in ("DAY", "WEEK"):
        raise ValueError(f"period 只支持 DAY 或 WEEK，收到 {period!r}")
    return p


def key_prefix_for(site: str, period: str, report_date: date, *, prefix: str | None = None) -> str:
    """某次上传允许写入的 object key 前缀，凭证据此限权。

    形如 ``imports/US/DAY/2026-07-18/``。前端只能往这个前缀下写，无法越权到其他站点/期次。
    """
    root = (prefix if prefix is not None else settings.OSS_KEY_PREFIX).strip("/")
    site = normalize_site_code(site)
    return f"{root}/{site}/{_period_segment(period)}/{report_date}/"


def build_object_key(
    site: str,
    period: str,
    report_date: date,
    original_filename: str,
    *,
    prefix: str | None = None,
) -> str:
    """生成 object key：``<前缀>/<uuid>_<原文件名>``。

    uuid 防止同期重复上传互相覆盖；原文件名只取 basename，杜绝路径穿越。
    """
    safe_name = basename(original_filename)
    uid = uuid.uuid4().hex[:8]
    return f"{key_prefix_for(site, period, report_date, prefix=prefix)}{uid}_{safe_name}"


def _upload_policy(bucket: str, key_prefix: str) -> str:
    """最小权限 RAM 策略：只允许向 ``bucket/key_prefix*`` 做 PutObject 及分片上传相关操作。"""
    resource = f"acs:oss:*:*:{bucket}/{key_prefix}*"
    policy = {
        "Version": "1",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "oss:PutObject",
                    "oss:InitiateMultipartUpload",
                    "oss:UploadPart",
                    "oss:CompleteMultipartUpload",
                    "oss:AbortMultipartUpload",
                ],
                "Resource": [resource],
            }
        ],
    }
    return json.dumps(policy, separators=(",", ":"))


@dataclass
class UploadGrant:
    """签发给前端的直传凭证与目标信息。"""

    object_key: str
    bucket: str
    endpoint: str
    region: str
    access_key_id: str
    access_key_secret: str
    security_token: str
    expiration: str

    def to_response(self) -> dict:
        return {
            "object_key": self.object_key,
            "bucket": self.bucket,
            "endpoint": self.endpoint,
            "region": self.region,
            "credentials": {
                "access_key_id": self.access_key_id,
                "access_key_secret": self.access_key_secret,
                "security_token": self.security_token,
                "expiration": self.expiration,
            },
        }


# --- 可注入的真实客户端工厂（惰性 import 云 SDK）------------------------------


def _require_config() -> None:
    if not settings.OSS_ENABLED:
        raise OSSNotConfigured(log_detail="OSS_ENABLED=false")
    missing = [
        name for name in ("OSS_ENDPOINT", "OSS_BUCKET", "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET")
        if not getattr(settings, name)
    ]
    if missing:
        raise OSSNotConfigured(log_detail=f"配置缺失: {', '.join(missing)}")


def _default_sts_assume_role(role_arn: str, session_name: str, policy: str, duration: int) -> dict:
    """默认 STS AssumeRole 实现（惰性 import 阿里云 SDK）。

    返回 ``{access_key_id, access_key_secret, security_token, expiration}``。
    """
    from aliyunsdkcore.client import AcsClient
    from aliyunsdksts.request.v20150401 import AssumeRoleRequest

    client = AcsClient(
        settings.OSS_ACCESS_KEY_ID,
        settings.OSS_ACCESS_KEY_SECRET,
        region_id=settings.OSS_REGION or "cn-hangzhou",
    )
    req = AssumeRoleRequest.AssumeRoleRequest()
    req.set_RoleArn(role_arn)
    req.set_RoleSessionName(session_name)
    req.set_Policy(policy)
    req.set_DurationSeconds(duration)
    req.set_accept_format("JSON")
    resp = json.loads(client.do_action_with_exception(req))
    creds = resp["Credentials"]
    return {
        "access_key_id": creds["AccessKeyId"],
        "access_key_secret": creds["AccessKeySecret"],
        "security_token": creds["SecurityToken"],
        "expiration": creds["Expiration"],
    }


def _default_bucket():
    """默认 OSS bucket 客户端（惰性 import oss2）。"""
    import oss2

    auth = oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)
    return oss2.Bucket(auth, settings.OSS_ENDPOINT, settings.OSS_BUCKET)


# --- 对外接口（均可注入客户端以便测试）---------------------------------------


def issue_upload_credentials(
    site: str,
    period: str,
    report_date: date,
    original_filename: str,
    *,
    assume_role=None,
) -> UploadGrant:
    """签发一次直传所需的受限临时凭证与目标 object key。

    ``assume_role(role_arn, session_name, policy, duration) -> dict`` 可注入（默认调用阿里云 STS）。
    凭证权限被 RAM 策略限死到本次上传的 bucket 与 key 前缀，仅写操作、短有效期。
    """
    _require_config()
    if not settings.OSS_STS_ROLE_ARN:
        raise OSSNotConfigured(log_detail="配置缺失: OSS_STS_ROLE_ARN")

    object_key = build_object_key(site, period, report_date, original_filename)
    prefix = key_prefix_for(site, period, report_date)
    policy = _upload_policy(settings.OSS_BUCKET, prefix)

    assume = assume_role or _default_sts_assume_role
    session_name = f"kiq-{normalize_site_code(site)}-{report_date}"[:32]
    creds = assume(settings.OSS_STS_ROLE_ARN, session_name, policy, settings.OSS_STS_DURATION_SECONDS)

    return UploadGrant(
        object_key=object_key,
        bucket=settings.OSS_BUCKET,
        endpoint=settings.OSS_ENDPOINT,
        region=settings.OSS_REGION,
        access_key_id=creds["access_key_id"],
        access_key_secret=creds["access_key_secret"],
        security_token=creds["security_token"],
        expiration=creds["expiration"],
    )


def is_key_within_allowed_prefix(object_key: str, site: str, period: str, report_date: date) -> bool:
    """校验前端回传的 object key 落在其被授权的前缀内，防越权触发导入其他切片。"""
    return object_key.startswith(key_prefix_for(site, period, report_date))


def object_exists(object_key: str, *, bucket=None) -> bool:
    """对象是否存在。``bucket`` 可注入以便测试。"""
    b = bucket or _default_bucket()
    return bool(b.object_exists(object_key))


def object_size(object_key: str, *, bucket=None) -> int:
    """对象字节数。``bucket`` 可注入以便测试。

    直传下字节不经过应用服务器，服务端唯一能称量文件的时机就是导入触发前的这次
    ``head_object``——所以 MAX_FILE_SIZE 的执行点在这里，不在"上传"那一步。
    """
    b = bucket or _default_bucket()
    return int(b.head_object(object_key).content_length)


def object_stream(object_key: str, *, bucket=None):
    """打开 OSS 对象，返回支持 ``read(size)`` 的二进制流。``bucket`` 可注入。

    对象不存在时抛 :class:`OSSObjectNotFound`——由导入层转为"拒绝导入、不写数据"。
    """
    b = bucket or _default_bucket()
    if not b.object_exists(object_key):
        raise OSSObjectNotFound(log_detail=f"object key 不存在: {object_key}")
    return b.get_object(object_key)
