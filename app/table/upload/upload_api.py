"""上传/导入 API（V2 OSS-first，本地文件为退路）。

V1 的分片上传（startChunkApi/chunkApi/finishChunkApi）、`/upload-csv`（走 UploadService +
csv_processor + import_batch_records V1 表）已随 V1 内核退役删除。V2 导入内核见
`import_service.run_import`（三段式 COPY → 暂存 → 维度 → 追加事实）。
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool

import database
from app.auth.permissions import UPLOAD_IMPORT
from app.auth.simple_auth import simple_auth
from app.common.errors import (
    CODE_UPLOAD_BAD_FILE,
    CODE_UPLOAD_FILE_TOO_LARGE,
    BadRequestError,
    PayloadTooLargeError,
)
from app.site_registry import SITE_CATALOG, UnknownSiteError, normalize_site_code
from app.table.upload import oss_client
from app.table.upload.import_service import ImportRejected, run_import
from app.table.upload.import_source import LocalFileSource, OSSObjectSource
from app.table.upload.importer import infer_period, parse_report_filename
from app.table.upload.upload_schemas import (
    FilenameInspectRequest,
    OSSImportRequest,
    UploadCredentialRequest,
)
from config import settings

logger = logging.getLogger(__name__)
upload_router = APIRouter()


def _run_import_bg(file_path: str, original_filename: str, site: str, period: str, report_date) -> None:
    """后台任务：从本地文件执行 V2 三段式导入（OSS 未启用时的退路）。异常吞进批次记录。"""
    try:
        batch = run_import(
            site=site, period=period, report_date=report_date,
            source=LocalFileSource(file_path), original_filename=original_filename,
        )
        logger.info("导入任务结束 %s: 状态=%s", original_filename, batch.status)
    except ImportRejected as exc:
        logger.warning("导入被拒（已有导入在进行）%s: %s", original_filename, exc)
    except Exception:  # 兜底，后台任务不应抛出
        # 必须 exception 而非 error：全局处理器不拦截 BackgroundTasks，
        # 栈是这条路径唯一的线索来源
        logger.exception("导入后台任务异常 %s", original_filename)


def _run_oss_import_bg(object_key: str, original_filename: str, site: str, period: str, report_date) -> None:
    """后台任务：以 OSS 对象为源执行 V2 三段式导入。从 OSS 流式读取喂 COPY，异常吞进批次记录。"""
    try:
        source = OSSObjectSource(oss_client.object_stream, object_key)
        batch = run_import(
            site=site, period=period, report_date=report_date,
            source=source, original_filename=original_filename, object_key=object_key,
        )
        logger.info("OSS 导入任务结束 %s: 状态=%s", object_key, batch.status)
    except ImportRejected as exc:
        logger.warning("导入被拒（已有导入在进行）%s: %s", object_key, exc)
    except Exception:  # 兜底，后台任务不应抛出
        logger.exception("OSS 导入后台任务异常 %s", object_key)


def _resolve_import_meta(filename: str, site: str, period: str | None):
    """从文件名 + 声明站点解析出 (site, period, report_date)。

    校验不通过直接抛 `BadRequestError`——此前是返回 ``(None, None, None, 错误消息)``，
    调用方必须记得检查第四个返回值才能发现失败，漏检就静默放行。
    """
    if not filename.lower().endswith(".csv"):
        raise BadRequestError("只支持 CSV 文件", code=CODE_UPLOAD_BAD_FILE)
    prefix_site, report_date = parse_report_filename(filename)
    if prefix_site and prefix_site != site:
        raise BadRequestError(
            f"声明站点为 {site}，但文件名前缀是 {prefix_site}，请确认", code=CODE_UPLOAD_BAD_FILE
        )
    if report_date is None:
        raise BadRequestError(
            "无法从文件名解析出报告日期（需形如 ..._2026_07_18.csv）", code=CODE_UPLOAD_BAD_FILE
        )
    inferred_period = infer_period(filename)
    if period:
        resolved_period = period.strip().upper()
        if resolved_period not in ("DAY", "WEEK"):
            raise BadRequestError("period 只支持 DAY 或 WEEK", code=CODE_UPLOAD_BAD_FILE)
        # 与站点同理：显式指定和文件名打架时拒绝，不任选一方静默继续
        if inferred_period and inferred_period != resolved_period:
            raise BadRequestError(
                f"指定周期为 {resolved_period}，但文件名表明是 {inferred_period}，请确认",
                code=CODE_UPLOAD_BAD_FILE,
            )
    elif inferred_period:
        resolved_period = inferred_period
    else:
        # 不兜底。此前是「非 day 即 week」，会把关键词缺失的日报静默写进周报事实表。
        raise BadRequestError(
            "无法从文件名判断这是日报还是周报，请在界面上指定周期",
            code=CODE_UPLOAD_BAD_FILE,
        )
    return site, resolved_period, report_date


@upload_router.get("/sites")
async def list_sites(
    _user=Depends(simple_auth.get_current_user),
) -> dict[str, Any]:
    """站点下拉数据。唯一事实来源是 app/site_registry.py，前端不得硬编码站点列表。"""
    return {
        "sites": [
            {"code": s.code, "name_zh": s.name_zh, "domain": s.domain}
            for s in SITE_CATALOG.values()
        ]
    }


@upload_router.post("/inspect-filename")
async def inspect_filename(
    req: FilenameInspectRequest,
    _user=Depends(simple_auth.require_permission(UPLOAD_IMPORT)),
) -> dict[str, Any]:
    """只读：把文件名解析成归属三元组（站点 / 周期 / 报告日期），供上传屏在上传前回显。

    **解析规则只在后端实现一份**：站点前缀正则、日期正则、周期关键词都在 importer 里，
    前端若自己再写一遍，两处迟早各自演化，用户看到的回显与实际写入的归属就会不一致。

    与其他端点不同，这里**判定不出不算错误**——哪一项判定不出正是前端要拿来决定"该让用户
    补选哪一项"的信息，故一律 200 返回，缺的项为 null 并列在 ``needs`` 里。
    """
    filename = req.filename
    if not filename.lower().endswith(".csv"):
        raise BadRequestError("只支持 CSV 文件", code=CODE_UPLOAD_BAD_FILE)

    prefix_site, report_date = parse_report_filename(filename)
    period = infer_period(filename)

    site = None
    if prefix_site:
        # 文件名前缀可能是任意两个字母，未登记的站点码等同于判定不出，交给用户补选
        try:
            site = normalize_site_code(prefix_site)
        except UnknownSiteError:
            site = None

    needs = [k for k, v in (("site", site), ("period", period), ("report_date", report_date)) if not v]
    return {
        "filename": filename,
        "site": site,
        "period": period,
        "report_date": str(report_date) if report_date else None,
        "needs": needs,
    }


@upload_router.post("/oss/upload-credentials")
async def oss_upload_credentials(
    req: UploadCredentialRequest,
    _user=Depends(simple_auth.require_permission(UPLOAD_IMPORT)),
) -> dict[str, Any]:
    """签发前端直传 OSS 的受限临时凭证与目标 object key。

    权限被 RAM 策略限死到本次上传的 bucket 与 key 前缀、仅写操作、短有效期。
    前端据此直传，完成后把 object_key 回传到 /oss/import 触发导入。
    """
    site, period, report_date = _resolve_import_meta(req.filename, req.site, req.period)
    grant = oss_client.issue_upload_credentials(site, period, report_date, req.filename)
    return {
        **grant.to_response(),
        "site": site,
        "period": period,
        "report_date": str(report_date),
        "filename": req.filename,
    }


@upload_router.post("/oss/import")
async def oss_import(
    req: OSSImportRequest,
    background_tasks: BackgroundTasks,
    _user=Depends(simple_auth.require_permission(UPLOAD_IMPORT)),
) -> dict[str, Any]:
    """前端完成直传后，凭 object key 触发从 OSS 拉取导入。

    校验 object key 落在授权前缀内、对象确实存在，再交后台流式导入。
    """
    site, period, report_date = _resolve_import_meta(req.filename, req.site, req.period)

    # 越权防护：object key 必须落在该 (site, period, report_date) 被授权的前缀内。
    # 错误消息不回显 object_key——它含站点/期次/日期/原文件名。
    if not oss_client.is_key_within_allowed_prefix(req.object_key, site, period, report_date):
        raise BadRequestError(
            "上传凭证与导入目标不匹配，拒绝导入",
            code=CODE_UPLOAD_BAD_FILE,
            log_detail=f"object_key 越权: {req.object_key}",
        )

    # 一次 head 同时确认「对象存在」（不存在则拒绝，不产生部分导入）与「有多大」。
    # 走线程池：oss2 是阻塞 SDK，直接在 async 端点里调会占住事件循环一整个网络往返。
    #
    # 大小上限在这里执行。直传下字节不经应用服务器，这是服务端唯一能称量文件的时机；
    # 拦在这里挡掉的是"导入一个超大文件"的代价，而非"上传"的代价。
    size = await run_in_threadpool(oss_client.stat_object, req.object_key)
    if size > settings.MAX_FILE_SIZE:
        raise PayloadTooLargeError(
            f"文件超过上限（{size / 1024 ** 3:.2f} GB > "
            f"{settings.MAX_FILE_SIZE / 1024 ** 3:.2f} GB），请拆分后再导入",
            code=CODE_UPLOAD_FILE_TOO_LARGE,
            log_detail=f"object key {req.object_key} 大小 {size} 超过 {settings.MAX_FILE_SIZE}",
        )

    background_tasks.add_task(
        _run_oss_import_bg, req.object_key, req.filename, site, period, report_date
    )
    return {
        "object_key": req.object_key,
        "site": site,
        "period": period,
        "report_date": str(report_date),
    }


@upload_router.post("/import-csv")
async def import_csv_file(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        site: str = Form(...),
        period: str | None = Form(None),
        _user=Depends(simple_auth.require_permission(UPLOAD_IMPORT)),
) -> dict[str, Any]:
    """V2 本地导入退路（OSS_ENABLED=false）：站点必填，周期从文件名推断，报告日期从文件名解析。

    HTTP 上传落盘后交后台三段式处理。GB 级生产场景走 OSS 直传（/oss/*），此路供本地/CI。
    """
    if not file or not file.filename:
        raise BadRequestError("请选择要上传的文件", code=CODE_UPLOAD_BAD_FILE)

    # 站点校验（必填、必须合法）；站点非法的消息对用户有意义，可直接展示
    try:
        site = normalize_site_code(site)
    except UnknownSiteError as exc:
        raise BadRequestError(str(exc), code=CODE_UPLOAD_BAD_FILE) from exc

    # 文件名/日期/周期校验与 OSS 路径共用同一套规则
    site, period, report_date = _resolve_import_meta(file.filename, site, period)

    # 落盘后再交后台
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_dir = Path(settings.UPLOAD_DIR) / period.lower()
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{timestamp}_{file.filename}"
    # 边写边计数：超上限立刻中止并删掉半截文件。不能等写完再判——那样磁盘已经被占了，
    # 而这条退路本就是给本地/CI 用的，没有 OSS 那样的空间冗余。
    written = 0
    try:
        async with aiofiles.open(file_path, "wb") as f:
            while chunk := await file.read(8192):
                written += len(chunk)
                if written > settings.MAX_FILE_SIZE:
                    raise PayloadTooLargeError(
                        f"文件超过上限（> {settings.MAX_FILE_SIZE / 1024 ** 3:.2f} GB），"
                        "请拆分后再导入",
                        code=CODE_UPLOAD_FILE_TOO_LARGE,
                        log_detail=f"{file.filename} 写入 {written} 字节时超过 {settings.MAX_FILE_SIZE}",
                    )
                await f.write(chunk)
    except PayloadTooLargeError:
        file_path.unlink(missing_ok=True)
        raise

    background_tasks.add_task(
        _run_import_bg, str(file_path), file.filename, site, period, report_date
    )
    return {
        "filename": file.filename,
        "site": site,
        "period": period,
        "report_date": str(report_date),
    }


@upload_router.get("/processing-status")
async def get_processing_status(
    data_type: str | None = None,
    _user=Depends(simple_auth.get_current_user),
) -> dict[str, Any]:
    """最近导入批次状态（读 V2 import_batch）。data_type 兼容前端旧写法 daily/weekly。"""
    from sqlalchemy import desc, select

    from app.table.upload.import_batch_model import ImportBatch

    # 数据库异常不再吞成"空列表 + status:1"——那会让前端把"查询失败"显示成"没有批次"。
    # 交给全局的 SQLAlchemyError 处理器区分 503/500。
    async with database.AsyncSessionFactory() as db:
        period = {"daily": "DAY", "weekly": "WEEK"}.get(data_type)
        stmt = select(ImportBatch)
        if period:
            stmt = stmt.where(ImportBatch.period_type == period)
        stmt = stmt.order_by(desc(ImportBatch.created_at)).limit(5)
        records = list((await db.execute(stmt)).scalars().all())

    items = [
        {
            "batch_name": r.file_name or f"{r.site} {r.period_type} {r.report_date}",
            "progress_percent": round((r.written_rows / max(r.total_rows, 1)) * 100, 1),
            "total_records": r.total_rows,
            "status": "处理中" if r.status == "PROCESSING" else r.status,
        }
        for r in records
    ]
    return {"items": items}
