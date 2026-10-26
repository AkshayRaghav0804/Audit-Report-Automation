"""Add reviewer comments and approval-role permissions.

Revision ID: 0006_role_scoped_reviews
Revises: 0005_partner_requirement_fix
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_role_scoped_reviews"
down_revision: str | Sequence[str] | None = "0005_partner_requirement_fix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_comments",
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("report_revision_id", sa.Uuid(), nullable=False),
        sa.Column("report_approval_stage_id", sa.Uuid(), nullable=True),
        sa.Column("section_key", sa.String(length=160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["author_user_id"], ["application_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["report_approval_stage_id"],
            ["report_approval_stages.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["report_revision_id"], ["report_revisions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_report_comments_author_user_id",
        "report_comments",
        ["author_user_id"],
    )
    op.create_index("ix_report_comments_report_id", "report_comments", ["report_id"])
    op.create_index(
        "ix_report_comments_report_revision_id",
        "report_comments",
        ["report_revision_id"],
    )
    op.create_index(
        "ix_report_comments_report_approval_stage_id",
        "report_comments",
        ["report_approval_stage_id"],
    )
    op.create_index(
        "ix_report_comments_report_section",
        "report_comments",
        ["report_id", "section_key"],
    )
    op.execute(
        """
        UPDATE approval_step_definitions
        SET required_permission = 'report.approve'
        WHERE stage_code IN ('partner_review', 'qc_review', 'eqcr_review')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE approval_step_definitions
        SET required_permission = 'report.review'
        WHERE stage_code IN ('partner_review', 'qc_review', 'eqcr_review')
        """
    )
    op.drop_index("ix_report_comments_report_section", table_name="report_comments")
    op.drop_index("ix_report_comments_report_approval_stage_id", table_name="report_comments")
    op.drop_index("ix_report_comments_report_revision_id", table_name="report_comments")
    op.drop_index("ix_report_comments_report_id", table_name="report_comments")
    op.drop_index("ix_report_comments_author_user_id", table_name="report_comments")
    op.drop_table("report_comments")
