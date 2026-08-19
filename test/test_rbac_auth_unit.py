"""RBAC 鉴权核心逻辑单元测试（无需 PostgreSQL）。

覆盖：角色→权限解析、require_permission 放行/拒绝、is_super 兜底、serialize_user 形状、
JWT 承载 role 的签发/解码 roundtrip、畸形令牌解码返回 None（回归 jwt.PyJWTError 修正）。
"""
import pytest
from fastapi import HTTPException

from app.auth import permissions as P
from app.auth.login_auth import auth_service
from app.auth.simple_auth import serialize_user, simple_auth
from app.user.rbac_model import Permission, Role
from app.user.user_model import UserCenter


def _make_user(role_code, perm_codes, *, is_super=False, is_active=True, uid=1):
    role = Role(code=role_code, name=role_code)
    role.permissions = [Permission(code=c, name=c) for c in perm_codes]
    user = UserCenter(
        id=uid, user_name="tester", hashed_pwd="x", is_active=is_active, is_super=is_super
    )
    user.role = role
    return user


def _operator():
    return _make_user("operator", P.ROLES["operator"][1])


def _analyst():
    return _make_user("analyst", P.ROLES["analyst"][1])


def _super():
    # 超管：无角色也应放行（is_super 兜底）
    u = UserCenter(id=9, user_name="root", hashed_pwd="x", is_active=True, is_super=True)
    u.role = None
    return u


def test_permission_codes_from_role():
    op = _operator()
    assert op.permission_codes == {
        "analysis:view", "analysis:export", "upload:import", "toolbox:view",
    }
    assert op.role_code == "operator"


def test_no_role_means_no_permissions():
    u = UserCenter(id=2, user_name="x", hashed_pwd="x", is_active=True, is_super=False)
    u.role = None
    assert u.permission_codes == set()
    assert u.role_code is None


def test_require_permission_allows_when_role_has_it():
    dep = simple_auth.require_permission(P.UPLOAD_IMPORT)
    op = _operator()
    assert dep(current_user=op) is op


def test_require_permission_denies_when_missing():
    dep = simple_auth.require_permission(P.UPLOAD_IMPORT)
    with pytest.raises(HTTPException) as exc:
        dep(current_user=_analyst())  # analyst 无 upload:import
    assert exc.value.status_code == 403


def test_super_user_bypasses_permission_check():
    dep = simple_auth.require_permission(P.ANALYSIS_EXPORT)
    su = _super()
    assert dep(current_user=su) is su


# ---------------- require_super：不可授予的能力（用户管理） ----------------


def _admin_not_super():
    """admin 角色、但 is_super=False。

    这个组合是本组测试的全部意义：用户管理收紧后，「谁能管用户」不再由角色配置决定。
    此前那个 `get_super_user` 判据是「is_super **或** role_code == 'admin'」，
    这种用户会被放行。
    """
    return _make_user("admin", P.PERMISSIONS.keys(), is_super=False, uid=3)


def test_require_super_allows_super():
    dep = simple_auth.require_super()
    su = _super()
    assert dep(current_user=su) is su


def test_require_super_denies_admin_role_without_is_super():
    """核心回归：admin 角色 + 拥有全部权限点，只要 is_super 为假就必须被拒。"""
    dep = simple_auth.require_super()
    with pytest.raises(HTTPException) as exc:
        dep(current_user=_admin_not_super())
    assert exc.value.status_code == 403


@pytest.mark.parametrize("factory", [_operator, _analyst])
def test_require_super_denies_ordinary_roles(factory):
    dep = simple_auth.require_super()
    with pytest.raises(HTTPException) as exc:
        dep(current_user=factory())
    assert exc.value.status_code == 403


def test_user_manage_is_not_a_grantable_permission():
    """用户管理没有权限点——它不可授予。

    留着一个 `user:manage` 而端点只认 is_super，等于在角色编辑界面放一个勾了也不生效
    的复选框：管理员以为把它给了运营，运营却仍然进不去。
    """
    assert "user:manage" not in P.PERMISSIONS
    assert "role:manage" not in P.PERMISSIONS  # 从加入起就没有任何代码读过，同批清掉
    for _name, codes in P.ROLES.values():
        assert "user:manage" not in codes
        assert "role:manage" not in codes


def test_removed_permissions_are_not_referenced_anywhere():
    """常量也一并删掉——留着会被下一个人当成「还能用」而挂回端点上。"""
    assert not hasattr(P, "USER_MANAGE")
    assert not hasattr(P, "ROLE_MANAGE")


def test_legacy_loose_super_guard_is_gone():
    """`get_super_user`（is_super 或 admin 角色）已被 require_super 取代。

    两个近义守卫共存迟早会有人挂错那个宽松的，而两者的差别恰恰是这次收紧的全部内容。
    """
    assert not hasattr(simple_auth, "get_super_user")


def test_serialize_user_shape():
    d = serialize_user(_operator())
    assert d == {
        "id": 1,
        "username": "tester",
        "role": "operator",
        # 角色展示名与 role 表同源，前端据此显示身份，不自建 code → 中文名的映射
        "role_name": "operator",
        "permissions": [
            "analysis:export", "analysis:view", "toolbox:view", "upload:import",
        ],
        "is_active": True,
        "is_super": False,
        # 账号来源与展示名（钉钉 SSO 引入）。本地账号两者皆空——前端据此把「来源」
        # 显示成「本地」、显示名回落到 username。
        #
        # 这条断言用**精确相等**是有意的：serialize_user 是前后端契约，多一个键少一个键
        # 都该在这里被看见。加字段时更新期望值，不要改成子集断言。
        "display_name": None,
        "external_idp": None,
    }


def test_jwt_roundtrip_carries_role():
    tok = auth_service.create_access_token(7, "alice", False, "analyst")
    payload = auth_service.decode_access_token(tok)
    assert payload is not None
    assert payload["role"] == "analyst"
    assert payload["username"] == "alice"
    assert payload["is_super"] is False


def test_decode_malformed_token_returns_none():
    # 回归：PyJWT 无 jwt.JWTError，畸形令牌不应抛 AttributeError，而是返回 None
    assert auth_service.decode_access_token("not-a-jwt") is None


def test_role_permission_matrix_matches_spec():
    # 矩阵与 design 决策 4b 一致
    assert P.ROLES["admin"][1] == set(P.PERMISSIONS.keys())
    assert P.ROLES["operator"][1] == {
        "analysis:view", "analysis:export", "upload:import", "toolbox:view",
    }
    assert P.ROLES["analyst"][1] == {"analysis:view", "analysis:export", "toolbox:view"}
