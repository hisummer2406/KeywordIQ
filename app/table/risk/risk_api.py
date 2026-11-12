"""外观专利核查 API（change add-patent-risk-check）。瘦控制器：调 service。

- ``GET  /risk/meta``：Key 是否已配置（不含 Key 本身）、市场、法域、限制
- ``POST /risk/checks``（multipart，202）、``GET /risk/checks``、``GET /risk/checks/{id}``、
  ``GET /risk/checks/{id}/image``
- ``GET /risk/checks/{id}/files/{candidate_id}/{kind}/{seq}``：已存档的全部附图 / 公报 PDF（经 OSS 代读）
- 显式付费动作（202）：``POST /risk/checks/{id}/pages``、``/evidence``、``/retry``
- ``POST /risk/checks/{id}/reviews``：追加人工意见

**没有全局鉴权中间件**，每个端点逐个挂依赖：查看要 ``risk:view``，任何可能计费的调用要
``risk:search``，签署意见要 ``risk:review``。私有性由 service 每条查询带 ``owner_id`` 保证。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import RISK_REVIEW, RISK_SEARCH, RISK_VIEW
from app.auth.simple_auth import simple_auth
from app.table.risk import check_service
from app.table.risk.jurisdictions import MAX_IMAGE_BYTES
from app.table.risk.risk_errors import InvalidCheckInputError, InvalidImageError
from app.table.risk.risk_schemas import CheckCreate, EvidenceRequest, ReviewCreate, TokenRequest
from database import get_async_db

risk_router = APIRouter()

_require_view = simple_auth.require_permission(RISK_VIEW)
_require_search = simple_auth.require_permission(RISK_SEARCH)
_require_review = simple_auth.require_permission(RISK_REVIEW)


def _accepted(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=202, content=body)


@risk_router.get("/meta")
async def meta(user=Depends(_require_view)) -> dict[str, Any]:
    return check_service.meta()


@risk_router.post("/checks", status_code=202)
async def create_check(
    product_name: str = Form(...),
    product_version: str = Form(...),
    markets: str = Form(..., description="逗号分隔的站点码"),
    jurisdictions: str = Form(..., description="逗号分隔的法域编码"),
    page_size: int = Form(...),
    client_token: str = Form(...),
    source_ref: str | None = Form(None),
    description: str | None = Form(None),
    previous_check_id: int | None = Form(None),
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_search),
) -> JSONResponse:
    content = await image.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise InvalidImageError()
    try:
        req = CheckCreate(
            product_name=product_name, product_version=product_version, source_ref=source_ref,
            description=description, markets=markets.split(","), jurisdictions=jurisdictions.split(","),
            page_size=page_size, previous_check_id=previous_check_id, client_token=client_token,
        )
    except ValidationError as exc:
        fields = "、".join(str(e["loc"][0]) for e in exc.errors() if e.get("loc"))
        raise InvalidCheckInputError(f"核查参数不合法：{fields}") from None
    return _accepted(await check_service.create_check(db, user.id, req, content))


@risk_router.get("/checks")
async def list_checks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search_status: Literal["queued", "running", "completed", "failed", "unknown"] | None = None,
    review_state: Literal["none", "partial", "complete"] | None = None,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> dict[str, Any]:
    return await check_service.list_checks(db, user.id, page=page, page_size=page_size,
                                           search_status=search_status, review_state=review_state)


@risk_router.get("/checks/{check_id}")
async def get_check(check_id: int, db: AsyncSession = Depends(get_async_db),
                    user=Depends(_require_view)) -> dict[str, Any]:
    return await check_service.get_check(db, user.id, check_id)


@risk_router.get("/checks/{check_id}/image")
async def get_image(check_id: int, db: AsyncSession = Depends(get_async_db),
                    user=Depends(_require_view)) -> Response:
    data, content_type = await check_service.get_image(db, user.id, check_id)
    return Response(content=data, media_type=content_type,
                    headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@risk_router.get("/checks/{check_id}/files/{candidate_id}/{kind}/{seq}")
async def get_evidence_file(check_id: int, candidate_id: int, kind: Literal["drawings", "pdf"],
                            seq: int = Path(ge=1, le=1000), db: AsyncSession = Depends(get_async_db),
                            user=Depends(_require_view)) -> Response:
    data, content_type, filename = await check_service.get_evidence_file(
        db, user.id, check_id, candidate_id, kind, seq)
    return Response(content=data, media_type=content_type, headers={
        "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'inline; filename="{filename}"',
    })


@risk_router.post("/checks/{check_id}/pages", status_code=202)
async def next_page(check_id: int, body: TokenRequest, db: AsyncSession = Depends(get_async_db),
                    user=Depends(_require_search)) -> JSONResponse:
    return _accepted(await check_service.request_next_page(db, user.id, check_id, body.client_token))


@risk_router.post("/checks/{check_id}/retry", status_code=202)
async def retry(check_id: int, body: TokenRequest, db: AsyncSession = Depends(get_async_db),
                user=Depends(_require_search)) -> JSONResponse:
    return _accepted(await check_service.request_retry(db, user.id, check_id, body.client_token))


@risk_router.post("/checks/{check_id}/evidence", status_code=202)
async def evidence(check_id: int, body: EvidenceRequest, db: AsyncSession = Depends(get_async_db),
                   user=Depends(_require_search)) -> JSONResponse:
    return _accepted(await check_service.request_details(db, user.id, check_id, body))


@risk_router.post("/checks/{check_id}/reviews")
async def add_review(check_id: int, body: ReviewCreate, db: AsyncSession = Depends(get_async_db),
                     user=Depends(_require_review)) -> dict[str, Any]:
    return await check_service.add_review(db, user.id, check_id, body)
