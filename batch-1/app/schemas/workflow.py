"""Validated approval workflow configuration schemas."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.database.models.enums import (
    AnswerRequirement,
    AssignmentSource,
    CompletionPolicy,
    ExecutionMode,
    StageType,
)


class ApprovalStageConfiguration(BaseModel):
    stage_code: str = Field(min_length=1, max_length=100)
    stage_type: StageType
    display_name: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=1)
    required_role_key: str = Field(min_length=1, max_length=60)
    required_permission: str = "report.review"
    fixed_user_id: UUID | None = None
    min_assignees: int = Field(ge=0)
    max_assignees: int | None = Field(default=None, ge=0)
    execution_mode: ExecutionMode = ExecutionMode.SEQUENTIAL
    completion_policy: CompletionPolicy = CompletionPolicy.ALL
    approval_threshold: int | None = Field(default=None, ge=1)
    assignment_source: AssignmentSource = AssignmentSource.REPORT_SELECTION
    answer_requirement: AnswerRequirement = AnswerRequirement.REQUIRED_ONLY
    due_in_days: int | None = Field(default=None, ge=1, le=3_650)
    applicability_rule_json: dict[str, object] = Field(default_factory=dict)
    return_policy_json: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_cardinality(self) -> ApprovalStageConfiguration:
        if self.max_assignees is not None and self.max_assignees < self.min_assignees:
            raise ValueError("Maximum assignees cannot be less than minimum assignees.")
        if self.completion_policy is CompletionPolicy.THRESHOLD:
            if self.approval_threshold is None:
                raise ValueError("Threshold completion requires an approval threshold.")
            if self.max_assignees is not None and self.approval_threshold > self.max_assignees:
                raise ValueError("Approval threshold cannot exceed maximum assignees.")
        elif self.approval_threshold is not None:
            raise ValueError("Approval threshold is only valid for threshold completion.")
        if self.assignment_source is AssignmentSource.FIXED_USER and self.fixed_user_id is None:
            raise ValueError("Fixed-user assignment requires a user.")
        return self


class WorkflowConfiguration(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=250)
    description: str | None = None
    self_approval_allowed: bool = False
    stages: tuple[ApprovalStageConfiguration, ...]
    template_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def validate_stages(self) -> WorkflowConfiguration:
        if not self.stages:
            raise ValueError("A workflow must contain approval stages.")
        stage_codes = [stage.stage_code for stage in self.stages]
        sequences = [stage.sequence for stage in self.stages]
        if len(stage_codes) != len(set(stage_codes)):
            raise ValueError("Workflow stage codes must be unique.")
        if len(sequences) != len(set(sequences)):
            raise ValueError("Workflow stage sequence numbers must be unique.")

        for mandatory_type in (StageType.PARTNER, StageType.QC):
            matching = [stage for stage in self.stages if stage.stage_type is mandatory_type]
            if len(matching) != 1:
                raise ValueError(
                    f"Workflow must contain exactly one {mandatory_type.value.upper()} stage."
                )
            stage = matching[0]
            if stage.min_assignees != 1 or stage.max_assignees != 1:
                raise ValueError(f"{stage.display_name} must require exactly one assignee.")

        partner_sequence = next(
            stage.sequence for stage in self.stages if stage.stage_type is StageType.PARTNER
        )
        qc_sequence = next(
            stage.sequence for stage in self.stages if stage.stage_type is StageType.QC
        )
        if partner_sequence >= qc_sequence:
            raise ValueError("Partner Review must occur before QC Review.")
        return self


def default_workflow_configuration() -> WorkflowConfiguration:
    return WorkflowConfiguration(
        key="standard_audit_approval",
        name="Standard Audit Approval",
        description=(
            "Up to three optional Manager-level reviewers, optional Director-level review, "
            "mandatory Partner and QC approval, and optional EQCR approval."
        ),
        stages=(
            ApprovalStageConfiguration(
                stage_code="manager_review",
                stage_type=StageType.MANAGER,
                display_name="Manager-level Review",
                sequence=1,
                required_role_key="manager_level",
                min_assignees=0,
                max_assignees=3,
                required_permission="report.approve",
                execution_mode=ExecutionMode.SEQUENTIAL,
                completion_policy=CompletionPolicy.ALL,
                answer_requirement=AnswerRequirement.REQUIRED_ONLY,
            ),
            ApprovalStageConfiguration(
                stage_code="director_review",
                stage_type=StageType.DIRECTOR,
                display_name="Director-level Review",
                sequence=2,
                required_role_key="director_level",
                min_assignees=0,
                max_assignees=1,
                required_permission="report.approve",
                answer_requirement=AnswerRequirement.REQUIRED_ONLY,
            ),
            ApprovalStageConfiguration(
                stage_code="partner_review",
                stage_type=StageType.PARTNER,
                display_name="Partner Review",
                sequence=3,
                required_role_key="partner",
                min_assignees=1,
                max_assignees=1,
                required_permission="report.approve",
                answer_requirement=AnswerRequirement.REQUIRED_ONLY,
            ),
            ApprovalStageConfiguration(
                stage_code="qc_review",
                stage_type=StageType.QC,
                display_name="QC Review",
                sequence=4,
                required_role_key="qc",
                required_permission="report.approve",
                min_assignees=1,
                max_assignees=1,
                answer_requirement=AnswerRequirement.ALL_APPLICABLE,
            ),
            ApprovalStageConfiguration(
                stage_code="eqcr_review",
                stage_type=StageType.EQCR,
                display_name="EQCR Review",
                sequence=5,
                required_role_key="eqcr",
                min_assignees=0,
                max_assignees=1,
                required_permission="report.approve",
                answer_requirement=AnswerRequirement.ALL_APPLICABLE,
            ),
        ),
    )
