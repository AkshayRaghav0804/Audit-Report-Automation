"""Import all ORM models so Alembic can discover their metadata."""

from app.database.models.actor import ApplicationUser, ProfessionalCredential, Role, UserRole
from app.database.models.approval import (
    ApprovalAction,
    ApprovalCycle,
    ReportApprovalStage,
    ReportStageAssignment,
)
from app.database.models.import_data import MasterDataImportBatch, MasterDataImportError
from app.database.models.notification import EmailOutbox, UserNotification
from app.database.models.report import (
    Report,
    ReportAnswer,
    ReportAnswerHistory,
    ReportAttachment,
    ReportComment,
    ReportRevision,
    ReportSectionChange,
    ReportSectionContent,
    ReportSectionHeading,
    ReportSignatory,
    ReportWorkflowAssignment,
)
from app.database.models.section_access import ReportSectionLock, SectionEditRequest
from app.database.models.template import (
    QuestionDefinition,
    ReportTemplate,
    ReportTemplateVersion,
    TemplateQuestion,
    TemplateSection,
)
from app.database.models.workflow import (
    ApprovalStepDefinition,
    ApprovalWorkflow,
    ApprovalWorkflowVersion,
    ReportTemplateWorkflow,
)

__all__ = [
    "ReportSectionLock",
    "SectionEditRequest",
    "ApplicationUser",
    "ApprovalAction",
    "ApprovalCycle",
    "ApprovalStepDefinition",
    "ApprovalWorkflow",
    "ApprovalWorkflowVersion",
    "EmailOutbox",
    "MasterDataImportBatch",
    "MasterDataImportError",
    "ProfessionalCredential",
    "QuestionDefinition",
    "Report",
    "ReportAnswer",
    "ReportAnswerHistory",
    "ReportAttachment",
    "ReportComment",
    "ReportApprovalStage",
    "ReportRevision",
    "ReportSectionContent",
    "ReportSectionChange",
    "ReportSectionHeading",
    "ReportSignatory",
    "ReportWorkflowAssignment",
    "ReportStageAssignment",
    "ReportTemplate",
    "ReportTemplateVersion",
    "ReportTemplateWorkflow",
    "Role",
    "TemplateQuestion",
    "TemplateSection",
    "UserRole",
    "UserNotification",
]
