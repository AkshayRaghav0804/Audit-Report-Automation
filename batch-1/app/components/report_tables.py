"""Accessible, horizontally scrollable report data tables."""

from __future__ import annotations

from datetime import date

from nicegui import ui

from app.components.status_badge import status_badge
from app.models.report import AttentionItem, Report
from app.models.user import User
from app.services.permission_service import may_perform_report_action


def attention_table(items: tuple[AttentionItem, ...], user: User) -> None:
    if not items:
        _empty_state("No actions require your attention", "You are up to date with report reviews.")
        return
    priority_order = {"High": 0, "Medium": 1, "Normal": 2}
    ordered = sorted(
        items,
        key=lambda item: (
            item.due_date is None,
            item.due_date is not None and item.due_date >= date.today(),
            priority_order.get(item.priority, 3),
            item.due_date or date.max,
        ),
    )
    with ui.element("div").classes("data-table-scroll"):
        with ui.element("div").classes("data-table attention-table").props('role="table"'):
            _table_header(
                (
                    "Report",
                    "Client",
                    "Received From",
                    "Required Action",
                    "Due Date",
                    "Current Stage",
                    "Priority",
                    "Action",
                )
            )
            for item in ordered:
                with ui.element("div").classes("data-table__row").props('role="row"'):
                    with ui.element("div").classes("table-cell table-cell--primary"):
                        ui.link(item.report, f"/reports/{item.report_id}")
                        ui.label(item.report_id).classes("cell-supporting")
                    ui.label(item.client).classes("table-cell").props('data-label="Client"')
                    ui.label(item.received_from).classes("table-cell").props(
                        'data-label="Received from"'
                    )
                    ui.label(item.required_action).classes("table-cell").props(
                        'data-label="Required action"'
                    )
                    with ui.element("div").classes("table-cell").props('data-label="Due date"'):
                        ui.label(
                            item.due_date.strftime("%d %b %Y")
                            if item.due_date is not None
                            else "No due date"
                        )
                        if item.due_date is not None and item.due_date < date.today():
                            ui.label("Overdue").classes("overdue-label")
                    with (
                        ui.element("div").classes("table-cell").props('data-label="Current stage"')
                    ):
                        status_badge(item.current_stage)
                    with ui.element("div").classes("table-cell").props('data-label="Priority"'):
                        status_badge(item.priority)
                    with (
                        ui.row()
                        .classes("table-cell table-actions items-center no-wrap")
                        .props('data-label="Actions"')
                    ):
                        for action in item.permitted_actions:
                            if may_perform_report_action(user, action):
                                _report_action(item.report_id, action)


def recent_reports_table(
    reports: tuple[Report, ...], user: User, *, basic_actions_only: bool = False
) -> None:
    if not reports:
        _empty_state("No recent reports", "Reports you access will appear here.")
        return
    with ui.element("div").classes("data-table-scroll"):
        with ui.element("div").classes("data-table recent-table").props('role="table"'):
            _table_header(
                (
                    "Report Name",
                    "Client",
                    "Report Type",
                    "Status",
                    "Last Updated",
                    "Owner",
                    "More Actions",
                )
            )
            for report in reports:
                with ui.element("div").classes("data-table__row").props('role="row"'):
                    with ui.element("div").classes("table-cell table-cell--primary"):
                        ui.link(report.name, f"/reports/{report.report_id}")
                        ui.label(report.report_id).classes("cell-supporting")
                    ui.label(report.client).classes("table-cell").props('data-label="Client"')
                    ui.label(report.report_type).classes("table-cell").props(
                        'data-label="Report type"'
                    )
                    with ui.element("div").classes("table-cell").props('data-label="Status"'):
                        status_badge(report.status)
                    with ui.element("div").classes("table-cell").props('data-label="Last updated"'):
                        ui.label(report.last_updated.strftime("%d %b %Y"))
                        ui.label(report.last_updated.strftime("%I:%M %p")).classes(
                            "cell-supporting"
                        )
                    ui.label(report.owner).classes("table-cell").props('data-label="Owner"')
                    with ui.element("div").classes("table-cell").props('data-label="Actions"'):
                        _more_actions(report, user, basic_actions_only=basic_actions_only)


def _table_header(labels: tuple[str, ...]) -> None:
    with ui.element("div").classes("data-table__header").props('role="row"'):
        for label in labels:
            ui.label(label).classes("table-cell").props('role="columnheader"')


def _report_action(report_id: str, action: str) -> None:
    def selected() -> None:
        action_query = action.lower()
        ui.navigate.to(f"/reports/{report_id}?action={action_query}")

    props = "unelevated no-caps dense" if action == "Approve" else "flat no-caps dense"
    ui.button(action, on_click=selected).props(
        f'{props} aria-label="{action} {report_id}"'
    ).classes("table-action table-action--primary" if action == "Approve" else "table-action")


def _more_actions(report: Report, user: User, *, basic_actions_only: bool = False) -> None:
    with (
        ui.button(icon="more_vert")
        .props('flat round dense aria-label="More report actions"')
        .classes("more-actions-button")
    ):
        ui.tooltip("More actions")
        with ui.menu().classes("row-action-menu"):
            _menu_action(report, "Open Report", "open_in_new", True)
            _menu_action(report, "View Workflow", "account_tree", True)
            if basic_actions_only:
                return
            _menu_action(
                report, "Duplicate", "content_copy", may_perform_report_action(user, "Duplicate")
            )
            _menu_action(
                report, "Download", "download", may_perform_report_action(user, "Download")
            )
            _menu_action(report, "Archive", "archive", may_perform_report_action(user, "Archive"))


def _menu_action(report: Report, label: str, icon: str, permitted: bool) -> None:
    if not permitted:
        return

    def selected() -> None:
        if label == "Open Report":
            ui.navigate.to(f"/reports/{report.report_id}")
        elif label == "View Workflow":
            ui.navigate.to(f"/reports/{report.report_id}?view=workflow")
        else:
            ui.notify(f"{label} requested for {report.report_id}", position="top-right")

    with ui.button(on_click=selected).props("flat no-caps").classes("row-action-menu__item"):
        ui.icon(icon)
        ui.label(label)


def _empty_state(title: str, message: str) -> None:
    with ui.column().classes("empty-state items-center"):
        ui.icon("inbox")
        ui.label(title).classes("empty-state__title")
        ui.label(message).classes("empty-state__message")
