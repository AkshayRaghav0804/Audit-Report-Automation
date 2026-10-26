"""Admin editor for publishing immutable approval workflow versions."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, cast
from uuid import UUID

from nicegui import app, ui

from app.components.header import add_application_head, render_header
from app.components.notification_menu import NotificationState
from app.components.route_guard import require_authentication
from app.components.sidebar import render_sidebar
from app.database.models.enums import StageType
from app.schemas.workflow import (
    ApprovalStageConfiguration,
    WorkflowConfiguration,
    default_workflow_configuration,
)
from app.services.permission_service import has_permission
from app.services.report_creation_service import ReportCreationService
from app.services.workflow_service import WorkflowService

ROUTE = "/admin/workflows"


def _storage() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], app.storage.user)


def register_admin_workflow_routes() -> None:
    @ui.page(ROUTE, title="Approval Workflows | QC Report Automation")
    async def admin_workflow_page() -> None:
        user = await require_authentication(ROUTE)
        if user is None:
            return
        add_application_head()
        drawer = render_sidebar(user, ROUTE)
        render_header(
            user,
            "Approval Workflows",
            await NotificationState.for_user(user.email, _storage()),
            drawer,
        )
        if not has_permission(user, "workflow.manage"):
            _access_denied()
            return

        creation_context = await ReportCreationService().context_for(user)
        workflow_service = WorkflowService()
        active = await workflow_service.active_configuration()
        configuration = active or default_workflow_configuration()
        stage_by_type = {stage.stage_type: stage for stage in configuration.stages}

        with ui.element("main").classes("app-main"):
            with ui.column().classes("gap-1"):
                ui.label("Approval workflow configuration").classes("welcome-title")
                ui.label(
                    "Publishing creates a new immutable version. Existing reports keep the "
                    "workflow version with which they were created."
                ).classes("welcome-subtitle")

            with ui.card().classes("w-full q-mt-lg"):
                ui.label("Stages").classes("section-title")
                ui.label(
                    "Manager-level review accepts up to three people in sequence. Director and "
                    "EQCR are optional; Partner and QC Team are mandatory."
                ).classes("section-subtitle")

                eqcr_stage = stage_by_type.get(StageType.EQCR)
                _locked_stage(
                    "Manager-level Review",
                    "Optional. The maker may add up to three people, reviewed in selected order.",
                )
                _locked_stage(
                    "Director-level Review",
                    "Optional. This is an approval level and does not depend on job title.",
                )
                _locked_stage("Partner Review", "Exactly one Partner is always required.")
                _locked_stage(
                    "QC Team Review",
                    "The selected QC address is notified; any signed-in QC member may act.",
                )
                eqcr_enabled = ui.checkbox("Include EQCR Review", value=eqcr_stage is not None)
                eqcr_required = ui.checkbox(
                    "EQCR is required",
                    value=bool(eqcr_stage and eqcr_stage.min_assignees > 0),
                )
                configured_due_days = {
                    stage.due_in_days
                    for stage in configuration.stages
                    if stage.due_in_days is not None
                }
                stage_due_days = ui.number(
                    "Stage due in calendar days (blank = no deadline)",
                    value=(
                        next(iter(configured_due_days)) if len(configured_due_days) == 1 else None
                    ),
                    min=1,
                    precision=0,
                ).props("outlined clearable")

            with ui.card().classes("w-full q-mt-lg"):
                ui.label("Applicable report templates").classes("section-title")
                template_options = {
                    str(template.id): template.name for template in creation_context.templates
                }
                selected_template_ids = [
                    str(template.id)
                    for template in creation_context.templates
                    if template.id in configuration.template_ids
                ] or list(template_options)
                template_select = ui.select(
                    template_options,
                    value=selected_template_ids,
                    multiple=True,
                    with_input=True,
                    label="Templates",
                ).props("outlined use-chips")

                async def publish() -> None:
                    try:
                        stages = _configured_stages(
                            eqcr_enabled=bool(eqcr_enabled.value),
                            eqcr_required=bool(eqcr_required.value),
                            stage_due_days=(
                                int(stage_due_days.value)
                                if stage_due_days.value is not None
                                else None
                            ),
                        )
                        template_ids = tuple(
                            UUID(str(value)) for value in (template_select.value or ())
                        )
                        if not template_ids:
                            raise ValueError("Select at least one report template.")
                        await workflow_service.publish(
                            WorkflowConfiguration(
                                key=configuration.key,
                                name=configuration.name,
                                description=configuration.description,
                                self_approval_allowed=False,
                                stages=stages,
                                template_ids=template_ids,
                            ),
                            user,
                        )
                    except (PermissionError, ValueError) as error:
                        ui.notify(str(error), type="negative", position="top-right")
                        return
                    ui.notify(
                        "A new approval workflow version was published.",
                        type="positive",
                        position="top-right",
                    )
                    ui.navigate.to(ROUTE)

                ui.button("Publish new version", icon="publish", on_click=publish).props(
                    "unelevated no-caps"
                ).classes("primary-action q-mt-md")


def _configured_stages(
    *,
    eqcr_enabled: bool,
    eqcr_required: bool,
    stage_due_days: int | None,
) -> tuple[ApprovalStageConfiguration, ...]:
    defaults = default_workflow_configuration().stages
    included: list[ApprovalStageConfiguration] = []
    for stage in defaults:
        if stage.stage_type is StageType.EQCR:
            if not eqcr_enabled:
                continue
            stage = stage.model_copy(update={"min_assignees": 1 if eqcr_required else 0})
        included.append(stage.model_copy(update={"due_in_days": stage_due_days}))
    return tuple(
        stage.model_copy(update={"sequence": sequence})
        for sequence, stage in enumerate(included, start=1)
    )


def _locked_stage(title: str, description: str) -> None:
    with ui.row().classes("items-center gap-2"):
        ui.icon("lock").classes("text-positive")
        with ui.column().classes("gap-0"):
            ui.label(title).classes("text-subtitle1")
            ui.label(description).classes("text-caption")


def _access_denied() -> None:
    with ui.element("main").classes("app-main"):
        with ui.column().classes("placeholder-page items-center text-center"):
            ui.icon("block").classes("placeholder-page__icon")
            ui.label("Access denied").classes("welcome-title")
            ui.label("Workflow management requires workflow.manage.").classes("welcome-subtitle")
