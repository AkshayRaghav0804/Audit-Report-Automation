"""Correct persisted enum name for partner review requirement.

Revision ID: 0005_partner_requirement_fix
Revises: 0004_partner_required_answers
Create Date: 2026-07-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_partner_requirement_fix"
down_revision: str | Sequence[str] | None = "0004_partner_required_answers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE approval_step_definitions
        SET answer_requirement = 'REQUIRED_ONLY'
        WHERE stage_code = 'partner_review'
          AND answer_requirement = 'ALL_APPLICABLE'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE approval_step_definitions
        SET answer_requirement = 'ALL_APPLICABLE'
        WHERE stage_code = 'partner_review'
          AND answer_requirement = 'REQUIRED_ONLY'
        """
    )
