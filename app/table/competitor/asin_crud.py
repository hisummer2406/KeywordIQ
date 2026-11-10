"""ASIN 上报的数据访问层：只出语句与纯函数，不执行、不提交、无业务判定。

写入顺序是**先 report、再 ASIN、最后回填 `new_count`**（三步同一事务）：

1. `CompetitorSellerAsin.first_report_id` 指向上报记录，所以记录必须先有 id；
2. `new_count` 要等 ASIN 插入的 `RETURNING` 回来才知道，所以只能回填。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.table.competitor.asin_model import CompetitorAsinReport, CompetitorSellerAsin
from app.table.competitor.seller_model import CompetitorSeller
from app.table.competitor.watch_model import UserSellerWatch

#: 摘要各字段之间的分隔符。用 `\x1f`（ASCII 单元分隔符）而不是逗号或竖线，是因为它不可能
#: 出现在 ASIN、站点码或页面上下文里——用普通字符做分隔，`page_context="a|b"` 与
#: `page_context="a"` + 某个恰好叫 `b` 的字段就会撞出同一个摘要。
_SEP = "\x1f"


def normalize_asin(raw: str) -> str:
    """统一大写、去首尾空白。判重、入库与摘要都用这一份规范化结果。"""
    return raw.strip().upper()


def compute_request_hash(
    *,
    seller_id: str,
    site: str,
    asins: Iterable[str],
    captured_at: datetime | None,
    ship_to_region: str | None,
    page_context: str | None,
) -> str:
    """请求摘要：判定「命中同一个幂等键的这两次，是不是同一次观测」。

    **覆盖判定"同一次观测"的全部依据**，但 `request_id` 本身不进摘要（它是钥匙，不是内容）。
    ASIN 集合先规范化、去重、排序——顺序是采集端遍历 DOM 的产物，同一次观测换个遍历顺序
    仍是同一次观测。

    **标题与图片不进摘要**：易变的展示属性（同一个品在不同渲染下标题会被截到不同长度），
    且入库时只在首次落定、本就不影响写入结果。

    **`captured_at` / `ship_to_region` / `page_context` 进摘要。** 这条曾被写反过，理由是
    "重读 DOM 后重试会撞出假 409"——但那不是重试：真正的幂等重试重发的是已经序列化好的原
    请求体，三个字段逐字节相同；重新采集产生了新的 `captured_at`，它就是另一次观测，本该
    带新的键。判据是两种误判的代价不对称：多返一个 409，采集端换个键再发一次即可（响亮、
    可自愈）；漏判成重放，一次真实观测凭空消失（安静、事后查不出来）。
    """
    parts = [
        seller_id,
        site,
        # None 与空串要能区分：用固定前缀标记有无，避免 ("", None) 与 (None, "") 同摘要。
        _optional(captured_at.isoformat() if captured_at else None),
        _optional(ship_to_region),
        _optional(page_context),
        *sorted({normalize_asin(a) for a in asins}),
    ]
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()


def _optional(value: str | None) -> str:
    return "\x00" if value is None else f"\x01{value}"


# ---------- 授权 ----------


def watched_seller_stmt(user_id: int, seller_id: str, site: str):
    """该卖家是否在**上报者本人**的关注清单里。

    恒带 `user_id`——在售数据是共享层，若不限制写入来源，任何持有令牌的用户都能污染其他人
    看到的数据。
    """
    return select(UserSellerWatch.watch_id).where(
        UserSellerWatch.user_id == user_id,
        UserSellerWatch.seller_id == seller_id,
        UserSellerWatch.site == site,
    )


# ---------- 上报记录 ----------


def insert_report_stmt(
    *,
    user_id: int,
    request_id: str,
    request_hash: str,
    seller_id: str,
    site: str,
    captured_at: datetime | None,
    ship_to_region: str | None,
    page_context: str | None,
    item_count: int,
) -> Any:
    """插入上报记录；命中幂等键则**不插入**并返回空。

    `new_count` 先写 0，等 ASIN 写完再回填——它的真值要靠 `RETURNING` 才知道。
    调用方据「有没有 RETURNING 出行」判断是不是命中了重复，然后回查原行比对摘要。
    """
    return (
        pg_insert(CompetitorAsinReport)
        .values(
            user_id=user_id,
            request_id=request_id,
            request_hash=request_hash,
            seller_id=seller_id,
            site=site,
            captured_at=captured_at,
            ship_to_region=ship_to_region,
            page_context=page_context,
            item_count=item_count,
            new_count=0,
        )
        .on_conflict_do_nothing(constraint="uq_competitor_asin_report_request")
        .returning(CompetitorAsinReport.report_id)
    )


def existing_report_stmt(user_id: int, request_id: str):
    """回查幂等键对应的原始记录，供比对摘要与回放计数。"""
    return select(
        CompetitorAsinReport.report_id,
        CompetitorAsinReport.request_hash,
        CompetitorAsinReport.item_count,
        CompetitorAsinReport.new_count,
    ).where(
        CompetitorAsinReport.user_id == user_id,
        CompetitorAsinReport.request_id == request_id,
    )


def update_report_new_count_stmt(report_id: int, new_count: int):
    return (
        update(CompetitorAsinReport)
        .where(CompetitorAsinReport.report_id == report_id)
        .values(new_count=new_count)
    )


# ---------- 在售关系 ----------


def insert_asins_stmt(rows: list[dict[str, Any]]):
    """批量写在售关系，`ON CONFLICT DO NOTHING`，`RETURNING` 出真正新增的那些。

    **不带 `DO UPDATE`**：`first_seen_at` 与已落定的展示属性绝不因重复上报而改动——运营每
    刷新一次页面插件就会再报一次，若每次都更新 `first_seen_at`，一个老品会因为被重新看到
    而跳进"今天的新发现"。

    `first_seen_at` 不在 `values` 里，走列上的 `server_default now()`——服务端事务时间是
    唯一权威，客户端的 `captured_at` 只落进上报记录作审计。
    """
    return (
        pg_insert(CompetitorSellerAsin)
        .values(rows)
        .on_conflict_do_nothing(constraint="pk_competitor_seller_asin")
        .returning(CompetitorSellerAsin.asin)
    )


def set_baseline_stmt(seller_id: str, site: str):
    """落基线，**一旦落定不再改动**。

    改动基线会让基线批次的商品在下一次查询里整批涌进上新流——这是本路径唯一能造成大面积
    假数据的方式，且没有任何地方会报错。

    保护写成 `WHERE baseline_at IS NULL` 而不是 `SET baseline_at = COALESCE(baseline_at,
    now())`，两者的保证等价（都绝不覆盖已有值），但前者更好：

    - **常态下根本不写。** 后者每次上报都 UPDATE 一行、标死旧元组、重写索引条目，而
      99% 的上报是「基线早就有了」——与 `dim_product` 那条 `IS DISTINCT FROM` 是同一笔账。
    - **「本次是否落定了基线」是免费的**：有 `RETURNING` 出行即是落定，没有即早已有值。
      COALESCE 版恒返回一行，还得把新旧值比一遍才知道。
    """
    return (
        update(CompetitorSeller)
        .where(
            CompetitorSeller.seller_id == seller_id,
            CompetitorSeller.site == site,
            CompetitorSeller.baseline_at.is_(None),
        )
        .values(baseline_at=func.now())
        .returning(CompetitorSeller.baseline_at)
    )


def update_seller_name_stmt(seller_id: str, site: str, seller_name: str):
    """店名按最新非空值覆盖（#166 D5）。

    调用方只在**非重放且带了非空店名**时调用；``IS DISTINCT FROM`` 让店名没变的常态上报不写
    行——与 ``set_baseline_stmt`` 只在 NULL 时写是同一笔账：每次 UPDATE 都会标死旧元组、重写
    索引条目，而绝大多数上报带来的店名与库里一致。
    """
    return (
        update(CompetitorSeller)
        .where(
            CompetitorSeller.seller_id == seller_id,
            CompetitorSeller.site == site,
            CompetitorSeller.seller_name.is_distinct_from(seller_name),
        )
        .values(seller_name=seller_name)
    )
