"""Report-local locks and editing requests; missing lock rows mean locked."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_section_access"
down_revision: str | Sequence[str] | None = "0011_section_heading_edits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_section_locks",
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("template_section_id", sa.Uuid(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("changed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"],
            ["application_users.id"],
            name="fk_report_section_locks_changed_by_user_id_application_users",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"], ["reports.id"], name="fk_report_section_locks_report_id_reports"
        ),
        sa.ForeignKeyConstraint(
            ["template_section_id"],
            ["template_sections.id"],
            name="fk_report_section_locks_template_section_id_template_sections",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_report_section_locks"),
        sa.UniqueConstraint(
            *["report_id", "template_section_id"], name="uq_report_section_locks_report_id"
        ),
    )
    op.create_table(
        "section_edit_requests",
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("template_section_id", sa.Uuid(), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["application_users.id"],
            name="fk_section_edit_requests_decided_by_user_id_application_users",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"], ["reports.id"], name="fk_section_edit_requests_report_id_reports"
        ),
        sa.ForeignKeyConstraint(
            ["requester_user_id"],
            ["application_users.id"],
            name="fk_section_edit_requests_requester_user_id_application_users",
        ),
        sa.ForeignKeyConstraint(
            ["template_section_id"],
            ["template_sections.id"],
            name="fk_section_edit_requests_template_section_id_template_sections",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_section_edit_requests"),
    )
    op.create_index("ix_section_edit_requests_report_id", "section_edit_requests", ["report_id"])


def downgrade() -> None:
    for table in ("report_section_locks", "section_edit_requests"):
        # The identifier comes exclusively from the fixed allowlist above; SQL parameters
        # cannot represent table identifiers.
        if op.get_bind().scalar(sa.text(f"SELECT COUNT(*) FROM {table}")):  # nosec B608
            raise RuntimeError(
                "Cannot discard section permissions. Preserve records before downgrade."
            )
    op.drop_index("ix_section_edit_requests_report_id", table_name="section_edit_requests")
    op.drop_table("section_edit_requests")
    op.drop_table("report_section_locks")
