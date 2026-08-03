"""创建初始管理员用户（幂等）。

V2 迁移只种角色/权限，不种用户；全新库跑完迁移后没有任何账号可登录。本脚本补齐这一步。
is_super=True 的用户由 UserCenterCRUD 自动赋 admin 角色（见 app/auth/permissions.py）。

用法：
    uv run python scripts/seed_admin.py                # 默认 admin / pwd123
    uv run python scripts/seed_admin.py --username boss --password s3cret
    # 指定库（不改 .env）：
    DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_v2_dev \\
        uv run python scripts/seed_admin.py
"""
import argparse
import asyncio
import sys
from pathlib import Path

# 让脚本直接运行时也能 import 仓库根的模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.common.errors import APIError
from app.user.user_crud import UserCenterCRUD
from app.user.user_schemas import UserCenterCreate
from database import AsyncSessionFactory


async def _run(username: str, password: str) -> int:
    """UserCenterCRUD 随鉴权路径一并异步化，故脚本用 asyncio.run 驱动它。"""
    async with AsyncSessionFactory() as db:
        crud = UserCenterCRUD(db)
        existing = await crud.get_user_by_username(username)
        if existing:
            print(f"用户已存在，跳过：{username}（role={existing.role_code}）")
            return 0

        try:
            user = await crud.create_user(
                UserCenterCreate(
                    user_name=username,
                    password=password,
                    is_super=True,
                    is_active=True,
                )
            )
        except APIError as exc:
            # 这是命令行入口，没有全局处理器接管，故在此收口成退出码与人读的提示。
            print(f"创建失败：{exc.message}", file=sys.stderr)
            return 1
        print(f"已创建管理员：{user.user_name}（id={user.id}, role={user.role_code}）")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="创建初始管理员用户（幂等）")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="pwd123")
    args = parser.parse_args()
    return asyncio.run(_run(args.username, args.password))


if __name__ == "__main__":
    raise SystemExit(main())
