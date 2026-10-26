"""Durable Microsoft Graph mail delivery for queued workflow notifications."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

import msal
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database.models.actor import ApplicationUser
from app.database.models.approval import ApprovalCycle, ReportApprovalStage, ReportStageAssignment
from app.database.models.enums import (
    ApprovalCycleStatus,
    ApprovalStageStatus,
    AssignmentStatus,
    ReportStatus,
)
from app.database.models.mixins import utc_now
from app.database.models.notification import EmailOutbox
from app.database.models.report import Report, ReportRevision
from app.database.session import DatabaseManager, database
from app.services.notification_service import email_bodies

LOGGER = logging.getLogger(__name__)
GRAPH_SCOPE = ("https://graph.microsoft.com/.default",)
MAX_ATTEMPTS = 5
CLAIM_LEASE = timedelta(minutes=5)


class MicrosoftGraphMailDispatcher:
    def __init__(
        self,
        manager: DatabaseManager = database,
        settings: Settings | None = None,
    ) -> None:
        self.manager = manager
        self.settings = settings or Settings.from_environment()
        self._stop = asyncio.Event()

    async def run(self) -> None:
        if not self.settings.graph_mail_configured:
            LOGGER.warning(
                "Approval email delivery is disabled: configure the Microsoft client ID, "
                "client secret and tenant ID, and disable demo authentication. "
                "Queued emails remain in email_outbox."
            )
            return
        while not self._stop.is_set():
            try:
                await self.process_pending()
            except Exception:
                LOGGER.exception("Unexpected Microsoft Graph email dispatcher failure")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30)
            except TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()

    async def process_pending(self, *, limit: int = 20) -> int:
        if not self.settings.graph_mail_configured:
            return 0
        now = utc_now()
        message_ids = await self._claim_pending(now=now, limit=limit)
        sent = 0
        token: str | None = None
        for message_id in message_ids:
            async with self.manager.session() as session:
                message = await session.get(EmailOutbox, message_id)
                if message is None or message.status != "processing":
                    continue
                if not await _approval_still_current(session, message):
                    message.status = "superseded"
                    message.last_error = (
                        "This reviewer no longer has an active approval assignment."
                    )
                    await session.commit()
                    continue
                sender_email = self.settings.microsoft_graph_sender_email
                if not sender_email:
                    sender_email = await session.scalar(
                        select(ApplicationUser.email)
                        .join(Report, Report.created_by_user_id == ApplicationUser.id)
                        .where(Report.id == message.report_id)
                    )
                public_id = await session.scalar(
                    select(Report.public_id).where(Report.id == message.report_id)
                )
                # Queued emails may predate the deployment's public URL setting.
                # Rebuild our generated link without changing the recorded message.
                copy, separator, _old_url = message.text_body.rpartition(
                    "\n\nOpen the secure report: "
                )
                if public_id and separator:
                    message.text_body, message.html_body = email_bodies(
                        copy, public_id, self.settings.application_base_url
                    )
            try:
                if not sender_email:
                    raise RuntimeError(
                        "No report creator mailbox is available. Configure "
                        "MICROSOFT_GRAPH_SENDER_EMAIL for these notifications."
                    )
                if token is None:
                    token = await asyncio.to_thread(self._access_token)
                await asyncio.to_thread(self._send, token, message, sender_email=sender_email)
            except (OSError, RuntimeError) as error:
                attempt_count = message.attempt_count + 1
                async with self.manager.session() as session:
                    async with session.begin():
                        await session.execute(
                            update(EmailOutbox)
                            .where(
                                EmailOutbox.id == message_id,
                                EmailOutbox.status == "processing",
                            )
                            .values(
                                status="failed",
                                attempt_count=attempt_count,
                                last_error=str(error)[:2000],
                                available_at=now + timedelta(minutes=min(60, 2**attempt_count)),
                            )
                        )
                LOGGER.warning(
                    "Microsoft Graph email delivery failed for outbox %s: %s",
                    message.id,
                    error,
                )
            else:
                async with self.manager.session() as session:
                    async with session.begin():
                        updated_id = await session.scalar(
                            update(EmailOutbox)
                            .where(
                                EmailOutbox.id == message_id,
                                EmailOutbox.status == "processing",
                            )
                            .values(
                                status="sent",
                                attempt_count=message.attempt_count + 1,
                                sent_at=utc_now(),
                                last_error=None,
                                text_body=message.text_body,
                                html_body=message.html_body,
                            )
                            .returning(EmailOutbox.id)
                        )
                sent += int(updated_id is not None)
        return sent

    async def _claim_pending(self, *, now: datetime, limit: int) -> tuple[Any, ...]:
        """Lease ready messages so concurrent application workers cannot send them together."""

        async with self.manager.session() as session:
            async with session.begin():
                candidates = tuple(
                    await session.scalars(
                        select(EmailOutbox.id)
                        .where(
                            EmailOutbox.status.in_(("pending", "failed", "processing")),
                            EmailOutbox.attempt_count < MAX_ATTEMPTS,
                            EmailOutbox.available_at <= now,
                        )
                        .order_by(EmailOutbox.created_at)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                claimed: list[Any] = []
                for message_id in candidates:
                    claimed_id = await session.scalar(
                        update(EmailOutbox)
                        .where(
                            EmailOutbox.id == message_id,
                            EmailOutbox.status.in_(("pending", "failed", "processing")),
                            EmailOutbox.attempt_count < MAX_ATTEMPTS,
                            EmailOutbox.available_at <= now,
                        )
                        .values(status="processing", available_at=now + CLAIM_LEASE)
                        .returning(EmailOutbox.id)
                    )
                    if claimed_id is not None:
                        claimed.append(message_id)
                return tuple(claimed)

    def _access_token(self) -> str:
        client_id = self.settings.microsoft_client_id
        client_secret = self.settings.microsoft_client_secret
        tenant_id = self.settings.microsoft_tenant_id
        if not client_id or not client_secret or not tenant_id:
            raise RuntimeError("Microsoft Graph authentication settings are incomplete.")
        client = msal.ConfidentialClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
        result: dict[str, Any] = client.acquire_token_for_client(scopes=list(GRAPH_SCOPE))
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            description = result.get("error_description") or result.get("error")
            raise RuntimeError(f"Unable to acquire Microsoft Graph token: {description}")
        return token

    def _send(self, token: str, message: EmailOutbox, *, sender_email: str | None = None) -> None:
        sender_email = sender_email or self.settings.microsoft_graph_sender_email
        if not sender_email:
            raise RuntimeError("Microsoft Graph sender email is not configured.")
        sender = quote(sender_email, safe="")
        endpoint = f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
        payload = {
            "message": {
                "subject": message.subject,
                "body": {"contentType": "HTML", "content": message.html_body},
                "toRecipients": [{"emailAddress": {"address": message.to_address}}],
            },
            "saveToSentItems": True,
        }
        request = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        # The origin and path are fixed here; only the path-escaped configured sender varies.
        with urlopen(request, timeout=20) as response:  # noqa: S310  # nosec B310
            if response.status not in {200, 202}:
                raise RuntimeError(f"Microsoft Graph returned unexpected status {response.status}.")


async def _approval_still_current(session: AsyncSession, message: EmailOutbox) -> bool:
    """Do not send obsolete approval requests when delivery resumes after an outage."""

    if not message.subject.startswith("Approval required:"):
        return True
    return bool(
        await session.scalar(
            select(ReportStageAssignment.id)
            .join(
                ReportApprovalStage,
                ReportApprovalStage.id == ReportStageAssignment.report_approval_stage_id,
            )
            .join(ApprovalCycle, ApprovalCycle.id == ReportApprovalStage.approval_cycle_id)
            .join(ReportRevision, ReportRevision.id == ApprovalCycle.report_revision_id)
            .join(Report, Report.id == ReportRevision.report_id)
            .join(ApplicationUser, ApplicationUser.id == ReportStageAssignment.user_id)
            .where(
                Report.id == message.report_id,
                Report.status == ReportStatus.IN_REVIEW,
                ReportRevision.revision_number == Report.current_revision_number,
                ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
                ReportApprovalStage.status == ApprovalStageStatus.ACTIVE,
                ReportApprovalStage.activated_at <= message.created_at,
                ReportStageAssignment.status == AssignmentStatus.ACTIVE,
                ApplicationUser.email == message.to_address,
                ApplicationUser.active.is_(True),
            )
            .limit(1)
        )
    )
