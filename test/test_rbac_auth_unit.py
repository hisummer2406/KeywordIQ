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
    assert op.permission_codes == {"analysis:view", "analysis:export", "upload:import"}
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
    dep = simple_auth.require_permission(P.USER_MANAGE)
    su = _super()
    assert dep(current_user=su) is su


def test_serialize_user_shape():
    d = serialize_user(_operator())
    assert d == {
        "id": 1,
        "username": "tester",
        "role": "operator",
        # 角色展示名与 role 表同源，前端据此显示身份，不自建 code → 中文名的映射
        "role_name": "operator",
        "permissions": ["analysis:export", "analysis:view", "upload:import"],
        "is_active": True,
        "is_super": False,
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
    assert P.ROLES["operator"][1] == {"analysis:view", "analysis:export", "upload:import"}
    assert P.ROLES["analyst"][1] == {"analysis:view", "analysis:export"}
