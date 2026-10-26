"""Role catalog and directory membership administration."""

from uuid import UUID

from nicegui import ui

from app.components.header import add_application_head, render_header
from app.components.notification_menu import NotificationState
from app.components.route_guard import require_authentication
from app.components.sidebar import render_sidebar
from app.config import Settings
from app.pages.admin_people import _storage
from app.services.directory_service import DirectoryService
from app.services.permission_service import has_permission
from app.services.role_management_service import RoleManagementService


def register_admin_role_routes() -> None:
    @ui.page("/admin/roles", title="Roles & Permissions | QC Report Automation")
    async def admin_roles_page() -> None:
        user = await require_authentication("/admin/roles")
        if user is None:
            return
        add_application_head()
        drawer = render_sidebar(user, "/admin/roles")
        render_header(
            user,
            "Roles & Permissions",
            await NotificationState.for_user(user.email, _storage()),
            drawer,
        )
        with ui.element("main").classes("app-main"):
            ui.label("Roles & Permissions").classes("welcome-title")
            if not has_permission(user, "user.manage"):
                ui.label("You do not have permission to manage roles.")
                return
            directory = DirectoryService()
            if Settings.from_environment().demo_auth_enabled:
                await directory.ensure_profile_person(user)
            service = RoleManagementService()
            try:
                catalog = await service.catalog(user)
            except PermissionError as error:
                ui.label(str(error))
                return
            ui.label(
                "Application permission sets are fixed. Manage people's role memberships below."
            )
            ui.label(
                "Report approvals require an active assignment. Edit highlights follow the "
                "report's assigned role; the report creator uses the preparer color."
            )
            with ui.card().classes("w-full"):
                ui.table(
                    columns=[
                        {"name": key, "label": label, "field": key, "align": "left"}
                        for key, label in (
                            ("name", "Role"),
                            ("status", "Status"),
                            ("permissions", "Permissions"),
                        )
                    ],
                    rows=catalog,
                    row_key="key",
                ).classes("w-full").props("wrap-cells")
            people = await directory.list_people(active_only=True, limit=None)
            people_by_id = {str(person.id): person for person in people}
            with ui.card().classes("w-full"):
                ui.label("Assign roles").classes("section-title")
                person_select = (
                    ui.select(
                        {
                            key: f"{person.display_name} ({person.email})"
                            for key, person in people_by_id.items()
                        },
                        label="Person",
                        with_input=True,
                    )
                    .classes("w-full")
                    .props("outlined")
                )
                role_select = (
                    ui.select(
                        {row["key"]: row["name"] for row in catalog if row["status"] == "Active"},
                        label="Assigned roles",
                        multiple=True,
                        value=[],
                    )
                    .classes("w-full")
                    .props("outlined use-chips")
                )

                def load_roles() -> None:
                    person = people_by_id.get(str(person_select.value))
                    role_select.value = list(person.roles) if person else []

                person_select.on_value_change(load_roles)

                async def save() -> None:
                    if not person_select.value:
                        ui.notify("Select a person.", type="warning")
                        return
                    try:
                        await service.assign(
                            user, UUID(str(person_select.value)), list(role_select.value or [])
                        )
                    except (ValueError, PermissionError) as error:
                        ui.notify(str(error), type="negative")
                        return
                    updated = await directory.list_people(active_only=True, limit=None)
                    people_by_id.update({str(person.id): person for person in updated})
                    ui.notify(
                        "Roles saved. Access changes apply on the next page load.",
                        type="positive",
                    )

                ui.button("Save roles", icon="save", on_click=save).props("unelevated no-caps")
                ui.link("Manage people directory", "/admin/users")
