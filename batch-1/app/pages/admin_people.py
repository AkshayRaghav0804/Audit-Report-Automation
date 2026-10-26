"""Database-backed Admin people directory and preview-first bulk import."""

from __future__ import annotations

import io
from collections.abc import MutableMapping
from typing import Any, cast

from nicegui import app, events, ui
from openpyxl import Workbook
from pydantic import ValidationError

from app.components.entra_import import render_entra_import
from app.components.header import add_application_head, render_header
from app.components.notification_menu import NotificationState
from app.components.route_guard import require_authentication
from app.components.sidebar import render_sidebar
from app.models.user import User
from app.schemas.directory import DirectoryPerson, PeopleImportPreview, PersonUpsert
from app.services.directory_service import ROLE_CATALOG, DirectoryService
from app.services.people_import_service import PeopleImportService
from app.services.permission_service import has_permission
from app.services.report_definition_service import get_report_definition_service

ROUTE = "/admin/users"


def _storage() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], app.storage.user)


def register_admin_people_routes() -> None:
    @ui.page(ROUTE, title="People Directory | QC Report Automation")
    async def admin_people_page() -> None:
        current_user = await require_authentication(ROUTE)
        if current_user is None:
            return

        add_application_head()
        drawer = render_sidebar(current_user, ROUTE)
        render_header(
            current_user,
            "People Directory",
            await NotificationState.for_user(current_user.email, _storage()),
            drawer,
        )
        if not has_permission(current_user, "user.manage"):
            _render_access_denied()
            return

        directory = DirectoryService()
        actor = await _ensure_directory_actor(directory, current_user)
        people = await directory.list_people(active_only=False, limit=None)
        people_by_id = {str(person.id): person for person in people}
        preview_state: dict[str, PeopleImportPreview] = {}

        with ui.element("main").classes("app-main"):
            with ui.row().classes("w-full items-start justify-between gap-4"):
                with ui.column().classes("gap-1"):
                    ui.label("People Directory").classes("welcome-title")
                    ui.label(
                        "Manage approvers once, then reuse them in searchable report "
                        "workflow dropdowns."
                    ).classes("welcome-subtitle")
                with ui.row().classes("gap-2"):
                    ui.button(
                        "Download prefilled import workbook",
                        icon="download",
                        on_click=lambda: ui.download(
                            _people_import_template(),
                            "QC_People_Import_Template.xlsx",
                        ),
                    ).props("outline no-caps")
                    ui.button(
                        "Add person",
                        icon="person_add",
                        on_click=lambda: _open_person_dialog(
                            directory,
                            current_user,
                            None,
                        ),
                    ).props("unelevated no-caps").classes("primary-action")

            render_entra_import(actor.id)

            with ui.card().classes("w-full q-mt-lg"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("Directory").classes("section-title")
                    search = (
                        ui.input(placeholder="Search name, email or employee code")
                        .props("outlined dense clearable")
                        .classes("w-80")
                    )
                rows = [_person_row(person) for person in people]
                table = ui.table(
                    rows=rows,
                    columns=_directory_columns(),
                    row_key="id",
                    selection="single",
                    pagination={"rowsPerPage": 15},
                ).classes("w-full")
                table.bind_filter_from(search, "value")

                async def edit_selected() -> None:
                    if not table.selected:
                        ui.notify(
                            "Select one person to edit.",
                            type="warning",
                            position="top-right",
                        )
                        return
                    selected = people_by_id.get(str(table.selected[0]["id"]))
                    if selected is not None:
                        _open_person_dialog(directory, current_user, selected)

                ui.button("Edit selected", icon="edit", on_click=edit_selected).props(
                    "flat no-caps"
                )

            with ui.card().classes("w-full q-mt-lg"):
                ui.label("Bulk import").classes("section-title")
                ui.label(
                    "Upload the People sheet as XLSX or CSV. Nothing is saved until "
                    "the validation preview is confirmed."
                ).classes("welcome-subtitle")

                @ui.refreshable
                def import_preview() -> None:
                    preview = preview_state.get("current")
                    if preview is None:
                        return
                    with ui.row().classes("gap-4 q-mt-md"):
                        _metric("New", preview.inserted_count)
                        _metric("Updates", preview.updated_count)
                        _metric("Unchanged", preview.unchanged_count)
                        _metric("Warnings", preview.warning_count)
                        _metric("Errors", preview.error_count)
                    issue_rows = [
                        {
                            "row": row.row_number,
                            "field": issue.field_name or "—",
                            "severity": "Error" if issue.blocking else "Warning",
                            "message": issue.message,
                        }
                        for row in preview.rows
                        for issue in row.issues
                    ]
                    if issue_rows:
                        ui.table(
                            rows=issue_rows,
                            columns=[
                                {"name": "row", "label": "Row", "field": "row"},
                                {"name": "field", "label": "Field", "field": "field"},
                                {
                                    "name": "severity",
                                    "label": "Severity",
                                    "field": "severity",
                                },
                                {
                                    "name": "message",
                                    "label": "Message",
                                    "field": "message",
                                    "align": "left",
                                },
                            ],
                            pagination={"rowsPerPage": 10},
                        ).classes("w-full")

                    async def commit_import() -> None:
                        try:
                            await PeopleImportService().commit(
                                preview, uploaded_by_user_id=actor.id
                            )
                        except (PermissionError, ValueError) as error:
                            ui.notify(str(error), type="negative", position="top-right")
                            return
                        ui.notify(
                            "People directory import completed.",
                            type="positive",
                            position="top-right",
                        )
                        ui.navigate.to(ROUTE)

                    commit_button = (
                        ui.button(
                            "Confirm import",
                            icon="check",
                            on_click=commit_import,
                        )
                        .props("unelevated no-caps")
                        .classes("primary-action q-mt-md")
                    )
                    if not preview.can_commit:
                        commit_button.disable()

                async def uploaded(event: events.UploadEventArguments) -> None:
                    try:
                        preview_state["current"] = await PeopleImportService().preview(
                            event.file.name,
                            await event.file.read(),
                        )
                    except (ValueError, OSError) as error:
                        ui.notify(str(error), type="negative", position="top-right")
                        return
                    import_preview.refresh()

                ui.upload(
                    label="Upload People XLSX or CSV",
                    on_upload=uploaded,
                    auto_upload=True,
                    max_file_size=5_000_000,
                ).props('accept=".xlsx,.csv"').classes("w-full q-mt-md")
                import_preview()


async def _ensure_directory_actor(
    directory: DirectoryService, current_user: User
) -> DirectoryPerson:
    return await directory.ensure_profile_person(current_user)


def _open_person_dialog(
    directory: DirectoryService,
    profile: User,
    person: DirectoryPerson | None,
) -> None:
    dialog = ui.dialog()
    with dialog, ui.card().classes("report-dialog"):
        ui.label("Edit person" if person else "Add person").classes("report-dialog__title")
        employee_code = ui.input(
            "Employee Code", value=person.employee_code if person else ""
        ).props("outlined")
        display_name = ui.input("Full Name", value=person.display_name if person else "").props(
            "outlined"
        )
        email = ui.input("Email", value=person.email if person else "").props("outlined type=email")
        department = ui.input("Department", value=person.department if person else "").props(
            "outlined"
        )
        roles = ui.select(
            options=ROLE_CATALOG,
            value=list(person.roles) if person else [],
            label="Roles",
            multiple=True,
            with_input=True,
        ).props("outlined use-chips")
        icai = ui.input(
            "ICAI Membership Number",
            value=person.icai_membership_number if person else "",
        ).props("outlined")
        active = ui.checkbox("Active", value=person.active if person else True)

        async def save() -> None:
            try:
                payload = PersonUpsert(
                    employee_code=str(employee_code.value or ""),
                    display_name=str(display_name.value or ""),
                    email=str(email.value or ""),
                    department=str(department.value or "") or None,
                    roles=tuple(roles.value or ()),
                    icai_membership_number=str(icai.value or "") or None,
                    active=bool(active.value),
                )
                await directory.save_people((payload,), profile=profile)
            except (ValidationError, ValueError) as error:
                ui.notify(str(error), type="negative", position="top-right")
                return
            dialog.close()
            ui.notify("Person saved.", type="positive", position="top-right")
            ui.navigate.to(ROUTE)

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            ui.button("Save", icon="save", on_click=save).props("unelevated no-caps").classes(
                "primary-action"
            )
    dialog.open()


def _person_row(person: DirectoryPerson) -> dict[str, Any]:
    return {
        "id": str(person.id),
        "employee_code": person.employee_code,
        "name": person.display_name,
        "email": person.email,
        "roles": ", ".join(role.replace("_", " ").title() for role in person.roles),
        "icai": person.icai_membership_number or "—",
        "status": "Active" if person.active else "Inactive",
    }


def _directory_columns() -> list[dict[str, str]]:
    return [
        {"name": "employee_code", "label": "Employee Code", "field": "employee_code"},
        {"name": "name", "label": "Name", "field": "name", "align": "left"},
        {"name": "email", "label": "Email", "field": "email", "align": "left"},
        {"name": "roles", "label": "Roles", "field": "roles", "align": "left"},
        {"name": "icai", "label": "ICAI", "field": "icai"},
        {"name": "status", "label": "Status", "field": "status"},
    ]


def _metric(label: str, value: int) -> None:
    with ui.column().classes("gap-0"):
        ui.label(str(value)).classes("text-h6")
        ui.label(label).classes("text-caption")


def _people_import_template() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "People"
    headers = (
        "employee_code",
        "full_name",
        "email",
        "department",
        "roles",
        "icai_membership_number",
        "active",
    )
    sheet.append(headers)
    sheet.append(
        (
            "EMP-001",
            "Example Partner",
            "partner@example.com",
            "Audit",
            "partner",
            "123456",
            "yes",
        )
    )
    sheet.append(
        (
            "EMP-002",
            "Example Manager",
            "manager@example.com",
            "Audit",
            "manager;director",
            "",
            "yes",
        )
    )
    try:
        for index, partner in enumerate(get_report_definition_service().load().partners, start=1):
            sheet.append(
                (
                    f"PARTNER-{index:03d}",
                    partner.name,
                    "",
                    "Audit",
                    "partner",
                    partner.icai_membership_number,
                    "yes",
                )
            )
    except (OSError, ValueError):
        pass
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = 28
    instructions = workbook.create_sheet("Instructions")
    instructions.append(("People directory bulk import",))
    instructions.append(
        (
            "Partner names and ICAI numbers are prefilled from the controlled Word source. "
            "Replace the placeholder employee codes and enter a unique work email before upload.",
        )
    )
    instructions.append(
        (
            "Roles may be separated with semicolons, for example manager;director. "
            "Duplicate display names are allowed; employee code, email and ICAI "
            "number must be unique.",
        )
    )
    instructions.column_dimensions["A"].width = 120
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _render_access_denied() -> None:
    with ui.element("main").classes("app-main"):
        with ui.column().classes("placeholder-page items-center text-center"):
            ui.icon("block").classes("placeholder-page__icon")
            ui.label("Access denied").classes("welcome-title")
            ui.label("User management requires the user.manage permission.").classes(
                "welcome-subtitle"
            )
