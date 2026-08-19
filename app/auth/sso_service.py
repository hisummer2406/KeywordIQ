"""钉钉单点登录的业务逻辑：授权 URL、state、账号匹配与建档、签发本系统令牌。

登录成功后签发的是**同一套 JWT**（`AuthService`），下游 `require_permission` /
`require_super` 一行不改——这是 issue #36「不引入第二套权限判定路径」那条的落点。
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import secrets
from urllib.parse import urlencode

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dingtalk_client import AUTHORIZE_URL, DingTalkClient
from app.auth.login_auth import auth_service
from app.auth.sso_errors import (
    SsoAccountDisabledError,
    SsoNotEmployeeError,
    SsoStateInvalidError,
)
from app.user.rbac_model import Role
from app.user.user_crud import UserCenterCRUD
from app.user.user_model import UserCenter
from config import settings

logger = logging.getLogger(__name__)

#: 身份源标识，写进 `user_center.external_idp`。
IDP_DINGTALK = "dingtalk"

#: state 的存活时长。够用户扫码 + 确认，又短到重放没有意义。
_STATE_TTL_SECONDS = 600

#: state 令牌的类型标记。防止别处签发的同密钥令牌（如登录 JWT）被当成 state 使用——
#: 两者都用 ADMIN_SECRET_KEY 签，不加区分的话一个有效的登录令牌就能冒充 state。
_STATE_TYPE = "sso_state"

#: 用户名兜底前缀。钉钉 userid 允许是纯中文，规范化后可能一个字符都不剩。
_FALLBACK_USER_PREFIX = "ding"


# ---------- state：无状态短命签名令牌 ----------

def issue_state() -> str:
    """签发 state。

    **不落任何存储。** `start.sh` 固定起 4 个 uvicorn worker，签发与校验落在同一进程
    纯属偶然；存进程内存的失败率约 3/4，且时好时坏——那种症状与钉钉侧偶发故障无从区分，
    排查成本极高。存 Redis 要引入新组件，存库要为一个 10 分钟生命周期的对象建表并清理。
    签名令牌三者都不要。
    """
    now = dt.datetime.now(dt.UTC)
    payload = {
        "typ": _STATE_TYPE,
        "nonce": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + dt.timedelta(seconds=_STATE_TTL_SECONDS),
    }
    return jwt.encode(payload, settings.ADMIN_SECRET_KEY, algorithm="HS256")


def verify_state(state: str) -> None:
    """校验 state，不合法即抛 :class:`SsoStateInvalidError`（400）。"""
    if not state:
        raise SsoStateInvalidError(log_detail="回调未带 state")
    try:
        payload = jwt.decode(state, settings.ADMIN_SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise SsoStateInvalidError(log_detail=f"state 解码失败: {exc!r}") from exc

    if payload.get("typ") != _STATE_TYPE:
        # 没有这道检查，一个有效的登录 JWT 就能当 state 用（同一把密钥签的）。
        raise SsoStateInvalidError(log_detail=f"state 类型不符: {payload.get('typ')!r}")


# ---------- 启动期检查 ----------

async def assert_default_role_exists(db) -> None:
    """`SSO_DEFAULT_ROLE_CODE` 必须真的存在于角色表，否则中断启动。

    配置层判不了这件事——要连库。而它错了的后果很隐蔽：建出来的账号 `role_id` 为空，
    权限集合是空集，进每一个页面都 403，管理员看到的却是「这人明明登录成功了」。

    **只在能确定角色不存在时中断。** 数据库连不上是另一回事，由 lifespan 既有的连接
    检查按记日志处理——为了一个配置校验而让应用在数据库抖动时起不来，比这个配置错
    本身更糟。
    """
    role_id = await db.scalar(select(Role.id).where(Role.code == settings.SSO_DEFAULT_ROLE_CODE))
    if role_id is None:
        raise RuntimeError(
            f"SSO_DEFAULT_ROLE_CODE={settings.SSO_DEFAULT_ROLE_CODE!r} 在角色表中不存在，"
            "SSO 建档会产生无权限的空壳账号。请改配置或先建好该角色。"
        )


# ---------- 授权 URL ----------

def build_authorize_url() -> str:
    """构造钉钉扫码授权页地址。

    `redirect_uri` 取自配置、**绝不由请求方传入**——可传入即是开放重定向，攻击者能把
    授权码引到自己的域名上。scope 固定 `openid`（钉钉只支持 `openid` 与 `openid corpid`
    两种取值；我们不需要让用户选组织）。
    """
    params = {
        "client_id": settings.DINGTALK_APP_KEY,
        "redirect_uri": settings.SSO_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid",
        "prompt": "consent",
        "state": issue_state(),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


# ---------- 用户名 ----------

def normalize_user_name(raw: str) -> str:
    """把钉钉 userid 规范化成与管理员建号同一套规则的用户名（小写字母数字）。

    `UserCenterCreate` 要求 `isalnum()` 并转小写，而钉钉 userid 可能带 `-`/`_`，也可能
    是纯中文。SSO 建档不走那个 Pydantic 模型，本可以绕过——但那会让库里出现**两套**
    用户名规则，而这种不一致没有任何地方会拦。
    """
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", raw or "").lower()
    if not cleaned:
        # 纯中文/纯符号的 userid 剥完就空了。建出空用户名比随机后缀糟得多：
        # 唯一约束会让第二个这样的员工直接登录失败，而错误信息指不到成因。
        return f"{_FALLBACK_USER_PREFIX}{secrets.token_hex(4)}"
    return cleaned


def resolve_user_name(base: str, taken: set[str]) -> str:
    """用户名冲突时追加数字后缀。

    冲突**不意味着是同一个人**——恰恰相反，绝不能因为用户名相同就绑定到已有账号
    （见 `find_or_create_sso_user`）。所以这里只负责换一个不冲突的名字。
    """
    if base not in taken:
        return base
    suffix = 2
    while f"{base}{suffix}" in taken:
        suffix += 1
    return f"{base}{suffix}"


# ---------- 账号匹配与建档 ----------

async def find_or_create_sso_user(
    db: AsyncSession,
    *,
    external_id: str,
    dingtalk_userid: str,
    display_name: str | None,
) -> UserCenter:
    """按 `(external_idp, external_id)` 找账号，没有就建档。

    **只认这一对，绝不按用户名匹配。** 这是本功能唯一实质的攻击面：若按用户名自动绑定
    已有账号，任何能修改自己钉钉 userid 的人都可以把它改成 `admin`，扫一次码就接管
    超级管理员。用户名撞了只说明重名，**不说明是同一个人**，所以撞了就换个名字。

    `external_id` 存的是 unionId 而非企业内 userid——后者管理员可改、离职再入职会换，
    变一次即老账号成孤儿、当事人被当新人重新开号、已配置的角色全部丢失。

    已建档账号**不回写任何属性**：回写会撞上用户名唯一约束（要处理冲突与重命名的连锁
    影响），而收益只是显示名跟随钉钉侧改名。
    """
    crud = UserCenterCRUD(db)

    existing = await crud.get_by_external_identity(IDP_DINGTALK, external_id)
    if existing is not None:
        return existing

    taken = await crud.list_user_names()
    user_name = resolve_user_name(normalize_user_name(dingtalk_userid), taken)

    return await crud.create_sso_user(
        user_name=user_name,
        display_name=display_name,
        idp=IDP_DINGTALK,
        external_id=external_id,
        role_code=settings.SSO_DEFAULT_ROLE_CODE,
    )


# ---------- 主流程 ----------

async def login_with_auth_code(
    db: AsyncSession,
    client: DingTalkClient,
    *,
    auth_code: str,
    state: str,
) -> dict:
    """回调换令牌的完整流程。返回 `{token, user}`，与 `POST /api/auth/token` 同形。"""
    verify_state(state)

    user_token = await client.exchange_user_token(auth_code)
    me = await client.contact_me(user_token)
    union_id = me.get("unionId")
    if not union_id:
        raise SsoNotEmployeeError(log_detail=f"contact/users/me 未返回 unionId: {me}")

    corp_token = await client.corp_access_token()
    dingtalk_userid = await client.userid_by_unionid(corp_token, union_id)
    if not dingtalk_userid:
        # 离职同步就落在这里：钉钉侧把人移出通讯录后，这一步拿不到 userid。
        raise SsoNotEmployeeError(log_detail=f"unionId 不在通讯录内: {union_id}")

    detail = await client.user_detail(corp_token, dingtalk_userid)

    user = await find_or_create_sso_user(
        db,
        external_id=union_id,
        dingtalk_userid=dingtalk_userid,
        display_name=detail.get("name"),
    )
    if not user.is_active:
        # 与「不在通讯录内」分开：那条是钉钉侧没有你，这条是本系统的管理员停用了你。
        # 合成一个错误码会让管理员无从判断该去哪边查。
        raise SsoAccountDisabledError(log_detail=f"账号已停用: id={user.id}")

    token = auth_service.create_access_token(
        user.id, user.user_name, user.is_super, user.role_code
    )
    return {"token": token, "user": user}
