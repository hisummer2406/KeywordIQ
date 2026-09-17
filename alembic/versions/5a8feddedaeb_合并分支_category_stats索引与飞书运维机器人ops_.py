"""合并分支：category_stats索引与飞书运维机器人ops schema两个并行头

Revision ID: 5a8feddedaeb
Revises: 47df76b2f424, d4e5f6a7b8c9
Create Date: 2026-09-17 20:03:47.482854

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a8feddedaeb'
down_revision: Union[str, None] = ('47df76b2f424', 'd4e5f6a7b8c9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
