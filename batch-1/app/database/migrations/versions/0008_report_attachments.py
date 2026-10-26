"""Persist conditional CARO and IFC supporting files.

Revision ID: 0008_report_attachments
Revises: 0007_dynamic_workflow
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_report_attachments"
down_revision: str | Sequence[str] | None = "0007_dynamic_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_attachments",
        sa.Column("report_revision_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_kind", sa.String(length=30), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=150), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["report_revision_id"], ["report_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"], ["application_users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "report_revision_id",
            "attachment_kind",
            name="uq_report_attachments_report_revision_id",
        ),
    )
    op.create_index(
        "ix_report_attachments_report_revision_id",
        "report_attachments",
        ["report_revision_id"],
    )
    op.create_index(
        "ix_report_attachments_uploaded_by_user_id",
        "report_attachments",
        ["uploaded_by_user_id"],
    )
    op.create_index(
        "ix_report_attachments_revision_kind",
        "report_attachments",
        ["report_revision_id", "attachment_kind"],
    )


def downgrade() -> None:
    op.drop_index("ix_report_attachments_revision_kind", table_name="report_attachments")
    op.drop_index("ix_report_attachments_uploaded_by_user_id", table_name="report_attachments")
    op.drop_index("ix_report_attachments_report_revision_id", table_name="report_attachments")
    op.drop_table("report_attachments")
