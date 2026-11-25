"""竞对卖家 ASIN 上报 API。瘦控制器：调 service，异常交给全局处理器。

`POST /competitor/asin-reports` —— 采集端（浏览器插件）主动上报某卖家名下见到的 ASIN。
`GET /competitor/collector-config` —— 采集端读取服务端当前生效的单批上限（#166 D4）。

**`user_id` 一律取自访问令牌**，请求体里的同名字段被 schema 无视。在售数据是共享层，
归属若能从请求体指定，任何持有令牌的用户都能以别人的名义写入。

权限点沿用 `competitor:view`——**没有全局鉴权中间件**，漏挂依赖就是裸奔的。
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import COMPETITOR_VIEW
from app.auth.simple_auth import simple_auth
from app.table.competitor import asin_service as service
from app.table.competitor.asin_schemas import (
    AsinReportRequest,
    AsinReportResponse,
    CollectorConfigResponse,
)
from database import get_async_db

logger = logging.getLogger(__name__)

asin_router = APIRouter()

_require_view = simple_auth.require_permission(COMPETITOR_VIEW)


@asin_router.post("/asin-reports", response_model=AsinReportResponse)
async def submit_asin_report(
    req: AsinReportRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> AsinReportResponse:
    """受理一次上报。

    三种结局，状态码刻意不同：

    - **新观测** → 200，`duplicate_of` 为空。
    - **重放**（幂等键命中且摘要一致）→ 200，`duplicate_of` 指向原记录。**不返错误码**：
      采集端收到失败的正常反应是继续重试，而这次重试本就是它要处理的那个失败。
    - **键被复用**（幂等键命中但摘要不同）→ 409。静默回放会把一次真实观测丢掉。
    """
    return await service.submit_report(db, user.id, req)


@asin_router.get("/collector-config", response_model=CollectorConfigResponse)
def get_collector_config(_user=Depends(_require_view)) -> CollectorConfigResponse:
    """插件打开采集面板时读取；取不到就禁止开始采集，不回落到本地默认值（design D4）。

    同步函数：要 stat/读选择器文件（很小，但仍是阻塞 IO），交给线程池而不是事件循环。
    """
    return service.collector_config()
