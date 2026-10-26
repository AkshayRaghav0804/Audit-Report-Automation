"""Store the initial lock state for each template section.

Revision ID: 0013_template_lock_defaults
Revises: 0012_section_access
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_template_lock_defaults"
down_revision: str | Sequence[str] | None = "0012_section_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "template_sections",
        sa.Column(
            "default_locked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    unlocked_count = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM template_sections WHERE default_locked = false")
    )
    if unlocked_count:
        raise RuntimeError(
            "Cannot discard unlocked template defaults. Reset them to locked or preserve the "
            "configuration through an approved recovery procedure first."
        )
    op.drop_column("template_sections", "default_locked")
