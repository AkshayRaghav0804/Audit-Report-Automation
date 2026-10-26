"""Auditable master-data import batches and row validation errors."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.enums import ImportKind, ImportStatus
from app.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class MasterDataImportBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "master_data_import_batches"

    kind: Mapped[ImportKind] = mapped_column(
        Enum(
            ImportKind,
            name="master_data_import_kind",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[ImportStatus] = mapped_column(
        Enum(
            ImportStatus,
            name="master_data_import_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    uploaded_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False
    )
    preview_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    inserted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MasterDataImportError(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "master_data_import_errors"

    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("master_data_import_batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sheet_name: Mapped[str] = mapped_column(String(120), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(120))
    error_code: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    raw_row_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
