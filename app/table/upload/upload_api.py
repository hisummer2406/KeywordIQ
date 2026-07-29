"""上传/导入 API（V2 OSS-first，本地文件为退路）。

V1 的分片上传（startChunkApi/chunkApi/finishChunkApi）、`/upload-csv`（走 UploadService +
csv_processor + import_batch_records V1 表）已随 V1 内核退役删除。V2 导入内核见
`import_service.run_import`（三段式 COPY → 暂存 → 维度 → 追加事实）。
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile

from app.auth.simple_auth import simple_auth
from app.auth.permissions import UPLOAD_IMPORT
from app.table.upload.upload_schemas import OSSImportRequest, UploadCredentialRequest
from app.table.upload.importer import parse_report_filename
from app.table.upload.import_service import run_import, ImportRejected
from app.table.upload.import_source import LocalFileSource, OSSObjectSource
from app.table.upload import oss_client
from app.site_registry import UnknownSiteError, normalize_site_code, SITE_CATALOG
from config import settings
import database

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
    except Exception as exc:  # 兜底，后台任务不应抛出
        logger.error("导入后台任务异常 %s: %s", original_filename, exc)


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
    except Exception as exc:  # 兜底，后台任务不应抛出
        logger.error("OSS 导入后台任务异常 %s: %s", object_key, exc)


def _resolve_import_meta(filename: str, site: str, period: Optional[str]):
    """从文件名 + 声明站点解析出 (site, period, report_date)，或返回错误消息。

    返回 ``(site, period, report_date, None)`` 或 ``(None, None, None, 错误消息)``。
    站点前缀与声明站点冲突、日期解析失败、周期非法都在此拦截。
    """
    if not filename.lower().endswith(".csv"):
        return None, None, None, "只支持 CSV 文件"
    prefix_site, report_date = parse_report_filename(filename)
    if prefix_site and prefix_site != site:
        return None, None, None, f"声明站点为 {site}，但文件名前缀是 {prefix_site}，请确认"
    if report_date is None:
        return None, None, None, "无法从文件名解析出报告日期（需形如 ..._2026_07_18.csv）"
    resolved_period = (period or ("DAY" if "day" in filename.lower() else "WEEK")).strip().upper()
    if resolved_period not in ("DAY", "WEEK"):
        return None, None, None, "period 只支持 DAY 或 WEEK"
    return site, resolved_period, report_date, None


@upload_router.get("/sites")
async def list_sites() -> Dict[str, Any]:
    """站点下拉数据。唯一事实来源是 app/site_registry.py，前端不得硬编码站点列表。"""
    return {
        "sites": [
            {"code": s.code, "name_zh": s.name_zh, "domain": s.domain}
            for s in SITE_CATALOG.values()
        ]
    }


@upload_router.post("/oss/upload-credentials")
async def oss_upload_credentials(
    req: UploadCredentialRequest,
    _user=Depends(simple_auth.require_permission(UPLOAD_IMPORT)),
) -> Dict[str, Any]:
    """签发前端直传 OSS 的受限临时凭证与目标 object key。

    权限被 RAM 策略限死到本次上传的 bucket 与 key 前缀、仅写操作、短有效期。
    前端据此直传，完成后把 object_key 回传到 /oss/import 触发导入。
    """
    try:
        site, period, report_date, err = _resolve_import_meta(req.filename, req.site, req.period)
        if err:
            return {"status": 1, "msg": err}
        grant = oss_client.issue_upload_credentials(site, period, report_date, req.filename)
        return {
            "status": 0,
            "msg": "凭证已签发",
            "data": {
                **grant.to_response(),
                "site": site, "period": period, "report_date": str(report_date),
                "filename": req.filename,
            },
        }
    except oss_client.OSSNotConfigured as exc:
        return {"status": 1, "msg": f"OSS 未就绪：{exc}"}
    except Exception as exc:
        logger.error("签发直传凭证失败: %s", exc)
        return {"status": 1, "msg": str(exc)}


@upload_router.post("/oss/import")
async def oss_import(
    req: OSSImportRequest,
    background_tasks: BackgroundTasks,
    _user=Depends(simple_auth.require_permission(UPLOAD_IMPORT)),
) -> Dict[str, Any]:
    """前端完成直传后，凭 object key 触发从 OSS 拉取导入。

    校验 object key 落在授权前缀内、对象确实存在，再交后台流式导入。
    """
    try:
        site, period, report_date, err = _resolve_import_meta(req.filename, req.site, req.period)
        if err:
            return {"status": 1, "msg": err}

        # 越权防护：object key 必须落在该 (site, period, report_date) 被授权的前缀内。
        if not oss_client.is_key_within_allowed_prefix(req.object_key, site, period, report_date):
            return {"status": 1, "msg": "object_key 不在授权前缀内，拒绝导入"}

        # 对象必须存在，否则拒绝（不产生部分导入）。
        if not oss_client.object_exists(req.object_key):
            return {"status": 1, "msg": f"OSS 对象不存在：{req.object_key}"}

        background_tasks.add_task(
            _run_oss_import_bg, req.object_key, req.filename, site, period, report_date
        )
        return {
            "status": 0,
            "msg": "已触发导入，正在后台从 OSS 拉取处理",
            "data": {"object_key": req.object_key, "site": site, "period": period,
                     "report_date": str(report_date)},
        }
    except oss_client.OSSNotConfigured as exc:
        return {"status": 1, "msg": f"OSS 未就绪：{exc}"}
    except Exception as exc:
        logger.error("触发 OSS 导入失败: %s", exc)
        return {"status": 1, "msg": str(exc)}


@upload_router.post("/import-csv")
async def import_csv_file(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        site: str = Form(...),
        period: Optional[str] = Form(None),
        _user=Depends(simple_auth.require_permission(UPLOAD_IMPORT)),
) -> Dict[str, Any]:
    """V2 本地导入退路（OSS_ENABLED=false）：站点必填，周期从文件名推断，报告日期从文件名解析。

    HTTP 上传落盘后交后台三段式处理。GB 级生产场景走 OSS 直传（/oss/*），此路供本地/CI。
    """
    try:
        if not file or not file.filename:
            return {"status": 1, "msg": "请选择要上传的文件"}
        if not file.filename.lower().endswith(".csv"):
            return {"status": 1, "msg": "只支持 CSV 文件"}

        # 站点校验（必填、必须合法）
        try:
            site = normalize_site_code(site)
        except UnknownSiteError as exc:
            return {"status": 1, "msg": str(exc)}

        prefix_site, report_date = parse_report_filename(file.filename)
        if prefix_site and prefix_site != site:
            return {"status": 1, "msg": f"声明站点为 {site}，但文件名前缀是 {prefix_site}，请确认"}
        if report_date is None:
            return {"status": 1, "msg": "无法从文件名解析出报告日期（需形如 ..._2026_07_18.csv）"}

        period = (period.strip().upper() if period else ("DAY" if "day" in file.filename.lower() else "WEEK"))
        if period not in ("DAY", "WEEK"):
            return {"status": 1, "msg": "period 只支持 DAY 或 WEEK"}

        # 落盘后再交后台
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        upload_dir = Path(settings.UPLOAD_DIR) / period.lower()
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = upload_dir / f"{timestamp}_{file.filename}"
        async with aiofiles.open(file_path, "wb") as f:
            while chunk := await file.read(8192):
                await f.write(chunk)

        background_tasks.add_task(
            _run_import_bg, str(file_path), file.filename, site, period, report_date
        )
        return {
            "status": 0,
            "msg": "文件上传成功，正在后台导入",
            "data": {"filename": file.filename, "site": site, "period": period,
                     "report_date": str(report_date)},
        }
    except Exception as e:
        logger.error(f"V2 导入上传失败: {e}")
        return {"status": 1, "msg": str(e)}


@upload_router.get("/processing-status")
async def get_processing_status(data_type: Optional[str] = None) -> Dict[str, Any]:
    """最近导入批次状态（读 V2 import_batch）。data_type 兼容前端旧写法 daily/weekly。"""
    async with database.AsyncSessionFactory() as db:
        try:
            from app.table.upload.import_batch_model import ImportBatch
            from sqlalchemy import desc, select

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
            return {"status": 0, "data": {"items": items}}
        except Exception as e:
            logger.error(f"获取状态失败: {e}")
            return {"status": 1, "data": {"items": []}}
