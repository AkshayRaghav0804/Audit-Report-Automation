"""Central locations for the immutable Word and Excel source files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MASTER_TEMPLATE_SOURCE = PROJECT_ROOT / "Audit Reports March_2025-26.docx"
DEFAULT_TEMPLATE_SOURCE = PROJECT_ROOT / "report_templates" / "manifest.json"
DEFAULT_QUESTION_SOURCE = PROJECT_ROOT / "Basic Questions for Report Review Tool.xlsx"


@dataclass(frozen=True, slots=True)
class ReportSourceConfig:
    template_path: Path
    question_path: Path

    @classmethod
    def from_environment(cls) -> ReportSourceConfig:
        return cls(
            template_path=Path(
                os.getenv("QC_TEMPLATE_SOURCE", str(DEFAULT_TEMPLATE_SOURCE))
            ).expanduser(),
            question_path=Path(
                os.getenv("QC_QUESTION_SOURCE", str(DEFAULT_QUESTION_SOURCE))
            ).expanduser(),
        )
