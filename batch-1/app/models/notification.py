"""Notification model shared by the header and notifications page."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Notification:
    notification_id: str
    notification_type: str
    title: str
    message: str
    created_at: str
    is_read: bool = False
    report_id: str | None = None
    section_id: str | None = None
    comment_id: str | None = None
    target_path: str | None = None

    @property
    def is_approval(self) -> bool:
        return self.notification_type in {
            "report_submitted",
            "report_approved",
            "reviewer_assigned",
            "approval_stage_changed",
        }

    @property
    def is_comment(self) -> bool:
        return self.notification_type in {"comment_added", "mention", "comment_reply"}
