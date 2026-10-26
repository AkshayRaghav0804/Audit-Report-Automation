"""Permission checks independent of UI and role names."""

from __future__ import annotations

from app.models.user import User


def has_permission(user: User, permission_name: str) -> bool:
    return permission_name in user.permissions


def may_perform_report_action(user: User, action: str) -> bool:
    if action == "Open":
        return any(
            has_permission(user, permission)
            for permission in ("report.create", "report.review", "report.view_all")
        )
    required_permission = {
        "Edit": "report.create",
        "Review": "report.review",
        "Approve": "report.approve",
        "Return": "report.approve",
        "Duplicate": "report.duplicate",
        "Download": "report.download",
        "Archive": "report.archive",
    }.get(action)
    return required_permission is not None and has_permission(user, required_permission)


def may_comment_on_report(user: User) -> bool:
    return has_permission(user, "report.comment")


def may_edit_fixed_section(user: User) -> bool:
    return has_permission(user, "report.fixed_section.edit")


def is_report_creator(user: User) -> bool:
    return has_permission(user, "report.create") and not has_permission(user, "report.view_all")


def is_report_reviewer(user: User) -> bool:
    return has_permission(user, "report.approve") and not has_permission(user, "report.view_all")


def reviewer_role_key(user: User) -> str | None:
    return {
        "Partner": "partner",
        "QC Reviewer": "qc",
        "EQCR Reviewer": "eqcr",
    }.get(user.role)
