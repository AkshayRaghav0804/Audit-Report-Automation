"""Navigation metadata and notification deep-link creation."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from app.models.notification import Notification
from app.models.user import User
from app.services.permission_service import has_permission


@dataclass(frozen=True, slots=True)
class NavigationItem:
    label: str
    icon: str
    route: str
    permission: str | None = None


PRIMARY_NAVIGATION = (
    NavigationItem("Dashboard", "dashboard", "/dashboard"),
    NavigationItem("Reports", "description", "/reports"),
    NavigationItem("Create Report", "add_circle", "/dashboard?action=create", "report.create"),
    NavigationItem("Tasks & Approvals", "assignment_turned_in", "/tasks", "report.review"),
    NavigationItem("Templates", "library_books", "/admin/templates", "template.manage"),
)

ADMIN_NAVIGATION = (
    NavigationItem("Users", "group", "/admin/users", "user.manage"),
    NavigationItem("Roles & Permissions", "admin_panel_settings", "/admin/roles", "user.manage"),
    NavigationItem("Approval Workflows", "account_tree", "/admin/workflows", "workflow.manage"),
    NavigationItem("Audit Logs", "history", "/admin/audit-logs", "audit_log.view"),
)


def visible_items(user: User, items: tuple[NavigationItem, ...]) -> tuple[NavigationItem, ...]:
    return tuple(
        item for item in items if item.permission is None or has_permission(user, item.permission)
    )


def notification_target(notification: Notification) -> str:
    if notification.target_path:
        return notification.target_path
    if not notification.report_id:
        return "/notifications"
    query: dict[str, str] = {}
    if notification.is_comment:
        if notification.section_id:
            query["section"] = notification.section_id
        if notification.comment_id:
            query["comment"] = notification.comment_id
    elif notification.is_approval:
        query["view"] = "workflow"
    suffix = f"?{urlencode(query)}" if query else ""
    return f"/reports/{notification.report_id}{suffix}"
