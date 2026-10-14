"""store unranked search results

Revision ID: 4ba8d0806713
Revises: 80c15d7e8933
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4ba8d0806713"
down_revision: str | Sequence[str] | None = "80c15d7e8933"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recommendations", sa.Column("position", sa.Integer(), server_default="0", nullable=False)
    )
    op.alter_column(
        "recommendations", "score", existing_type=sa.DOUBLE_PRECISION(precision=53), nullable=True
    )

    op.create_check_constraint("ck_recommendations_position", "recommendations", "position >= 0")


def downgrade() -> None:
    op.alter_column(
        "recommendations", "score", existing_type=sa.DOUBLE_PRECISION(precision=53), nullable=False
    )
    op.drop_constraint("ck_recommendations_position", "recommendations", type_="check")
    op.drop_column("recommendations", "position")
