"""Use required-only validation for partner review

Revision ID: 0004_partner_required_answers
Revises: 0003_dashboard_read_model
Create Date: 2026-07-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_partner_required_answers"
down_revision: str | Sequence[str] | None = "0003_dashboard_read_model"
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
