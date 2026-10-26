"""Persist selected-text anchors for Word-style preview comments.

Revision ID: 0010_comment_text_anchors
Revises: 0009_edit_attribution
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_comment_text_anchors"
down_revision: str | Sequence[str] | None = "0009_edit_attribution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add optional anchors without changing existing section comments."""
    op.add_column("report_comments", sa.Column("selected_text", sa.Text(), nullable=True))
    op.add_column("report_comments", sa.Column("selection_start", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Remove anchors while retaining comment bodies and section attribution."""
    op.drop_column("report_comments", "selection_start")
    op.drop_column("report_comments", "selected_text")
