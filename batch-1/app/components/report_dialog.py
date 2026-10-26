"""Database-backed report-creation dialog with role-aware assignee search."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any
from uuid import UUID

from nicegui import ui

from app.database.models.enums import StageType
from app.models.user import User
from app.schemas.directory import DirectoryPerson
from app.services.report_basic_details import (
    AS_IGAAP,
    REPORT_CATEGORIES,
    STATUTORY_REPORT,
    audit_type_answer,
    is_bank_template,
    report_category_for_template,
)
from app.services.report_creation_service import (
    NewReportRequest,
    ReportCreationContext,
    ReportCreationService,
    WorkflowStageChoice,
)
from app.services.report_draft_service import draft_storage_key, save_answer
from app.services.report_persistence_service import ReportPersistenceService

LOGGER = logging.getLogger(__name__)


class ReportDialogController:
    def __init__(self, current_user: User, storage: MutableMapping[str, Any]) -> None:
        self.current_user = current_user
        self.storage = storage
        self.dialog: ui.dialog | None = None

    async def open(self) -> None:
        if self.dialog is None:
            self.dialog = await _build_report_dialog(self.current_user, self.storage)
        self.dialog.open()


def create_report_dialog(
    current_user: User, storage: MutableMapping[str, Any]
) -> ReportDialogController:
    return ReportDialogController(current_user, storage)


async def _build_report_dialog(current_user: User, storage: MutableMapping[str, Any]) -> ui.dialog:
    """Build a report dialog from active database templates and workflow versions."""

    service = ReportCreationService()
    context: ReportCreationContext | None = None
    source_error = ""
    try:
        context = await service.context_for(current_user)
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.exception("Report creation context could not be loaded")
        source_error = str(error)

    dialog = ui.dialog()
    with dialog, ui.card().classes("report-dialog report-create-dialog"):
        with ui.row().classes("report-dialog__header items-start justify-between"):
            with ui.row().classes("items-center no-wrap gap-3"):
                with ui.element("div").classes("report-dialog__header-icon"):
                    ui.icon("note_add")
                with ui.column().classes("gap-1"):
                    ui.label("REPORT WORKSPACE").classes("report-dialog__eyebrow")
                    ui.label("Create New Report").classes("report-dialog__title")
                    ui.label(
                        "Choose a controlled Word template and assign its approval workflow."
                    ).classes("report-dialog__subtitle")
            with (
                ui.button(icon="close", on_click=dialog.close)
                .props('flat round dense aria-label="Close dialog"')
                .classes("dialog-close")
            ):
                ui.tooltip("Close")

        ui.separator()
        if context is None:
            _source_error(source_error or "No report configuration is available.", dialog)
            return dialog
        if not context.templates:
            _source_error(
                "No active report templates are available. Ask an administrator to import one.",
                dialog,
            )
            return dialog

        templates_by_id = {str(template.id): template for template in context.templates}

        def template_options(category: str) -> dict[str, str]:
            return {
                key: template.name
                for key, template in templates_by_id.items()
                if report_category_for_template(template.key, template.name) == category
            }

        with ui.element("section").classes("report-dialog__section report-dialog__section--basics"):
            with ui.row().classes("report-dialog__section-heading items-center no-wrap"):
                with ui.element("div").classes("report-dialog__step-number"):
                    ui.label("1")
                with ui.column().classes("gap-0"):
                    ui.label("Report details").classes("report-dialog__section-title")
                    ui.label("Select the report format and engagement reference.").classes(
                        "report-dialog__section-copy"
                    )
            with ui.element("div").classes("report-template-fields"):
                report_category = (
                    ui.select(
                        options=list(REPORT_CATEGORIES),
                        value=STATUTORY_REPORT,
                        label="Report Category *",
                    )
                    .props("outlined stack-label options-dense")
                    .classes("report-type-select w-full")
                    .mark("report-category-select")
                )
                report_type = (
                    ui.select(
                        options=template_options(STATUTORY_REPORT),
                        label="Report Type *",
                        with_input=True,
                    )
                    .props("outlined stack-label clearable options-dense")
                    .classes("report-type-select w-full")
                    .mark("report-template-select")
                )
            with ui.element("div").classes("report-form-grid"):
                engagement_code = ui.input(
                    label="Engagement Code", placeholder="ENG-2026-001"
                ).props("outlined")
                ui.input(value=context.preparer.display_name, label="Prepared By").props(
                    "outlined readonly"
                ).mark("prepared-by-profile")

            template_summary = ui.label(
                "Select a report type to see its source-derived question count."
            ).classes("template-summary")
        workflow_controls: dict[str, tuple[WorkflowStageChoice, Any]] = {}

        @ui.refreshable
        def workflow_fields() -> None:
            workflow_controls.clear()
            selected = templates_by_id.get(str(report_type.value or ""))
            if selected is None:
                return
            stages = context.stages_by_template.get(selected.id, ())
            with ui.element("section").classes(
                "report-dialog__section report-dialog__section--workflow"
            ):
                with ui.row().classes("report-dialog__section-heading items-center no-wrap"):
                    with ui.element("div").classes("report-dialog__step-number"):
                        ui.label("2")
                    with ui.column().classes("gap-0"):
                        ui.label("Approval assignments").classes("report-dialog__section-title")
                        ui.label("Search and select the reviewers for this engagement.").classes(
                            "report-dialog__section-copy"
                        )
                with ui.element("div").classes("report-form-grid workflow-stage-grid"):
                    for stage in stages:
                        source_people = (
                            context.all_people
                            if stage.stage_type in {StageType.MANAGER, StageType.DIRECTOR}
                            else context.people_by_role.get(stage.required_role_key, ())
                        )
                        candidates = _eligible_candidates(source_people, context.preparer.id)
                        options = _person_options(candidates, stage.required_role_key)
                        if stage.stage_type is StageType.MANAGER:
                            label = "Manager-level reviewers (optional, up to 3)"
                        elif stage.stage_type is StageType.QC:
                            label = "QC reviewer / mailbox *"
                        else:
                            label = stage.display_name + (
                                "" if not stage.optional else " (optional)"
                            )
                        control = ui.select(
                            options=options,
                            label=label,
                            multiple=stage.multiple,
                            with_input=True,
                            clearable=stage.optional,
                            value=[] if stage.multiple else None,
                        ).props(
                            "outlined use-chips options-dense"
                            + (' max-values="3"' if stage.stage_type is StageType.MANAGER else "")
                        )
                        if stage.stage_type is StageType.MANAGER:
                            control.add_slot("prepend", '<q-icon name="add" />')
                        workflow_controls[stage.stage_code] = (stage, control)
                        if not candidates:
                            ui.label(
                                f"No active users are available for {stage.display_name}. "
                                "Add them in Admin > People Directory."
                            ).classes("form-error")

                        if stage.stage_type is StageType.PARTNER:
                            icai = (
                                ui.input(
                                    label="Partner ICAI Membership No.",
                                    placeholder="Select a partner to verify",
                                )
                                .props("outlined readonly")
                                .mark("partner-icai-membership")
                            )
                            candidates_by_id = {str(person.id): person for person in candidates}

                            def partner_changed(
                                _event: Any,
                                *,
                                partner_control: Any = control,
                                icai_control: Any = icai,
                                people: dict[str, Any] = candidates_by_id,
                            ) -> None:
                                person = people.get(str(partner_control.value or ""))
                                icai_control.set_value(
                                    person.icai_membership_number if person else ""
                                )

                            control.on_value_change(partner_changed)
                        elif stage.stage_type is StageType.QC:
                            ui.label(
                                "The approval email is sent to the selected QC address. "
                                "Any active QC Team member can approve after signing in."
                            ).classes("field-help")

                with ui.element("section").classes("workflow-default"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("account_tree")
                        ui.label("Selected approval path").classes("workflow-default__title")
                    selected_path = ui.label().classes("workflow-default__path")

                    def update_selected_path() -> None:
                        parts = [f"Maker: {context.preparer.display_name}"]
                        for stage in stages:
                            _choice, stage_control = workflow_controls[stage.stage_code]
                            raw = stage_control.value
                            values = raw if isinstance(raw, list) else [raw] if raw else []
                            selected_names = [
                                str(stage_control.options.get(str(value), value))
                                for value in values
                            ]
                            if stage.stage_type is StageType.QC and selected_names:
                                parts.append("QC Team")
                            elif selected_names:
                                parts.append(f"{stage.display_name}: " + ", ".join(selected_names))
                            elif stage.optional:
                                parts.append(f"{stage.display_name}: not selected")
                            else:
                                parts.append(f"{stage.display_name}: select reviewer")
                        selected_path.set_text(" → ".join(parts))

                    for _stage, stage_control in workflow_controls.values():
                        stage_control.on_value_change(lambda _event: update_selected_path())
                    update_selected_path()

        def selection_changed() -> None:
            selected = templates_by_id.get(str(report_type.value or ""))
            if selected is None:
                available = len(template_options(str(report_category.value or "")))
                template_summary.set_text(
                    "No reports are currently available for this category."
                    if available == 0
                    else "Select a report type to see its source-derived question count."
                )
            else:
                template_summary.set_text(
                    f"{selected.question_count} questions · {selected.section_count} "
                    f"Word sections · template version {selected.version_number}"
                )
            workflow_fields.refresh()

        def category_changed() -> None:
            category = str(report_category.value or "")
            options = template_options(category)
            report_type.set_options(options, value=None)
            if options:
                report_type.enable()
            else:
                report_type.disable()
            selection_changed()

        report_type.on_value_change(lambda _: selection_changed())
        report_category.on_value_change(lambda _: category_changed())
        workflow_fields()

        validation_message = ui.label().classes("form-error")
        validation_message.set_visibility(False)

        async def submit() -> None:
            category = str(report_category.value or "")
            if category not in REPORT_CATEGORIES:
                _show_error(validation_message, "Select Statutory Report or Limited Review.")
                return
            selected = templates_by_id.get(str(report_type.value or ""))
            if selected is None:
                _show_error(validation_message, "Select an available report type.")
                return
            assignments: dict[str, tuple[UUID, ...]] = {}
            for stage_code, (stage, control) in workflow_controls.items():
                values = control.value or ([] if stage.multiple else None)
                raw_values = values if isinstance(values, list) else [values] if values else []
                assignments[stage_code] = tuple(UUID(str(value)) for value in raw_values)
            try:
                created = await service.create(
                    NewReportRequest(
                        template_id=selected.id,
                        engagement_code=str(engagement_code.value or "") or None,
                        creator_email=current_user.email,
                        assignments=assignments,
                    )
                )
            except (PermissionError, ValueError) as error:
                _show_error(validation_message, str(error))
                return

            storage_key = draft_storage_key(selected.key, created.public_id)
            audit_type = audit_type_answer(category)
            save_answer(storage, storage_key, "type_of_audit", audit_type)
            await ReportPersistenceService().save_answer(
                public_id=created.public_id,
                template_key=selected.key,
                field_name="type_of_audit",
                value=audit_type,
                actor_email=current_user.email,
            )
            if is_bank_template(selected.key, selected.name):
                save_answer(
                    storage,
                    storage_key,
                    "applicable_financial_reporting_framework",
                    AS_IGAAP,
                )
                await ReportPersistenceService().save_answer(
                    public_id=created.public_id,
                    template_key=selected.key,
                    field_name="applicable_financial_reporting_framework",
                    value=AS_IGAAP,
                    actor_email=current_user.email,
                )
            save_answer(storage, storage_key, "workflow_report_public_id", created.public_id)
            save_answer(
                storage,
                storage_key,
                "workflow_engagement_code",
                engagement_code.value,
            )
            save_answer(
                storage,
                storage_key,
                "workflow_prepared_by",
                context.preparer.display_name,
            )
            for stage_code, user_ids in assignments.items():
                save_answer(
                    storage,
                    storage_key,
                    f"workflow_{stage_code}",
                    [str(user_id) for user_id in user_ids],
                )
            partner_stage = next(
                (
                    stage
                    for stage, _control in workflow_controls.values()
                    if stage.stage_type is StageType.PARTNER
                ),
                None,
            )
            if partner_stage is not None:
                partner_ids = assignments.get(partner_stage.stage_code, ())
                partner = next(
                    (
                        person
                        for person in context.people_by_role.get("partner", ())
                        if partner_ids and person.id == partner_ids[0]
                    ),
                    None,
                )
                if partner is not None:
                    save_answer(storage, storage_key, "workflow_partner", partner.display_name)
                    save_answer(
                        storage,
                        storage_key,
                        "workflow_partner_icai_membership_number",
                        partner.icai_membership_number,
                    )
            validation_message.set_visibility(False)
            dialog.close()
            ui.navigate.to(f"/reports/new/{created.template_key}?report_id={created.public_id}")

        with ui.row().classes("report-dialog__actions justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            ui.button("Create Report", icon="add", on_click=submit).props(
                "unelevated no-caps"
            ).classes("primary-action").mark("create-report-submit")
    return dialog


def _source_error(message: str, dialog: ui.dialog) -> None:
    with ui.row().classes("source-error items-start no-wrap"):
        ui.icon("error_outline")
        with ui.column().classes("gap-1"):
            ui.label("Report configuration is unavailable").classes("source-error__title")
            ui.label(message).classes("source-error__message")
    with ui.row().classes("report-dialog__actions justify-end"):
        ui.button("Close", on_click=dialog.close).props("flat no-caps")
        ui.button("Retry", icon="refresh", on_click=lambda: ui.navigate.to("/dashboard")).props(
            "unelevated no-caps"
        ).classes("primary-action")


def _show_error(label: Any, message: str) -> None:
    label.set_text(message)
    label.set_visibility(True)


def _person_options(people: tuple[DirectoryPerson, ...], role_key: str) -> dict[str, str]:
    if role_key != "partner":
        return {str(person.id): person.selection_label for person in people}

    name_totals: dict[str, int] = {}
    for person in people:
        key = person.display_name.casefold()
        name_totals[key] = name_totals.get(key, 0) + 1

    name_positions: dict[str, int] = {}
    options: dict[str, str] = {}
    for person in people:
        key = person.display_name.casefold()
        name_positions[key] = name_positions.get(key, 0) + 1
        label = f"{person.display_name} — {person.email}"
        if name_totals[key] > 1:
            label = f"{label} (Partner {name_positions[key]})"
        options[str(person.id)] = label
    return options


def _eligible_candidates(
    people: tuple[DirectoryPerson, ...], preparer_id: UUID
) -> tuple[DirectoryPerson, ...]:
    """Prevent a report preparer from assigning themselves as an approver."""

    return tuple(person for person in people if person.id != preparer_id)
