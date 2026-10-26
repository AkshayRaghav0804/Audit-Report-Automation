"""dashboard read model

Revision ID: 0003_dashboard_read_model
Revises: 630ef15f3add
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_dashboard_read_model"
down_revision: str | Sequence[str] | None = "630ef15f3add"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("entity_name", sa.String(length=300), nullable=True))
    op.add_column(
        "reports",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.add_column(
        "approval_step_definitions",
        sa.Column("due_in_days", sa.Integer(), nullable=True),
    )

    op.create_index("ix_reports_entity_name", "reports", ["entity_name"], unique=False)
    op.create_index(
        "ix_reports_last_activity_at",
        "reports",
        ["last_activity_at"],
        unique=False,
    )
    op.create_index(
        "ix_reports_status_last_activity",
        "reports",
        ["status", "last_activity_at"],
        unique=False,
    )
    op.create_index(
        "ix_reports_owner_status",
        "reports",
        ["owner_user_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_approval_cycles_status_revision",
        "approval_cycles",
        ["status", "report_revision_id"],
        unique=False,
    )
    op.create_index(
        "ix_report_approval_stages_status_due",
        "report_approval_stages",
        ["status", "due_at"],
        unique=False,
    )
    op.create_index(
        "ix_report_stage_assignments_user_status",
        "report_stage_assignments",
        ["user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_report_stage_assignments_user_status",
        table_name="report_stage_assignments",
    )
    op.drop_index(
        "ix_report_approval_stages_status_due",
        table_name="report_approval_stages",
    )
    op.drop_index(
        "ix_approval_cycles_status_revision",
        table_name="approval_cycles",
    )
    op.drop_index("ix_reports_owner_status", table_name="reports")
    op.drop_index("ix_reports_status_last_activity", table_name="reports")
    op.drop_index("ix_reports_last_activity_at", table_name="reports")
    op.drop_index("ix_reports_entity_name", table_name="reports")
    op.drop_column("approval_step_definitions", "due_in_days")
    op.drop_column("reports", "last_activity_at")
    op.drop_column("reports", "entity_name")
