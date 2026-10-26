"""Persist role and reason metadata for preview edit attribution.

Revision ID: 0009_edit_attribution
Revises: 0008_report_attachments
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_edit_attribution"
down_revision: str | Sequence[str] | None = "0008_report_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("report_answer_history", sa.Column("reason", sa.Text(), nullable=True))
    op.add_column(
        "report_answer_history",
        sa.Column("actor_label_snapshot", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "report_answer_history",
        sa.Column("actor_role_snapshot", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "report_section_changes",
        sa.Column("actor_role_snapshot", sa.String(length=120), nullable=True),
    )
    with op.batch_alter_table("report_section_changes") as batch_op:
        batch_op.alter_column(
            "report_approval_stage_id",
            existing_type=sa.Uuid(),
            nullable=True,
        )


def downgrade() -> None:
    preparation_change_count = op.get_bind().scalar(
        sa.text(
            """
            SELECT COUNT(*)
            FROM report_section_changes
            WHERE report_approval_stage_id IS NULL
            """
        )
    )
    if preparation_change_count:
        raise RuntimeError(
            "Cannot downgrade edit attribution while preparation-stage section changes exist. "
            "Those audit records have no approval stage in revision 0008; archive or migrate "
            "them through an approved recovery procedure before retrying."
        )
    with op.batch_alter_table("report_section_changes") as batch_op:
        batch_op.alter_column(
            "report_approval_stage_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
    op.drop_column("report_section_changes", "actor_role_snapshot")
    op.drop_column("report_answer_history", "actor_role_snapshot")
    op.drop_column("report_answer_history", "actor_label_snapshot")
    op.drop_column("report_answer_history", "reason")
