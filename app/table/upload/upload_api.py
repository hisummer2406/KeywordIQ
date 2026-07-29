import logging
import os
import uuid
import threading
import aiofiles
import tempfile
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, Depends

from app.auth.simple_auth import simple_auth
from app.auth.permissions import UPLOAD_IMPORT

from app.table.upload.upload_schemas import (
    ChunkStartRequest,
    FinishChunkRequest,
    OSSImportRequest,
    UploadCredentialRequest,
)
from database import SessionFactory
from app.table.upload.upload_service import UploadService
from app.table.upload.importer import parse_report_filename
from app.table.upload.import_service import run_import, ImportRejected
from app.table.upload.import_source import LocalFileSource, OSSObjectSource
from app.table.upload import oss_client
from app.site_registry import UnknownSiteError, normalize_site_code, SITE_CATALOG
from config import settings
import database

logger = logging.getLogger(__name__)
upload_router = APIRouter()

# 分块上传状态管理（单用户场景无需锁）
chunk_sessions: Dict[str, Dict[str, Any]] = {}

# 单用户场景优化
MAX_CONCURRENT_UPLOADS = 2  # 最多2个同时上传（防止误操作）
upload_semaphore = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)
ALLOWED_DATA_TYPES = {"daily", "weekly"}

# 每种数据类型同一时刻只允许一个处理任务，防止并发 UPSERT 行锁竞争
_processing_locks: Dict[str, threading.Lock] = {
    "daily": threading.Lock(),
    "weekly": threading.Lock(),
}


async def process_csv_background_async(file_path: str, original_filename: str, data_type: str) -> None:
    """异步后台处理CSV文件 - 独立数据库会话，确保会话正确关闭"""
    db = None
    try:
        db = SessionFactory()
        upload_service = UploadService(db)
        success, message, batch_record = await upload_service.process_csv_file(
            file_path, original_filename, data_type
        )
        if success:
            logger.info(f"处理成功: {original_filename}, 批次: {batch_record.batch_name if batch_record else 'N/A'}")
        else:
            logger.error(f"处理失败: {message}")
    except Exception as e:
        logger.error(f"后台处理异常: {e}", exc_info=True)
    finally:
        # 确保数据库会话关闭
        if db is not None:
            try:
                db.close()
            except Exception as e:
                logger.warning(f"关闭数据库会话失败: {e}")


def process_csv_background(file_path: str, original_filename: str, data_type: str) -> None:
    """同步包装器 - 在新的事件循环中运行异步函数，含超时保护和串行化锁"""
    lock = _processing_locks.get(data_type)
    lock_acquired = False
    if lock is None:
        logger.warning(f"未知 data_type={data_type}，跳过串行锁")
    else:
        logger.info(f"等待 {data_type} 处理锁: {original_filename}")
        lock.acquire()
        lock_acquired = True
        logger.info(f"获得 {data_type} 处理锁，开始处理: {original_filename}")

    # 周报(~300万行)最长允许 2 小时，日报(~30万行)最长 30 分钟
    timeout = 7200 if data_type == 'weekly' else 1800
    try:
        asyncio.run(
            asyncio.wait_for(
                process_csv_background_async(file_path, original_filename, data_type),
                timeout=timeout
            )
        )
    except asyncio.TimeoutError:
        logger.error(f"处理超时（>{timeout}s），文件: {original_filename}")
    except Exception as e:
        logger.error(f"后台处理包装器异常: {e}", exc_info=True)
    finally:
        if lock_acquired:
            lock.release()
            logger.info(f"释放 {data_type} 处理锁: {original_filename}")
        if os.path.exists(file_path):
            try:
                os.unlink(file_path)
            except Exception as cleanup_error:
                logger.error(f"清理文件失败: {cleanup_error}")


async def _cleanup_chunk_session(session_key: str, delay: int = 300) -> None:
    """延迟清理分块上传会话（默认5分钟后清理）"""
    await asyncio.sleep(delay)
    if session_key in chunk_sessions:
        session = chunk_sessions.pop(session_key, None)
        if session:
            # 清理临时目录（防止merge失败遗留）
            import shutil
            temp_dir = session.get("temp_dir")
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"清理临时目录失败: {e}")
        logger.info(f"已延迟清理会话: {session_key}")


@upload_router.post("/startChunkApi")
async def start_chunk_api(chunk: ChunkStartRequest) -> Dict[str, Any]:
    """AMIS分块上传 - 开始上传接口"""
    logger.info(f"收到AMIS分块上传请求 - filename: {chunk.filename}, data_type: {chunk.data_type}")

    if len(chunk_sessions) >= MAX_CONCURRENT_UPLOADS:
        return {"status": 1, "msg": "已有上传任务进行中，请稍后"}

    # 生成唯一标识
    upload_id = str(uuid.uuid4())
    key = f"{uuid.uuid4().hex}_{chunk.filename}"
    temp_dir = tempfile.mkdtemp(prefix="amis_chunk_")

    # 保存会话信息
    chunk_sessions[key] = {
        "upload_id": upload_id,
        "filename": chunk.filename,
        "data_type": chunk.data_type,
        "temp_dir": temp_dir,
        "chunks": {},
    }

    logger.info(f"AMIS分块上传会话创建: key={key}, upload_id={upload_id}")

    return {
        "status": 0,
        "data": {"key": key, "uploadId": upload_id, "date": datetime.now().isoformat()},
        "msg": "分块上传会话创建成功"
    }


@upload_router.post("/chunkApi")
async def chunk_api(
        key: str = Form(..., description="上传会话key"),
        partNumber: str = Form(..., description="分块序号"),
        file: UploadFile = File(..., description="分块文件")
) -> Dict[str, Any]:
    """AMIS分块上传 - 上传分块接口"""
    session = chunk_sessions.get(key)
    if not session:
        return {"status": 1, "msg": "无效的上传会话key"}

    if session.get("locked"):
        logger.warning(f"会话已锁定，拒绝新分块: {key}")
        return {"status": 1, "msg": "上传已完成，拒绝新分块"}

    part_num = int(partNumber)
    logger.info(f"收到分块上传: key={key}, partNumber={part_num}")

    # 保存分块文件
    chunk_filename = f"part_{part_num:04d}.chunk"
    chunk_path = os.path.join(session["temp_dir"], chunk_filename)

    chunk_size = 0
    async with aiofiles.open(chunk_path, 'wb') as f:
        while upload_chunk := await file.read(65536):
            await f.write(upload_chunk)
            chunk_size += len(upload_chunk)

    # 记录分块信息
    session["chunks"][part_num] = {
        "path": chunk_path,
        "size": chunk_size,
        "uploaded_at": datetime.now()
    }

    logger.info(f"分块 {part_num} 上传完成, key={key}, size={chunk_size}")

    return {"status": 0, "msg": "分块上传成功", "data": {"partNumber": part_num, "key": key}}


def merge_chunks_and_process(
        session_key: str,
        session_data: Dict[str, Any],
        _: FinishChunkRequest = None
) -> None:
    """后台任务：合并分块文件并处理CSV"""
    import shutil
    temp_dir = session_data.get("temp_dir")
    try:
        logger.info(f"开始后台合并分块文件: key={session_key}, 分块数量={len(session_data['chunks'])}")

        # 创建最终文件路径
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_filename = f"{timestamp}_{session_data['filename']}"
        upload_dir = Path(settings.UPLOAD_DIR) / session_data["data_type"]
        upload_dir.mkdir(parents=True, exist_ok=True)
        final_path = upload_dir / final_filename

        # 按序号合并分块文件（流式拷贝 + 合并后立即删除分块，降低峰值磁盘占用）
        total_size = 0
        with open(final_path, 'wb') as outfile:
            sorted_parts = sorted(session_data["chunks"].items())
            for part_num, chunk_info in sorted_parts:
                chunk_path = chunk_info["path"]
                if os.path.exists(chunk_path):
                    with open(chunk_path, 'rb') as chunk_file:
                        shutil.copyfileobj(chunk_file, outfile)
                    total_size += os.path.getsize(chunk_path)
                    try:
                        os.unlink(chunk_path)
                    except Exception as e:
                        logger.warning(f"删除分块文件失败: {chunk_path}: {e}")
                else:
                    logger.error(f"分块文件不存在: {chunk_path}")
                    return

        logger.info(f"文件合并完成: {final_filename}, 总大小: {total_size / 1024 / 1024:.2f}MB")

        # 触发CSV处理
        process_csv_background(str(final_path), session_data['filename'], session_data['data_type'])

    except Exception as e:
        logger.error(f"后台合并处理失败: {e}", exc_info=True)
    finally:
        # 清理临时目录
        try:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

                # 从全局字典中删除会话
                chunk_sessions.pop(session_key, None)
                
                logger.info(f"已清理临时目录: {temp_dir}")
        except Exception as e:
            logger.warning(f"清理临时目录失败: {e}")


@upload_router.post("/finishChunkApi")
async def finish_chunk_api(
        background_tasks: BackgroundTasks,
        finish_chunk: FinishChunkRequest,
) -> Dict[str, Any]:
    """AMIS分块上传 - 完成上传接口（后台合并，避免504超时）"""
    session = chunk_sessions.get(finish_chunk.key)
    if not session:
        return {"status": 1, "msg": "无效的上传会话key"}

    # 验证分块完整性
    expected = len(finish_chunk.partList)
    actual = len(session["chunks"])
    if actual < expected:
        logger.warning(f"分块不完整: {actual}/{expected}, key={finish_chunk.key}")
        return {"status": 1, "msg": f"分块未完成: {actual}/{expected}"}

    # 标记会话为已锁定，拒绝新分块
    session["locked"] = True
    session_copy = session.copy()

    # 后台合并
    background_tasks.add_task(merge_chunks_and_process, finish_chunk.key, session_copy)

    # 延迟删除会话（5分钟后）
    # background_tasks.add_task(_cleanup_chunk_session, finish_chunk.key, 300)

    return {
        "status": 0,
        "msg": "文件上传成功，正在后台处理",
        "data": {
            "filename": session_copy['filename'],
            "chunks_count": actual,
            "status": "processing"
        }
    }


def _run_import_bg(file_path: str, original_filename: str, site: str, period: str, report_date) -> None:
    """后台任务：从本地文件执行 V2 三段式导入（OSS 未启用时的退路）。

    异常都吞进批次记录，不冒泡到后台任务。
    """
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
    """后台任务：以 OSS 对象为源执行 V2 三段式导入。

    从 OSS 流式读取喂 COPY，object key 记入批次记录供溯源。异常吞进批次记录。
    """
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
    """V2 导入接口：站点必填，周期可从文件名推断，报告日期从文件名解析。

    触发形态沿用 V1（HTTP 上传 → 后台三段式处理），只换处理内核。
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

        # 从文件名解析站点前缀与报告日期
        prefix_site, report_date = parse_report_filename(file.filename)

        # 站点一致性校验：文件名前缀与声明站点冲突时拒绝，不猜
        if prefix_site and prefix_site != site:
            return {"status": 1, "msg": f"声明站点为 {site}，但文件名前缀是 {prefix_site}，请确认"}

        if report_date is None:
            return {"status": 1, "msg": "无法从文件名解析出报告日期（需形如 ..._2026_07_18.csv）"}

        # 周期：优先用显式参数，否则从文件名推断
        if period:
            period = period.strip().upper()
        else:
            period = "DAY" if "day" in file.filename.lower() else "WEEK"
        if period not in ("DAY", "WEEK"):
            return {"status": 1, "msg": "period 只支持 DAY 或 WEEK"}

        # 保存文件（落盘后再交后台，保持与 V1 一致的形态）
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


# 保持原有的传统上传接口和状态查询接口不变
@upload_router.post("/upload-csv")
async def upload_csv_file(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        data_type: Optional[str] = Form(None)
) -> Dict[str, Any]:
    """传统上传方式 - 兼容小文件"""
    try:
        if not file or not file.filename:
            return {"status": 1, "msg": "请选择要上传的文件"}

        if not file.filename.lower().endswith('.csv'):
            return {"status": 1, "msg": "只支持CSV文件"}

        # 确定数据类型
        data_type = (data_type or ('daily' if 'day' in file.filename.lower() else 'weekly')).strip().lower()
        if data_type not in ALLOWED_DATA_TYPES:
            return {"status": 1, "msg": "data_type 只支持 daily 或 weekly"}

        # 保存文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_filename = f"{timestamp}_{file.filename}"
        upload_dir = Path(settings.UPLOAD_DIR) / data_type
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = upload_dir / safe_filename

        async with aiofiles.open(file_path, 'wb') as f:
            while chunk := await file.read(8192):
                await f.write(chunk)

        # 后台处理
        background_tasks.add_task(
            process_csv_background,
            str(file_path),
            file.filename,
            data_type
        )

        return {
            "status": 0,
            "msg": "文件上传成功，正在后台处理",
            "data": {
                "filename": file.filename,
                "data_type": data_type
            }
        }

    except Exception as e:
        logger.error(f"上传失败: {e}")
        return {"status": 1, "msg": str(e)}


@upload_router.get("/processing-status")
async def get_processing_status(data_type: Optional[str] = None) -> Dict[str, Any]:
    """获取处理状态"""
    async with database.AsyncSessionFactory() as db:
        try:
            from app.table.upload.import_model import ImportBatchRecords
            from sqlalchemy import desc, select

            stmt = select(ImportBatchRecords)
            if data_type == 'daily':
                stmt = stmt.where(ImportBatchRecords.is_day_data == True)
            elif data_type == 'weekly':
                stmt = stmt.where(ImportBatchRecords.is_week_data == True)

            stmt = stmt.order_by(desc(ImportBatchRecords.created_at)).limit(5)
            result = await db.execute(stmt)
            records = list(result.scalars().all())

            items = []
            for r in records:
                progress = round((r.processed_keywords / max(r.total_records, 1)) * 100, 1)
                items.append({
                    "batch_name": r.batch_name,
                    "progress_percent": progress,
                    "total_records": r.total_records,
                    "status": "处理中" if r.status.value == 'PROCESSING' else r.status.value
                })

            return {
                "status": 0,
                "data": {
                    "items": items,
                    "active_sessions": len(chunk_sessions)
                }
            }

        except Exception as e:
            logger.error(f"获取状态失败: {e}")
            return {"status": 1, "data": {"items": []}}


@upload_router.get("/concurrent-status")
async def get_concurrent_status() -> Dict[str, Any]:
    """获取上传会话状态"""
    return {
        "status": 0,
        "data": {
            "active_chunk_sessions": len(chunk_sessions),
            "chunk_session_keys": list(chunk_sessions.keys())[:10]  # 只返回前10个key用于调试
        }
    }
