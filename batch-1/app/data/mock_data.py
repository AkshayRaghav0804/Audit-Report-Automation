"""Typed mock data kept outside page-level rendering code."""

from __future__ import annotations

from datetime import date, datetime

from app.models.notification import Notification
from app.models.report import AttentionItem, DashboardMetric, Report, WorkflowStage
from app.models.user import User

MOCK_CURRENT_USER = User(
    id="USR-001",
    name="Aarav Mehta",
    email="aarav.mehta@example.com",
    role="Partner",
    permissions=frozenset(
        {
            "report.review",
            "report.approve",
            "report.comment",
            "report.fixed_section.edit",
        }
    ),
)

MOCK_METRICS = (
    DashboardMetric("Active Reports", 34, "Across all active workflows", "description", "blue"),
    DashboardMetric("Pending My Approval", 6, "2 are due today", "approval", "amber"),
    DashboardMetric("Reports in Review", 11, "Across Partner, QC and EQCR", "rate_review", "teal"),
    DashboardMetric("Approved This Month", 18, "4 more than last month", "task_alt", "green"),
)

MOCK_ATTENTION_ITEMS = (
    AttentionItem(
        "RPT-2026-001",
        "Statutory Audit Report",
        "ABC Industries",
        "Aarav Mehta",
        "Partner approval required",
        date(2026, 7, 12),
        "Partner Review",
        "High",
        ("Open", "Review", "Approve"),
    ),
    AttentionItem(
        "RPT-2026-014",
        "Tax Audit Report",
        "Meridian Exports",
        "Priya Shah",
        "Respond to reviewer comments",
        date(2026, 7, 14),
        "Returned for Changes",
        "High",
        ("Open", "Review"),
    ),
    AttentionItem(
        "RPT-2026-021",
        "Compliance Report",
        "Northstar Services",
        "Rohan Iyer",
        "Review revised wording",
        date(2026, 7, 16),
        "QC Review",
        "Medium",
        ("Open", "Review"),
    ),
    AttentionItem(
        "RPT-2026-028",
        "Limited Review Report",
        "Horizon Technologies",
        "Neha Kapoor",
        "Final sign-off required",
        date(2026, 7, 18),
        "EQCR Review",
        "Normal",
        ("Open", "Approve"),
    ),
)

MOCK_RECENT_REPORTS = (
    Report(
        "RPT-2026-031",
        "Independent Auditor's Report",
        "Ardent Manufacturing",
        "Statutory Audit Report",
        "Draft",
        datetime(2026, 7, 14, 10, 35),
        "Neha Kapoor",
    ),
    Report(
        "RPT-2026-001",
        "Annual Statutory Audit",
        "ABC Industries",
        "Statutory Audit Report",
        "Partner Review",
        datetime(2026, 7, 14, 9, 20),
        "Priya Shah",
    ),
    Report(
        "RPT-2026-025",
        "Quarterly Limited Review",
        "Horizon Technologies",
        "Limited Review Report",
        "QC Review",
        datetime(2026, 7, 13, 17, 45),
        "Rohan Iyer",
    ),
    Report(
        "RPT-2026-019",
        "FY 2025-26 Tax Audit",
        "Meridian Exports",
        "Tax Audit Report",
        "Returned for Changes",
        datetime(2026, 7, 13, 15, 10),
        "Kavya Nair",
    ),
    Report(
        "RPT-2026-009",
        "Regulatory Compliance Review",
        "Northstar Services",
        "Compliance Report",
        "Approved",
        datetime(2026, 7, 12, 12, 5),
        "Vikram Rao",
    ),
)

MOCK_WORKFLOW_STAGES = (
    WorkflowStage("Draft", 8, "edit_note"),
    WorkflowStage("Partner Review", 6, "approval"),
    WorkflowStage("QC Review", 3, "fact_check"),
    WorkflowStage("EQCR Review", 2, "verified"),
    WorkflowStage("Approved", 18, "task_alt"),
)

MOCK_NOTIFICATIONS = (
    Notification(
        "NTF-001",
        "comment_added",
        "Comment added",
        "Priya Shah commented on Basis for Opinion.",
        "10 minutes ago",
        report_id="RPT-2026-001",
        section_id="basis_for_opinion",
        comment_id="CMT-028",
    ),
    Notification(
        "NTF-002",
        "report_submitted",
        "Report submitted for approval",
        "ABC Industries is ready for your partner approval.",
        "35 minutes ago",
        report_id="RPT-2026-001",
    ),
    Notification(
        "NTF-003",
        "mention",
        "You were mentioned",
        "Rohan Iyer mentioned you in a comment on Key Audit Matters.",
        "1 hour ago",
        report_id="RPT-2026-025",
        section_id="key_audit_matters",
        comment_id="CMT-031",
    ),
    Notification(
        "NTF-004",
        "report_returned",
        "Report returned for changes",
        "Meridian Exports requires updates before review can continue.",
        "2 hours ago",
        report_id="RPT-2026-019",
    ),
    Notification(
        "NTF-005",
        "approval_stage_changed",
        "Approval stage changed",
        "Horizon Technologies moved from Manager Review to QC Review.",
        "Yesterday",
        report_id="RPT-2026-025",
    ),
    Notification(
        "NTF-006",
        "report_approved",
        "Report approved",
        "Northstar Services received final approval.",
        "Yesterday",
        is_read=True,
        report_id="RPT-2026-009",
    ),
    Notification(
        "NTF-007",
        "report_rejected",
        "Report rejected",
        "The certification report was rejected with reviewer notes.",
        "2 days ago",
        report_id="RPT-2026-017",
    ),
    Notification(
        "NTF-008",
        "comment_reply",
        "Reply added to comment",
        "Kavya Nair replied to your comment on Management Responsibility.",
        "2 days ago",
        is_read=True,
        report_id="RPT-2026-019",
        section_id="management_responsibility",
        comment_id="CMT-024",
    ),
    Notification(
        "NTF-009",
        "reviewer_assigned",
        "Reviewer assigned",
        "You were assigned as Partner reviewer for Ardent Manufacturing.",
        "3 days ago",
        is_read=True,
        report_id="RPT-2026-031",
    ),
)

MOCK_PEOPLE = ("Aarav Mehta", "Priya Shah", "Rohan Iyer", "Neha Kapoor", "Kavya Nair", "Vikram Rao")
