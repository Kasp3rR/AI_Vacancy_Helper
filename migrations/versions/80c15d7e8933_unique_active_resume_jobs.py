"""unique active resume jobs

Revision ID: 80c15d7e8933
Revises: 22e28c9f2fd6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "80c15d7e8933"
down_revision: str | Sequence[str] | None = "22e28c9f2fd6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_jobs_active_resume_kind",
        "jobs",
        ["resume_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_jobs_active_resume_kind",
        table_name="jobs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
