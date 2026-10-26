"""Administrator controls for previewing and confirming an Entra employee import."""

from __future__ import annotations

import logging
from uuid import UUID

from nicegui import ui

from app.services.entra_directory_service import (
    EntraDirectoryService,
    EntraImportError,
    EntraImportPreview,
)

LOGGER = logging.getLogger(__name__)


def render_entra_import(actor_id: UUID) -> None:
    service = EntraDirectoryService()
    preview: EntraImportPreview | None = None
    busy = False

    with ui.card().classes("w-full q-mt-lg"):
        ui.label("Import from Microsoft Entra ID").classes("section-title")
        ui.label(
            "Add employees before they first sign in. Preview the list, then confirm "
            "the import. New employees receive the Employee role."
        ).classes("welcome-subtitle")
        if service.settings.entra_employee_group_id:
            ui.label("Source: direct employee members of the configured Entra group.").classes(
                "text-sm text-gray-600"
            )
        else:
            ui.label(
                f"Source: enabled company accounts in "
                f"{service.settings.allowed_microsoft_domain or 'your configured domain'}. "
                "Ask your administrator to configure an employee group to narrow this list."
            ).classes("text-sm text-gray-600")
        ui.label(
            "Existing approval roles and ICAI details are preserved. Guest and disabled "
            "Microsoft accounts are excluded. Existing inactive or unassigned application "
            "accounts are left unchanged."
        ).classes("text-sm text-gray-600")
        if not service.configured:
            ui.label(
                "Entra import is unavailable until Microsoft sign-in is configured and demo "
                "sign-in is disabled. Your administrator must also grant directory read permission."
            ).classes("text-amber-900")
        status = ui.label("").props('role="status" aria-live="polite"').mark("entra-import-status")

        @ui.refreshable
        def render_preview() -> None:
            if preview is None:
                return
            ui.label(
                f"{preview.count('insert')} new · {preview.count('update')} updates · "
                f"{preview.count('unchanged')} unchanged · {preview.count('skip')} skipped · "
                f"{preview.count('error')} conflicts · {preview.excluded_count} excluded"
            ).classes("font-medium")
            if not preview.rows:
                ui.label("No eligible employees were found in the configured Entra source.")
                return
            search = ui.input("Search preview").props("outlined dense clearable").classes("w-full")
            table = (
                ui.table(
                    columns=[
                        {"name": "name", "label": "Employee", "field": "name", "align": "left"},
                        {"name": "email", "label": "Work email", "field": "email", "align": "left"},
                        {"name": "action", "label": "Action", "field": "action", "align": "left"},
                        {"name": "detail", "label": "Details", "field": "detail", "align": "left"},
                    ],
                    rows=[
                        {
                            "id": row.employee.object_id,
                            "name": row.employee.person.display_name,
                            "email": row.employee.person.email,
                            "action": row.action.title(),
                            "detail": row.detail,
                        }
                        for row in preview.rows
                    ],
                    row_key="id",
                    pagination={"rowsPerPage": 10},
                )
                .classes("w-full")
                .props("wrap-cells")
            )
            table.bind_filter_from(search, "value")
            ui.label(
                "Assign Partner, QC and EQCR roles in the directory after import. "
                "Run this import again when new employees join."
            ).classes("text-sm text-gray-600")

        async def fetch() -> None:
            nonlocal preview, busy
            if busy:
                return
            busy = True
            preview = None
            render_preview.refresh()
            fetch_button.disable()
            confirm_button.disable()
            status.set_text("Reading employees from Microsoft Entra…")
            try:
                preview = await service.preview(actor_id=actor_id)
                status.set_text("Preview ready. No employees have been saved yet.")
                render_preview.refresh()
            except EntraImportError as error:
                status.set_text(str(error))
            except Exception:
                LOGGER.exception("Entra employee preview failed")
                status.set_text("The employee preview could not be prepared. Please try again.")
            finally:
                busy = False
                fetch_button.enable()
                confirm_button.set_enabled(bool(preview and preview.can_commit))

        async def confirm() -> None:
            nonlocal preview, busy
            if busy or preview is None:
                return
            busy = True
            fetch_button.disable()
            confirm_button.disable()
            status.set_text("Saving employees…")
            try:
                counts = await service.commit(preview, actor_id=actor_id)
                ui.notify(
                    f"Entra import completed: {counts['insert']} added, "
                    f"{counts['update']} updated.",
                    type="positive",
                    position="top-right",
                )
                ui.navigate.to("/admin/users")
            except EntraImportError as error:
                status.set_text(str(error))
            except Exception:
                LOGGER.exception("Entra employee import failed")
                status.set_text("The import could not be saved. Fetch a new preview and try again.")
            finally:
                # A retry always starts with fresh data, including after a lost response.
                preview = None
                render_preview.refresh()
                busy = False
                fetch_button.enable()

        with ui.row().classes("gap-2 flex-wrap"):
            fetch_button = (
                ui.button("Fetch Entra employees", icon="cloud_download", on_click=fetch)
                .props("outline no-caps")
                .mark("entra-fetch")
            )
            confirm_button = (
                ui.button("Confirm Entra import", icon="check", on_click=confirm)
                .props("unelevated no-caps")
                .mark("entra-confirm")
            )
        fetch_button.set_enabled(service.configured)
        confirm_button.disable()
        render_preview()
