"""OSS 直传链路自检：把「配置填对了没」变成一条可执行的判据。

填完 ``.env`` 的 ``OSS_*`` 后跑这个脚本。它按前端直传的真实顺序走一遍完整回路——
签 STS 凭证 → 用临时凭证直传 → 验证越权被拒 → 服务端流式读回 → 检查 bucket CORS
→ 检查未完成分片的生命周期规则，每一步单独判定，失败时直接指出该去阿里云控制台改哪里。

之所以要有它：直传链路的失败大多发生在**浏览器里**（CORS 预检被拒、临时凭证权限过窄），
那里的报错信息既零散又滞后；而权限过**宽**（凭证能写到授权前缀之外）在浏览器里根本不会
报错，只会安静地留下一个越权可写的 bucket。两类问题都要在上线前用一次显式检查兜住。

用法::

    # 全部检查（用 DEFAULT_SITE、当天日期造一个临时探针对象，跑完即删）
    uv run python scripts/check_oss.py

    # 指定前端开发服务器的 origin 来核对 CORS（默认 http://localhost:8000）
    uv run python scripts/check_oss.py --origin http://localhost:8001

    # 保留探针对象以便手工排查
    uv run python scripts/check_oss.py --keep

退出码非 0 表示有检查未通过，可直接用于 CI 或部署前门禁。

注意：生产 ECS 上 ``OSS_ENDPOINT`` 通常配内网地址（``oss-cn-xxx-internal.aliyuncs.com``）以省
公网流量费，而临时凭证是给浏览器用的、必须走公网。本脚本模拟浏览器上传时会自动把内网
endpoint 换成公网再连，以免在 ECS 上跑出误报。
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
import uuid
from datetime import date
from pathlib import Path

# 以 `python scripts/check_oss.py` 直接调用时，仓库根不在 sys.path 上（约定同 seed_admin.py）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 探针对象的内容：小、可识别、可比对（读回时逐字节比较）
PROBE_PAYLOAD = b"keywordiq-oss-selfcheck\n"
PROBE_FILENAME = "__oss_selfcheck__.csv"


class Report:
    """逐项打印 + 汇总。失败与告警分开计，只有失败才影响退出码。"""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, name: str, detail: str = "") -> None:
        print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))

    def fail(self, name: str, detail: str, hint: str = "") -> None:
        print(f"  ❌ {name} — {detail}")
        if hint:
            print(f"     ↳ {hint}")
        self.failures.append(name)

    def warn(self, name: str, detail: str, hint: str = "") -> None:
        print(f"  ⚠️  {name} — {detail}")
        if hint:
            print(f"     ↳ {hint}")
        self.warnings.append(name)

    def summary(self) -> int:
        print()
        if self.failures:
            print(f"结论：{len(self.failures)} 项未通过 —— {', '.join(self.failures)}")
        else:
            print("结论：OSS 直传链路检查全部通过")
        if self.warnings:
            print(f"另有 {len(self.warnings)} 项告警：{', '.join(self.warnings)}")
        return 1 if self.failures else 0


def _mask(secret: str) -> str:
    """凭证只露头尾，别把完整 AK 打进终端或 CI 日志。"""
    if len(secret) <= 8:
        return "***"
    return f"{secret[:4]}***{secret[-4:]}"


def _public_endpoint(endpoint: str) -> str:
    """把内网 endpoint 还原成公网地址——临时凭证是给浏览器用的，必须公网可达。"""
    return endpoint.replace("-internal.aliyuncs.com", ".aliyuncs.com")


def _check_config(report: Report) -> bool:
    """配置齐备性：缺哪项就说哪项（这是本地终端，配置项名可以直说）。"""
    from config import settings

    print("[1/7] 配置齐备性")
    if not settings.OSS_ENABLED:
        report.fail(
            "OSS_ENABLED", "为 false，当前走的是本地文件退路",
            "本机要验证直传就把 .env 的 OSS_ENABLED 改成 true",
        )
        return False

    # 角色 ARN 单独判定：缺它只挡住 STS/直传三段，CORS 与 bucket 连通性仍可先验，
    # 免得配置分步落地时整个脚本什么也验不了。
    required = ("OSS_ENDPOINT", "OSS_REGION", "OSS_BUCKET",
                "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET")
    missing = [name for name in required if not getattr(settings, name)]
    if missing:
        report.fail("必填项", f"缺失 {', '.join(missing)}", "对照 .env.example 的 OSS 段补齐")
        return False

    report.ok("配置齐备", f"bucket={settings.OSS_BUCKET} region={settings.OSS_REGION} "
                          f"prefix={settings.OSS_KEY_PREFIX} ak={_mask(settings.OSS_ACCESS_KEY_ID)}")
    return True


def _check_sts(report: Report, site: str, period: str, report_date: date):
    """STS AssumeRole：拿不到凭证就是角色信任策略或子账号权限的问题。"""
    from app.table.upload import oss_client

    print("[2/7] STS 签发临时凭证")
    try:
        grant = oss_client.issue_upload_credentials(site, period, report_date, PROBE_FILENAME)
    except Exception as exc:  # noqa: BLE001 — 运维脚本要把云端原始报错原样摆出来
        report.fail(
            "AssumeRole", f"{type(exc).__name__}: {exc}",
            "检查 ①子账号是否有 sts:AssumeRole 权限 ②角色信任策略是否把该子账号列为可信实体 "
            "③OSS_STS_ROLE_ARN 是否写全（acs:ram::<主账号ID>:role/<角色名>）",
        )
        return None

    report.ok("AssumeRole", f"expiration={grant.expiration} token={_mask(grant.security_token)}")
    report.ok("object key", grant.object_key)
    return grant


def _sts_bucket(grant):
    """用签发出的临时凭证构造 bucket 客户端——等价于浏览器端 ali-oss 的处境。"""
    import oss2

    auth = oss2.StsAuth(grant.access_key_id, grant.access_key_secret, grant.security_token)
    return oss2.Bucket(auth, _public_endpoint(grant.endpoint), grant.bucket)


def _check_upload(report: Report, grant) -> bool:
    """授权前缀内可写：这条通了才说明角色本身的权限没被 RAM 策略卡死。

    注意 STS 的 Policy 参数只做**收窄**，最终权限是角色策略与该 Policy 的交集——
    角色策略若没给 PutObject，这里一样会 403。
    """
    import oss2

    print("[3/7] 临时凭证写入授权前缀")
    try:
        _sts_bucket(grant).put_object(grant.object_key, PROBE_PAYLOAD)
    except oss2.exceptions.OssError as exc:
        report.fail(
            "前缀内写入", f"{exc.status} {exc.code}: {exc.message}",
            "角色自身的权限策略需允许对 bucket 的 PutObject/分片上传（STS Policy 只能收窄、不能放宽）",
        )
        return False
    report.ok("前缀内写入", f"{len(PROBE_PAYLOAD)} 字节已直传")
    return True


def _check_denied(report: Report, grant) -> None:
    """授权前缀外必须被拒——这一项通过才证明前端无法越权写到别的站点/期次。"""
    import oss2

    from config import settings

    print("[4/7] 临时凭证越权写入（预期被拒）")
    outside_key = f"{settings.OSS_KEY_PREFIX.strip('/')}/__selfcheck_denied__/{uuid.uuid4().hex}.txt"
    try:
        _sts_bucket(grant).put_object(outside_key, b"should-not-be-writable")
    except oss2.exceptions.OssError as exc:
        if exc.status == 403:
            report.ok("越权被拒", f"{exc.code}（最小权限生效）")
        else:
            report.warn("越权被拒", f"被拒但状态码是 {exc.status} {exc.code}，非预期的 403",
                        "确认拒绝来自权限策略而非其他错误")
        return

    # 写成功 = 凭证权限比设计更宽。留着这个对象只会掩盖问题，顺手删掉并报失败。
    report.fail(
        "越权被拒", f"临时凭证成功写入了授权前缀之外的 {outside_key}",
        "STS Policy 已限死前缀，出现这种情况通常是角色策略被直接附加了过宽的 OSS 权限；"
        "收窄角色策略的 Resource 到 acs:oss:*:*:<bucket>/<前缀>*",
    )
    try:
        from app.table.upload.oss_client import _default_bucket

        _default_bucket().delete_object(outside_key)
    except Exception:  # noqa: BLE001 — 清理失败不改变结论，对象路径已打印出来
        print(f"     ↳ 越权写入的对象未能自动删除，请手工清理：{outside_key}")


def _check_readback(report: Report, grant) -> None:
    """服务端读回：导入链路真正吃的就是这条流，内容必须逐字节一致。"""
    from app.table.upload import oss_client

    print("[5/7] 服务端读回对象")
    try:
        if not oss_client.object_exists(grant.object_key):
            report.fail("object_exists", "服务端认为对象不存在",
                        "确认服务端 AK 有该 bucket 的 oss:GetObject 权限")
            return
        with oss_client.object_stream(grant.object_key) as stream:
            content = stream.read()
    except Exception as exc:  # noqa: BLE001
        report.fail("读回", f"{type(exc).__name__}: {exc}",
                    "服务端 AK 需要对该 bucket 的 oss:GetObject 权限")
        return

    if content != PROBE_PAYLOAD:
        report.fail("读回", f"内容不一致：期望 {PROBE_PAYLOAD!r}，得到 {content[:64]!r}")
        return
    report.ok("读回", "内容与上传一致，导入侧可从 OSS 流式读取")


def _rule_allows(rule, origin: str) -> bool:
    return any(fnmatch.fnmatch(origin, allowed) for allowed in rule.allowed_origins)


def _check_cors(report: Report, origin: str) -> None:
    """bucket CORS：浏览器直传的第一道门，配错了前端连预检都过不去。

    需要 PUT（简单上传）与 POST（分片上传的 Initiate/Complete）；``ETag`` 必须在
    ``expose_headers`` 里——ali-oss 的分片上传要读取每片的 ETag 才能 Complete。
    """
    import oss2

    from app.table.upload.oss_client import _default_bucket

    print(f"[6/7] bucket CORS（origin={origin}）")
    try:
        rules = _default_bucket().get_bucket_cors().rules
    except oss2.exceptions.NoSuchCors:
        report.fail(
            "CORS 规则", "bucket 未配置任何 CORS 规则",
            f"控制台 → bucket → 权限管理 → 跨域设置，加一条：来源 {origin}、"
            "方法 PUT/POST/GET/HEAD、Header 允许 *、暴露 ETag、缓存 600 秒",
        )
        return
    except oss2.exceptions.OssError as exc:
        report.warn("CORS 规则", f"读取失败：{exc.status} {exc.code}",
                    "服务端 AK 缺 oss:GetBucketCors 权限，跳过本项；请手工到控制台核对")
        return

    matched = [rule for rule in rules if _rule_allows(rule, origin)]
    if not matched:
        origins = sorted({o for rule in rules for o in rule.allowed_origins})
        report.fail("CORS 来源", f"{len(rules)} 条规则都不匹配 {origin}（现有来源：{', '.join(origins)}）",
                    f"把 {origin} 加进允许来源")
        return

    rule = matched[0]
    methods = {m.upper() for m in rule.allowed_methods}
    missing = {"PUT", "POST"} - methods
    if missing:
        report.fail("CORS 方法", f"匹配规则缺少 {', '.join(sorted(missing))}",
                    "分片直传需要 POST（Initiate/Complete）与 PUT（UploadPart）")
    else:
        report.ok("CORS 方法", f"允许 {', '.join(sorted(methods))}")

    if not any(h == "*" for h in rule.allowed_headers):
        report.warn("CORS 请求头", f"未允许 * （当前 {rule.allowed_headers}）",
                    "直传会带 x-oss-* 与 content-type 等头，建议放开为 *")
    else:
        report.ok("CORS 请求头", "允许 *")

    if not any(h.lower() == "etag" for h in rule.expose_headers):
        report.fail("CORS 暴露头", f"未暴露 ETag（当前 {rule.expose_headers}）",
                    "ali-oss 分片上传需读取每片 ETag 才能 CompleteMultipartUpload")
    else:
        report.ok("CORS 暴露头", "已暴露 ETag")


def _check_lifecycle(report: Report) -> None:
    """未完成分片的兜底清理规则。

    分片上传中途放弃（关页面、断网、浏览器崩溃）会在 bucket 里留下已上传的分片：它们不
    构成对象、在文件列表里看不见，却**持续计费**。前端在本次会话内放弃时会主动 abort，
    但页面一关就没人来收了——只有 bucket 生命周期规则能兜住这种情况。

    缺规则只告警不判失败：它是运维加固项，不影响直传链路本身能否跑通。
    """
    import oss2

    from app.table.upload.oss_client import _default_bucket

    print("[7/7] 未完成分片的生命周期规则")
    try:
        rules = _default_bucket().get_bucket_lifecycle().rules
    except oss2.exceptions.NoSuchLifecycle:
        report.warn(
            "生命周期规则", "bucket 未配置任何生命周期规则",
            "控制台 → bucket → 数据管理 → 生命周期，加一条：对整个 bucket，"
            "「碎片」策略设为 7 天后删除未完成的分片上传",
        )
        return
    except oss2.exceptions.OssError as exc:
        report.warn("生命周期规则", f"读取失败：{exc.status} {exc.code}",
                    "服务端 AK 缺 oss:GetBucketLifecycle 权限，跳过本项；请手工到控制台核对")
        return

    # oss2 把「碎片」策略映射为 rule.abort_multipart_upload
    aborts = [r for r in rules if getattr(r, "abort_multipart_upload", None)]
    if not aborts:
        report.warn(
            "生命周期规则", f"{len(rules)} 条规则里没有清理未完成分片的那条",
            "补一条「碎片」策略（AbortMultipartUpload），否则放弃的分片会一直计费",
        )
        return
    days = getattr(aborts[0].abort_multipart_upload, "days", None)
    report.ok("生命周期规则", f"已配置未完成分片清理（{days or '按指定日期'} 天）")


def _cleanup(report: Report, grant) -> None:
    from app.table.upload.oss_client import _default_bucket

    try:
        _default_bucket().delete_object(grant.object_key)
        print(f"\n探针对象已清理：{grant.object_key}")
    except Exception as exc:  # noqa: BLE001
        report.warn("清理探针对象", f"{type(exc).__name__}: {exc}",
                    f"请手工删除 {grant.object_key}（服务端 AK 可能缺 oss:DeleteObject 权限）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--site", default=None, help="用哪个站点签凭证，默认 DEFAULT_SITE")
    parser.add_argument("--period", default="DAY", choices=["DAY", "WEEK"], help="周期，默认 DAY")
    parser.add_argument("--origin", default="http://localhost:8000",
                        help="核对 CORS 用的前端 origin，默认 http://localhost:8000")
    parser.add_argument("--keep", action="store_true", help="保留探针对象，便于手工排查")
    args = parser.parse_args(argv)

    from config import settings

    report = Report()
    print(f"OSS 直传链路自检 — endpoint={settings.OSS_ENDPOINT or '(未配置)'}\n")

    if not _check_config(report):
        return report.summary()

    site = args.site or settings.default_site
    grant = None
    if settings.OSS_STS_ROLE_ARN:
        grant = _check_sts(report, site, args.period, date.today())
    else:
        print("[2/7] STS 签发临时凭证")
        report.fail(
            "OSS_STS_ROLE_ARN", "未配置，跳过 STS 与直传三段检查",
            "填入前端直传所扮演的角色 ARN（acs:ram::<主账号ID>:role/<角色名>）",
        )

    uploaded = False
    if grant is not None:
        uploaded = _check_upload(report, grant)
        _check_denied(report, grant)
        if uploaded:
            _check_readback(report, grant)
    _check_cors(report, args.origin)
    _check_lifecycle(report)

    if uploaded and not args.keep:
        _cleanup(report, grant)

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(main())
