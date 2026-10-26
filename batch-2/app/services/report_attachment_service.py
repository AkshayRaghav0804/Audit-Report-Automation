"""Persistence and validation for report-level supporting files."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.actor import ApplicationUser
from app.database.models.enums import ReportStatus, RevisionStatus
from app.database.models.mixins import utc_now
from app.database.models.report import Report, ReportAttachment, ReportRevision
from app.database.models.template import ReportTemplate, ReportTemplateVersion
from app.database.session import DatabaseManager, database

CARO_ATTACHMENT = "caro_file"
IFC_ATTACHMENT = "ifc_file"
ATTACHMENT_LABELS = {
    CARO_ATTACHMENT: "Legacy CARO file",
    IFC_ATTACHMENT: "Legacy IFC file",
}
ATTACHMENT_KINDS = frozenset(ATTACHMENT_LABELS)
ALLOWED_EXTENSIONS = frozenset({".pdf", ".doc", ".docx"})
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_ATTACHMENT_BYTES = 10_000_000


@dataclass(frozen=True, slots=True)
class ReportAttachmentMetadata:
    kind: str
    file_name: str
    content_type: str
    file_size: int


class ReportAttachmentService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def list_for_report(
        self, public_id: str, template_key: str
    ) -> dict[str, ReportAttachmentMetadata]:
        async with self.manager.session() as session:
            report = await _report_for_key(session, public_id, template_key)
            revision = await _current_revision(session, report)
            rows = await session.scalars(
                select(ReportAttachment).where(ReportAttachment.report_revision_id == revision.id)
            )
            return {
                row.attachment_kind: ReportAttachmentMetadata(
                    kind=row.attachment_kind,
                    file_name=row.file_name,
                    content_type=row.content_type,
                    file_size=row.file_size,
                )
                for row in rows
            }

    async def save(
        self,
        *,
        public_id: str,
        template_key: str,
        kind: str,
        file_name: str,
        content_type: str,
        content: bytes,
        actor_email: str,
    ) -> ReportAttachmentMetadata:
        normalized_name = re.split(r"[\\/]", str(file_name))[-1].strip()
        _validate_upload(kind, normalized_name, content)
        del content_type  # Browser-provided MIME types are not authoritative.
        normalized_type = CONTENT_TYPES[Path(normalized_name).suffix.casefold()]
        async with self.manager.session() as session:
            async with session.begin():
                actor = await session.scalar(
                    select(ApplicationUser).where(
                        ApplicationUser.email == actor_email.casefold(),
                        ApplicationUser.active.is_(True),
                    )
                )
                if actor is None:
                    raise ValueError("The current user is not active in the people directory.")
                report = await _report_for_key(session, public_id, template_key)
                if actor.id not in {report.created_by_user_id, report.owner_user_id}:
                    raise PermissionError("Only the report owner can upload supporting files.")
                if report.status not in {ReportStatus.DRAFT, ReportStatus.RETURNED}:
                    raise ValueError(
                        "Supporting files cannot be changed while the report is in review."
                    )
                revision = await _current_revision(session, report)
                if revision.status is not RevisionStatus.EDITABLE:
                    raise ValueError("The current report revision is not editable.")
                attachment = await session.scalar(
                    select(ReportAttachment).where(
                        ReportAttachment.report_revision_id == revision.id,
                        ReportAttachment.attachment_kind == kind,
                    )
                )
                if attachment is None:
                    attachment = ReportAttachment(
                        report_revision_id=revision.id,
                        attachment_kind=kind,
                        file_name=normalized_name,
                        content_type=normalized_type,
                        file_size=len(content),
                        content=content,
                        uploaded_by_user_id=actor.id,
                    )
                    session.add(attachment)
                else:
                    attachment.file_name = normalized_name
                    attachment.content_type = normalized_type
                    attachment.file_size = len(content)
                    attachment.content = content
                    attachment.uploaded_by_user_id = actor.id
                report.last_activity_at = utc_now()
        return ReportAttachmentMetadata(
            kind=kind,
            file_name=normalized_name,
            content_type=normalized_type,
            file_size=len(content),
        )


def required_attachment_fields(
    answers: dict[str, object], present_kinds: set[str] | frozenset[str]
) -> tuple[str, ...]:
    """Retain the legacy API while attachments cease to block submission.

    CARO and IFCR are now first-class conditional report workspaces. Existing
    stored attachments remain readable data, but new reports no longer require
    either file in order to enter the approval workflow.
    """

    del answers, present_kinds
    return ()


def _validate_upload(kind: str, file_name: str, content: bytes) -> None:
    if kind not in ATTACHMENT_KINDS:
        raise ValueError("Unknown supporting-file type.")
    if not file_name:
        raise ValueError("The uploaded file must have a name.")
    if len(file_name) > 255 or any(ord(character) < 32 for character in file_name):
        raise ValueError("The uploaded filename is invalid or too long.")
    extension = Path(file_name).suffix.casefold()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Upload a PDF, DOC, or DOCX file.")
    if not content:
        raise ValueError("The uploaded file is empty.")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError("The uploaded file must be 10 MB or smaller.")
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("The uploaded file is not a valid PDF document.")
    if extension == ".doc" and not content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ValueError("The uploaded file is not a valid Word document.")
    if extension == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                members = set(archive.namelist())
        except (OSError, zipfile.BadZipFile) as error:
            raise ValueError("The uploaded file is not a valid DOCX document.") from error
        if not {"[Content_Types].xml", "word/document.xml"}.issubset(members):
            raise ValueError("The uploaded file is not a valid DOCX document.")


async def _report_for_key(session: AsyncSession, public_id: str, template_key: str) -> Report:
    report = await session.scalar(
        select(Report)
        .join(ReportTemplateVersion, ReportTemplateVersion.id == Report.template_version_id)
        .join(ReportTemplate, ReportTemplate.id == ReportTemplateVersion.template_id)
        .where(Report.public_id == public_id, ReportTemplate.key == template_key)
    )
    if report is None:
        raise ValueError("This report draft does not exist or uses another template.")
    return report


async def _current_revision(session: AsyncSession, report: Report) -> ReportRevision:
    revision = await session.scalar(
        select(ReportRevision).where(
            ReportRevision.report_id == report.id,
            ReportRevision.revision_number == report.current_revision_number,
        )
    )
    if revision is None:
        raise RuntimeError("The report has no current revision.")
    return revision
