"""Publishing and retrieval of immutable approval workflow versions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.enums import PublicationStatus, ReportStatus
from app.database.models.report import Report, ReportWorkflowAssignment
from app.database.models.template import ReportTemplate
from app.database.models.workflow import (
    ApprovalStepDefinition,
    ApprovalWorkflow,
    ApprovalWorkflowVersion,
    ReportTemplateWorkflow,
)
from app.database.session import DatabaseManager, database
from app.models.user import User
from app.schemas.workflow import (
    ApprovalStageConfiguration,
    WorkflowConfiguration,
    default_workflow_configuration,
)
from app.services.authorization_service import require_permission


class WorkflowService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def ensure_default_workflow(self) -> UUID:
        async with self.manager.session() as session:
            async with session.begin():
                existing = await session.execute(
                    select(
                        ApprovalWorkflowVersion.id,
                        ApprovalWorkflowVersion.workflow_id,
                    )
                    .join(
                        ApprovalWorkflow,
                        ApprovalWorkflow.id == ApprovalWorkflowVersion.workflow_id,
                    )
                    .where(
                        ApprovalWorkflow.key == "standard_audit_approval",
                        ApprovalWorkflowVersion.status == PublicationStatus.ACTIVE,
                    )
                )
                templates = tuple(await session.scalars(select(ReportTemplate)))
                existing_row = existing.one_or_none()
                if existing_row is not None:
                    await _ensure_template_assignments(
                        session,
                        existing_row.workflow_id,
                        tuple(template.id for template in templates),
                    )
                    return cast(UUID, existing_row.id)
                return await publish_workflow(
                    session,
                    default_workflow_configuration().model_copy(
                        update={"template_ids": tuple(template.id for template in templates)}
                    ),
                )

    async def publish(self, configuration: WorkflowConfiguration, profile: User) -> UUID:
        async with self.manager.session() as session:
            async with session.begin():
                await require_permission(session, profile, "workflow.manage")
                return await publish_workflow(session, configuration)

    async def activate_creator_reviewer_workflow(self) -> WorkflowActivationResult:
        """Publish the dynamic maker-reviewer workflow and rebase untouched drafts."""

        async with self.manager.session() as session:
            async with session.begin():
                templates = tuple(await session.scalars(select(ReportTemplate)))
                drafts = tuple(
                    await session.scalars(select(Report).where(Report.status == ReportStatus.DRAFT))
                )
                draft_ids = tuple(report.id for report in drafts)
                old_rows = (
                    tuple(
                        await session.execute(
                            select(ReportWorkflowAssignment, ApprovalStepDefinition.stage_code)
                            .join(
                                ApprovalStepDefinition,
                                ApprovalStepDefinition.id
                                == ReportWorkflowAssignment.step_definition_id,
                            )
                            .where(ReportWorkflowAssignment.report_id.in_(draft_ids))
                        )
                    )
                    if draft_ids
                    else ()
                )
                new_version_id = await publish_workflow(
                    session,
                    default_workflow_configuration().model_copy(
                        update={"template_ids": tuple(template.id for template in templates)}
                    ),
                )
                await session.flush()
                new_steps = {
                    step.stage_code: step
                    for step in await session.scalars(
                        select(ApprovalStepDefinition).where(
                            ApprovalStepDefinition.workflow_version_id == new_version_id
                        )
                    )
                }
                assignments_by_report: dict[UUID, list[tuple[ReportWorkflowAssignment, str]]] = {}
                for assignment, stage_code in old_rows:
                    if stage_code in new_steps:
                        assignments_by_report.setdefault(assignment.report_id, []).append(
                            (assignment, stage_code)
                        )
                if draft_ids:
                    await session.execute(
                        delete(ReportWorkflowAssignment).where(
                            ReportWorkflowAssignment.report_id.in_(draft_ids)
                        )
                    )
                for report in drafts:
                    report.workflow_version_id = new_version_id
                    for old_assignment, stage_code in assignments_by_report.get(report.id, []):
                        session.add(
                            ReportWorkflowAssignment(
                                report_id=report.id,
                                step_definition_id=new_steps[stage_code].id,
                                user_id=old_assignment.user_id,
                                assignment_order=old_assignment.assignment_order,
                                role_key_snapshot=new_steps[stage_code].required_role_key,
                                display_name_snapshot=old_assignment.display_name_snapshot,
                                email_snapshot=old_assignment.email_snapshot,
                                selected_by_user_id=old_assignment.selected_by_user_id,
                            )
                        )
                return WorkflowActivationResult(new_version_id, len(drafts))

    async def active_configuration(
        self, key: str = "standard_audit_approval"
    ) -> WorkflowConfiguration | None:
        async with self.manager.session() as session:
            row = (
                await session.execute(
                    select(ApprovalWorkflow, ApprovalWorkflowVersion)
                    .join(
                        ApprovalWorkflowVersion,
                        ApprovalWorkflowVersion.workflow_id == ApprovalWorkflow.id,
                    )
                    .where(
                        ApprovalWorkflow.key == key,
                        ApprovalWorkflowVersion.status == PublicationStatus.ACTIVE,
                    )
                    .order_by(ApprovalWorkflowVersion.version_number.desc())
                )
            ).first()
            if row is None:
                return None
            workflow, version = row
            steps = tuple(
                await session.scalars(
                    select(ApprovalStepDefinition)
                    .where(ApprovalStepDefinition.workflow_version_id == version.id)
                    .order_by(ApprovalStepDefinition.sequence)
                )
            )
            template_ids = tuple(
                await session.scalars(
                    select(ReportTemplateWorkflow.template_id).where(
                        ReportTemplateWorkflow.workflow_id == workflow.id,
                        ReportTemplateWorkflow.is_default.is_(True),
                    )
                )
            )
            return WorkflowConfiguration(
                key=workflow.key,
                name=workflow.name,
                description=workflow.description,
                self_approval_allowed=version.self_approval_allowed,
                stages=tuple(_stage_configuration(step) for step in steps),
                template_ids=template_ids,
            )

    async def active_steps_for_template(
        self, template_id: UUID
    ) -> tuple[ApprovalStepDefinition, ...]:
        async with self.manager.session() as session:
            workflow_version = await session.scalar(
                select(ApprovalWorkflowVersion)
                .join(
                    ApprovalWorkflow,
                    ApprovalWorkflow.id == ApprovalWorkflowVersion.workflow_id,
                )
                .join(
                    ReportTemplateWorkflow,
                    ReportTemplateWorkflow.workflow_id == ApprovalWorkflow.id,
                )
                .where(
                    ReportTemplateWorkflow.template_id == template_id,
                    ReportTemplateWorkflow.is_default.is_(True),
                    ApprovalWorkflowVersion.status == PublicationStatus.ACTIVE,
                )
                .order_by(ApprovalWorkflowVersion.version_number.desc())
            )
            if workflow_version is None:
                return ()
            return tuple(
                await session.scalars(
                    select(ApprovalStepDefinition)
                    .where(ApprovalStepDefinition.workflow_version_id == workflow_version.id)
                    .order_by(ApprovalStepDefinition.sequence)
                )
            )


async def publish_workflow(session: AsyncSession, configuration: WorkflowConfiguration) -> UUID:
    workflow = await session.scalar(
        select(ApprovalWorkflow).where(ApprovalWorkflow.key == configuration.key)
    )
    if workflow is None:
        workflow = ApprovalWorkflow(
            key=configuration.key,
            name=configuration.name,
            description=configuration.description,
            active=True,
        )
        session.add(workflow)
        await session.flush()
    else:
        workflow.name = configuration.name
        workflow.description = configuration.description

    active_versions = tuple(
        await session.scalars(
            select(ApprovalWorkflowVersion).where(
                ApprovalWorkflowVersion.workflow_id == workflow.id,
                ApprovalWorkflowVersion.status == PublicationStatus.ACTIVE,
            )
        )
    )
    for version in active_versions:
        version.status = PublicationStatus.RETIRED
    next_version = (
        await session.scalar(
            select(func.max(ApprovalWorkflowVersion.version_number)).where(
                ApprovalWorkflowVersion.workflow_id == workflow.id
            )
        )
        or 0
    ) + 1
    from app.database.models.mixins import utc_now

    workflow_version = ApprovalWorkflowVersion(
        workflow_id=workflow.id,
        version_number=next_version,
        status=PublicationStatus.ACTIVE,
        self_approval_allowed=configuration.self_approval_allowed,
        published_at=utc_now(),
    )
    session.add(workflow_version)
    await session.flush()
    session.add_all([_step_model(workflow_version.id, stage) for stage in configuration.stages])

    await _ensure_template_assignments(session, workflow.id, configuration.template_ids)
    await session.flush()
    return workflow_version.id


async def _ensure_template_assignments(
    session: AsyncSession,
    workflow_id: UUID,
    template_ids: Sequence[UUID],
) -> None:
    existing_assignments = {
        assignment.template_id: assignment
        for assignment in await session.scalars(
            select(ReportTemplateWorkflow).where(ReportTemplateWorkflow.workflow_id == workflow_id)
        )
    }
    for template_id in template_ids:
        assignment = existing_assignments.get(template_id)
        if assignment is None:
            session.add(
                ReportTemplateWorkflow(
                    template_id=template_id,
                    workflow_id=workflow_id,
                    is_default=True,
                )
            )
        else:
            assignment.is_default = True


def validate_report_assignments(
    stages: Sequence[ApprovalStepDefinition],
    assignments: dict[str, tuple[UUID, ...]],
    *,
    preparer_user_id: UUID,
) -> None:
    assigned_across_stages: set[UUID] = set()
    for stage in stages:
        users = assignments.get(stage.stage_code, ())
        if len(users) < stage.min_assignees:
            raise ValueError(
                f"{stage.display_name} requires at least {stage.min_assignees} assignee(s)."
            )
        if stage.max_assignees is not None and len(users) > stage.max_assignees:
            raise ValueError(
                f"{stage.display_name} allows at most {stage.max_assignees} assignee(s)."
            )
        if len(users) != len(set(users)):
            raise ValueError(f"{stage.display_name} contains a duplicate assignee.")
        if preparer_user_id in users:
            raise ValueError("The report preparer cannot approve their own report.")
        overlap = assigned_across_stages.intersection(users)
        if overlap:
            raise ValueError(
                "The same person cannot occupy multiple approval stages on one report."
            )
        assigned_across_stages.update(users)


def _step_model(
    workflow_version_id: UUID, stage: ApprovalStageConfiguration
) -> ApprovalStepDefinition:
    return ApprovalStepDefinition(
        workflow_version_id=workflow_version_id,
        stage_code=stage.stage_code,
        stage_type=stage.stage_type,
        display_name=stage.display_name,
        sequence=stage.sequence,
        required_role_key=stage.required_role_key,
        required_permission=stage.required_permission,
        fixed_user_id=stage.fixed_user_id,
        min_assignees=stage.min_assignees,
        max_assignees=stage.max_assignees,
        execution_mode=stage.execution_mode,
        completion_policy=stage.completion_policy,
        approval_threshold=stage.approval_threshold,
        assignment_source=stage.assignment_source,
        answer_requirement=stage.answer_requirement,
        due_in_days=stage.due_in_days,
        applicability_rule_json=stage.applicability_rule_json,
        return_policy_json=stage.return_policy_json,
    )


def _stage_configuration(step: ApprovalStepDefinition) -> ApprovalStageConfiguration:
    return ApprovalStageConfiguration(
        stage_code=step.stage_code,
        stage_type=step.stage_type,
        display_name=step.display_name,
        sequence=step.sequence,
        required_role_key=step.required_role_key,
        required_permission=step.required_permission,
        fixed_user_id=step.fixed_user_id,
        min_assignees=step.min_assignees,
        max_assignees=step.max_assignees,
        execution_mode=step.execution_mode,
        completion_policy=step.completion_policy,
        approval_threshold=step.approval_threshold,
        assignment_source=step.assignment_source,
        answer_requirement=step.answer_requirement,
        due_in_days=step.due_in_days,
        applicability_rule_json=step.applicability_rule_json,
        return_policy_json=step.return_policy_json,
    )


@dataclass(frozen=True, slots=True)
class WorkflowActivationResult:
    workflow_version_id: UUID
    rebased_drafts: int
