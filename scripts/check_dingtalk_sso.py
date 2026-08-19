"""钉钉单点登录链路自检：把「配置填对了没」变成一条可执行的判据。

填完 ``.env`` 的 ``SSO_*`` / ``DINGTALK_*`` 后跑这个脚本。它按真实登录顺序走一遍完整
回路——打印授权地址供人工扫码 → 用回调拿到的 authCode 换用户凭证 → 取个人身份 →
换企业凭证 → 通讯录反查 userid → 取用户详情，每一步单独判定，失败时直接指出该去
钉钉开发者后台改哪里。

之所以要有它：这条链路上有两件事**无法靠 code review 确认**——

1. **重定向 URL 是否与开发者后台登记的一致。** 不一致的表现是扫码后停在钉钉页面提示
   「无权限访问」，而本系统这边一条日志都没有（请求根本没回来过）。
2. **应用有没有通讯录读取权限。** 缺了它，前面几步全部成功，直到反查 userid 才失败——
   而那个失败在界面上长得跟「这个人离职了」一模一样（都是拿不到 userid）。

用法::

    # 打印授权地址，扫码后把浏览器地址栏里的 authCode 贴回来
    uv run python scripts/check_dingtalk_sso.py

    # 已经有 authCode 时直接验后半程（authCode 一次性，只能用一次）
    uv run python scripts/check_dingtalk_sso.py --auth-code <code>

退出码非 0 表示有检查未通过。

注意：本脚本**只读**，不会在 ``user_center`` 里建任何账号——建档规则由集成测试覆盖，
这里要验的是与钉钉之间那段。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 以 `python scripts/check_dingtalk_sso.py` 直接调用时，仓库根不在 sys.path 上
# （约定同 seed_admin.py / check_oss.py）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth.dingtalk_client import DingTalkClient
from app.auth.sso_errors import (
    SsoCodeInvalidError,
    SsoProviderUnavailableError,
)
from app.auth.sso_service import build_authorize_url
from config import settings

OK = "✅"
BAD = "❌"
INFO = "ℹ️ "


def _check_config() -> bool:
    """配置齐不齐。开启 SSO 时 config.py 已经在启动期卡过一遍，这里是给关闭状态下的人看的。"""
    print("\n== 1. 配置 ==")
    ok = True
    if not settings.SSO_ENABLED:
        print(f"{INFO} SSO_ENABLED=false。脚本仍可跑（直接读配置），但登录页不会显示钉钉按钮。")
    for name in ("DINGTALK_APP_KEY", "DINGTALK_APP_SECRET", "SSO_REDIRECT_URI"):
        value = getattr(settings, name)
        if value:
            shown = value if name != "DINGTALK_APP_SECRET" else f"<已配置，{len(value)} 字符>"
            print(f"{OK} {name} = {shown}")
        else:
            print(f"{BAD} {name} 为空 —— 去 .env 补上")
            ok = False
    print(f"{INFO} SSO_DEFAULT_ROLE_CODE = {settings.SSO_DEFAULT_ROLE_CODE}"
          "（是否存在于 role 表由应用启动期检查，不在本脚本范围）")
    return ok


def _print_authorize_url() -> None:
    print("\n== 2. 授权地址 ==")
    url = build_authorize_url()
    print(f"{INFO} 用浏览器打开下面这条地址并用钉钉扫码：\n\n{url}\n")
    print(f"{INFO} 扫码确认后浏览器会跳到 {settings.SSO_REDIRECT_URI}?authCode=...&state=...")
    print(f"{INFO} 若停在钉钉页面提示「无权限访问」，说明开发者后台登记的重定向 URL")
    print("     与 SSO_REDIRECT_URI 不一致 —— 去「开发配置 > 安全设置」核对，必须完全相同。")


async def _check_exchange(auth_code: str) -> bool:
    client = DingTalkClient(
        app_key=settings.DINGTALK_APP_KEY,
        app_secret=settings.DINGTALK_APP_SECRET,
    )

    print("\n== 3. 换用户访问凭证 ==")
    try:
        user_token = await client.exchange_user_token(auth_code)
    except SsoCodeInvalidError:
        print(f"{BAD} authCode 已失效或已被使用。它是一次性的——重新扫一次码取新的。")
        return False
    except SsoProviderUnavailableError as exc:
        print(f"{BAD} 换取失败：{exc.log_detail}")
        print("     常见成因：AppKey/AppSecret 填错，或本机到 api.dingtalk.com 不通。")
        return False
    print(f"{OK} 拿到用户 accessToken（{len(user_token)} 字符）")

    print("\n== 4. 取个人身份 ==")
    me = await client.contact_me(user_token)
    union_id = me.get("unionId")
    if not union_id:
        print(f"{BAD} contact/users/me 未返回 unionId：{me}")
        return False
    print(f"{OK} unionId = {union_id}，nick = {me.get('nick')}")
    print(f"{INFO} 注意这一步拿不到企业信息（钉钉文档明写「无手机号和企业相关信息」），"
          "企业内 userid 要靠下面两步。")

    print("\n== 5. 换企业访问凭证 ==")
    try:
        corp_token = await client.corp_access_token()
    except SsoProviderUnavailableError as exc:
        print(f"{BAD} 换取企业 accessToken 失败：{exc.log_detail}")
        return False
    print(f"{OK} 拿到企业 accessToken（{len(corp_token)} 字符）")

    print("\n== 6. 通讯录反查 userid ==")
    userid = await client.userid_by_unionid(corp_token, union_id)
    if not userid:
        print(f"{BAD} 反查不到 userid。两种成因，处置完全不同：")
        print("     a) 应用没有「通讯录个人信息读权限」—— 去开发者后台「权限管理」开通；")
        print("     b) 扫码的这个钉钉账号确实不在本企业通讯录内（外部人员/已离职）。")
        print("     换一个确定在职的账号再跑一次即可区分。")
        return False
    print(f"{OK} userid = {userid}")
    print(f"{INFO} 登录时的离职判定就落在这一步：查不到即拒登，不需要定时任务。")

    print("\n== 7. 取用户详情 ==")
    detail = await client.user_detail(corp_token, userid)
    if not detail:
        print(f"{BAD} 取详情失败（不致命，建档时显示名会为空，但通常与权限不足同因）")
        return False
    print(f"{OK} name = {detail.get('name')}")

    print("\n== 建档预演（只读，不写库）==")
    from app.auth.sso_service import normalize_user_name

    print(f"{INFO} 该员工首次登录会建出：user_name={normalize_user_name(userid)}、"
          f"display_name={detail.get('name')}、role={settings.SSO_DEFAULT_ROLE_CODE}、"
          "is_super=False、hashed_pwd=NULL")
    print(f"{INFO} external_id 存的是 unionId（{union_id}），不是 userid——"
          "后者管理员可改，改了会让账号成孤儿。")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--auth-code",
        help="钉钉回调地址里的 authCode。不传则只打印授权地址并交互式等待粘贴。",
    )
    args = parser.parse_args()

    if not _check_config():
        print(f"\n{BAD} 配置不全，后续检查无法进行。")
        return 1

    auth_code = args.auth_code
    if not auth_code:
        _print_authorize_url()
        try:
            auth_code = input("\n把回调地址里的 authCode 粘贴到这里（直接回车退出）：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            auth_code = ""
        if not auth_code:
            print(f"{INFO} 未提供 authCode，只完成了配置与授权地址检查。")
            return 1

    ok = asyncio.run(_check_exchange(auth_code))
    if ok:
        print(f"\n{OK} 整条链路通过。")
        return 0
    print(f"\n{BAD} 有检查未通过，见上面的提示。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
