"""Administrative view of immutable, source-backed report template versions."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, cast

from nicegui import app, ui

from app.components.header import add_application_head, render_header
from app.components.notification_menu import NotificationState
from app.components.route_guard import require_authentication
from app.components.sidebar import render_sidebar
from app.services.permission_service import has_permission
from app.services.report_creation_service import ReportCreationService
from app.services.template_catalog_import_service import TemplateCatalogImportService
from app.services.template_section_lock_service import (
    TemplateSectionLockDefault,
    TemplateSectionLockService,
)

ROUTE = "/admin/templates"
LEGACY_ROUTE = "/templates"


def _storage() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], app.storage.user)


def register_admin_template_routes() -> None:
    @ui.page(LEGACY_ROUTE, title="Templates | QC Report Automation")
    async def legacy_template_page() -> None:
        user = await require_authentication(LEGACY_ROUTE)
        if user is None:
            return
        ui.navigate.to(ROUTE)

    @ui.page(ROUTE, title="Report Templates | QC Report Automation")
    async def admin_template_page() -> None:
        user = await require_authentication(ROUTE)
        if user is None:
            return
        add_application_head()
        drawer = render_sidebar(user, ROUTE)
        render_header(
            user,
            "Report Templates",
            await NotificationState.for_user(user.email, _storage()),
            drawer,
        )
        if not has_permission(user, "template.manage"):
            _access_denied()
            return

        context = await ReportCreationService().context_for(user)
        lock_service = TemplateSectionLockService()
        section_defaults = await lock_service.active_defaults(user)
        with ui.element("main").classes("app-main"):
            with ui.row().classes("w-full items-start justify-between gap-4"):
                with ui.column().classes("gap-1"):
                    ui.label("Controlled report templates").classes("welcome-title")
                    ui.label(
                        "Each extracted Word file is hashed and published as an immutable "
                        "database version. Existing reports remain pinned to their version."
                    ).classes("welcome-subtitle")

                async def refresh_sources() -> None:
                    try:
                        result = await TemplateCatalogImportService().import_current_sources(user)
                    except (OSError, PermissionError, ValueError) as error:
                        ui.notify(str(error), type="negative", position="top-right")
                        return
                    ui.notify(
                        f"Checked {result.template_count} templates; "
                        f"published {result.new_version_count} new version(s).",
                        type="positive",
                        position="top-right",
                    )
                    ui.navigate.to(ROUTE)

                ui.button(
                    "Refresh controlled sources",
                    icon="sync",
                    on_click=refresh_sources,
                ).props("unelevated no-caps").classes("primary-action")

            with ui.card().classes("w-full q-mt-lg"):
                ui.table(
                    rows=[
                        {
                            "name": template.name,
                            "key": template.key,
                            "version": template.version_number,
                            "questions": template.question_count,
                            "sections": template.section_count,
                            "status": "Active",
                        }
                        for template in context.templates
                    ],
                    columns=[
                        {
                            "name": "name",
                            "label": "Report Template",
                            "field": "name",
                            "align": "left",
                        },
                        {"name": "key", "label": "Stable Key", "field": "key"},
                        {"name": "version", "label": "Version", "field": "version"},
                        {"name": "questions", "label": "Questions", "field": "questions"},
                        {"name": "sections", "label": "Word Sections", "field": "sections"},
                        {"name": "status", "label": "Status", "field": "status"},
                    ],
                    row_key="key",
                    pagination={"rowsPerPage": 10},
                ).classes("w-full")

            with ui.card().classes("w-full q-mt-lg").mark("template-section-locks"):
                ui.label("Default section locks").classes("section-title")
                ui.label(
                    "Choose how each section starts when a new report is created. New reports "
                    "copy these defaults; existing reports keep their current lock settings."
                ).classes("section-subtitle")
                defaults_by_template: dict[str, list[TemplateSectionLockDefault]] = {}
                for section_default in section_defaults:
                    defaults_by_template.setdefault(section_default.template_key, []).append(
                        section_default
                    )
                template_options = {
                    template.key: f"{template.name} · Version {template.version_number}"
                    for template in context.templates
                }

                @ui.refreshable
                def render_section_defaults(template_key: str) -> None:
                    for section_default in defaults_by_template.get(template_key, []):
                        with ui.row().classes(
                            "w-full items-center justify-between gap-4 q-py-sm border-bottom"
                        ):
                            with ui.column().classes("gap-0"):
                                ui.label(section_default.section_title).classes(
                                    "text-weight-medium"
                                )
                                ui.label(
                                    "Locked by default"
                                    if section_default.locked
                                    else "Unlocked by default"
                                ).classes(
                                    "text-caption text-negative"
                                    if section_default.locked
                                    else "text-caption text-positive"
                                )

                            async def change_default(
                                item: TemplateSectionLockDefault = section_default,
                            ) -> None:
                                try:
                                    await lock_service.set_default(
                                        item.template_key,
                                        item.section_key,
                                        user,
                                        not item.locked,
                                    )
                                except (PermissionError, ValueError) as error:
                                    ui.notify(str(error), type="negative", position="top-right")
                                    return
                                state = "locked" if not item.locked else "unlocked"
                                ui.notify(
                                    f"New reports will start with {item.section_title} {state}.",
                                    type="positive",
                                    position="top-right",
                                )
                                ui.navigate.to(ROUTE)

                            ui.button(
                                "Set unlocked default"
                                if section_default.locked
                                else "Set locked default",
                                icon="lock_open" if section_default.locked else "lock",
                                on_click=change_default,
                            ).props("outline no-caps color=primary").mark(
                                f"template-section-lock-{section_default.template_key}-"
                                f"{section_default.section_key}"
                            )

                selected_template = next(iter(template_options), None)
                if selected_template is not None:
                    ui.select(
                        template_options,
                        value=selected_template,
                        label="Report template",
                        on_change=lambda event: render_section_defaults.refresh(event.value),
                    ).props("outlined dense").classes("w-full q-mt-md").mark(
                        "template-section-lock-template"
                    )
                    render_section_defaults(selected_template)
                else:
                    ui.label("No active report templates are available.").classes("text-grey-7")

            with ui.card().classes("w-full q-mt-lg"):
                ui.label("Adding a new report type").classes("section-title")
                ui.label(
                    "Add the approved report to the controlled master Word document, run the "
                    "existing extraction command, review the generated standalone DOCX and "
                    "manifest entry, then use Refresh controlled sources. This keeps formatting "
                    "and source hashes auditable."
                ).classes("section-subtitle")
                ui.code("python -m scripts.extract_report_templates")


def _access_denied() -> None:
    with ui.element("main").classes("app-main"):
        with ui.column().classes("placeholder-page items-center text-center"):
            ui.icon("block").classes("placeholder-page__icon")
            ui.label("Access denied").classes("welcome-title")
            ui.label("Template management requires template.manage.").classes("welcome-subtitle")
