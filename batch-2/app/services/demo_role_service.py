"""Temporary role definitions for the local prototype role switcher."""

from __future__ import annotations

DEMO_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "Employee": frozenset(
        {
            "report.create",
            "report.view_own",
            "report.review",
            "report.approve",
            "report.comment",
            "report.fixed_section.edit",
        }
    ),
    "Manager": frozenset(
        {
            "report.create",
            "report.view_own",
            "report.review",
            "report.approve",
            "report.comment",
            "report.fixed_section.edit",
        }
    ),
    "Director": frozenset(
        {
            "report.review",
            "report.approve",
            "report.comment",
            "report.fixed_section.edit",
        }
    ),
    "Partner": frozenset(
        {
            "report.review",
            "report.approve",
            "report.comment",
            "report.fixed_section.edit",
        }
    ),
    "QC Reviewer": frozenset(
        {
            "report.review",
            "report.approve",
            "report.comment",
            "report.fixed_section.edit",
        }
    ),
    "EQCR Reviewer": frozenset(
        {
            "report.review",
            "report.approve",
            "report.comment",
            "report.fixed_section.edit",
        }
    ),
    "Administrator": frozenset(
        {
            "report.create",
            "report.view_all",
            "report.review",
            "report.approve",
            "report.comment",
            "report.fixed_section.edit",
            "report.duplicate",
            "report.download",
            "report.archive",
            "template.manage",
            "user.manage",
            "workflow.manage",
            "audit_log.view",
        }
    ),
}

DEMO_ROLES = tuple(DEMO_ROLE_PERMISSIONS)


def permissions_for_demo_role(role: str) -> frozenset[str]:
    try:
        return DEMO_ROLE_PERMISSIONS[role]
    except KeyError as error:
        raise ValueError(f"Unknown demo role: {role}") from error
