"""Persist report-specific section heading edits.

Revision ID: 0011_section_heading_edits
Revises: 0010_comment_text_anchors
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_section_heading_edits"
down_revision: str | Sequence[str] | None = "0010_comment_text_anchors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_section_headings",
        sa.Column("report_revision_id", sa.Uuid(), nullable=False),
        sa.Column("template_section_id", sa.Uuid(), nullable=False),
        sa.Column("heading", sa.String(length=300), nullable=False),
        sa.Column("heading_version", sa.Integer(), nullable=False),
        sa.Column("edited_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["edited_by_user_id"],
            ["application_users.id"],
            name=op.f("fk_report_section_headings_edited_by_user_id_application_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_revision_id"],
            ["report_revisions.id"],
            name=op.f("fk_report_section_headings_report_revision_id_report_revisions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_section_id"],
            ["template_sections.id"],
            name=op.f("fk_report_section_headings_template_section_id_template_sections"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_section_headings")),
        sa.UniqueConstraint(
            "report_revision_id",
            "template_section_id",
            name=op.f("uq_report_section_headings_report_revision_id"),
        ),
    )
    with op.batch_alter_table("report_section_headings") as batch_op:
        batch_op.create_index(
            batch_op.f("ix_report_section_headings_report_revision_id"),
            ["report_revision_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_report_section_headings_template_section_id"),
            ["template_section_id"],
            unique=False,
        )
    op.add_column(
        "report_section_changes",
        sa.Column(
            "change_target",
            sa.String(length=20),
            nullable=False,
            server_default="content",
        ),
    )


def downgrade() -> None:
    heading_count = op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM report_section_headings"))
    heading_change_count = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM report_section_changes WHERE change_target = 'heading'")
    )
    if heading_count or heading_change_count:
        raise RuntimeError(
            "Cannot downgrade section heading edits while heading overrides or heading history "
            "exist. Export or migrate those records through an approved recovery procedure first."
        )
    op.drop_column("report_section_changes", "change_target")
    with op.batch_alter_table("report_section_headings") as batch_op:
        batch_op.drop_index(batch_op.f("ix_report_section_headings_template_section_id"))
        batch_op.drop_index(batch_op.f("ix_report_section_headings_report_revision_id"))
    op.drop_table("report_section_headings")
