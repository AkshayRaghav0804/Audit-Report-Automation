"""Dynamic reviewers, controlled wording history, notifications, and email outbox.

Revision ID: 0007_dynamic_workflow
Revises: 0006_role_scoped_reviews
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_dynamic_workflow"
down_revision: str | Sequence[str] | None = "0006_role_scoped_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_section_changes",
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("report_revision_id", sa.Uuid(), nullable=False),
        sa.Column("template_section_id", sa.Uuid(), nullable=False),
        sa.Column("report_approval_stage_id", sa.Uuid(), nullable=False),
        sa.Column("previous_content", sa.Text(), nullable=False),
        sa.Column("new_content", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("actor_label_snapshot", sa.String(length=200), nullable=False),
        sa.Column("stage_label_snapshot", sa.String(length=200), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"], ["application_users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["report_revision_id"], ["report_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["report_approval_stage_id"],
            ["report_approval_stages.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_section_id"], ["template_sections.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "report_id",
        "report_revision_id",
        "template_section_id",
        "report_approval_stage_id",
        "changed_by_user_id",
        "changed_at",
    ):
        op.create_index(f"ix_report_section_changes_{column}", "report_section_changes", [column])
    op.create_index(
        "ix_report_section_changes_revision_section",
        "report_section_changes",
        ["report_revision_id", "template_section_id"],
    )

    op.create_table(
        "user_notifications",
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=True),
        sa.Column("notification_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=250), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("target_path", sa.String(length=500), nullable=False),
        sa.Column("is_read", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"], ["application_users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("recipient_user_id", "report_id", "notification_type"):
        op.create_index(f"ix_user_notifications_{column}", "user_notifications", [column])
    op.create_index(
        "ix_user_notifications_recipient_unread",
        "user_notifications",
        ["recipient_user_id", "is_read"],
    )

    op.create_table(
        "email_outbox",
        sa.Column("report_id", sa.Uuid(), nullable=True),
        sa.Column("to_address", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_outbox_report_id", "email_outbox", ["report_id"])
    op.create_index("ix_email_outbox_status", "email_outbox", ["status"])
    op.create_index("ix_email_outbox_status_available", "email_outbox", ["status", "available_at"])


def downgrade() -> None:
    op.drop_index("ix_email_outbox_status_available", table_name="email_outbox")
    op.drop_index("ix_email_outbox_status", table_name="email_outbox")
    op.drop_index("ix_email_outbox_report_id", table_name="email_outbox")
    op.drop_table("email_outbox")
    op.drop_index("ix_user_notifications_recipient_unread", table_name="user_notifications")
    op.drop_index("ix_user_notifications_notification_type", table_name="user_notifications")
    op.drop_index("ix_user_notifications_report_id", table_name="user_notifications")
    op.drop_index("ix_user_notifications_recipient_user_id", table_name="user_notifications")
    op.drop_table("user_notifications")
    op.drop_index("ix_report_section_changes_revision_section", table_name="report_section_changes")
    for column in (
        "changed_at",
        "changed_by_user_id",
        "report_approval_stage_id",
        "template_section_id",
        "report_revision_id",
        "report_id",
    ):
        op.drop_index(f"ix_report_section_changes_{column}", table_name="report_section_changes")
    op.drop_table("report_section_changes")
