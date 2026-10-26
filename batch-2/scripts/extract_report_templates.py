"""Split the controlled master Word file into standalone report templates."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZipInfo

from docx import Document
from lxml import etree

from app.services.document_template_service import (
    EXPECTED_REPORT_COUNT,
    REPORT_START,
    DocumentTemplateService,
    _normalized,
)
from app.source_config import DEFAULT_MASTER_TEMPLATE_SOURCE, DEFAULT_TEMPLATE_SOURCE

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NAMESPACES = {"w": W_NS}
DOCUMENT_PART = "word/document.xml"
TITLE_PATTERN = re.compile(r"^Report\s+([IVX]+)\s*[^\w\s]", re.IGNORECASE)


def extract_report_templates(source_path: Path, manifest_path: Path) -> dict[str, object]:
    """Create standalone DOCX packages and return the generated manifest."""

    source_bytes = source_path.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    document = Document(BytesIO(source_bytes))
    report_names = DocumentTemplateService._extract_report_names(document)
    partners = DocumentTemplateService._extract_partners(document)
    if len(report_names) != EXPECTED_REPORT_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_REPORT_COUNT} report names in the version matrix; "
            f"found {len(report_names)}."
        )
    if not partners:
        raise ValueError("The master Word file did not contain partner reference data.")

    with ZipFile(BytesIO(source_bytes)) as source_zip:
        source_parts = {
            info.filename: source_zip.read(info.filename) for info in source_zip.infolist()
        }
        source_infos = source_zip.infolist()
    master_document_xml = source_parts[DOCUMENT_PART]
    master_root = etree.fromstring(master_document_xml)
    master_body = master_root.find(f"{{{W_NS}}}body")
    if master_body is None or not len(master_body):
        raise ValueError("The master Word file did not contain a document body.")
    section_properties = master_body[-1]
    if section_properties.tag != f"{{{W_NS}}}sectPr":
        raise ValueError("The master Word file did not end with section properties.")

    boundaries = _find_report_boundaries(master_body)
    expected_ids = [report_id for report_id, _ in report_names]
    detected_ids = [f"report-{_roman_slug(roman)}" for _, roman, _ in boundaries]
    if detected_ids != expected_ids:
        raise ValueError(
            "The report title order does not match the version matrix: "
            f"expected {expected_ids}, found {detected_ids}."
        )

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    templates: list[dict[str, object]] = []
    for source_order, ((report_id, name), boundary) in enumerate(
        zip(report_names, boundaries, strict=True), start=1
    ):
        start_index, _, body_title = boundary
        next_start = (
            boundaries[source_order][0] if source_order < len(boundaries) else len(master_body) - 1
        )
        end_index = _find_report_end(master_body, start_index, next_start)
        output_path = manifest_path.parent / f"{report_id}.docx"
        output_bytes = _build_package(
            source_parts,
            source_infos,
            master_document_xml,
            start_index,
            end_index,
            section_properties,
        )
        _validate_output_package(
            output_bytes,
            source_parts,
            report_id=report_id,
            expected_title=body_title,
        )
        _atomic_write_bytes(output_path, output_bytes)
        templates.append(
            {
                "id": report_id,
                "name": name,
                "source_order": source_order,
                "file": output_path.name,
                "sha256": hashlib.sha256(output_bytes).hexdigest(),
                "body_title": body_title,
            }
        )

    manifest: dict[str, object] = {
        "schema_version": 1,
        "source_file": source_path.name,
        "source_sha256": source_sha256,
        "templates": templates,
        "partners": [
            {
                "name": partner.name,
                "icai_membership_number": partner.icai_membership_number,
            }
            for partner in partners
        ],
    }
    _atomic_write_text(
        manifest_path,
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != source_sha256:
        raise RuntimeError("The retained master Word file changed during extraction.")
    return manifest


def _find_report_boundaries(body: etree._Element) -> list[tuple[int, str, str]]:
    boundaries: list[tuple[int, str, str]] = []
    for index, element in enumerate(body):
        drawing_text = _joined_text(
            element.xpath(".//w:drawing//w:t/text()", namespaces=NAMESPACES)
        )
        match = TITLE_PATTERN.match(drawing_text)
        if not match:
            continue
        boundaries.append((index, match.group(1).upper(), _deduplicate_title(drawing_text)))
    if len(boundaries) != EXPECTED_REPORT_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_REPORT_COUNT} report title drawings; found {len(boundaries)}."
        )
    return boundaries


def _find_report_end(body: etree._Element, start: int, stop: int) -> int:
    for index in range(stop - 1, start - 1, -1):
        direct_text = _direct_paragraph_text(body[index])
        if _normalized(direct_text).startswith("date:"):
            return index
    raise ValueError(f"Report starting at body element {start} did not end with a Date paragraph.")


def _build_package(
    source_parts: dict[str, bytes],
    source_infos: list[ZipInfo],
    master_document_xml: bytes,
    start_index: int,
    end_index: int,
    section_properties: etree._Element,
) -> bytes:
    root = etree.fromstring(master_document_xml)
    body = root.find(f"{{{W_NS}}}body")
    if body is None:
        raise ValueError("The master Word file did not contain a document body.")
    selected = [deepcopy(body[index]) for index in range(start_index, end_index + 1)]
    for element in list(body):
        body.remove(element)
    for element in selected:
        body.append(element)
    body.append(deepcopy(section_properties))
    document_xml = etree.tostring(
        root,
        encoding="UTF-8",
        xml_declaration=True,
        standalone=True,
    )

    package = BytesIO()
    with ZipFile(package, "w") as output_zip:
        for info in source_infos:
            filename = info.filename
            output_zip.writestr(
                info,
                document_xml if filename == DOCUMENT_PART else source_parts[filename],
            )
    return package.getvalue()


def _validate_output_package(
    output_bytes: bytes,
    source_parts: dict[str, bytes],
    *,
    report_id: str,
    expected_title: str,
) -> None:
    with ZipFile(BytesIO(output_bytes)) as output_zip:
        if set(output_zip.namelist()) != set(source_parts):
            raise ValueError(f"Standalone template '{report_id}' changed the package part set.")
        for filename, source_payload in source_parts.items():
            if filename == DOCUMENT_PART:
                continue
            if (
                hashlib.sha256(output_zip.read(filename)).digest()
                != hashlib.sha256(source_payload).digest()
            ):
                raise ValueError(
                    f"Standalone template '{report_id}' changed preserve-only part '{filename}'."
                )
        document_xml = output_zip.read(DOCUMENT_PART)

    root = etree.fromstring(document_xml)
    title_texts = [
        _deduplicate_title(_joined_text(element.xpath(".//w:t/text()", namespaces=NAMESPACES)))
        for element in root.xpath("//w:drawing", namespaces=NAMESPACES)
    ]
    if expected_title not in title_texts:
        raise ValueError(f"Standalone template '{report_id}' lost its source title drawing.")
    direct_paragraphs = [
        _normalized(_direct_paragraph_text(element))
        for element in root.xpath("/w:document/w:body/w:p", namespaces=NAMESPACES)
    ]
    if direct_paragraphs.count(REPORT_START) != 1:
        raise ValueError(
            f"Standalone template '{report_id}' must contain exactly one auditor-report start."
        )
    bookmark_starts = set(root.xpath("//w:bookmarkStart/@w:id", namespaces=NAMESPACES))
    bookmark_ends = set(root.xpath("//w:bookmarkEnd/@w:id", namespaces=NAMESPACES))
    if bookmark_starts != bookmark_ends:
        raise ValueError(f"Standalone template '{report_id}' contains unbalanced bookmarks.")


def _direct_paragraph_text(element: etree._Element) -> str:
    return _joined_text(
        element.xpath(
            ".//w:t[not(ancestor::w:drawing) and not(ancestor::w:pict)]/text()",
            namespaces=NAMESPACES,
        )
    )


def _joined_text(values: list[str]) -> str:
    return " ".join("".join(values).split())


def _deduplicate_title(value: str) -> str:
    midpoint = len(value) // 2
    if len(value) % 2 == 0 and value[:midpoint] == value[midpoint:]:
        return value[:midpoint]
    return value


def _roman_slug(value: str) -> str:
    return value.casefold()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _atomic_write_text(path: Path, payload: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_MASTER_TEMPLATE_SOURCE)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_TEMPLATE_SOURCE)
    arguments = parser.parse_args()
    manifest = extract_report_templates(arguments.source.resolve(), arguments.manifest.resolve())
    templates = manifest["templates"]
    print(f"Created {len(templates) if isinstance(templates, list) else 0} report templates")
    print(arguments.manifest.resolve())


if __name__ == "__main__":
    main()
