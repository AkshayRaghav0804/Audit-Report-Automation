"""Protected question-and-preview workspace for a new report draft."""

from __future__ import annotations

import asyncio
import json
import re
from base64 import b64encode
from collections.abc import MutableMapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any, cast
from urllib.parse import quote

from nicegui import app, events, run, ui

from app.components.header import add_application_head, render_header
from app.components.notification_menu import NotificationState
from app.components.route_guard import require_authentication
from app.components.section_permissions import section_permission_controls
from app.components.sidebar import render_sidebar
from app.models.report_question import ReportQuestion
from app.models.report_template import (
    ReportParagraph,
    ReportSection,
    ReportTable,
    ReportTemplate,
)
from app.models.user import User
from app.services.approval_service import ApprovalService, SubmissionReadiness
from app.services.permission_service import has_permission
from app.services.report_access_service import ReportAccess, ReportAccessService
from app.services.report_basic_details import (
    AS_IGAAP,
    CFS_DETAIL_FIELDS,
    HIDDEN_BASIC_FIELDS,
    MANAGED_FRAMEWORK_WORD_FIELDS,
    financial_year_options,
    framework_options,
    is_bank_template,
    is_consolidated_template,
    managed_report_answers,
    report_text_for_answers,
)
from app.services.report_collaboration_service import (
    ReportCollaborationService,
    SectionChangeSummary,
    SectionComment,
)
from app.services.report_definition_service import get_report_definition_service
from app.services.report_draft_service import (
    WORKFLOW_STAGES,
    DraftSubmissionError,
    PreviewSegment,
    draft_storage_key,
    format_answer_for_preview,
    link_section_override_fields,
    missing_questions_for_submission,
    next_workflow_stage_for,
    placeholder_segments,
    relink_section_editor_content,
    report_answers_for,
    report_numbering_labels,
    save_answer,
    section_overrides_for,
    section_placeholder_texts,
    set_section_override,
    source_paragraph_is_visible,
    source_section_is_visible,
    submit_for_approval,
    substitute_placeholders,
    workflow_answered_question_fields,
    workflow_stage_for,
)
from app.services.report_export_service import ReportExportError, ReportExportService
from app.services.report_persistence_service import (
    AnswerChangeSummary,
    PersistedDraft,
    ReportPersistenceService,
)
from app.services.review_delta_service import ReviewDelta, ReviewDeltaSection, ReviewDeltaService
from app.services.section_access_service import SectionAccess, SectionAccessService
from app.services.source_errors import SourceDataError
from app.services.submission_policy import (
    ENFORCE_ANSWER_COMPLETENESS,
    has_answer,
    is_applicable,
    is_review_required,
)
from app.services.supplementary_report_service import (
    AUDIT_REPORT,
    REPORT_DOCUMENT_LABELS,
    available_report_documents,
    supplementary_report_for_question,
)
from app.services.workflow_timeline_service import WorkflowTimeline, WorkflowTimelineService

WORD_LIKE_PREVIEW_SCRIPT = """
<script>
(() => {
  if (window.__qcWordLikePreviewInstalled) return;
  window.__qcWordLikePreviewInstalled = true;

  const decodeAnchors = (encoded) => {
    if (!encoded) return [];
    try {
      const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
      return JSON.parse(new TextDecoder().decode(bytes));
    } catch (_) {
      return [];
    }
  };

  const textNodes = (root) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    return nodes;
  };

  const boundaryAt = (nodes, requestedOffset) => {
    let offset = Math.max(0, requestedOffset);
    for (const node of nodes) {
      const length = node.textContent?.length || 0;
      if (offset <= length) return {node, offset};
      offset -= length;
    }
    const node = nodes[nodes.length - 1];
    return node ? {node, offset: node.textContent?.length || 0} : null;
  };

  let highlightObserver = null;
  const observeHighlights = () => highlightObserver?.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['data-comment-anchors'],
  });

  const applyFallbackHighlights = () => {
    highlightObserver?.disconnect();
    document.querySelectorAll('mark.preview-comment-highlight').forEach((mark) => {
      mark.replaceWith(document.createTextNode(mark.textContent || ''));
    });
    document.querySelectorAll('.preview-section__content').forEach((root) => root.normalize());
    document.querySelectorAll('.preview-section[data-comment-anchors]').forEach((section) => {
      const root = section.querySelector('.preview-section__content');
      if (!root) return;
      const anchors = decodeAnchors(section.dataset.commentAnchors)
        .filter((anchor) => anchor.text)
        .sort((left, right) => (right.start || 0) - (left.start || 0));
      anchors.forEach((anchor) => {
        const nodes = textNodes(root);
        const fullText = nodes.map((node) => node.textContent || '').join('');
        let start = Number.isInteger(anchor.start) ? anchor.start : -1;
        if (start < 0 || fullText.slice(start, start + anchor.text.length) !== anchor.text) {
          start = fullText.indexOf(anchor.text);
        }
        if (start < 0) return;
        const end = start + anchor.text.length;
        let nodeStart = 0;
        const slices = [];
        nodes.forEach((node) => {
          const nodeEnd = nodeStart + (node.textContent?.length || 0);
          const sliceStart = Math.max(start, nodeStart);
          const sliceEnd = Math.min(end, nodeEnd);
          if (sliceStart < sliceEnd) {
            slices.push({
              node,
              start: sliceStart - nodeStart,
              end: sliceEnd - nodeStart,
            });
          }
          nodeStart = nodeEnd;
        });
        slices.reverse().forEach((slice) => {
          const range = document.createRange();
          range.setStart(slice.node, slice.start);
          range.setEnd(slice.node, slice.end);
          const mark = document.createElement('mark');
          mark.className = 'preview-comment-highlight'
            + (anchor.draft ? ' preview-comment-highlight--draft' : '');
          range.surroundContents(mark);
        });
      });
    });
    observeHighlights();
  };

  window.qcApplyCommentHighlights = () => {
    if (!window.CSS?.highlights || typeof Highlight === 'undefined') {
      applyFallbackHighlights();
      return;
    }
    const savedRanges = [];
    const draftRanges = [];
    document.querySelectorAll('.preview-section[data-comment-anchors]').forEach((section) => {
      const root = section.querySelector('.preview-section__content');
      if (!root) return;
      const nodes = textNodes(root);
      const fullText = nodes.map((node) => node.textContent || '').join('');
      decodeAnchors(section.dataset.commentAnchors).forEach((anchor) => {
        if (!anchor.text) return;
        let start = Number.isInteger(anchor.start) ? anchor.start : -1;
        if (start < 0 || fullText.slice(start, start + anchor.text.length) !== anchor.text) {
          start = fullText.indexOf(anchor.text);
        }
        if (start < 0) return;
        const startBoundary = boundaryAt(nodes, start);
        const endBoundary = boundaryAt(nodes, start + anchor.text.length);
        if (!startBoundary || !endBoundary) return;
        const range = document.createRange();
        range.setStart(startBoundary.node, startBoundary.offset);
        range.setEnd(endBoundary.node, endBoundary.offset);
        (anchor.draft ? draftRanges : savedRanges).push(range);
      });
    });
    CSS.highlights.set('report-comments', new Highlight(...savedRanges));
    CSS.highlights.set('report-comment-draft', new Highlight(...draftRanges));
  };

  let selectionAction = null;
  let selectionTimer = null;
  const removeSelectionAction = () => {
    selectionAction?.remove();
    selectionAction = null;
  };

  const showSelectionAction = () => {
    removeSelectionAction();
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    const startElement = range.startContainer.nodeType === Node.TEXT_NODE
      ? range.startContainer.parentElement
      : range.startContainer;
    const endElement = range.endContainer.nodeType === Node.TEXT_NODE
      ? range.endContainer.parentElement
      : range.endContainer;
    const content = startElement?.closest?.('.preview-section__content');
    const section = content?.closest?.('.preview-section');
    if (!content || !section || !content.contains(endElement)) return;
    if (content.getAttribute('contenteditable') === 'true') return;
    const selectedText = selection.toString();
    if (!selectedText.trim() || !section.dataset.canComment) return;
    const before = document.createRange();
    before.selectNodeContents(content);
    before.setEnd(range.startContainer, range.startOffset);
    const selectionStart = before.toString().length;
    const rect = range.getBoundingClientRect();
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'preview-selection-comment-action';
    button.innerHTML = '<span aria-hidden="true">＋</span> Comment';
    const actionLeft = Math.min(
      window.innerWidth - 116,
      Math.max(8, rect.left + rect.width / 2 - 52),
    );
    button.style.left = `${actionLeft}px`;
    button.style.top = `${Math.max(8, rect.top - 42)}px`;
    button.addEventListener('mousedown', (event) => event.preventDefault());
    button.addEventListener('click', () => {
      section.dispatchEvent(new CustomEvent('qc-comment-selection', {
        detail: {selectedText, selectionStart},
        bubbles: false,
      }));
      removeSelectionAction();
      selection.removeAllRanges();
    });
    document.body.appendChild(button);
    selectionAction = button;
  };

  document.addEventListener('mouseup', (event) => {
    if (event.target.closest?.('.preview-selection-comment-action')) return;
    window.setTimeout(showSelectionAction, 0);
  });
  document.addEventListener('selectionchange', () => {
    window.clearTimeout(selectionTimer);
    selectionTimer = window.setTimeout(showSelectionAction, 160);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') removeSelectionAction();
  });
  document.addEventListener('scroll', removeSelectionAction, true);
  document.addEventListener('mousedown', (event) => {
    if (!event.target.closest?.('.preview-selection-comment-action')) removeSelectionAction();
  });

  let previewChromeFrame = null;
  const syncPreviewChrome = () => {
    document.querySelectorAll('.report-preview').forEach((preview) => {
      const heading = preview.querySelector('.report-preview__heading');
      if (!heading) return;
      preview.style.setProperty(
        '--preview-heading-height',
        `${heading.getBoundingClientRect().height}px`,
      );
    });
  };
  const schedulePreviewChrome = () => {
    if (previewChromeFrame !== null) return;
    previewChromeFrame = requestAnimationFrame(() => {
      previewChromeFrame = null;
      syncPreviewChrome();
    });
  };
  const touchesPreviewChrome = (mutation) => {
    const selector = '.report-preview, .report-preview__heading';
    const nodes = [mutation.target, ...mutation.addedNodes, ...mutation.removedNodes];
    return nodes.some((node) => {
      const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
      return element && (
        element.matches?.(selector)
        || element.closest?.(selector)
        || element.querySelector?.(selector)
      );
    });
  };
  const previewChromeObserver = new MutationObserver((mutations) => {
    if (mutations.some(touchesPreviewChrome)) schedulePreviewChrome();
  });
  previewChromeObserver.observe(document.documentElement, {childList: true, subtree: true});
  window.addEventListener('resize', schedulePreviewChrome);
  schedulePreviewChrome();

  let reportFindQuery = '';
  let reportFindMatches = [];
  let reportFindIndex = -1;
  let reportFindFallbackMarks = [];

  const reportFindHighlightNames = [
    'report-find-editable',
    'report-find-protected',
    'report-find-current-editable',
    'report-find-current-protected',
  ];

  const clearReportFindFallback = () => {
    reportFindFallbackMarks.forEach((mark) => {
      mark.replaceWith(document.createTextNode(mark.textContent || ''));
    });
    document.querySelectorAll(
      '.preview-section__heading, .preview-section__content'
    ).forEach((root) => root.normalize());
    reportFindFallbackMarks = [];
  };

  const updateReportFindStatus = () => {
    const status = document.getElementById('report-find-status');
    if (!status) return;
    const editable = reportFindMatches.filter((match) => match.editable).length;
    const protectedCount = reportFindMatches.length - editable;
    const current = reportFindMatches[reportFindIndex];
    status.textContent = reportFindMatches.length
      ? `${reportFindIndex + 1} of ${reportFindMatches.length} · ${editable} editable · `
        + `${protectedCount} protected · Current: ${current?.editable ? 'editable' : 'protected'}`
      : (reportFindQuery ? 'No matches found' : 'Enter text to search the full report');
    const replaceButton = document.querySelector('.report-find-panel__replace');
    const replacePanel = document.getElementById('report-find-panel');
    if (replaceButton instanceof HTMLButtonElement && replacePanel) {
      replaceButton.disabled = replacePanel.dataset.replaceEnabled !== 'true'
        || !reportFindQuery || editable === 0;
    }
  };

  const applyReportFindHighlights = () => {
    if (window.CSS?.highlights && typeof Highlight !== 'undefined') {
      clearReportFindFallback();
      const editableRanges = reportFindMatches
        .filter((match) => match.editable)
        .map((match) => match.range);
      const protectedRanges = reportFindMatches
        .filter((match) => !match.editable)
        .map((match) => match.range);
      const current = reportFindMatches[reportFindIndex];
      CSS.highlights.set('report-find-editable', new Highlight(...editableRanges));
      CSS.highlights.set('report-find-protected', new Highlight(...protectedRanges));
      CSS.highlights.set(
        'report-find-current-editable',
        new Highlight(...(current?.editable ? [current.range] : [])),
      );
      CSS.highlights.set(
        'report-find-current-protected',
        new Highlight(...(current && !current.editable ? [current.range] : [])),
      );
      return;
    }
    if (!reportFindFallbackMarks.length && reportFindMatches.length) {
      [...reportFindMatches].reverse().forEach((match) => {
        const mark = document.createElement('mark');
        mark.className = 'report-find-highlight report-find-highlight--'
          + (match.editable ? 'editable' : 'protected');
        match.range.surroundContents(mark);
        reportFindFallbackMarks.unshift(mark);
      });
    }
    reportFindFallbackMarks.forEach((mark, index) => {
      mark.classList.toggle('report-find-highlight--current', index === reportFindIndex);
    });
  };

  const collectReportFindRanges = (query) => {
    const matches = [];
    const needle = query.toLocaleLowerCase();
    document.querySelectorAll(
      '.preview-section__heading, .preview-section__content'
    ).forEach((root) => {
      const section = root.closest('.preview-section');
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode;
        const text = node.textContent || '';
        const searchable = text.toLocaleLowerCase();
        let offset = 0;
        while (needle && (offset = searchable.indexOf(needle, offset)) >= 0) {
          const range = document.createRange();
          range.setStart(node, offset);
          range.setEnd(node, offset + query.length);
          const wordMatch = node.parentElement?.closest('[data-find-replace-kind="word"]');
          const editable = wordMatch
            ? section?.dataset.findWordEditable === 'true'
            : section?.dataset.findFixedEditable === 'true';
          matches.push({range, editable});
          offset += Math.max(1, query.length);
        }
      }
    });
    return matches;
  };

  window.qcReportFind = (query, movement = 0, scroll = true) => {
    const normalized = String(query || '').trim();
    if (normalized !== reportFindQuery) {
      clearReportFindFallback();
      reportFindQuery = normalized;
      reportFindMatches = normalized ? collectReportFindRanges(normalized) : [];
      reportFindIndex = reportFindMatches.length ? 0 : -1;
    } else if (reportFindMatches.length && movement) {
      reportFindIndex = (
        reportFindIndex + movement + reportFindMatches.length
      ) % reportFindMatches.length;
    }
    applyReportFindHighlights();
    updateReportFindStatus();
    const current = reportFindFallbackMarks[reportFindIndex]
      || reportFindMatches[reportFindIndex]?.range;
    if (current && scroll) {
      const target = current instanceof HTMLElement
        ? current
        : current.startContainer.parentElement;
      target?.scrollIntoView({behavior: 'smooth', block: 'center'});
    }
    const editable = reportFindMatches.filter((match) => match.editable).length;
    return {
      total: reportFindMatches.length,
      current: reportFindIndex + 1,
      editable,
      protected: reportFindMatches.length - editable,
    };
  };

  window.qcClearReportFind = () => {
    clearReportFindFallback();
    reportFindQuery = '';
    reportFindMatches = [];
    reportFindIndex = -1;
    reportFindHighlightNames.forEach((name) => CSS.highlights?.delete(name));
    updateReportFindStatus();
  };

  window.qcReportReplaceComplete = (found, replaced, protectedCount = 0) => {
    clearReportFindFallback();
    reportFindQuery = '';
    reportFindMatches = [];
    reportFindIndex = -1;
    reportFindHighlightNames.forEach((name) => CSS.highlights?.delete(name));
    const status = document.getElementById('report-find-status');
    if (status) {
      status.textContent = `Found ${found} · Replaced ${replaced} · `
        + `${protectedCount} protected unchanged`;
    }
  };

  window.qcToggleReportFind = (mode) => {
    const panel = document.getElementById('report-find-panel');
    const replace = document.getElementById('report-replace-fields');
    const accessKey = document.getElementById('report-find-access-key');
    const input = document.querySelector('.report-find-panel__find-input input');
    if (!panel || !replace) return;
    const closing = panel.dataset.mode === mode && panel.classList.contains('is-open');
    panel.classList.toggle('is-open', !closing);
    panel.dataset.mode = closing ? '' : mode;
    replace.hidden = mode !== 'replace' || closing;
    if (accessKey) accessKey.hidden = mode !== 'replace' || closing;
    if (closing) window.qcClearReportFind();
    else {
      updateReportFindStatus();
      window.setTimeout(() => input?.focus(), 0);
    }
  };

  document.addEventListener('input', (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement)) return;
    if (!input.closest('.report-find-panel__find-input')) return;
    window.qcReportFind(input.value, 0, false);
  });
  document.addEventListener('keydown', (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement)) return;
    if (!input.closest('.report-find-panel__find-input')) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      window.qcReportFind(input.value, event.shiftKey ? -1 : 1);
    } else if (event.key === 'Escape') {
      window.qcToggleReportFind(
        document.getElementById('report-find-panel')?.dataset.mode || 'find'
      );
    }
  });

  let highlightFrame = null;
  const scheduleHighlights = () => {
    if (highlightFrame !== null) return;
    highlightFrame = requestAnimationFrame(() => {
      highlightFrame = null;
      window.qcApplyCommentHighlights();
    });
  };
  const touchesCommentHighlights = (mutation) => {
    if (mutation.type === 'attributes') return true;
    const selector = '.preview-section[data-comment-anchors]';
    const nodes = [mutation.target, ...mutation.addedNodes, ...mutation.removedNodes];
    return nodes.some((node) => {
      const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
      return element && (
        element.matches?.(selector)
        || element.closest?.(selector)
        || element.querySelector?.(selector)
      );
    });
  };
  highlightObserver = new MutationObserver((mutations) => {
    if (mutations.some(touchesCommentHighlights)) scheduleHighlights();
  });
  observeHighlights();
  scheduleHighlights();
})();
</script>
"""
COMMENT_SELECTION_HANDLER = """(event) => {
  window.setTimeout(() => {
    document.querySelectorAll('.preview-selection-comment-action').forEach((item) => item.remove());
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    const startElement = range.startContainer.nodeType === Node.TEXT_NODE
      ? range.startContainer.parentElement : range.startContainer;
    const endElement = range.endContainer.nodeType === Node.TEXT_NODE
      ? range.endContainer.parentElement : range.endContainer;
    const content = startElement?.closest?.('.preview-section__content');
    const section = content?.closest?.('.preview-section');
    if (
      !content || !section || !content.contains(endElement) || !section.dataset.canComment
    ) return;
    if (content.getAttribute('contenteditable') === 'true') return;
    const selectedText = selection.toString();
    if (!selectedText.trim()) return;
    const before = document.createRange();
    before.selectNodeContents(content);
    before.setEnd(range.startContainer, range.startOffset);
    const rect = range.getBoundingClientRect();
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'preview-selection-comment-action';
    button.textContent = '＋ Comment';
    button.style.left = `${Math.min(
      window.innerWidth - 116, Math.max(8, rect.left + rect.width / 2 - 52)
    )}px`;
    button.style.top = `${Math.max(8, rect.top - 42)}px`;
    button.addEventListener('mousedown', (mouseEvent) => mouseEvent.preventDefault());
    button.addEventListener('click', () => {
      section.dispatchEvent(new CustomEvent('qc-comment-selection', {
        detail: {selectedText, selectionStart: before.toString().length},
        bubbles: false,
      }));
      button.remove();
      selection.removeAllRanges();
    });
    document.body.appendChild(button);
  }, 0);
}"""

SECTION_EDIT_CLICK_HANDLER = """(event) => {
  window.clearTimeout(event.currentTarget.__qcEditClickTimer);
  if (event.detail > 1) return;
  event.currentTarget.__qcEditClickTimer = window.setTimeout(() => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) emit();
  }, 500);
}"""

COMMENT_HIGHLIGHT_HANDLER = """(event) => {
  const section = event.currentTarget;
  const encoded = section.dataset.commentAnchors || '';
  if (!encoded || section.dataset.appliedCommentAnchors === encoded) return;
  const root = section.querySelector('.preview-section__content');
  if (!root) return;
  root.querySelectorAll('mark.preview-comment-highlight').forEach((mark) => {
    mark.replaceWith(document.createTextNode(mark.textContent || ''));
  });
  root.normalize();
  let anchors = [];
  try {
    const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
    anchors = JSON.parse(new TextDecoder().decode(bytes));
  } catch (_) {
    return;
  }
  anchors.filter((anchor) => anchor.text).reverse().forEach((anchor) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    const fullText = nodes.map((node) => node.textContent || '').join('');
    let start = Number.isInteger(anchor.start) ? anchor.start : -1;
    if (start < 0 || fullText.slice(start, start + anchor.text.length) !== anchor.text) {
      start = fullText.indexOf(anchor.text);
    }
    if (start < 0) return;
    const end = start + anchor.text.length;
    let nodeStart = 0;
    const slices = [];
    nodes.forEach((node) => {
      const nodeEnd = nodeStart + (node.textContent?.length || 0);
      const sliceStart = Math.max(start, nodeStart);
      const sliceEnd = Math.min(end, nodeEnd);
      if (sliceStart < sliceEnd) {
        slices.push({node, start: sliceStart - nodeStart, end: sliceEnd - nodeStart});
      }
      nodeStart = nodeEnd;
    });
    slices.reverse().forEach((slice) => {
      const range = document.createRange();
      range.setStart(slice.node, slice.start);
      range.setEnd(slice.node, slice.end);
      const mark = document.createElement('mark');
      mark.className = 'preview-comment-highlight'
        + (anchor.draft ? ' preview-comment-highlight--draft' : '');
      range.surroundContents(mark);
    });
  });
  section.dataset.appliedCommentAnchors = encoded;
}"""

SECTION_EDITOR_AUTOSAVE_DELAY_MS = 600


def _section_editor_input_handler(role_key: str, status_id: str) -> str:
    """Color live edits and debounce a complete linked-section autosave payload."""

    script = """(event) => {
  const editor = event.currentTarget;
  const selection = window.getSelection();
  let node = selection && selection.anchorNode;
  let element = node && (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement);
  const fixed = element && element.closest('[data-editor-kind="fixed"]');
  if (fixed && editor.contains(fixed)) {
    Array.from(fixed.classList)
      .filter((name) => name.startsWith('preview-role-edit--'))
      .forEach((name) => fixed.classList.remove(name));
    fixed.classList.add('preview-role-edit', 'preview-fixed-edit',
      `preview-role-edit--${__ROLE__}`);
    fixed.dataset.editorRole = __ROLE__;
  }
  const status = document.getElementById(__STATUS_ID__);
  editor.__qcAutosaveRevision = (editor.__qcAutosaveRevision || 0) + 1;
  const revision = editor.__qcAutosaveRevision;
  if (status) {
    status.textContent = 'Saving…';
    status.dataset.saveRevision = String(revision);
    status.dataset.saveState = 'saving';
  }
  editor.__qcEmitAutosave = (finish = false) => {
    const fields = Array.from(editor.querySelectorAll('[data-editor-field]'))
      .map((field) => ({
        fieldName: field.dataset.editorField,
        value: field.textContent || '',
      }));
    const linked = editor.cloneNode(true);
    linked.querySelectorAll('[data-editor-field]').forEach((field) => {
      field.replaceWith(document.createTextNode(`{{${field.dataset.editorField}}}`));
    });
    editor.dispatchEvent(new CustomEvent('qc-section-autosave', {
      bubbles: false,
      detail: {
        content: editor.innerText,
        linkedContent: linked.textContent || '',
        fields,
        finish,
        revision: editor.__qcAutosaveRevision || revision,
      },
    }));
  };
  window.clearTimeout(editor.__qcAutosaveTimer);
  editor.__qcAutosaveTimer = window.setTimeout(() => {
    editor.__qcAutosaveTimer = null;
    editor.__qcEmitAutosave(false);
  }, __DELAY__);
}"""
    return (
        script.replace("__ROLE__", json.dumps(role_key))
        .replace("__STATUS_ID__", json.dumps(status_id))
        .replace("__DELAY__", str(SECTION_EDITOR_AUTOSAVE_DELAY_MS))
    )


SECTION_EDITOR_FOCUSOUT_HANDLER = """(event) => {
  const editor = event.currentTarget;
  if (editor.contains(event.relatedTarget)) return;
  window.clearTimeout(editor.__qcAutosaveTimer);
  editor.__qcAutosaveTimer = null;
  if (typeof editor.__qcEmitAutosave === 'function') {
    editor.__qcEmitAutosave(false);
    return;
  }
  editor.__qcAutosaveRevision = (editor.__qcAutosaveRevision || 0) + 1;
  const fields = Array.from(editor.querySelectorAll('[data-editor-field]'))
    .map((field) => ({fieldName: field.dataset.editorField, value: field.textContent || ''}));
  const linked = editor.cloneNode(true);
  linked.querySelectorAll('[data-editor-field]').forEach((field) => {
    field.replaceWith(document.createTextNode(`{{${field.dataset.editorField}}}`));
  });
  editor.dispatchEvent(new CustomEvent('qc-section-autosave', {
    bubbles: false,
    detail: {
      content: editor.innerText,
      linkedContent: linked.textContent || '',
      fields,
      finish: false,
      revision: editor.__qcAutosaveRevision,
    },
  }));
}"""


def _section_editor_done_handler(editor_id: str) -> str:
    """Flush pending section changes and leave in-place editing."""

    return """(event) => {
  event.preventDefault();
  const editor = document.getElementById(__EDITOR_ID__);
  if (!editor) return;
  window.clearTimeout(editor.__qcAutosaveTimer);
  editor.__qcAutosaveTimer = null;
  if (typeof editor.__qcEmitAutosave === 'function') {
    editor.__qcEmitAutosave(true);
    return;
  }
  editor.__qcAutosaveRevision = (editor.__qcAutosaveRevision || 0) + 1;
  const fields = Array.from(editor.querySelectorAll('[data-editor-field]'))
    .map((field) => ({fieldName: field.dataset.editorField, value: field.textContent || ''}));
  const linked = editor.cloneNode(true);
  linked.querySelectorAll('[data-editor-field]').forEach((field) => {
    field.replaceWith(document.createTextNode(`{{${field.dataset.editorField}}}`));
  });
  editor.dispatchEvent(new CustomEvent('qc-section-autosave', {
    bubbles: false,
    detail: {
      content: editor.innerText,
      linkedContent: linked.textContent || '',
      fields,
      finish: true,
      revision: editor.__qcAutosaveRevision,
    },
  }));
}""".replace("__EDITOR_ID__", json.dumps(editor_id))


SECTION_EDITOR_HISTORY_SHORTCUT_HANDLER = """(event) => {
  if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
  const key = event.key.toLowerCase();
  const command = key === 'z' && !event.shiftKey
    ? 'undo'
    : (key === 'y' || (key === 'z' && event.shiftKey) ? 'redo' : null);
  if (!command) return;
  event.preventDefault();
  const editor = event.currentTarget;
  const before = editor.innerHTML;
  document.execCommand(command, false);
  if (editor.innerHTML !== before) {
    editor.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: `history${command}`}));
  }
}"""


def _section_editor_history_button_handler(editor_id: str, command: str) -> str:
    return (
        """(event) => {
  event.preventDefault();
  const editor = document.getElementById(__EDITOR_ID__);
  if (!editor) return;
  const before = editor.innerHTML;
  editor.focus();
  document.execCommand(__COMMAND__, false);
  if (editor.innerHTML !== before) {
    editor.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: __INPUT_TYPE__}));
  }
}""".replace("__EDITOR_ID__", json.dumps(editor_id))
        .replace("__COMMAND__", json.dumps(command))
        .replace("__INPUT_TYPE__", json.dumps(f"history{command}"))
    )


def _report_editor_history_button_handler(command: str) -> str:
    """Undo or redo in whichever report editor currently owns the caret."""

    return """(event) => {
  event.preventDefault();
  const active = document.activeElement?.closest?.('[contenteditable="true"]');
  const editor = active || document.querySelector('.preview-section__content--editing');
  if (!editor) return;
  const before = editor.innerHTML;
  editor.focus();
  document.execCommand(__COMMAND__, false);
  if (editor.innerHTML !== before) {
    editor.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: __INPUT_TYPE__}));
  }
}""".replace("__COMMAND__", json.dumps(command)).replace(
        "__INPUT_TYPE__", json.dumps(f"history{command}")
    )


EDIT_ROLE_LEGEND = (
    ("template", "Template editable"),
    ("employee", "Employee edit"),
    ("manager", "Manager edit"),
    ("director", "Director edit"),
    ("partner", "Partner edit"),
    ("qc", "QC edit"),
    ("eqcr", "EQCR edit"),
)


@dataclass(frozen=True, slots=True)
class AttributedPreviewText:
    text: str
    change: SectionChangeSummary | None = None


@dataclass(frozen=True, slots=True)
class SectionEditorSpan:
    """A styled text run inside the unified section editor."""

    text: str
    field_name: str | None = None
    change: SectionChangeSummary | None = None


@dataclass(frozen=True, slots=True)
class OverrideParagraphSlice:
    start: int
    end: int
    source: ReportParagraph | None = None
    numbering_label: str | None = None


@dataclass(frozen=True, slots=True)
class ReportReplacementPlan:
    answer_updates: dict[str, Any]
    section_updates: dict[str, str]
    heading_updates: dict[str, str]
    changed_value_count: int
    answer_replacement_counts: dict[str, int] = field(default_factory=dict)
    section_replacement_counts: dict[str, int] = field(default_factory=dict)
    heading_replacement_counts: dict[str, int] = field(default_factory=dict)


def _replace_text(text: str, query: str, replacement: str) -> tuple[str, int]:
    if not query:
        return text, 0
    changed = 0

    def replace_match(match: re.Match[str]) -> str:
        nonlocal changed
        if match.group(0) != replacement:
            changed += 1
        return replacement

    return re.sub(re.escape(query), replace_match, text, flags=re.IGNORECASE), changed


def _replace_linked_text(text: str, query: str, replacement: str) -> tuple[str, int]:
    """Replace fixed wording without corrupting linked ``{{answer}}`` tokens."""

    parts = re.split(r"({{[^{}]+}})", text)
    total = 0
    for index in range(0, len(parts), 2):
        parts[index], count = _replace_text(parts[index], query, replacement)
        total += count
    return "".join(parts), total


def _replace_answer_value(value: Any, query: str, replacement: str) -> tuple[Any, int]:
    if isinstance(value, str):
        return _replace_text(value, query, replacement)
    if isinstance(value, list):
        resolved: list[Any] = []
        total = 0
        for item in value:
            updated, count = _replace_answer_value(item, query, replacement)
            resolved.append(updated)
            total += count
        return resolved, total
    return value, 0


def _section_answer_fields(section: ReportSection) -> set[str]:
    fields = set(section_placeholder_texts(section))
    fields.update(re.findall(r"{{([a-z0-9_]+)}}", section.content))
    if section.heading_field_name:
        fields.add(section.heading_field_name)
    for paragraph in section.paragraphs:
        fields.update(
            segment.field_name for segment in paragraph.segments if segment.field_name is not None
        )
        if paragraph.table is not None:
            fields.update(
                segment.field_name
                for cell in paragraph.table.cells
                for segment in cell.segments
                if segment.field_name is not None
            )
            if paragraph.table.repeatable_field_name:
                fields.add(paragraph.table.repeatable_field_name)
    return fields


def _report_replacement_plan(
    template: ReportTemplate,
    answers: dict[str, Any],
    section_overrides: dict[str, str],
    heading_overrides: dict[str, str],
    query: str,
    replacement: str,
) -> ReportReplacementPlan:
    """Build deterministic full-report updates for a case-insensitive Replace All."""

    normalized_query = query.strip()
    if not normalized_query:
        return ReportReplacementPlan({}, {}, {}, 0)
    section_updates: dict[str, str] = {}
    heading_updates: dict[str, str] = {}
    section_replacement_counts: dict[str, int] = {}
    heading_replacement_counts: dict[str, int] = {}
    answer_fields: set[str] = set()
    answer_defaults: dict[str, str] = {}
    changed_value_count = 0
    for section in template.sections:
        if not source_section_is_visible(section, answers):
            continue
        answer_fields.update(_section_answer_fields(section))
        for field_name, source_text in section_placeholder_texts(section).items():
            answer_defaults.setdefault(field_name, source_text)
        linked_content = link_section_override_fields(
            section,
            section_overrides.get(section.id, section.content),
        )
        updated_content, content_count = _replace_linked_text(
            linked_content,
            normalized_query,
            replacement,
        )
        if content_count:
            section_updates[section.id] = updated_content
            section_replacement_counts[section.id] = content_count
            changed_value_count += content_count

        if section.id in heading_overrides:
            heading = heading_overrides[section.id]
        elif section.heading_field_name and section.heading_field_name in answers:
            continue
        else:
            heading = section.heading
        updated_heading, heading_count = _replace_text(
            heading,
            normalized_query,
            replacement,
        )
        if heading_count:
            heading_updates[section.id] = updated_heading
            heading_replacement_counts[section.id] = heading_count
            changed_value_count += heading_count

    answer_updates: dict[str, Any] = {}
    answer_replacement_counts: dict[str, int] = {}
    for field_name in answer_fields:
        current_value = answers.get(field_name)
        if not has_answer(current_value):
            current_value = answer_defaults.get(field_name)
        if current_value is None:
            continue
        updated_value, count = _replace_answer_value(
            current_value,
            normalized_query,
            replacement,
        )
        if count:
            answer_updates[field_name] = updated_value
            answer_replacement_counts[field_name] = count
            changed_value_count += count
    return ReportReplacementPlan(
        answer_updates,
        section_updates,
        heading_updates,
        changed_value_count,
        answer_replacement_counts,
        section_replacement_counts,
        heading_replacement_counts,
    )


def _permitted_report_replacement_plan(
    plan: ReportReplacementPlan,
    *,
    can_edit_answers: bool,
    can_edit_fixed: bool,
    section_permissions: dict[str, SectionAccess],
) -> ReportReplacementPlan:
    """Remove locked or otherwise read-only matches and retain their exact counts."""

    answer_updates = plan.answer_updates if can_edit_answers else {}
    section_updates = {
        key: value
        for key, value in plan.section_updates.items()
        if can_edit_fixed and section_permissions.get(key, SectionAccess()).can_edit
    }
    heading_updates = {
        key: value
        for key, value in plan.heading_updates.items()
        if can_edit_fixed and section_permissions.get(key, SectionAccess()).can_edit
    }
    answer_counts = {
        key: plan.answer_replacement_counts[key]
        for key in answer_updates
        if key in plan.answer_replacement_counts
    }
    section_counts = {
        key: plan.section_replacement_counts[key]
        for key in section_updates
        if key in plan.section_replacement_counts
    }
    heading_counts = {
        key: plan.heading_replacement_counts[key]
        for key in heading_updates
        if key in plan.heading_replacement_counts
    }
    changed_value_count = sum(answer_counts.values())
    changed_value_count += sum(section_counts.values())
    changed_value_count += sum(heading_counts.values())
    return ReportReplacementPlan(
        answer_updates,
        section_updates,
        heading_updates,
        changed_value_count,
        answer_counts,
        section_counts,
        heading_counts,
    )


def _storage() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], app.storage.user)


async def _owner_submission_readiness(
    report_id: str,
    access: ReportAccess,
) -> SubmissionReadiness | None:
    if not access.is_owner:
        return None
    return await ApprovalService().submission_readiness(report_id)


def register_report_preparation_routes() -> None:
    @ui.page(
        "/reports/new/{template_id}",
        title="Prepare Report | QC Report Automation",
    )
    async def report_preparation_page(template_id: str, report_id: str | None = None) -> None:
        return_path = f"/reports/new/{quote(template_id, safe='')}"
        if report_id:
            return_path += f"?report_id={quote(report_id, safe='')}"
        user = await require_authentication(return_path)
        if user is None:
            return

        add_application_head()
        ui.add_head_html(WORD_LIKE_PREVIEW_SCRIPT)
        drawer = render_sidebar(user, "/reports")
        render_header(
            user,
            "Prepare Report" if has_permission(user, "report.create") else "Review Report",
            await NotificationState.for_user(user.email, _storage()),
            drawer,
        )
        if report_id is None and not has_permission(user, "report.create"):
            _render_page_error(
                "Report creation unavailable", "Open an assigned report to review it."
            )
            return
        service = get_report_definition_service()
        try:
            definitions = service.load()
        except SourceDataError as error:
            _render_page_error("Report sources are unavailable", str(error))
            return

        template = definitions.template(template_id)
        if template is None:
            _render_page_error(
                "Unknown report template",
                "Choose a report type again from the dashboard. No draft was created.",
            )
            return

        questions = definitions.questions_for(template.id)
        storage = _storage()
        storage_key = draft_storage_key(template.id, report_id)
        approval_state: dict[str, SubmissionReadiness | None] = {"readiness": None}
        access: ReportAccess | None = None
        timeline: WorkflowTimeline | None = None
        fixed_changes: tuple[SectionChangeSummary, ...] = ()
        word_changes: tuple[AnswerChangeSummary, ...] = ()
        review_delta: ReviewDelta | None = None
        section_permissions: dict[str, SectionAccess] = {}
        heading_overrides: dict[str, str] = {}
        comment_counts: dict[str, int] = {}
        section_comments: dict[str, tuple[SectionComment, ...]] = {}
        inline_edit_state: dict[str, Any] = {
            "mode": None,
            "section_key": None,
            "field_name": None,
            "value": None,
            "preview_marker": None,
        }
        comment_draft_state: dict[str, Any] = {
            "section_key": None,
            "selected_text": None,
            "selection_start": None,
        }
        if report_id:
            try:
                access = await ReportAccessService().access_for(report_id, user)
                if not access.can_view:
                    raise PermissionError("You do not have access to this report.")
                section_permissions = await SectionAccessService().states(report_id, user)
                persistence = ReportPersistenceService()
                collaboration = ReportCollaborationService()
                loaded_report_data = cast(
                    tuple[
                        PersistedDraft,
                        SubmissionReadiness | None,
                        dict[str, str],
                        dict[str, str],
                        tuple[SectionChangeSummary, ...],
                        tuple[AnswerChangeSummary, ...],
                        dict[str, tuple[SectionComment, ...]],
                        WorkflowTimeline,
                        ReviewDelta | None,
                    ],
                    await asyncio.gather(
                        persistence.load_draft(report_id, template.id),
                        _owner_submission_readiness(report_id, access),
                        collaboration.section_overrides(report_id, user, access=access),
                        collaboration.section_heading_overrides(report_id, user, access=access),
                        collaboration.section_changes(report_id, user, access=access),
                        persistence.answer_changes(report_id, template.id),
                        collaboration.comments_for_report(report_id, user, access=access),
                        WorkflowTimelineService().load(report_id),
                        ReviewDeltaService().load(report_id, user, access=access),
                    ),
                )
                (
                    persisted,
                    loaded_readiness,
                    section_overrides,
                    loaded_heading_overrides,
                    fixed_changes,
                    word_changes,
                    loaded_section_comments,
                    timeline,
                    review_delta,
                ) = loaded_report_data
                approval_state["readiness"] = loaded_readiness
                for field_name, value in persisted.answers.items():
                    save_answer(storage, storage_key, field_name, value)
                for field_name, value in persisted.workflow_answers.items():
                    save_answer(storage, storage_key, field_name, value)
                for section_key, content in section_overrides.items():
                    set_section_override(storage, storage_key, section_key, content)
                heading_overrides.update(loaded_heading_overrides)
                section_comments.update(loaded_section_comments)
                comment_counts.update(
                    {
                        section_key: len(comments)
                        for section_key, comments in section_comments.items()
                    }
                )
            except (PermissionError, ValueError) as error:
                _render_page_error("Report draft unavailable", str(error))
                return
        can_edit_answers = (
            access.can_edit_answers if access is not None else has_permission(user, "report.create")
        )
        can_manage_submission = access.is_owner if access is not None else can_edit_answers
        workflow_answered_fields = workflow_answered_question_fields(storage, storage_key)
        basic_questions = tuple(
            _adapt_basic_question(question, template)
            for question in questions
            if not question.source_sheet.startswith("Word:")
            and _show_basic_question(question.field_name, template)
        )
        input_basic_questions = tuple(
            question
            for question in basic_questions
            if question.field_name not in workflow_answered_fields
        )
        word_questions = tuple(
            question
            for question in questions
            if question.source_sheet.startswith("Word:")
            and question.field_name not in MANAGED_FRAMEWORK_WORD_FIELDS
        )
        can_edit_word_content = can_edit_answers
        initial_answers = report_answers_for(storage, storage_key)
        if is_bank_template(template.id, template.name) and not has_answer(
            initial_answers.get("applicable_financial_reporting_framework")
        ):
            save_answer(
                storage,
                storage_key,
                "applicable_financial_reporting_framework",
                AS_IGAAP,
            )
            initial_answers["applicable_financial_reporting_framework"] = AS_IGAAP
        preview_display_state = {
            "mode": "normal",
            "view": "working",
            "document": AUDIT_REPORT,
        }
        question_display_state: dict[str, Any] = {
            "collapsed": False,
            "panel": None,
            "workspace": None,
        }
        review_delta_state = {"index": 0}

        @ui.refreshable
        def workflow_progress() -> None:
            current_stage = (
                access.active_stage
                if access is not None and access.active_stage in WORKFLOW_STAGES
                else workflow_stage_for(storage, storage_key)
            )
            current_index = WORKFLOW_STAGES.index(current_stage)
            with ui.element("section").classes("live-workflow"):
                ui.label("Approval workflow").classes("live-workflow__label")
                with ui.element("div").classes("live-workflow__steps"):
                    for index, stage in enumerate(WORKFLOW_STAGES):
                        classes = "live-workflow__step"
                        if index < current_index:
                            classes += " live-workflow__step--complete"
                        elif index == current_index:
                            classes += " live-workflow__step--active"
                        label = stage.removesuffix(" Review").replace("Final Approval", "Final")
                        ui.label(label).classes(classes)
                        if index < len(WORKFLOW_STAGES) - 1:
                            ui.icon("chevron_right").classes("live-workflow__arrow")

        with ui.element("main").classes("app-main report-preparation-page"):
            heading_classes = "preparation-heading"
            if timeline is not None:
                heading_classes += " preparation-heading--compact"
            with ui.element("section").classes(heading_classes):
                with ui.column().classes("preparation-heading__content"):
                    ui.label(
                        "Report preparation" if can_manage_submission else "Report review"
                    ).classes("preparation-eyebrow")
                    ui.label(template.name).classes("preparation-template-name")
                    with ui.row().classes("preparation-meta items-center"):
                        ui.icon("quiz").props('aria-hidden="true"').classes(
                            "preparation-meta__icon"
                        )
                        ui.label(f"{len(basic_questions)} basic questions")
                        ui.element("span").props('aria-hidden="true"').classes(
                            "preparation-meta__divider"
                        )
                        ui.icon("description").props('aria-hidden="true"').classes(
                            "preparation-meta__icon"
                        )
                        ui.label(f"{len(template.sections)} Word sections")
                with ui.row().classes("preparation-heading__actions items-center"):
                    ui.button(
                        "Dashboard",
                        icon="arrow_back",
                        on_click=lambda: ui.navigate.to("/dashboard"),
                    ).props("flat no-caps").classes("back-link")
                    if timeline is not None:
                        ui.button(
                            "View approval path",
                            icon="visibility",
                            on_click=lambda: _open_workflow_timeline(timeline),
                        ).props("flat no-caps").classes("view-approval-path-button").mark(
                            "view-approval-path"
                        )
                if timeline is None:
                    with ui.element("div").classes("preparation-heading__workflow"):
                        workflow_progress()

            def set_preview_mode(mode: str) -> None:
                preview_display_state["mode"] = mode
                preview_display_state["view"] = (
                    "generated"
                    if mode == "expanded" and preview_display_state["document"] == AUDIT_REPORT
                    else "working"
                )
                report_preview.refresh()

            def set_preview_view(view: str) -> None:
                preview_display_state["view"] = view
                report_preview.refresh()

            def set_report_document(kind: str) -> None:
                available = available_report_documents(report_answers_for(storage, storage_key))
                preview_display_state["document"] = kind if kind in available else AUDIT_REPORT
                preview_display_state["view"] = "working"
                report_preview.refresh()

            def set_question_panel_collapsed(collapsed: bool) -> None:
                question_display_state["collapsed"] = collapsed
                panel = question_display_state["panel"]
                workspace = question_display_state["workspace"]
                if panel is not None:
                    panel.classes(
                        add="question-panel--collapsed" if collapsed else None,
                        remove=None if collapsed else "question-panel--collapsed",
                    )
                if workspace is not None:
                    workspace.classes(
                        add="report-preparation-grid--preview-only" if collapsed else None,
                        remove=(None if collapsed else "report-preparation-grid--preview-only"),
                    )
                report_preview.refresh()

            def open_report_document(kind: str) -> None:
                set_question_panel_collapsed(True)
                set_report_document(kind)

            async def comment_added(section_key: str) -> None:
                if report_id:
                    comments = await ReportCollaborationService().comments(
                        report_id, section_key, user, access=access
                    )
                    section_comments[section_key] = comments
                    comment_counts[section_key] = len(comments)
                await refresh_preview_sections(section_key)
                await _scroll_preview_to_section(section_key)

            section_refreshables: dict[str, Any] = {}

            async def refresh_preview_sections(*section_keys: str | None) -> None:
                """Refresh only sections whose interaction state changed."""

                refreshed: set[str] = set()
                for section_key in section_keys:
                    if section_key is None or section_key in refreshed:
                        continue
                    refreshed.add(section_key)
                    section_refreshable = section_refreshables.get(section_key)
                    if section_refreshable is not None:
                        await section_refreshable.refresh()

            async def navigate_review_delta(step: int) -> None:
                if review_delta is None or not review_delta.report_sections:
                    return
                section_count = len(review_delta.report_sections)
                review_delta_state["index"] = (
                    int(review_delta_state["index"]) + step
                ) % section_count
                review_delta_summary.refresh()
                await _scroll_preview_to_section(
                    str(review_delta.report_sections[int(review_delta_state["index"])].section_key)
                )

            def open_review_delta() -> None:
                if review_delta is None:
                    return
                selected_section = (
                    review_delta.report_sections[int(review_delta_state["index"])]
                    if review_delta.report_sections
                    else review_delta.sections[0]
                )
                _open_review_delta_dialog(
                    review_delta,
                    initial_index=review_delta.sections.index(selected_section),
                )

            @ui.refreshable
            def review_delta_summary() -> None:
                if review_delta is None:
                    return
                _render_review_delta_summary(
                    review_delta,
                    current_index=int(review_delta_state["index"]),
                    on_previous=lambda: navigate_review_delta(-1),
                    on_next=lambda: navigate_review_delta(1),
                    on_open=open_review_delta,
                )

            def section_refreshable(section: ReportSection) -> Any:
                existing = section_refreshables.get(section.id)
                if existing is not None:
                    return existing

                @ui.refreshable
                def render_section() -> None:
                    current_answers = managed_report_answers(
                        template.id,
                        template.name,
                        report_answers_for(storage, storage_key),
                    )
                    overrides = section_overrides_for(storage, storage_key)
                    has_override = section.id in overrides
                    content = overrides.get(section.id, section.content)
                    section_fields = {
                        question.field_name
                        for question in word_questions
                        if question.section_id == section.id
                    }
                    generated_view = (
                        preview_display_state["mode"] == "expanded"
                        and preview_display_state["view"] == "generated"
                    )
                    _render_preview_section(
                        section,
                        (
                            placeholder_segments(
                                link_section_override_fields(section, content),
                                current_answers,
                                placeholder_texts=section_placeholder_texts(section),
                            )
                            if has_override or not section.paragraphs
                            else ()
                        ),
                        template,
                        user,
                        storage,
                        report_preview,
                        storage_key=storage_key,
                        report_id=report_id,
                        access=access,
                        changes=tuple(
                            change for change in fixed_changes if change.section_key == section.id
                        ),
                        word_changes=tuple(
                            change
                            for change in word_changes
                            if change.section_key == section.id
                            or (change.section_key is None and change.field_name in section_fields)
                        ),
                        source_paragraphs=() if has_override else section.paragraphs,
                        answers=current_answers,
                        editable_word_content=can_edit_word_content,
                        on_word_save=word_answer_changed,
                        section_permission=section_permissions.get(section.id, SectionAccess()),
                        on_permission_refresh=refresh_section_permissions,
                        on_fixed_edit=begin_section_edit,
                        on_fixed_saved=fixed_section_saved,
                        heading_override=heading_overrides.get(section.id),
                        on_heading_save=heading_changed,
                        inline_edit_state=inline_edit_state,
                        comment_count=comment_counts.get(section.id, 0),
                        comments=section_comments.get(section.id, ()),
                        on_comment_submit=submit_inline_comment,
                        on_comment_begin=begin_inline_comment,
                        on_comment_cancel=cancel_inline_comment,
                        comment_draft_state=comment_draft_state,
                        generated=generated_view,
                        numbering_labels=report_numbering_labels(template, current_answers),
                        review_delta=next(
                            (
                                delta_section
                                for delta_section in review_delta.sections
                                if delta_section.section_key == section.id
                            ),
                            None,
                        )
                        if review_delta is not None
                        else None,
                        review_delta_context=review_delta,
                    )

                section_refreshables[section.id] = render_section
                return render_section

            async def refresh_section_permissions() -> None:
                nonlocal section_permissions
                if report_id:
                    section_permissions = await SectionAccessService().states(report_id, user)
                inline_edit_state.update(mode=None, section_key=None)
                report_preview.refresh()

            @ui.refreshable
            def report_preview() -> None:
                current_answers = managed_report_answers(
                    template.id,
                    template.name,
                    report_answers_for(storage, storage_key),
                )
                available_documents = available_report_documents(current_answers)
                active_document = str(preview_display_state["document"])
                if active_document not in available_documents:
                    active_document = AUDIT_REPORT
                    preview_display_state["document"] = AUDIT_REPORT
                    preview_display_state["view"] = "working"
                is_main_report = active_document == AUDIT_REPORT
                preview_mode = preview_display_state["mode"]
                generated_view = (
                    is_main_report
                    and preview_mode == "expanded"
                    and preview_display_state["view"] == "generated"
                )
                preview_classes = f"report-preview report-preview--{preview_mode}"
                if generated_view:
                    preview_classes += " report-preview--generated"
                if question_display_state["collapsed"]:
                    preview_classes += " report-preview--focus"
                with (
                    ui.element("section")
                    .classes(preview_classes)
                    .props('id="report-preview-scroll"')
                ):
                    if preview_mode == "collapsed":
                        ui.button(
                            "Show preview",
                            icon="visibility",
                            on_click=lambda: set_preview_mode("normal"),
                        ).props('flat no-caps aria-label="Show live report preview"').classes(
                            "preview-show-button"
                        ).mark("preview-show")
                        return
                    with ui.row().classes("report-preview__heading items-center justify-between"):
                        with ui.column().classes("gap-0"):
                            if is_main_report:
                                ui.label(
                                    "Generated report preview"
                                    if generated_view
                                    else "Live report preview"
                                ).classes("section-title")
                                ui.label(
                                    "Clean output preview without editing marks or review controls."
                                    if generated_view
                                    else (
                                        "Use the Lock section / Unlock section controls shown "
                                        "inside each report section."
                                        if report_id
                                        and any(
                                            state.can_manage
                                            for state in section_permissions.values()
                                        )
                                        else "Blue fields remain editable; fixed wording follows "
                                        "the section's lock status."
                                    )
                                ).classes("question-progress")
                            else:
                                ui.label(REPORT_DOCUMENT_LABELS[active_document]).classes(
                                    "section-title"
                                )
                                ui.label("Additional report workspace").classes("question-progress")
                        with ui.row().classes("report-preview__controls items-center gap-1"):
                            if question_display_state["collapsed"]:
                                ui.button(
                                    "Questions",
                                    icon="arrow_back",
                                    on_click=lambda: set_question_panel_collapsed(False),
                                ).props(
                                    'flat dense no-caps aria-label="Back to report questions"'
                                ).classes("preview-return-questions").mark(
                                    "back-to-report-questions"
                                )
                            if preview_mode == "expanded" and is_main_report:
                                with (
                                    ui.element("div")
                                    .classes("preview-view-switch")
                                    .props('role="group" aria-label="Preview appearance"')
                                ):
                                    working_classes = "preview-view-switch__button"
                                    generated_classes = "preview-view-switch__button"
                                    if not generated_view:
                                        working_classes += " preview-view-switch__button--active"
                                    else:
                                        generated_classes += " preview-view-switch__button--active"
                                    ui.button(
                                        "Working preview",
                                        icon="edit_note",
                                        on_click=lambda: set_preview_view("working"),
                                    ).props(
                                        "flat dense no-caps "
                                        f'aria-pressed="{str(not generated_view).lower()}"'
                                    ).classes(working_classes).mark("preview-working-view")
                                    ui.button(
                                        "Generated report",
                                        icon="article",
                                        on_click=lambda: set_preview_view("generated"),
                                    ).props(
                                        "flat dense no-caps "
                                        f'aria-pressed="{str(generated_view).lower()}"'
                                    ).classes(generated_classes).mark("preview-generated-view")
                            export_button = (
                                ui.button("Export", icon="download")
                                .props("outline no-caps dense")
                                .classes("report-export-button")
                            )
                            with export_button:
                                if not is_main_report:
                                    ui.tooltip(
                                        "Export will be enabled when the approved source template "
                                        "is added."
                                    )
                                with ui.menu().classes("report-export-menu"):
                                    ui.label("WITH BLUE HIGHLIGHTS").classes(
                                        "report-export-menu__label"
                                    )
                                    ui.button(
                                        "Word (.docx)",
                                        icon="description",
                                        on_click=lambda: export_report("docx", highlighted=True),
                                    ).props("flat no-caps").classes(
                                        "report-export-menu__item"
                                    ).mark("export-report-word-highlighted")
                                    ui.button(
                                        "PDF (.pdf)",
                                        icon="picture_as_pdf",
                                        on_click=lambda: export_report("pdf", highlighted=True),
                                    ).props("flat no-caps").classes(
                                        "report-export-menu__item"
                                    ).mark("export-report-pdf-highlighted")
                                    ui.separator()
                                    ui.label("CLEAN / PRINT READY").classes(
                                        "report-export-menu__label"
                                    )
                                    ui.button(
                                        "Word (.docx)",
                                        icon="description",
                                        on_click=lambda: export_report("docx", highlighted=False),
                                    ).props("flat no-caps").classes(
                                        "report-export-menu__item"
                                    ).mark("export-report-word")
                                    ui.button(
                                        "PDF (.pdf)",
                                        icon="picture_as_pdf",
                                        on_click=lambda: export_report("pdf", highlighted=False),
                                    ).props("flat no-caps").classes(
                                        "report-export-menu__item"
                                    ).mark("export-report-pdf")
                            if not is_main_report:
                                export_button.disable()
                            if preview_mode == "expanded":
                                ui.button(
                                    icon="fullscreen_exit",
                                    on_click=lambda: set_preview_mode("normal"),
                                ).props(
                                    'flat round dense aria-label="Restore live preview size"'
                                ).classes("preview-display-action").mark("preview-restore")
                            else:
                                ui.button(
                                    icon="fullscreen",
                                    on_click=lambda: set_preview_mode("expanded"),
                                ).props(
                                    'flat round dense aria-label="Enlarge live preview"'
                                ).classes("preview-display-action").mark("preview-enlarge")
                            if not question_display_state["collapsed"]:
                                ui.button(
                                    icon="visibility_off",
                                    on_click=lambda: set_preview_mode("collapsed"),
                                ).props(
                                    'flat round dense aria-label="Collapse live preview"'
                                ).classes("preview-display-action").mark("preview-collapse")
                            edit_count = (
                                len(fixed_changes) + len(word_changes) if is_main_report else 0
                            )
                            if edit_count:
                                ui.label(
                                    f"{edit_count} attributed edit{'s' if edit_count != 1 else ''}"
                                ).classes("controlled-change-count")
                    if len(available_documents) > 1:
                        with (
                            ui.element("nav")
                            .classes("report-document-tabs")
                            .props('aria-label="Report documents"')
                        ):
                            for document_kind in available_documents:
                                tab_classes = "report-document-tab"
                                document_is_active = document_kind == active_document
                                if document_is_active:
                                    tab_classes += " report-document-tab--active"
                                ui.button(
                                    REPORT_DOCUMENT_LABELS[document_kind],
                                    icon=(
                                        "description"
                                        if document_kind == AUDIT_REPORT
                                        else "post_add"
                                    ),
                                    on_click=lambda kind=document_kind: set_report_document(kind),
                                ).props(
                                    "flat dense no-caps "
                                    f'aria-current="{str(document_is_active).lower()}"'
                                ).classes(tab_classes).mark(f"report-document-{document_kind}")
                    if not is_main_report:
                        _render_pending_supplementary_report(active_document)
                        return
                    if not generated_view:
                        _render_report_editing_toolbar(
                            replace_report_text,
                            replace_enabled=can_edit_answers
                            or bool(access and access.can_edit_fixed_sections),
                        )
                        review_delta_summary()
                        _render_edit_highlight_legend(show_review_delta=review_delta is not None)
                        if report_id and any(
                            state.can_manage for state in section_permissions.values()
                        ):
                            _render_admin_section_lock_guide()
                    section_list_classes = "report-preview__sections gap-4"
                    if generated_view:
                        section_list_classes += " report-preview__sections--generated"
                    section_list = ui.column().classes(section_list_classes)
                    if generated_view:
                        section_list.mark("generated-report-document").props(
                            'aria-label="Generated report document preview"'
                        )
                    with section_list:
                        for section in template.sections:
                            if not source_section_is_visible(section, current_answers):
                                continue
                            section_refreshable(section)()

            async def export_report(export_format: str, *, highlighted: bool) -> None:
                current_answers = managed_report_answers(
                    template.id,
                    template.name,
                    report_answers_for(storage, storage_key),
                )
                overrides = section_overrides_for(storage, storage_key)
                stage = _export_stage_label(access, storage, storage_key)
                service = ReportExportService()
                ui.notify(
                    f"Preparing {'blue-highlighted' if highlighted else 'clean'} "
                    f"{export_format.upper()} export...",
                    type="info",
                    position="top-right",
                )
                try:
                    exporter = service.export_pdf if export_format == "pdf" else service.export_docx
                    artifact = await run.io_bound(
                        exporter,
                        template,
                        current_answers,
                        overrides,
                        heading_overrides=heading_overrides,
                        stage=stage,
                        report_id=report_id,
                        highlighted=highlighted,
                    )
                except ReportExportError as error:
                    ui.notify(str(error), type="negative", position="top-right")
                    return
                if artifact is None:
                    ui.notify(
                        "The export could not be generated.",
                        type="negative",
                        position="top-right",
                    )
                    return
                ui.download(
                    artifact.content,
                    filename=artifact.filename,
                    media_type=artifact.media_type,
                )

            @ui.refreshable
            def submission_panel() -> None:
                readiness = approval_state["readiness"]
                target_stage = (
                    readiness.target_stage
                    if readiness is not None
                    else next_workflow_stage_for(storage, storage_key)
                )
                missing = (
                    readiness.missing_fields
                    if readiness is not None
                    else missing_questions_for_submission(storage, storage_key, questions)
                )
                missing_labels = _missing_labels(missing, questions)
                with ui.element("section").classes("submission-panel"):
                    if target_stage is None:
                        ui.label("Final approval reached").classes("submission-panel__complete")
                        return

                    if missing_labels:
                        with (
                            ui.column()
                            .classes("submission-panel__validation gap-1")
                            .props('role="status" aria-live="polite"')
                        ):
                            ui.label(
                                f"{len(missing_labels)} required answer"
                                f"{'s' if len(missing_labels) != 1 else ''} remaining"
                            ).classes("submission-panel__validation-title")
                            ui.label(", ".join(missing_labels)).classes(
                                "submission-panel__validation-list"
                            )

                    async def submit() -> None:
                        try:
                            if report_id:
                                transition = await ApprovalService().submit(report_id, user.email)
                                submitted_stage = transition.active_stage or "Final Approval"
                                approval_state["readiness"] = None
                            else:
                                submitted_stage = submit_for_approval(
                                    storage, storage_key, questions
                                )
                        except DraftSubmissionError as error:
                            ui.notify(str(error), type="warning", position="top-right")
                            return
                        except ValueError as error:
                            ui.notify(str(error), type="info", position="top-right")
                            return
                        report_preview.refresh()
                        workflow_progress.refresh()
                        submission_panel.refresh()
                        ui.notify(
                            f"Report sent to {submitted_stage}.",
                            type="positive",
                            position="top-right",
                        )
                        if report_id:
                            ui.navigate.to(return_path)

                    async def confirm_submission() -> None:
                        dialog = ui.dialog().props("persistent")
                        with dialog, ui.card().classes("section-editor-dialog"):
                            ui.label("Submit report?").classes("report-dialog__title")
                            ui.label("Are you sure you want to submit?").classes(
                                "report-dialog__subtitle"
                            )
                            ui.label(f"The report will be submitted to {target_stage}.").classes(
                                "report-dialog__subtitle"
                            )

                            with ui.row().classes("w-full justify-end gap-2"):
                                (
                                    ui.button("No", on_click=lambda: dialog.submit(False))
                                    .props("flat no-caps")
                                    .mark("cancel-report-submission")
                                )
                                (
                                    ui.button(
                                        "Yes",
                                        icon="send",
                                        on_click=lambda: dialog.submit(True),
                                    )
                                    .props("unelevated no-caps")
                                    .classes("primary-action")
                                    .mark("confirm-report-submission")
                                )
                        if await dialog:
                            await submit()
                        dialog.delete()

                    with ui.row().classes("submission-panel__actions items-center gap-2"):
                        (
                            ui.button(
                                "Next",
                                icon="arrow_forward",
                                on_click=lambda: set_question_panel_collapsed(True),
                            )
                            .props("outline no-caps")
                            .classes("question-focus-action")
                            .mark("focus-report-preview")
                        )

                        button = (
                            ui.button("Submit", icon="send", on_click=confirm_submission)
                            .props("unelevated no-caps")
                            .classes("primary-action submission-panel__button")
                            .mark("submit-report-approval")
                        )
                        if missing:
                            button.disable()
                            with button:
                                ui.tooltip(
                                    f"Complete {len(missing)} remaining questions before "
                                    f"submitting to {target_stage}."
                                )
                        else:
                            with button:
                                ui.tooltip(f"Submit to {target_stage}")

            @ui.refreshable
            def review_panel() -> None:
                if access is None or not access.can_view or report_id is None:
                    return
                with ui.element("section").classes("submission-panel"):
                    if access.can_approve:
                        with ui.column().classes("submission-panel__validation gap-1"):
                            ui.label(f"Assigned stage: {access.active_stage}").classes(
                                "submission-panel__validation-title"
                            )
                            ui.label(f"Received from: {access.received_from}").classes(
                                "submission-panel__validation-list"
                            )
                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button(
                                "Return for Changes",
                                icon="assignment_return",
                                on_click=lambda: _open_return_dialog(
                                    report_id, user.email, timeline
                                ),
                            ).props("outline no-caps").classes("review-return-button")
                            ui.button(
                                "Approve",
                                icon="task_alt",
                                on_click=lambda: _open_approve_dialog(report_id, user.email),
                            ).props("unelevated no-caps").classes("primary-action")
                    elif access.is_completed_review:
                        ui.label("Completed review · read-only access").classes(
                            "submission-panel__complete"
                        )
                    else:
                        ui.label("This report is read-only at its current stage.").classes(
                            "submission-panel__complete"
                        )

            async def basic_answer_changed(field_name: str, value: Any) -> None:
                scroll_positions = await _workspace_scroll_positions()
                previous_answers = report_answers_for(storage, storage_key)
                previous_visible_fields = tuple(
                    question.field_name
                    for question in input_basic_questions
                    if is_applicable(question.field_name, previous_answers)
                )
                await _answer_changed(
                    storage,
                    storage_key,
                    template.id,
                    field_name,
                    value,
                    (submission_panel,),
                    report_id=report_id,
                    actor_email=user.email,
                    approval_state=approval_state,
                )
                current_answers = report_answers_for(storage, storage_key)
                if preview_display_state["document"] not in available_report_documents(
                    current_answers
                ):
                    preview_display_state["document"] = AUDIT_REPORT
                    preview_display_state["view"] = "working"
                current_visible_fields = tuple(
                    question.field_name
                    for question in input_basic_questions
                    if is_applicable(question.field_name, current_answers)
                )
                if (
                    field_name in {"caro_applicable", "internal_financial_controls_reporting"}
                    or current_visible_fields != previous_visible_fields
                ):
                    question_panel.refresh()
                report_preview.refresh()
                await _restore_workspace_scroll(scroll_positions)
                await _scroll_preview_to_answer(field_name)

            async def word_answer_changed(
                field_name: str,
                value: Any,
                preview_marker: str | None = None,
                reason: str | None = None,
                refresh_preview: bool = True,
            ) -> None:
                nonlocal word_changes
                scroll_positions = await _workspace_scroll_positions() if refresh_preview else None
                await _answer_changed(
                    storage,
                    storage_key,
                    template.id,
                    field_name,
                    value,
                    (submission_panel,),
                    report_id=report_id,
                    actor_email=user.email,
                    actor_role=user.role,
                    change_reason=reason,
                    approval_state=approval_state,
                )
                if report_id:
                    word_changes = await ReportPersistenceService().answer_changes(
                        report_id, template.id
                    )
                if refresh_preview:
                    report_preview.refresh()
                    assert scroll_positions is not None
                    await _restore_workspace_scroll(scroll_positions)
                    await _scroll_preview_to_answer(field_name, marker=preview_marker)

            async def heading_changed(section_key: str, value: str) -> None:
                nonlocal fixed_changes
                if report_id is None or access is None or not access.can_edit_fixed_sections:
                    ui.notify(
                        "This section heading is read-only.",
                        type="warning",
                        position="top-right",
                    )
                    return
                section = next(
                    (item for item in template.sections if item.id == section_key),
                    None,
                )
                if section is None:
                    return
                current_answers = managed_report_answers(
                    template.id,
                    template.name,
                    report_answers_for(storage, storage_key),
                )
                source_heading = (
                    format_answer_for_preview(current_answers[section.heading_field_name])
                    if section.heading_field_name and section.heading_field_name in current_answers
                    else section.heading
                )
                if " ".join(value.split()) == heading_overrides.get(section_key, source_heading):
                    return
                try:
                    await ReportCollaborationService().save_section_heading(
                        public_id=report_id,
                        template_key=template.id,
                        section_key=section_key,
                        heading=value,
                        profile=user,
                    )
                except (PermissionError, ValueError) as error:
                    ui.notify(str(error), type="negative", position="top-right")
                    await refresh_preview_sections(section_key)
                    return
                heading_overrides[section_key] = " ".join(value.split())
                fixed_changes = await ReportCollaborationService().section_changes(
                    report_id, user, access=access
                )
                await refresh_preview_sections(section_key)

            async def replace_report_text(query: str, replacement: str) -> None:
                nonlocal fixed_changes, word_changes
                normalized_query = str(query or "").strip()
                if not normalized_query:
                    ui.notify(
                        "Enter text to find before replacing.",
                        type="warning",
                        position="top-right",
                    )
                    return
                try:
                    browser_result = await ui.run_javascript(
                        f"window.qcReportFind({json.dumps(normalized_query)}, 0, false)",
                        timeout=2.0,
                    )
                except (RuntimeError, TimeoutError):
                    browser_result = {}
                found_count = (
                    int(browser_result.get("total", 0)) if isinstance(browser_result, dict) else 0
                )
                protected_count = (
                    int(browser_result.get("protected", 0))
                    if isinstance(browser_result, dict)
                    else 0
                )
                if not found_count:
                    try:
                        await ui.run_javascript(
                            "window.qcReportReplaceComplete(0, 0, 0)",
                            timeout=2.0,
                        )
                    except (RuntimeError, TimeoutError):
                        pass
                    ui.notify(
                        "No matching report text was found.",
                        type="info",
                        position="top-right",
                    )
                    return

                current_answers = managed_report_answers(
                    template.id,
                    template.name,
                    report_answers_for(storage, storage_key),
                )
                plan = _report_replacement_plan(
                    template,
                    current_answers,
                    section_overrides_for(storage, storage_key),
                    heading_overrides,
                    normalized_query,
                    str(replacement or ""),
                )
                can_edit_fixed = bool(
                    report_id and access is not None and access.can_edit_fixed_sections
                )
                if report_id:
                    fresh_permissions = await SectionAccessService().states(report_id, user)
                else:
                    fresh_permissions = {}
                permitted_plan = _permitted_report_replacement_plan(
                    plan,
                    can_edit_answers=can_edit_answers,
                    can_edit_fixed=can_edit_fixed,
                    section_permissions=fresh_permissions,
                )
                answer_updates = permitted_plan.answer_updates
                section_updates = permitted_plan.section_updates
                heading_updates = permitted_plan.heading_updates
                if not (answer_updates or section_updates or heading_updates):
                    try:
                        await ui.run_javascript(
                            f"window.qcReportReplaceComplete({found_count}, 0, {found_count})",
                            timeout=2.0,
                        )
                    except (RuntimeError, TimeoutError):
                        pass
                    ui.notify(
                        "Matches were found, but none are editable at the current report stage.",
                        type="warning",
                        position="top-right",
                    )
                    return

                reason = f'Find and replace: "{normalized_query}"'
                persistence = ReportPersistenceService()
                collaboration = ReportCollaborationService()
                for field_name, value in answer_updates.items():
                    save_answer(storage, storage_key, field_name, value)
                    if report_id:
                        await persistence.save_answer(
                            public_id=report_id,
                            template_key=template.id,
                            field_name=field_name,
                            value=value,
                            actor_email=user.email,
                            change_reason=reason,
                            actor_role=user.role,
                        )
                if report_id:
                    for section_key, content in section_updates.items():
                        await collaboration.save_section(
                            public_id=report_id,
                            template_key=template.id,
                            section_key=section_key,
                            content=content,
                            profile=user,
                            reason=reason,
                        )
                        set_section_override(storage, storage_key, section_key, content)
                    for section_key, heading in heading_updates.items():
                        await collaboration.save_section_heading(
                            public_id=report_id,
                            template_key=template.id,
                            section_key=section_key,
                            heading=heading,
                            profile=user,
                            reason=reason,
                        )
                        heading_overrides[section_key] = heading
                    fixed_changes = await collaboration.section_changes(
                        report_id, user, access=access
                    )
                    word_changes = await persistence.answer_changes(report_id, template.id)
                    if access is not None and access.is_owner:
                        approval_state["readiness"] = await ApprovalService().submission_readiness(
                            report_id
                        )
                else:
                    replaced_fields = set(answer_updates)
                    replacement_time = datetime.now(UTC)
                    section_by_field = {
                        field_name: section.id
                        for section in template.sections
                        for field_name in _section_answer_fields(section)
                    }
                    word_changes = (
                        *(
                            AnswerChangeSummary(
                                field_name=field_name,
                                section_key=section_by_field.get(field_name),
                                actor_label=user.name,
                                actor_role=user.role,
                                reason=reason,
                                changed_at=replacement_time,
                            )
                            for field_name in answer_updates
                        ),
                        *(
                            change
                            for change in word_changes
                            if change.field_name not in replaced_fields
                        ),
                    )
                await refresh_preview_sections(*(section.id for section in template.sections))
                submission_panel.refresh()
                replaced_count = permitted_plan.changed_value_count
                try:
                    await ui.run_javascript(
                        "window.qcReportReplaceComplete("
                        f"{found_count}, {replaced_count}, {protected_count})",
                        timeout=2.0,
                    )
                except (RuntimeError, TimeoutError):
                    pass
                skipped_message = (
                    f" {protected_count} protected match"
                    f"{'es were' if protected_count != 1 else ' was'} left unchanged."
                    if protected_count
                    else ""
                )
                ui.notify(
                    f"Found {found_count} and replaced {replaced_count} occurrence"
                    f"{'s' if replaced_count != 1 else ''}.{skipped_message}",
                    type="warning" if skipped_message else "positive",
                    position="top-right",
                )

            def reset_inline_edit_state() -> None:
                inline_edit_state.update(
                    {
                        "mode": None,
                        "section_key": None,
                        "field_name": None,
                        "value": None,
                        "preview_marker": None,
                    }
                )

            async def begin_section_edit(section_key: str) -> None:
                previous_section_key = inline_edit_state.get("section_key")
                inline_edit_state.update(
                    {
                        "mode": "section",
                        "section_key": section_key,
                        "field_name": None,
                        "value": None,
                        "preview_marker": None,
                    }
                )
                await refresh_preview_sections(previous_section_key, section_key)
                await _scroll_preview_to_section(section_key)

            async def fixed_section_saved(
                section_key: str,
                *,
                finish: bool = False,
                fixed_wording_changed: bool = False,
            ) -> tuple[SectionChangeSummary, ...]:
                nonlocal fixed_changes
                if report_id and fixed_wording_changed:
                    fixed_changes = await ReportCollaborationService().section_changes(
                        report_id, user, access=access
                    )
                if finish:
                    reset_inline_edit_state()
                    report_preview.refresh()
                    await _scroll_preview_to_section(section_key)
                return tuple(
                    change for change in fixed_changes if change.section_key == section_key
                )

            async def begin_inline_comment(
                section_key: str,
                selected_text: str,
                selection_start: int,
            ) -> None:
                previous_section_key = comment_draft_state.get("section_key")
                comment_draft_state.update(
                    {
                        "section_key": section_key,
                        "selected_text": selected_text,
                        "selection_start": selection_start,
                    }
                )
                await refresh_preview_sections(previous_section_key, section_key)
                await _scroll_preview_to_section(section_key)

            async def cancel_inline_comment() -> None:
                section_key = comment_draft_state.get("section_key")
                comment_draft_state.update(
                    {
                        "section_key": None,
                        "selected_text": None,
                        "selection_start": None,
                    }
                )
                await refresh_preview_sections(section_key)

            async def submit_inline_comment(
                section_key: str,
                body: str,
                selected_text: str | None = None,
                selection_start: int | None = None,
            ) -> bool:
                if report_id is None:
                    return False
                try:
                    await ReportCollaborationService().add_comment(
                        public_id=report_id,
                        section_key=section_key,
                        body=body,
                        profile=user,
                        selected_text=selected_text,
                        selection_start=selection_start,
                    )
                except (PermissionError, ValueError) as error:
                    ui.notify(str(error), type="negative", position="top-right")
                    return False
                ui.notify("Comment added.", type="positive", position="top-right")
                comment_draft_state.update(
                    {
                        "section_key": None,
                        "selected_text": None,
                        "selection_start": None,
                    }
                )
                await comment_added(section_key)
                return True

            @ui.refreshable
            def question_panel() -> None:
                current_answers = report_answers_for(storage, storage_key)
                panel_classes = "question-panel"
                if question_display_state["collapsed"]:
                    panel_classes += " question-panel--collapsed"
                with (
                    ui.element("aside").classes(panel_classes).props('id="report-question-scroll"')
                ) as panel:
                    question_display_state["panel"] = panel
                    if can_manage_submission:
                        visible_questions = tuple(
                            question
                            for question in input_basic_questions
                            if is_applicable(question.field_name, current_answers)
                        )
                        answered_count = sum(
                            1
                            for question in visible_questions
                            if has_answer(
                                current_answers.get(question.field_name, question.default_value)
                            )
                        )
                        with ui.row().classes(
                            "question-panel__heading items-center justify-between"
                        ):
                            with ui.column().classes("gap-0"):
                                ui.label("Report questions").classes("section-title")
                                ui.label(
                                    f"{answered_count} of {len(visible_questions)} answered"
                                ).classes("question-progress")
                        with ui.column().classes("question-list gap-4"):
                            for question in visible_questions:
                                _question_control(
                                    question,
                                    current_answers.get(
                                        question.field_name, question.default_value
                                    ),
                                    basic_answer_changed,
                                    editable=True,
                                )
                                report_kind = supplementary_report_for_question(
                                    question.field_name,
                                    current_answers,
                                )
                                if report_kind:
                                    _render_supplementary_report_entry(
                                        report_kind,
                                        lambda kind=report_kind: open_report_document(kind),
                                    )
                        submission_panel()
                    else:
                        review_panel()

            workspace_classes = "report-preparation-grid"
            if question_display_state["collapsed"]:
                workspace_classes += " report-preparation-grid--preview-only"
            with ui.element("div").classes(workspace_classes) as workspace:
                question_display_state["workspace"] = workspace
                question_panel()
                report_preview()


def _export_stage_label(
    access: ReportAccess | None,
    storage: MutableMapping[str, Any],
    storage_key: str,
) -> str:
    """Use the active approval stage in exported filenames and document metadata."""
    if access is not None:
        if access.active_stage:
            return access.active_stage
        status = access.report_status.value.replace("_", " ").title()
        return {
            "Draft": "Preparation",
            "Returned": "Returned for Changes",
        }.get(status, status)
    return workflow_stage_for(storage, storage_key)


async def _workspace_scroll_positions() -> dict[str, float]:
    """Capture both independent workspace scroll containers before a refresh."""
    try:
        positions = await ui.run_javascript(
            """
            (() => {
              const ids = ['report-question-scroll', 'report-preview-scroll'];
              return Object.fromEntries(ids.map((id) => {
                const element = document.getElementById(id);
                return [id, element ? element.scrollTop : 0];
              }));
            })()
            """,
            timeout=1.0,
        )
    except (RuntimeError, TimeoutError):
        return {}
    if not isinstance(positions, dict):
        return {}
    return {
        str(element_id): float(scroll_top)
        for element_id, scroll_top in positions.items()
        if isinstance(scroll_top, (int, float))
    }


async def _restore_workspace_scroll(positions: dict[str, float]) -> None:
    """Restore scroll after NiceGUI has replaced the refreshed DOM nodes."""
    if not positions:
        return
    payload = json.dumps(positions)
    try:
        await ui.run_javascript(
            f"""
            new Promise((resolve) => {{
              const positions = {payload};
              let frame = 0;
              const restore = () => {{
                Object.entries(positions).forEach(([id, scrollTop]) => {{
                  const element = document.getElementById(id);
                  if (element) element.scrollTop = scrollTop;
                }});
                frame += 1;
                if (frame < 4) requestAnimationFrame(restore);
                else resolve(true);
              }};
              requestAnimationFrame(restore);
            }})
            """,
            timeout=2.0,
        )
    except (RuntimeError, TimeoutError):
        return


async def _scroll_preview_to_answer(
    field_name: str,
    *,
    marker: str | None = None,
) -> bool:
    """Center the refreshed preview on the wording changed in report questions."""
    requested_marker = marker or field_name
    base_marker = f"preview-document-answer-{field_name}"
    marker_value = (
        requested_marker
        if requested_marker.startswith("preview-document-answer-")
        else f"preview-document-answer-{requested_marker}"
    )
    payload = json.dumps({"marker": marker_value, "base": base_marker})
    try:
        result = await ui.run_javascript(
            f"""
            new Promise((resolve) => {{
              const requested = {payload};
              let attempt = 0;
              const locate = () => {{
                const preview = document.getElementById('report-preview-scroll');
                if (!preview) {{
                  resolve(false);
                  return;
                }}
                const marked = Array.from(preview.querySelectorAll('[data-preview-marker]'));
                let target = marked.find(
                  (element) => element.getAttribute('data-preview-marker') === requested.marker
                );
                if (!target) {{
                  const candidates = marked.filter((element) => {{
                    const value = element.getAttribute('data-preview-marker') || '';
                    return value === requested.base || value.startsWith(requested.base + '-');
                  }});
                  target = candidates.sort((left, right) => {{
                    const center = preview.getBoundingClientRect().top + preview.clientHeight / 2;
                    return Math.abs(left.getBoundingClientRect().top - center)
                      - Math.abs(right.getBoundingClientRect().top - center);
                  }})[0];
                }}
                if (!target && attempt < 24) {{
                  attempt += 1;
                  requestAnimationFrame(locate);
                  return;
                }}
                if (!target) {{
                  resolve(false);
                  return;
                }}
                const previewRect = preview.getBoundingClientRect();
                const targetRect = target.getBoundingClientRect();
                const top = preview.scrollTop + targetRect.top - previewRect.top
                  - (preview.clientHeight - targetRect.height) / 2;
                preview.scrollTo({{top: Math.max(0, top), behavior: 'smooth'}});
                target.classList.remove('preview-scroll-target');
                void target.offsetWidth;
                target.classList.add('preview-scroll-target');
                window.setTimeout(
                  () => target.classList.remove('preview-scroll-target'),
                  1600,
                );
                resolve(true);
              }};
              requestAnimationFrame(locate);
            }})
            """,
            timeout=2.0,
        )
    except (RuntimeError, TimeoutError):
        return False
    return result is True


async def _scroll_preview_to_section(section_key: str) -> bool:
    """Center a newly commented section and briefly emphasize it."""
    marker = f"preview-section-{section_key}"
    payload = json.dumps(marker)
    try:
        result = await ui.run_javascript(
            f"""
            new Promise((resolve) => {{
              const marker = {payload};
              let attempt = 0;
              const locate = () => {{
                const preview = document.getElementById('report-preview-scroll');
                const target = preview?.querySelector(
                  `[data-preview-section="${{marker}}"]`
                );
                if (!target && attempt < 24) {{
                  attempt += 1;
                  requestAnimationFrame(locate);
                  return;
                }}
                if (!preview || !target) {{
                  resolve(false);
                  return;
                }}
                const previewRect = preview.getBoundingClientRect();
                const targetRect = target.getBoundingClientRect();
                const top = preview.scrollTop + targetRect.top - previewRect.top
                  - (preview.clientHeight - targetRect.height) / 2;
                preview.scrollTo({{top: Math.max(0, top), behavior: 'smooth'}});
                target.classList.remove('preview-comment-target');
                void target.offsetWidth;
                target.classList.add('preview-comment-target');
                window.setTimeout(
                  () => target.classList.remove('preview-comment-target'),
                  1800,
                );
                resolve(true);
              }};
              requestAnimationFrame(locate);
            }})
            """,
            timeout=2.0,
        )
    except (RuntimeError, TimeoutError):
        return False
    return result is True


def _source_cell_text(segments: tuple[Any, ...]) -> str:
    return "".join(segment.text for segment in segments if segment.role != "guidance")


def _normalise_table_rows(value: Any, column_count: int) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    rows: list[list[str]] = []
    for item in value:
        if not isinstance(item, (list, tuple)):
            continue
        row = [str(cell or "") for cell in item[:column_count]]
        row.extend([""] * (column_count - len(row)))
        rows.append(row)
    return rows


def _question_control(
    question: ReportQuestion,
    value: Any,
    on_change: Any,
    *,
    editable: bool = True,
) -> Any:
    required = ENFORCE_ANSWER_COMPLETENESS and is_review_required(
        question.field_name, source_required=question.required
    )
    label = question.label + (" *" if required else "")
    control: Any
    if question.field_type in {"select", "yes_no"}:
        options = list(question.options)
        if has_answer(value) and value not in options:
            options.append(value)
        locked_framework = (
            question.field_name == "applicable_financial_reporting_framework"
            and options == [AS_IGAAP]
        )
        control = ui.select(options, value=value, label=label).props(
            "outlined readonly" if locked_framework else "outlined clearable"
        )
    elif question.field_type == "multiselect":
        options = list(question.options)
        selected_values = value if isinstance(value, list) else ([] if value is None else [value])
        options.extend(item for item in selected_values if item not in options)
        control = ui.select(options, value=selected_values, label=label).props(
            "outlined multiple use-chips"
        )
    elif question.field_type == "checkbox":
        control = ui.checkbox(label, value=bool(value))
    elif question.field_type == "radio":
        options = list(question.options)
        if has_answer(value) and value not in options:
            options.append(value)
        control = ui.radio(options, value=value).props("inline")
    elif question.field_type == "number":
        control = ui.number(label=label, value=value).props("outlined")
    elif question.field_type == "date":
        control = ui.input(label=label, value=value).props("outlined type=date")
    elif question.field_type == "textarea":
        control = ui.textarea(label=label, value=value).props("outlined autogrow")
    else:
        control = ui.input(label=label, value=value).props("outlined")
    if question.field_type in {"text", "textarea", "number", "date"}:
        control.props("debounce=250")
    control.classes("question-control").mark(f"report-question-{question.field_name}")
    if editable:

        def value_changed(event: Any, field_name: str = question.field_name) -> Any:
            return on_change(field_name, event.value)

        control.on_value_change(value_changed)
    else:
        control.disable()
    if question.help_text:
        ui.label(question.help_text).classes("question-help")
    return control


def _show_basic_question(field_name: str, template: ReportTemplate) -> bool:
    if field_name in HIDDEN_BASIC_FIELDS:
        return False
    if field_name in CFS_DETAIL_FIELDS and not is_consolidated_template(template.id, template.name):
        return False
    return True


def _adapt_basic_question(question: ReportQuestion, template: ReportTemplate) -> ReportQuestion:
    if question.field_name == "financial_year":
        return replace(
            question,
            field_type="select",
            options=financial_year_options(),
            help_text="Select the Indian financial year covered by this report.",
        )
    if question.field_name == "applicable_financial_reporting_framework":
        options = framework_options(template.id, template.name)
        return replace(
            question,
            field_type="select",
            options=options,
            default_value=AS_IGAAP if len(options) == 1 else None,
            help_text=(
                "Bank audit reports use AS (IGAAP)."
                if len(options) == 1
                else "The selection updates framework wording throughout the report."
            ),
        )
    if question.field_name == "udin":
        return replace(
            question,
            field_type="text",
            label="UDIN",
            help_text="Enter the UDIN to be printed in the signing block.",
        )
    return question


def _render_supplementary_report_entry(kind: str, on_open: Any) -> None:
    label = REPORT_DOCUMENT_LABELS[kind]
    with ui.element("div").classes("supplementary-report-entry"):
        with ui.row().classes("items-center gap-2 min-w-0"):
            ui.icon("post_add").props('aria-hidden="true"').classes(
                "supplementary-report-entry__icon"
            )
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(f"{label} added").classes("supplementary-report-entry__title")
                ui.label("Available in the report workspace; no file upload is needed.").classes(
                    "supplementary-report-entry__hint"
                )
        ui.button(
            f"Open {label}",
            icon="arrow_forward",
            on_click=on_open,
        ).props("flat dense no-caps").classes("supplementary-report-entry__action").mark(
            f"open-{kind}"
        )


def _render_pending_supplementary_report(kind: str) -> None:
    label = REPORT_DOCUMENT_LABELS[kind]
    with (
        ui.element("section")
        .classes("supplementary-report-workspace")
        .props('role="status" aria-live="polite"')
        .mark(f"supplementary-workspace-{kind}")
    ):
        ui.icon("article").props('aria-hidden="true"').classes(
            "supplementary-report-workspace__icon"
        )
        ui.label(f"{label} is ready for its approved template").classes(
            "supplementary-report-workspace__title"
        )
        ui.label(
            "The report has been added to this engagement because the corresponding answer is "
            "Yes. Its editable wording will appear here after the approved source document is "
            "configured."
        ).classes("supplementary-report-workspace__description")
        with ui.element("div").classes("supplementary-report-workspace__note"):
            ui.icon("check_circle").props('aria-hidden="true"')
            ui.label("No supporting-file upload is required from the preparer.")


def _missing_labels(
    missing: tuple[str, ...],
    questions: tuple[ReportQuestion, ...],
) -> tuple[str, ...]:
    labels_by_key = {
        **{question.field_name: question.label for question in questions},
    }
    return tuple(labels_by_key.get(field_name, field_name) for field_name in missing)


async def _answer_changed(
    storage: MutableMapping[str, Any],
    storage_key: str,
    template_id: str,
    field_name: str,
    value: Any,
    refreshables: tuple[Any, ...],
    *,
    report_id: str | None,
    actor_email: str,
    approval_state: dict[str, SubmissionReadiness | None],
    actor_role: str | None = None,
    change_reason: str | None = None,
) -> None:
    save_answer(storage, storage_key, field_name, value)
    # Refresh optimistically so the preview never waits for database persistence.
    for refreshable in refreshables:
        refreshable.refresh()
    if report_id:
        try:
            await ReportPersistenceService().save_answer(
                public_id=report_id,
                template_key=template_id,
                field_name=field_name,
                value=value,
                actor_email=actor_email,
                change_reason=change_reason,
                actor_role=actor_role,
            )
            approval_state["readiness"] = await ApprovalService().submission_readiness(report_id)
        except (PermissionError, ValueError) as error:
            ui.notify(str(error), type="negative", position="top-right")
        for refreshable in refreshables:
            refreshable.refresh()


def _render_report_editing_toolbar(on_replace: Any, *, replace_enabled: bool) -> None:
    """Render one Word-like editing and search toolbar for the whole report."""

    with (
        ui.element("section")
        .classes("report-editing-toolbar")
        .props('aria-label="Report editing tools"')
    ):
        with ui.row().classes("report-editing-toolbar__commands items-center gap-1"):
            for command, icon, label in (
                ("undo", "undo", "Undo"),
                ("redo", "redo", "Redo"),
            ):
                button = (
                    ui.button(label, icon=icon)
                    .props(f'flat dense no-caps aria-label="{label} current edit"')
                    .classes("report-editing-toolbar__button")
                    .mark(f"report-{command}")
                )
                button.on("mousedown", js_handler="(event) => event.preventDefault()")
                button.on(
                    "click",
                    js_handler=_report_editor_history_button_handler(command),
                )
            ui.separator().props("vertical inset").classes("report-editing-toolbar__separator")
            (
                ui.button("Find", icon="search")
                .props("flat dense no-caps")
                .classes("report-editing-toolbar__button")
                .mark("report-find")
                .on(
                    "click",
                    js_handler="() => window.qcToggleReportFind('find')",
                )
            )
            replace_mode = (
                ui.button("Find & Replace", icon="find_replace")
                .props("flat dense no-caps")
                .classes("report-editing-toolbar__button")
                .mark("report-find-replace")
            )
            replace_mode.on(
                "click",
                js_handler="() => window.qcToggleReportFind('replace')",
            )
            if not replace_enabled:
                replace_mode.disable()
                with replace_mode:
                    ui.tooltip("Find & Replace is unavailable while this report is read-only.")

        with (
            ui.element("div")
            .classes("report-find-panel")
            .props(
                'id="report-find-panel" data-mode="" '
                f'data-replace-enabled="{str(replace_enabled).lower()}"'
            )
        ):
            find_input = (
                ui.input(label="Find in full report")
                .props('id="report-find-input" outlined dense clearable')
                .classes("report-find-panel__input report-find-panel__find-input")
                .mark("report-find-input")
            )
            with ui.row().classes("report-find-panel__navigation items-center gap-0"):
                ui.button(icon="keyboard_arrow_up").props(
                    'flat round dense aria-label="Previous match"'
                ).on(
                    "click",
                    js_handler=(
                        "() => window.qcReportFind("
                        "document.querySelector("
                        "'.report-find-panel__find-input input')?.value || '', -1)"
                    ),
                )
                ui.button(icon="keyboard_arrow_down").props(
                    'flat round dense aria-label="Next match"'
                ).on(
                    "click",
                    js_handler=(
                        "() => window.qcReportFind("
                        "document.querySelector("
                        "'.report-find-panel__find-input input')?.value || '', 1)"
                    ),
                )
            with (
                ui.element("div")
                .classes("report-replace-fields")
                .props('id="report-replace-fields" hidden')
            ):
                replace_input = (
                    ui.input(label="Replace with")
                    .props("outlined dense")
                    .classes("report-find-panel__input")
                    .mark("report-replace-input")
                )
                replace_button = (
                    ui.button(
                        "Replace all",
                        icon="published_with_changes",
                        on_click=lambda: on_replace(
                            str(find_input.value or ""),
                            str(replace_input.value or ""),
                        ),
                    )
                    .props("unelevated dense no-caps")
                    .classes("report-find-panel__replace primary-action")
                    .mark("report-replace-all")
                )
                if not replace_enabled:
                    replace_button.disable()
            with ui.element("div").classes("report-find-panel__summary"):
                ui.label("Enter text to search the full report").classes(
                    "report-find-panel__status"
                ).props('id="report-find-status" role="status" aria-live="polite"')
                with (
                    ui.element("div")
                    .classes("report-find-panel__access-key")
                    .props('id="report-find-access-key" hidden aria-label="Match access key"')
                ):
                    ui.label("Editable · will be replaced").classes(
                        "report-find-panel__key report-find-panel__key--editable"
                    )
                    ui.label("Protected · stays unchanged").classes(
                        "report-find-panel__key report-find-panel__key--protected"
                    )


def _render_review_delta_summary(
    delta: ReviewDelta,
    *,
    current_index: int,
    on_previous: Any,
    on_next: Any,
    on_open: Any,
) -> None:
    changed_sections = delta.report_sections
    with (
        ui.element("aside")
        .classes("review-delta-summary")
        .props('role="status" aria-label="Changes since the report was returned"')
        .mark("review-delta-summary")
    ):
        with ui.row().classes("review-delta-summary__message items-center no-wrap"):
            ui.icon("difference").classes("review-delta-summary__icon")
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(
                    f"{delta.total_changes} new change"
                    f"{'s' if delta.total_changes != 1 else ''} since return"
                ).classes("review-delta-summary__title")
                ui.label(f"Returned by {delta.returned_by} · {delta.returned_stage}").classes(
                    "review-delta-summary__meta"
                )
        with ui.row().classes("review-delta-summary__actions items-center no-wrap"):
            if changed_sections:
                bounded_index = min(max(current_index, 0), len(changed_sections) - 1)
                ui.label(f"{bounded_index + 1}/{len(changed_sections)} sections").classes(
                    "review-delta-summary__position"
                )
                ui.button(icon="arrow_upward", on_click=on_previous).props(
                    'flat round dense aria-label="Previous changed section"'
                ).classes("review-delta-summary__nav").mark("review-delta-previous")
                ui.button(icon="arrow_downward", on_click=on_next).props(
                    'flat round dense aria-label="Next changed section"'
                ).classes("review-delta-summary__nav").mark("review-delta-next")
            ui.button("Review changes", icon="pageview", on_click=on_open).props(
                "flat dense no-caps"
            ).classes("review-delta-summary__open").mark("review-delta-open")


def _open_review_delta_dialog(delta: ReviewDelta, *, initial_index: int = 0) -> None:
    dialog = ui.dialog()
    state = {"index": min(max(initial_index, 0), len(delta.sections) - 1)}

    def move(step: int) -> None:
        state["index"] = (int(state["index"]) + step) % len(delta.sections)
        dialog_body.refresh()

    async def jump_to_section() -> None:
        section = delta.sections[int(state["index"])]
        dialog.close()
        if section.section_key is not None:
            await _scroll_preview_to_section(section.section_key)

    @ui.refreshable
    def dialog_body() -> None:
        section = delta.sections[int(state["index"])]
        with ui.row().classes("review-delta-dialog__section-heading items-start justify-between"):
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(section.title).classes("review-delta-dialog__section-title")
                ui.label(
                    f"{len(section.changes)} change"
                    f"{'s' if len(section.changes) != 1 else ''} · "
                    f"Section {int(state['index']) + 1} of {len(delta.sections)}"
                ).classes("review-delta-dialog__section-meta")
            if section.section_key is not None:
                ui.button("Jump to section", icon="south", on_click=jump_to_section).props(
                    "flat dense no-caps"
                ).classes("review-delta-dialog__jump")
        with ui.column().classes("review-delta-dialog__changes"):
            for index, change in enumerate(section.changes):
                with (
                    ui.element("article")
                    .classes("review-delta-change")
                    .mark(f"review-delta-change-{index}")
                ):
                    with ui.row().classes("items-start justify-between w-full no-wrap"):
                        ui.label(change.target_label).classes("review-delta-change__label")
                        ui.label(change.changed_at.strftime("%d %b, %I:%M %p")).classes(
                            "review-delta-change__time"
                        )
                    ui.label(f"{change.actor_label} · {change.actor_role}").classes(
                        "review-delta-change__actor"
                    )
                    with ui.element("div").classes("review-delta-change__diff"):
                        for change_type, text in _word_diff(change.previous_text, change.new_text):
                            ui.label(text).classes(
                                f"inline section-diff section-diff--{change_type}"
                            )

    with dialog, ui.card().classes("review-delta-dialog"):
        with ui.row().classes("review-delta-dialog__header items-start justify-between"):
            with ui.row().classes("items-center gap-3 no-wrap"):
                ui.icon("difference").classes("review-delta-dialog__icon")
                with ui.column().classes("gap-0"):
                    ui.label("Changes since last review").classes("report-dialog__title")
                    ui.label(
                        f"{delta.total_changes} changes between return and resubmission"
                    ).classes("report-dialog__subtitle")
            ui.button(icon="close", on_click=dialog.close).props(
                'flat round dense aria-label="Close changes review"'
            )
        with ui.element("aside").classes("review-delta-dialog__return-note"):
            ui.label(f"Returned by {delta.returned_by} · {delta.returned_stage}").classes(
                "review-delta-dialog__return-meta"
            )
            ui.label(delta.return_reason).classes("review-delta-dialog__return-reason")
        dialog_body()
        with ui.row().classes("review-delta-dialog__footer items-center justify-between"):
            ui.button("Previous", icon="arrow_back", on_click=lambda: move(-1)).props(
                "flat dense no-caps"
            ).mark("review-delta-dialog-previous")
            ui.button("Next", icon="arrow_forward", on_click=lambda: move(1)).props(
                "flat dense no-caps icon-right"
            ).mark("review-delta-dialog-next")
    dialog.open()


def _change_is_in_review_delta(
    change: SectionChangeSummary | AnswerChangeSummary,
    delta: ReviewDeltaSection | None,
    review_delta: ReviewDelta | None,
) -> bool:
    if isinstance(change, AnswerChangeSummary):
        target_key = f"answer:{change.field_name}"
    else:
        target_key = f"section:{change.section_key}:{change.target}"
    return bool(
        delta is not None
        and review_delta is not None
        and review_delta.returned_at < change.changed_at <= review_delta.resubmitted_at
        and any(item.target_key == target_key for item in delta.changes)
    )


def _render_preview_section(
    section: ReportSection,
    content: tuple[PreviewSegment, ...],
    template: ReportTemplate,
    user: User,
    storage: MutableMapping[str, Any],
    preview: Any,
    *,
    storage_key: str,
    report_id: str | None,
    access: ReportAccess | None,
    changes: tuple[SectionChangeSummary, ...],
    word_changes: tuple[AnswerChangeSummary, ...] = (),
    source_paragraphs: tuple[ReportParagraph, ...] = (),
    answers: dict[str, Any] | None = None,
    editable_word_content: bool = False,
    on_word_save: Any | None = None,
    section_permission: SectionAccess | None = None,
    on_permission_refresh: Any | None = None,
    on_fixed_edit: Any | None = None,
    on_fixed_saved: Any | None = None,
    heading_override: str | None = None,
    on_heading_save: Any | None = None,
    inline_edit_state: dict[str, Any] | None = None,
    comment_count: int = 0,
    comments: tuple[SectionComment, ...] = (),
    on_comment_submit: Any | None = None,
    on_comment_begin: Any | None = None,
    on_comment_cancel: Any | None = None,
    comment_draft_state: dict[str, Any] | None = None,
    generated: bool = False,
    numbering_labels: dict[str, str] | None = None,
    review_delta: ReviewDeltaSection | None = None,
    review_delta_context: ReviewDelta | None = None,
) -> None:
    current_answers = answers or {}
    placeholder_texts = section_placeholder_texts(section)
    content_changes = tuple(change for change in changes if change.target == "content")
    heading_changes = tuple(change for change in changes if change.target == "heading")
    effective_fixed_changes = _effective_fixed_changes(
        content_changes,
        current_answers,
        placeholder_texts=placeholder_texts,
    )
    effective_word_changes = _effective_word_changes(
        word_changes,
        current_answers,
        placeholder_texts,
    )
    source_heading = (
        format_answer_for_preview(current_answers[section.heading_field_name])
        if section.heading_field_name and section.heading_field_name in current_answers
        else section.heading
    )
    display_heading = heading_override or source_heading
    effective_heading_changes = _effective_fixed_changes(heading_changes, {})
    edits: tuple[SectionChangeSummary | AnswerChangeSummary, ...] = (
        *effective_fixed_changes,
        *effective_heading_changes,
        *effective_word_changes,
    )
    history_changes = list(content_changes)
    section_history_changes = list(changes)
    latest_edit = max(edits, key=lambda change: change.changed_at) if edits else None
    comment_class = " preview-section--commented" if comment_count and not generated else ""
    generated_class = " preview-section--generated" if generated else ""
    review_delta_class = (
        " preview-section--review-delta" if review_delta is not None and not generated else ""
    )
    section_marker = f"preview-section-{section.id}"
    section_properties = f'data-preview-section="{section_marker}"'
    if latest_edit is not None and not generated:
        section_properties += f' data-latest-edit-role="{_edit_role_key(latest_edit.actor_role)}"'
    if effective_fixed_changes and not generated:
        section_properties += ' data-fixed-section-edited="true"'
    comment_anchors = [
        {
            "text": comment.selected_text,
            "start": comment.selection_start,
            "draft": False,
        }
        for comment in comments
        if comment.selected_text
    ]
    active_comment_draft = (
        comment_draft_state
        if comment_draft_state is not None and comment_draft_state.get("section_key") == section.id
        else None
    )
    if active_comment_draft is not None and active_comment_draft.get("selected_text"):
        comment_anchors.append(
            {
                "text": active_comment_draft.get("selected_text"),
                "start": active_comment_draft.get("selection_start"),
                "draft": True,
            }
        )
    if comment_anchors:
        encoded_anchors = b64encode(
            json.dumps(comment_anchors, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        section_properties += f' data-comment-anchors="{encoded_anchors}"'
    active_editor = (
        inline_edit_state
        if inline_edit_state is not None and inline_edit_state.get("section_key") == section.id
        else None
    )
    can_edit_fixed = bool(
        access is not None
        and access.can_edit_fixed_sections
        and report_id
        and not generated
        and section_permission is not None
        and section_permission.can_edit
    )
    section_properties += (
        f' data-find-fixed-editable="{str(can_edit_fixed).lower()}"'
        f' data-find-word-editable="{str(editable_word_content and not generated).lower()}"'
    )
    can_show_comments = bool(
        access is not None
        and (access.can_comment or access.is_completed_review or comments)
        and report_id
        and not generated
    )
    section_element = (
        ui.element("article")
        .classes(f"preview-section{comment_class}{generated_class}{review_delta_class}")
        .props(section_properties)
        .mark(section_marker)
    )
    if can_show_comments and access is not None and access.can_comment:
        section_element.props('data-can-comment="true"')

        async def selection_requested(event: events.GenericEventArguments) -> None:
            if on_comment_begin is None or not isinstance(event.args, dict):
                return
            selected_text = str(event.args.get("selectedText") or "")
            selection_start = event.args.get("selectionStart")
            if not selected_text.strip() or not isinstance(selection_start, int):
                return
            await on_comment_begin(section.id, selected_text, selection_start)

        section_element.on(
            "qc-comment-selection",
            selection_requested,
            js_handler="(event) => emit(event.detail)",
        )
    section_element.on("mouseenter", js_handler=COMMENT_HIGHLIGHT_HANDLER)
    section_element.on("focusin", js_handler=COMMENT_HIGHLIGHT_HANDLER)
    with section_element:
        if generated:
            ui.label(display_heading).classes("generated-report-section__heading")
        with ui.row().classes("preview-section__toolbar items-start justify-between no-wrap"):
            heading_classes = "preview-section__heading"
            latest_heading_change = (
                max(effective_heading_changes, key=lambda change: change.changed_at)
                if effective_heading_changes
                else None
            )
            if latest_heading_change is not None and not generated:
                role_key = _edit_role_key(latest_heading_change.actor_role)
                heading_classes += (
                    f" preview-role-edit preview-fixed-edit preview-role-edit--{role_key}"
                )
                if _change_is_in_review_delta(
                    latest_heading_change,
                    review_delta,
                    review_delta_context,
                ):
                    heading_classes += " preview-review-delta-change"
            with ui.row().classes("preview-section__heading-group items-start min-w-0"):
                heading_label = ui.label(display_heading).classes(heading_classes)
                if can_edit_fixed and on_heading_save is not None:
                    _enable_inline_heading_editing(
                        heading_label,
                        section.id,
                        display_heading,
                        on_heading_save,
                    )
                if review_delta is not None and not generated:
                    ui.label(f"{len(review_delta.changes)} new").classes(
                        "review-delta-section-badge"
                    ).props('aria-label="New corrections in this section"')
            with ui.row().classes("items-center gap-1 no-wrap"):
                if (
                    can_show_comments
                    and access is not None
                    and access.can_comment
                    and on_comment_begin is not None
                    and not generated
                ):
                    with (
                        ui.button(
                            icon="add_comment",
                            on_click=lambda: on_comment_begin(section.id, "", 0),
                        )
                        .props('flat round dense aria-label="Add section comment"')
                        .classes("preview-action preview-comment-action")
                        .mark(f"add-section-comment-{section.id}")
                    ):
                        ui.tooltip(
                            "Add a section comment, or select report text for a highlighted comment"
                        )
                permission_actions = (
                    ui.row()
                    .classes("section-permission-toolbar items-center gap-1 no-wrap")
                    .mark(f"section-permission-toolbar-{section.id}")
                    if report_id
                    and section_permission is not None
                    and not generated
                    and on_permission_refresh is not None
                    else None
                )
                if section_history_changes:
                    with (
                        ui.button(
                            icon="history",
                            on_click=lambda: _open_section_history(
                                display_heading,
                                tuple(section_history_changes),
                            ),
                        )
                        .props('flat round dense aria-label="View wording history"')
                        .classes("preview-action")
                    ):
                        ui.tooltip("View controlled wording history")
        if (
            report_id
            and section_permission is not None
            and not generated
            and on_permission_refresh is not None
            and permission_actions is not None
        ):
            section_permission_controls(
                report_id,
                template.id,
                section.id,
                display_heading,
                user,
                section_permission,
                bool(access and access.can_edit_fixed_sections),
                on_permission_refresh,
                permission_actions,
            )
        editing_fixed_section = bool(
            can_edit_fixed and active_editor is not None and active_editor.get("mode") == "section"
        )
        editor_id = f"in-place-section-content-{section.id}"
        status_id = f"in-place-section-status-{section.id}"
        if editing_fixed_section:
            _render_in_place_section_edit_controls(editor_id, status_id)
        content_classes = "preview-section__content"
        if generated:
            content_classes += " preview-section__content--generated"
        if editing_fixed_section:
            content_classes += " preview-section__content--editing"
        section_edit = (
            (lambda: on_fixed_edit(section.id))
            if can_edit_fixed and on_fixed_edit is not None
            else None
        )
        content_surface = ui.element("div").classes(content_classes)
        if section_edit is not None and not editing_fixed_section:
            _enable_section_text_editing(content_surface, section_edit)
        if editing_fixed_section:
            current_role_key = _edit_role_key(access.edit_role if access is not None else user.role)
            content_surface.props(
                f'id="{editor_id}" '
                f'data-current-edit-role="{current_role_key}" '
                'contenteditable="true" role="textbox" spellcheck="true" '
                'aria-label="Edit controlled section wording"'
            )
            content_surface.on(
                "input",
                js_handler=_section_editor_input_handler(
                    current_role_key,
                    status_id,
                ),
            )
            content_surface.on("focusout", js_handler=SECTION_EDITOR_FOCUSOUT_HANDLER)
            content_surface.on("keydown", js_handler=SECTION_EDITOR_HISTORY_SHORTCUT_HANDLER)
            content_surface.on(
                "paste",
                js_handler=(
                    "(event) => {"
                    "event.preventDefault();"
                    "const text = event.clipboardData.getData('text/plain');"
                    "document.execCommand('insertText', false, text);"
                    "}"
                ),
            )
        with content_surface:
            if editing_fixed_section:
                linked_content = link_section_override_fields(
                    section,
                    section_overrides_for(storage, storage_key).get(section.id, section.content),
                )
                placeholder_text_by_field = section_placeholder_texts(section)
                editor_spans = _section_editor_spans(
                    linked_content,
                    current_answers,
                    content_changes,
                    placeholder_texts=placeholder_text_by_field,
                )
                for editor_span in editor_spans:
                    span_classes = "preview-section__editor-span"
                    span_properties = 'data-editor-kind="fixed"'
                    if editor_span.field_name is not None:
                        span_classes += " preview-source-editable preview-section__editor-word"
                        span_properties = (
                            'data-editor-kind="word" data-find-replace-kind="word" '
                            f'data-editor-field="{editor_span.field_name}"'
                        )
                    elif editor_span.change is not None:
                        role_key = _edit_role_key(editor_span.change.actor_role)
                        span_classes += (
                            f" preview-role-edit preview-fixed-edit preview-role-edit--{role_key}"
                        )
                        if _change_is_in_review_delta(
                            editor_span.change,
                            review_delta,
                            review_delta_context,
                        ):
                            span_classes += " preview-review-delta-change"
                        span_properties += f' data-editor-role="{role_key}"'
                    ui.label(editor_span.text).classes(span_classes).props(span_properties)
            elif source_paragraphs:
                _render_source_paragraphs(
                    source_paragraphs,
                    current_answers,
                    word_changes=() if generated else effective_word_changes,
                    editable=editable_word_content and not generated and section_edit is None,
                    on_edit=None if generated or section_edit is not None else on_word_save,
                    on_fixed_edit=None,
                    clean=generated,
                    numbering_labels=numbering_labels,
                    review_delta=review_delta,
                    review_delta_context=review_delta_context,
                )
            elif not any(segment.text for segment in content):
                if not generated:
                    ui.label("No body text in this source section.").classes("inline")
            else:
                _render_override_content(
                    content,
                    current_answers,
                    fixed_changes=() if generated else content_changes,
                    word_changes=() if generated else effective_word_changes,
                    placeholder_texts=placeholder_texts,
                    editable=editable_word_content and not generated and section_edit is None,
                    on_edit=None if generated or section_edit is not None else on_word_save,
                    on_fixed_edit=None,
                    clean=generated,
                    source_paragraphs=section.paragraphs,
                    numbering_labels=numbering_labels,
                    review_delta=review_delta,
                    review_delta_context=review_delta_context,
                )
        if editing_fixed_section:
            assert report_id is not None and access is not None
            _enable_in_place_section_autosave(
                section,
                template,
                user,
                storage,
                storage_key=storage_key,
                report_id=report_id,
                access=access,
                content_surface=content_surface,
                status_id=status_id,
                history_changes=history_changes,
                on_word_save=on_word_save,
                on_saved=on_fixed_saved,
            )
        if can_show_comments:
            _render_inline_comments(
                section.id,
                comments,
                can_add=bool(access and access.can_comment),
                on_submit=on_comment_submit,
                draft=active_comment_draft,
                on_cancel=on_comment_cancel,
            )


def _render_override_content(
    content: tuple[PreviewSegment, ...],
    answers: dict[str, Any],
    *,
    fixed_changes: tuple[SectionChangeSummary, ...] = (),
    word_changes: tuple[AnswerChangeSummary, ...] = (),
    placeholder_texts: dict[str, str] | None = None,
    editable: bool = False,
    on_edit: Any | None = None,
    on_fixed_edit: Any | None = None,
    clean: bool = False,
    source_paragraphs: tuple[ReportParagraph, ...] = (),
    numbering_labels: dict[str, str] | None = None,
    review_delta: ReviewDeltaSection | None = None,
    review_delta_context: ReviewDelta | None = None,
) -> None:
    displayed_segments = tuple(
        (segment, report_text_for_answers(segment.text, answers)) for segment in content
    )
    displayed_text = "".join(text for _, text in displayed_segments)
    attributed_text = _fixed_section_attributed_text(
        displayed_text,
        fixed_changes,
        answers,
        placeholder_texts=placeholder_texts,
    )
    word_changes_by_field = _latest_word_changes_by_field(word_changes)
    segment_ranges: list[tuple[PreviewSegment, str, int, int]] = []
    offset = 0
    for segment, text in displayed_segments:
        segment_ranges.append((segment, text, offset, offset + len(text)))
        offset += len(text)

    def render_fragment(
        segment: PreviewSegment,
        text: str,
        segment_start: int,
        fragment_start: int,
        fragment_end: int,
    ) -> None:
        local_start = fragment_start - segment_start
        fragment = text[local_start : local_start + (fragment_end - fragment_start)]
        label: Any | None = None
        if clean:
            label = ui.label(fragment).classes("inline")
        elif segment.highlighted:
            word_change = word_changes_by_field.get(segment.field_name or "")
            classes = "inline"
            if word_change is None:
                classes += " preview-source-editable"
            else:
                role_key = _edit_role_key(word_change.actor_role)
                classes += f" preview-role-edit preview-role-edit--{role_key}"
                if _change_is_in_review_delta(
                    word_change,
                    review_delta,
                    review_delta_context,
                ):
                    classes += " preview-review-delta-change"
            preview_marker = (
                f"preview-document-answer-{segment.field_name}" if segment.field_name else None
            )
            if editable and segment.field_name and on_edit is not None:
                label = ui.label(fragment)
                _enable_inline_word_editing(
                    label,
                    segment.field_name,
                    fragment,
                    preview_marker,
                    on_edit,
                )
                classes += " preview-source-editable--interactive"
            else:
                label = ui.label(fragment)
            label.classes(classes)
            if on_fixed_edit is not None:
                _enable_section_text_editing(label, on_fixed_edit)
            if word_change is not None:
                label.props(f'data-edit-kind="word" data-edit-role="{role_key}"')
                ui.tooltip(_word_change_tooltip(word_change))
        else:
            for attributed_span in _slice_attributed_text(
                attributed_text,
                fragment_start,
                len(fragment),
            ):
                fixed_change = attributed_span.change
                classes = "inline"
                if fixed_change is not None:
                    role_key = _edit_role_key(fixed_change.actor_role)
                    classes += (
                        f" preview-role-edit preview-fixed-edit preview-role-edit--{role_key}"
                    )
                    if _change_is_in_review_delta(
                        fixed_change,
                        review_delta,
                        review_delta_context,
                    ):
                        classes += " preview-review-delta-change"
                label = ui.label(attributed_span.text).classes(classes)
                if fixed_change is not None:
                    label.props(f'data-edit-kind="fixed" data-edit-role="{role_key}"')
                    ui.tooltip(_fixed_change_tooltip(fixed_change))
                if on_fixed_edit is not None:
                    _enable_section_text_editing(label, on_fixed_edit)
        if segment.field_name and label is not None:
            preview_marker = f"preview-document-answer-{segment.field_name}"
            label.mark(preview_marker).props(
                f'data-preview-marker="{preview_marker}" data-find-replace-kind="word"'
            )

    def render_range(start: int, end: int) -> None:
        for segment, text, segment_start, segment_end in segment_ranges:
            fragment_start = max(start, segment_start)
            fragment_end = min(end, segment_end)
            if fragment_start < fragment_end:
                # Pass a range relative to the complete displayed section so edit attribution
                # remains aligned after paragraph wrappers are restored.
                render_fragment(
                    segment,
                    text,
                    segment_start,
                    fragment_start,
                    fragment_end,
                )

    paragraphs = _override_paragraph_slices(
        displayed_text,
        source_paragraphs,
        answers,
        numbering_labels or {},
    )
    if not paragraphs:
        render_range(0, len(displayed_text))
        return

    for paragraph in paragraphs:
        paragraph_classes = "preview-source-paragraph"
        paragraph_style = ""
        numbering = paragraph.source.numbering if paragraph.source is not None else None
        if paragraph.numbering_label and numbering is not None:
            paragraph_classes += " preview-source-paragraph--numbered"
            text_indent = max(0, numbering.left_indent_twips - numbering.hanging_indent_twips) / 15
            marker_width = max(24, numbering.hanging_indent_twips / 15)
            paragraph_style = (
                f"--preview-list-indent: {text_indent:.2f}px; "
                f"--preview-list-marker-width: {marker_width:.2f}px;"
            )
        paragraph_element = ui.element("p").classes(paragraph_classes)
        if paragraph_style:
            paragraph_element.style(paragraph_style)
        with paragraph_element:
            if paragraph.numbering_label:
                justification = (
                    numbering.justification
                    if numbering is not None
                    and numbering.justification in {"left", "center", "right"}
                    else "left"
                )
                ui.label(paragraph.numbering_label).classes(
                    "preview-source-paragraph__number "
                    f"preview-source-paragraph__number--{justification}"
                )
            with ui.element("span").classes("preview-source-paragraph__body"):
                render_range(paragraph.start, paragraph.end)


def _override_paragraph_slices(
    displayed_text: str,
    source_paragraphs: tuple[ReportParagraph, ...],
    answers: dict[str, Any],
    numbering_labels: dict[str, str],
) -> tuple[OverrideParagraphSlice, ...]:
    """Map an edited section back onto its source paragraph and list structure."""

    if not displayed_text or not source_paragraphs:
        return ()
    ranges: list[tuple[int, int]] = []
    start = 0
    for separator in re.finditer(r"\n(?:[ \t]*\n)+", displayed_text):
        if separator.start() > start:
            ranges.append((start, separator.start()))
        start = separator.end()
    if start < len(displayed_text):
        ranges.append((start, len(displayed_text)))
    visible_sources = tuple(
        paragraph
        for paragraph in source_paragraphs
        if paragraph.table is None
        and source_paragraph_is_visible(paragraph, answers)
        and any(segment.text for segment in paragraph.segments if segment.role != "guidance")
    )
    return tuple(
        OverrideParagraphSlice(
            start=paragraph_start,
            end=paragraph_end,
            source=visible_sources[index] if index < len(visible_sources) else None,
            numbering_label=(
                numbering_labels.get(visible_sources[index].id)
                if index < len(visible_sources)
                else None
            ),
        )
        for index, (paragraph_start, paragraph_end) in enumerate(ranges)
    )


def _render_edit_highlight_legend(*, show_review_delta: bool = False) -> None:
    with (
        ui.element("div")
        .classes("preview-edit-legend")
        .props('role="note" aria-label="Live preview highlight key"')
    ):
        ui.label("Highlight key").classes("preview-edit-legend__title")
        with ui.element("div").classes("preview-edit-legend__items"):
            for role_key, label in EDIT_ROLE_LEGEND:
                with ui.element("span").classes("preview-edit-legend__item"):
                    ui.element("span").classes(
                        f"preview-edit-legend__swatch preview-edit-legend__swatch--{role_key}"
                    )
                    ui.label(label)
            with ui.element("span").classes(
                "preview-edit-legend__item preview-edit-legend__item--fixed"
            ):
                ui.element("span").classes(
                    "preview-edit-legend__swatch preview-edit-legend__swatch--fixed"
                )
                ui.label("Fixed wording edit")
            if show_review_delta:
                with ui.element("span").classes(
                    "preview-edit-legend__item preview-edit-legend__item--review-delta"
                ):
                    ui.element("span").classes(
                        "preview-edit-legend__swatch preview-edit-legend__swatch--review-delta"
                    )
                    ui.label("New since return")


def _render_admin_section_lock_guide() -> None:
    with (
        ui.element("aside")
        .classes("section-lock-admin-guide")
        .props('role="note" aria-label="Administrator section lock controls"')
        .mark("section-lock-admin-guide")
    ):
        ui.icon("admin_panel_settings").classes("section-lock-admin-guide__icon")
        with ui.column().classes("gap-0"):
            ui.label("Administrator section controls").classes("section-lock-admin-guide__title")
            ui.label(
                "Scroll through this report. Each section's request and Lock section or Unlock "
                "section controls sit beside its Comments action. The setting applies only to "
                "this report."
            ).classes("section-lock-admin-guide__text")


def _edit_role_key(role: str) -> str:
    normalized = role.casefold()
    if "administrator" in normalized:
        return "administrator"
    if "eqcr" in normalized:
        return "eqcr"
    if "qc" in normalized:
        return "qc"
    if "partner" in normalized:
        return "partner"
    if "director" in normalized:
        return "director"
    if "manager" in normalized:
        return "manager"
    return "employee"


def _latest_word_changes_by_field(
    changes: tuple[AnswerChangeSummary, ...],
) -> dict[str, AnswerChangeSummary]:
    latest: dict[str, AnswerChangeSummary] = {}
    for change in changes:
        latest.setdefault(change.field_name, change)
    return latest


def _effective_word_changes(
    changes: tuple[AnswerChangeSummary, ...],
    answers: dict[str, Any],
    placeholder_texts: dict[str, str],
) -> tuple[AnswerChangeSummary, ...]:
    """Return only fields whose final value differs from controlled source wording."""

    latest = _latest_word_changes_by_field(changes)
    effective: list[AnswerChangeSummary] = []
    for field_name, change in latest.items():
        source_text = placeholder_texts.get(field_name)
        if source_text is None:
            effective.append(change)
            continue
        current_text = (
            format_answer_for_preview(answers[field_name]) if field_name in answers else source_text
        )
        if report_text_for_answers(current_text, answers) != report_text_for_answers(
            source_text,
            answers,
        ):
            effective.append(change)
    return tuple(effective)


def _word_change_tooltip(change: AnswerChangeSummary) -> str:
    tooltip = f"Edited by {change.actor_label} ({change.actor_role})."
    if change.reason:
        tooltip += f" Reason: {change.reason}"
    return tooltip


def _fixed_change_tooltip(change: SectionChangeSummary) -> str:
    tooltip = f"Fixed section edited by {change.actor_label} ({change.actor_role})."
    if change.reason:
        tooltip += f" Reason: {change.reason}"
    return tooltip


def _fixed_section_attributed_text(
    displayed_text: str,
    changes: tuple[SectionChangeSummary, ...],
    answers: dict[str, Any],
    *,
    placeholder_texts: dict[str, str] | None = None,
) -> tuple[AttributedPreviewText, ...]:
    """Attribute current fixed-section words to the role that last changed them."""

    if not displayed_text:
        return ()
    chronological = tuple(sorted(changes, key=lambda change: change.changed_at))
    if not chronological:
        return (AttributedPreviewText(displayed_text),)

    current_tokens = _preview_tokens(
        _display_section_change_text(
            chronological[0].previous_content,
            answers,
            placeholder_texts=placeholder_texts,
        )
    )
    attribution: list[SectionChangeSummary | None] = [None] * len(current_tokens)
    for history_change in chronological:
        target_tokens = _preview_tokens(
            _display_section_change_text(
                history_change.new_content,
                answers,
                placeholder_texts=placeholder_texts,
            )
        )
        current_tokens, attribution = _apply_section_change_attribution(
            current_tokens,
            attribution,
            target_tokens,
            history_change,
        )

    displayed_tokens = _preview_tokens(displayed_text)
    if current_tokens != displayed_tokens:
        current_tokens, attribution = _apply_section_change_attribution(
            current_tokens,
            attribution,
            displayed_tokens,
            chronological[-1],
        )

    baseline_tokens = _preview_tokens(
        _display_section_change_text(
            chronological[0].previous_content,
            answers,
            placeholder_texts=placeholder_texts,
        )
    )
    effective_mask = [False] * len(displayed_tokens)
    baseline_matcher = SequenceMatcher(a=baseline_tokens, b=displayed_tokens, autojunk=False)
    for operation, _old_start, _old_end, new_start, new_end in baseline_matcher.get_opcodes():
        if operation in {"insert", "replace"}:
            effective_mask[new_start:new_end] = [True] * (new_end - new_start)

    latest_change = chronological[-1]
    attribution = [
        (attribution_change or latest_change) if effective else None
        for attribution_change, effective in zip(attribution, effective_mask, strict=True)
    ]

    spans: list[AttributedPreviewText] = []
    for token, attribution_change in zip(current_tokens, attribution, strict=True):
        if spans and spans[-1].change is attribution_change:
            previous = spans[-1]
            spans[-1] = AttributedPreviewText(previous.text + token, attribution_change)
        else:
            spans.append(AttributedPreviewText(token, attribution_change))
    return tuple(spans)


def _display_section_change_text(
    text: str,
    answers: dict[str, Any],
    *,
    placeholder_texts: dict[str, str] | None = None,
) -> str:
    return report_text_for_answers(
        substitute_placeholders(
            text,
            answers,
            placeholder_texts=placeholder_texts,
        ),
        answers,
    )


def _preview_tokens(text: str) -> list[str]:
    """Keep words stable while making each whitespace character independently diffable."""

    return re.findall(r"\s|[^\s]+", text)


def _effective_fixed_changes(
    changes: tuple[SectionChangeSummary, ...],
    answers: dict[str, Any],
    *,
    placeholder_texts: dict[str, str] | None = None,
) -> tuple[SectionChangeSummary, ...]:
    """Return historical edits that still contribute to the current final wording."""

    if not changes:
        return ()
    chronological = tuple(sorted(changes, key=lambda change: change.changed_at))
    baseline_text = _display_section_change_text(
        chronological[0].previous_content,
        answers,
        placeholder_texts=placeholder_texts,
    )
    displayed_text = _display_section_change_text(
        chronological[-1].new_content,
        answers,
        placeholder_texts=placeholder_texts,
    )
    if displayed_text == baseline_text:
        return ()
    attributed = _fixed_section_attributed_text(
        displayed_text,
        changes,
        answers,
        placeholder_texts=placeholder_texts,
    )
    effective_ids = {id(span.change) for span in attributed if span.change is not None}
    effective = tuple(change for change in changes if id(change) in effective_ids)
    # A deletion has no current character to color. The history diff shows the exact
    # deleted text rather than fabricating an inline marker in the current report.
    return effective or (chronological[-1],)


def _apply_section_change_attribution(
    current_tokens: list[str],
    current_attribution: list[SectionChangeSummary | None],
    target_tokens: list[str],
    change: SectionChangeSummary,
) -> tuple[list[str], list[SectionChangeSummary | None]]:
    matcher = SequenceMatcher(a=current_tokens, b=target_tokens, autojunk=False)
    resolved_tokens: list[str] = []
    resolved_attribution: list[SectionChangeSummary | None] = []
    for operation, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if operation == "equal":
            resolved_tokens.extend(current_tokens[old_start:old_end])
            resolved_attribution.extend(current_attribution[old_start:old_end])
        elif operation in {"insert", "replace"}:
            replacement = target_tokens[new_start:new_end]
            resolved_tokens.extend(replacement)
            resolved_attribution.extend([change] * len(replacement))
    return resolved_tokens, resolved_attribution


def _slice_attributed_text(
    spans: tuple[AttributedPreviewText, ...],
    start: int,
    length: int,
) -> tuple[AttributedPreviewText, ...]:
    end = start + length
    position = 0
    result: list[AttributedPreviewText] = []
    for span in spans:
        span_end = position + len(span.text)
        overlap_start = max(start, position)
        overlap_end = min(end, span_end)
        if overlap_start < overlap_end:
            text = span.text[overlap_start - position : overlap_end - position]
            result.append(AttributedPreviewText(text, span.change))
        position = span_end
        if position >= end:
            break
    return tuple(result)


def _section_editor_spans(
    linked_content: str,
    answers: dict[str, Any],
    changes: tuple[SectionChangeSummary, ...],
    *,
    placeholder_texts: dict[str, str],
) -> tuple[SectionEditorSpan, ...]:
    """Render linked fields in blue and fixed wording with saved role attribution."""

    displayed_content = substitute_placeholders(
        linked_content,
        answers,
        placeholder_texts=placeholder_texts,
    )
    attributed_text = _fixed_section_attributed_text(
        displayed_content,
        changes,
        answers,
        placeholder_texts=placeholder_texts,
    )
    result: list[SectionEditorSpan] = []
    displayed_offset = 0
    linked_offset = 0

    def append_span(span: SectionEditorSpan) -> None:
        if not span.text:
            return
        if result and result[-1].field_name == span.field_name and result[-1].change is span.change:
            previous = result[-1]
            result[-1] = SectionEditorSpan(
                previous.text + span.text,
                previous.field_name,
                previous.change,
            )
        else:
            result.append(span)

    def append_fixed(raw_text: str) -> None:
        nonlocal displayed_offset
        rendered_text = substitute_placeholders(
            raw_text,
            answers,
            placeholder_texts=placeholder_texts,
        )
        for attributed_span in _slice_attributed_text(
            attributed_text,
            displayed_offset,
            len(rendered_text),
        ):
            append_span(SectionEditorSpan(attributed_span.text, change=attributed_span.change))
        displayed_offset += len(rendered_text)

    for placeholder in re.finditer(r"\{\{([a-z0-9_]+)\}\}", linked_content):
        append_fixed(linked_content[linked_offset : placeholder.start()])
        field_name = placeholder.group(1)
        rendered_value = substitute_placeholders(
            placeholder.group(0),
            answers,
            placeholder_texts=placeholder_texts,
        )
        append_span(SectionEditorSpan(rendered_value, field_name=field_name))
        displayed_offset += len(rendered_value)
        linked_offset = placeholder.end()
    append_fixed(linked_content[linked_offset:])
    return tuple(result)


def _render_source_paragraphs(
    paragraphs: tuple[ReportParagraph, ...],
    answers: dict[str, Any],
    *,
    word_changes: tuple[AnswerChangeSummary, ...] = (),
    editable: bool = False,
    on_edit: Any | None = None,
    on_fixed_edit: Any | None = None,
    clean: bool = False,
    numbering_labels: dict[str, str] | None = None,
    review_delta: ReviewDeltaSection | None = None,
    review_delta_context: ReviewDelta | None = None,
) -> None:
    changes_by_field = _latest_word_changes_by_field(word_changes)
    resolved_numbering_labels = numbering_labels or {}
    rendered = False
    for paragraph in paragraphs:
        if not source_paragraph_is_visible(paragraph, answers):
            continue
        if paragraph.table is not None:
            rendered = True
            _render_preview_table(
                paragraph.table,
                answers,
                changes_by_field,
                editable=editable,
                on_edit=on_edit,
                on_fixed_edit=on_fixed_edit,
                clean=clean,
                review_delta=review_delta,
                review_delta_context=review_delta_context,
            )
            continue
        visible_segments = tuple(
            segment for segment in paragraph.segments if segment.role != "guidance"
        )
        if not any(segment.text for segment in visible_segments):
            continue
        rendered = True
        numbering_label = resolved_numbering_labels.get(paragraph.id)
        paragraph_classes = "preview-source-paragraph"
        paragraph_style = ""
        if numbering_label and paragraph.numbering is not None:
            paragraph_classes += " preview-source-paragraph--numbered"
            text_indent = (
                max(
                    0,
                    paragraph.numbering.left_indent_twips
                    - paragraph.numbering.hanging_indent_twips,
                )
                / 15
            )
            marker_width = max(24, paragraph.numbering.hanging_indent_twips / 15)
            paragraph_style = (
                f"--preview-list-indent: {text_indent:.2f}px; "
                f"--preview-list-marker-width: {marker_width:.2f}px;"
            )
        paragraph_element = ui.element("p").classes(paragraph_classes)
        if paragraph_style:
            paragraph_element.style(paragraph_style)
        with paragraph_element:
            if numbering_label:
                numbering_justification = (
                    paragraph.numbering.justification
                    if paragraph.numbering is not None
                    and paragraph.numbering.justification in {"left", "center", "right"}
                    else "left"
                )
                ui.label(numbering_label).classes(
                    "preview-source-paragraph__number "
                    f"preview-source-paragraph__number--{numbering_justification}"
                )
            with ui.element("span").classes("preview-source-paragraph__body"):
                for segment in visible_segments:
                    value: Any = segment.text
                    if segment.field_name and segment.field_name in answers:
                        value = answers[segment.field_name]
                    text = report_text_for_answers(format_answer_for_preview(value), answers)
                    classes = "inline"
                    if segment.role == "editable" and not clean:
                        change = changes_by_field.get(segment.field_name or "")
                        classes += (
                            " preview-role-edit preview-role-edit--"
                            f"{_edit_role_key(change.actor_role)}"
                            if change is not None
                            else " preview-source-editable"
                        )
                        if change is not None and _change_is_in_review_delta(
                            change,
                            review_delta,
                            review_delta_context,
                        ):
                            classes += " preview-review-delta-change"
                    is_interactive = bool(
                        editable
                        and segment.role == "editable"
                        and segment.field_name
                        and on_edit is not None
                    )
                    label: Any
                    preview_marker = (
                        f"preview-document-answer-{segment.field_name}"
                        if segment.field_name
                        else None
                    )
                    if is_interactive:
                        assert on_edit is not None
                        assert segment.field_name is not None
                        label = ui.label(text)
                        _enable_inline_word_editing(
                            label,
                            segment.field_name,
                            text,
                            preview_marker,
                            on_edit,
                        )
                        classes += " preview-source-editable--interactive"
                    else:
                        label = ui.label(text)
                    label.classes(classes)
                    if on_fixed_edit is not None and not clean:
                        _enable_section_text_editing(label, on_fixed_edit)
                    if segment.field_name:
                        assert preview_marker is not None
                        label.mark(preview_marker).props(
                            f'data-preview-marker="{preview_marker}" data-find-replace-kind="word"'
                        )
                        change = changes_by_field.get(segment.field_name)
                        if change is not None:
                            ui.tooltip(_word_change_tooltip(change))
    if not rendered and not clean:
        ui.label("This section is not included for the selected scenario.").classes(
            "preview-section__empty"
        )


def _render_preview_table(
    table: ReportTable,
    answers: dict[str, Any],
    changes_by_field: dict[str, AnswerChangeSummary],
    *,
    editable: bool = False,
    on_edit: Any | None = None,
    on_fixed_edit: Any | None = None,
    clean: bool = False,
    review_delta: ReviewDeltaSection | None = None,
    review_delta_context: ReviewDelta | None = None,
) -> None:
    cells_by_row = {
        row: tuple(cell for cell in table.cells if cell.row == row)
        for row in range(1, table.row_count + 1)
    }
    source_rows = tuple(
        row
        for row, cells in cells_by_row.items()
        if any(_source_cell_text(cell.segments).strip() for cell in cells)
    )
    preview_table = ui.element("div").classes("preview-report-table")
    if table.repeatable_field_name:
        preview_marker = f"preview-document-answer-{table.repeatable_field_name}"
        preview_table.mark(preview_marker).props(f'data-preview-marker="{preview_marker}"')
    with preview_table:
        for row in source_rows:
            with (
                ui.element("div")
                .classes("preview-report-table__row")
                .style(f"grid-template-columns: repeat({table.column_count}, minmax(0, 1fr));")
            ):
                for cell in cells_by_row[row]:
                    with ui.element("div").classes("preview-report-table__cell"):
                        for segment in cell.segments:
                            if segment.role == "guidance":
                                continue
                            value = (
                                answers.get(segment.field_name, segment.text)
                                if segment.field_name
                                else segment.text
                            )
                            classes = "inline"
                            if segment.role == "editable" and not clean:
                                change = changes_by_field.get(segment.field_name or "")
                                classes += (
                                    " preview-role-edit preview-role-edit--"
                                    f"{_edit_role_key(change.actor_role)}"
                                    if change is not None
                                    else " preview-source-editable"
                                )
                                if change is not None and _change_is_in_review_delta(
                                    change,
                                    review_delta,
                                    review_delta_context,
                                ):
                                    classes += " preview-review-delta-change"
                            text = report_text_for_answers(
                                format_answer_for_preview(value), answers
                            )
                            cell_preview_marker = (
                                f"preview-document-answer-{segment.field_name}"
                                if segment.field_name
                                else None
                            )
                            if (
                                editable
                                and segment.role == "editable"
                                and segment.field_name
                                and on_edit is not None
                            ):
                                label = ui.label(text)
                                _enable_inline_word_editing(
                                    label,
                                    segment.field_name,
                                    text,
                                    cell_preview_marker,
                                    on_edit,
                                )
                                classes += " preview-source-editable--interactive"
                            else:
                                label = ui.label(text)
                            label.classes(classes)
                            if on_fixed_edit is not None and not clean:
                                _enable_section_text_editing(label, on_fixed_edit)
                            if segment.field_name:
                                assert cell_preview_marker is not None
                                label.mark(cell_preview_marker).props(
                                    f'data-preview-marker="{cell_preview_marker}" '
                                    'data-find-replace-kind="word"'
                                )
                                change = changes_by_field.get(segment.field_name)
                                if change is not None:
                                    ui.tooltip(_word_change_tooltip(change))
        if not table.repeatable_field_name:
            return
        rows = _normalise_table_rows(
            answers.get(table.repeatable_field_name),
            table.column_count,
        )
        if not rows:
            if not clean:
                with ui.element("div").classes(
                    "preview-report-table__row preview-report-table__row--empty"
                ):
                    ui.label("No table entries added.").classes("preview-report-table__empty")
            return
        for row_index, row_values in enumerate(rows):
            with (
                ui.element("div")
                .classes("preview-report-table__row")
                .style(f"grid-template-columns: repeat({table.column_count}, minmax(0, 1fr));")
            ):
                for column_index, value in enumerate(row_values):
                    marker = (
                        f"preview-document-answer-{table.repeatable_field_name}-"
                        f"{row_index}-{column_index}"
                    )
                    change = changes_by_field.get(table.repeatable_field_name)
                    role_class = ""
                    if not clean:
                        role_class = (
                            "preview-role-edit preview-role-edit--"
                            f"{_edit_role_key(change.actor_role)}"
                            if change is not None
                            else "preview-source-editable"
                        )
                        if change is not None and _change_is_in_review_delta(
                            change,
                            review_delta,
                            review_delta_context,
                        ):
                            role_class += " preview-review-delta-change"
                    cell_label = ui.label(value).classes(f"preview-report-table__cell {role_class}")
                    cell_label.mark(marker).props(
                        f'data-preview-marker="{marker}" data-find-replace-kind="word"'
                    )
                    if on_fixed_edit is not None and not clean:
                        _enable_section_text_editing(cell_label, on_fixed_edit)
                    if change is not None:
                        ui.tooltip(_word_change_tooltip(change))


def _enable_section_text_editing(label: Any, on_edit: Any) -> None:
    label.classes("preview-fixed-editable").props(
        'role="button" tabindex="0" title="Click to edit this section"'
    )
    label.on(
        "click",
        on_edit,
        js_handler=SECTION_EDIT_CLICK_HANDLER,
    )
    label.on("keydown.enter", on_edit)


def _enable_inline_heading_editing(
    label: Any,
    section_key: str,
    original_text: str,
    on_save: Any,
) -> None:
    """Make a section heading directly editable without adding a section-local toolbar."""

    label.props(
        'contenteditable="plaintext-only" role="textbox" spellcheck="true" '
        'aria-label="Edit section heading" title="Click and type to edit this heading" '
        f'data-section-heading="{section_key}"'
    ).classes("preview-section__heading--editable")

    async def save_if_changed(event: events.GenericEventArguments) -> None:
        if not isinstance(event.args, dict):
            return
        value = str(event.args.get("text") or "")
        if " ".join(value.split()) == " ".join(original_text.split()):
            return
        await on_save(section_key, value)

    label.on(
        "focusout",
        save_if_changed,
        js_handler="(event) => emit({text: event.currentTarget.innerText})",
    )
    label.on(
        "keydown",
        js_handler=(
            "(event) => {"
            "if (event.key === 'Enter') { event.preventDefault(); event.currentTarget.blur(); }"
            "else if (event.key === 'Escape') {"
            f"event.preventDefault(); event.currentTarget.innerText = {json.dumps(original_text)}; "
            "event.currentTarget.blur();"
            "}"
            "}"
        ),
    )


def _enable_inline_word_editing(
    label: Any,
    field_name: str,
    original_text: str,
    preview_marker: str | None,
    on_save: Any,
) -> None:
    """Turn a preview text run into a Word-like in-place editor."""

    label.props(
        'contenteditable="plaintext-only" role="textbox" spellcheck="true" '
        'aria-label="Editable report wording" title="Click and type to edit"'
    )

    async def save_if_changed(event: events.GenericEventArguments) -> None:
        if not isinstance(event.args, dict):
            return
        value = str(event.args.get("text") or "")
        if value == original_text:
            return
        await on_save(field_name, value, preview_marker)

    label.on(
        "focusout",
        save_if_changed,
        js_handler="(event) => emit({text: event.currentTarget.innerText})",
    )
    label.on(
        "keydown",
        js_handler=(
            "(event) => {"
            "if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {"
            "event.preventDefault(); event.currentTarget.blur();"
            "} else if (event.key === 'Escape') {"
            f"event.preventDefault(); event.currentTarget.innerText = {json.dumps(original_text)}; "
            "event.currentTarget.blur();"
            "}"
            "}"
        ),
    )


def _render_in_place_section_edit_controls(editor_id: str, status_id: str) -> None:
    """Show compact editing guidance and an explicit completion action."""

    with ui.element("div").classes("preview-in-place-edit-bar"):
        with ui.column().classes("preview-in-place-edit-bar__status gap-0"):
            ui.label("Editing in document · blue report answers remain linked").classes(
                "preview-in-place-edit-bar__title"
            )
            ui.label("Saved automatically").classes("preview-in-place-edit-bar__save-state").props(
                f'id="{status_id}" aria-live="polite" data-save-state="saved"'
            )
        done_button = (
            ui.button("Done", icon="check")
            .props('no-caps unelevated aria-label="Done editing report section"')
            .classes("preview-in-place-edit-bar__done")
        )
        done_button.on("click", js_handler=_section_editor_done_handler(editor_id))


def _enable_in_place_section_autosave(
    section: ReportSection,
    template: ReportTemplate,
    user: User,
    storage: MutableMapping[str, Any],
    *,
    storage_key: str,
    report_id: str,
    access: ReportAccess,
    content_surface: Any,
    status_id: str,
    history_changes: list[SectionChangeSummary],
    on_word_save: Any | None,
    on_saved: Any,
) -> None:
    if not access.can_edit_fixed_sections:
        ui.notify(
            "This report is no longer editable.",
            type="negative",
            position="top-right",
        )
        return
    linked_content = link_section_override_fields(
        section,
        section_overrides_for(storage, storage_key).get(section.id, section.content),
    )
    saved_answers = dict(report_answers_for(storage, storage_key))
    saved_linked_content = linked_content
    placeholder_texts = section_placeholder_texts(section)
    save_lock = asyncio.Lock()

    async def autosave(event: events.GenericEventArguments) -> None:
        nonlocal saved_linked_content
        arguments = event.args if isinstance(event.args, dict) else {}
        finish = bool(arguments.get("finish"))
        edited_text = str(arguments.get("content") or "")
        linked_edit = arguments.get("linkedContent")
        field_values: dict[str, str] = {}
        raw_fields = arguments.get("fields")
        if isinstance(raw_fields, list):
            for raw_field in raw_fields:
                if not isinstance(raw_field, dict):
                    continue
                field_name = str(raw_field.get("fieldName") or "")
                if field_name in placeholder_texts:
                    field_values[field_name] = str(raw_field.get("value") or "")

        async with save_lock:
            try:
                changed_fields = tuple(
                    (field_name, value)
                    for field_name, value in field_values.items()
                    if value
                    != substitute_placeholders(
                        f"{{{{{field_name}}}}}",
                        saved_answers,
                        placeholder_texts=placeholder_texts,
                    )
                )
                word_save = on_word_save
                if changed_fields:
                    if word_save is None:
                        raise ValueError("Linked report wording cannot be updated here.")
                    for field_name, value in changed_fields:
                        await word_save(
                            field_name,
                            value,
                            f"preview-document-answer-{field_name}",
                            None,
                            False,
                        )
                        saved_answers[field_name] = value

                content = (
                    str(linked_edit)
                    if isinstance(linked_edit, str)
                    else relink_section_editor_content(
                        saved_linked_content,
                        edited_text,
                        saved_answers,
                        placeholder_texts=placeholder_texts,
                    )
                )
                fixed_wording_changed = content != saved_linked_content
                if fixed_wording_changed:
                    await ReportCollaborationService().save_section(
                        public_id=report_id,
                        template_key=template.id,
                        section_key=section.id,
                        content=content,
                        profile=user,
                    )
                    set_section_override(storage, storage_key, section.id, content)
                    saved_linked_content = content
            except (PermissionError, ValueError) as error:
                ui.notify(str(error), type="negative", position="top-right")
                return

            await ui.run_javascript(
                """(() => {
  const status = document.getElementById(__STATUS_ID__);
  if (!status || status.dataset.saveRevision !== __REVISION__) return;
  status.textContent = 'Saved automatically';
  status.dataset.saveState = 'saved';
})()""".replace("__STATUS_ID__", json.dumps(status_id)).replace(
                    "__REVISION__", json.dumps(str(arguments.get("revision") or ""))
                )
            )

            if on_saved is not None:
                latest_history = await on_saved(
                    section.id,
                    finish=finish,
                    fixed_wording_changed=fixed_wording_changed,
                )
                if fixed_wording_changed and isinstance(latest_history, tuple):
                    history_changes[:] = latest_history

    content_surface.on(
        "qc-section-autosave",
        autosave,
        js_handler="(event) => emit(event.detail)",
    )


def _render_inline_comments(
    section_key: str,
    comments: tuple[SectionComment, ...],
    *,
    can_add: bool,
    on_submit: Any,
    draft: dict[str, Any] | None = None,
    on_cancel: Any | None = None,
) -> None:
    if not comments and draft is None:
        return
    with (
        ui.element("aside")
        .classes("preview-inline-comments")
        .props('role="region" aria-label="Section discussion"')
        .mark(f"inline-comments-{section_key}")
    ):
        with ui.row().classes("preview-inline-comments__heading items-center no-wrap"):
            ui.icon("forum").classes("preview-inline-comments__icon")
            ui.label("Discussion").classes("preview-inline-comments__title")
            if comments:
                ui.label(str(len(comments))).classes("preview-comment-count")
        if comments:
            with ui.column().classes("preview-inline-comments__thread gap-2"):
                for comment in comments:
                    with ui.element("div").classes("report-comment"):
                        if comment.selected_text:
                            ui.label(comment.selected_text).classes("report-comment__quote")
                        ui.label(comment.author).classes("report-comment__author")
                        ui.label(comment.body).classes("report-comment__body")
                        ui.label(comment.created_at.strftime("%d %b %Y, %I:%M %p")).classes(
                            "report-comment__time"
                        )
        elif can_add and draft is None:
            ui.label("Select text in this section, then choose Comment.").classes(
                "preview-inline-comments__empty"
            )
        if can_add and draft is not None:
            selected_text = str(draft.get("selected_text") or "")
            selection_start = draft.get("selection_start")
            comments_on_text = bool(selected_text.strip())
            with ui.element("div").classes("preview-comment-composer"):
                ui.label(
                    "Comment on selected text" if comments_on_text else "Add section comment"
                ).classes("preview-comment-composer__title")
                if comments_on_text:
                    ui.label(selected_text).classes("preview-comment-composer__quote")
            comment_input = (
                ui.textarea()
                .props('outlined dense autogrow autofocus placeholder="Write a comment…"')
                .classes("preview-inline-comments__input")
                .mark(f"inline-comment-input-{section_key}")
            )

            async def add() -> None:
                if await on_submit(
                    section_key,
                    str(comment_input.value or ""),
                    selected_text if comments_on_text else None,
                    (
                        selection_start
                        if comments_on_text and isinstance(selection_start, int)
                        else None
                    ),
                ):
                    comment_input.value = ""

            comment_input.on("keydown.ctrl.enter", add)
            with ui.row().classes("preview-inline-comments__actions items-center gap-2"):
                ui.label("Ctrl + Enter to post").classes("preview-inline-comments__shortcut")
                ui.button("Cancel", on_click=on_cancel).props("flat no-caps")
                ui.button("Add comment", icon="send", on_click=add).props(
                    "unelevated no-caps"
                ).classes("primary-action").mark(f"inline-comment-submit-{section_key}")


def _render_workflow_timeline(timeline: WorkflowTimeline) -> None:
    with ui.element("section").classes("approval-timeline"):
        ui.label("Report approval path").classes("live-workflow__label")
        with ui.element("div").classes("approval-timeline__list"):
            _timeline_node(
                "Maker",
                timeline.maker_name,
                timeline.maker_email,
                timeline.maker_status,
                "edit_document",
            )
            for stage in timeline.stages:
                if not stage.reviewers:
                    _timeline_node(
                        stage.label,
                        "Not selected",
                        "",
                        stage.status,
                        _timeline_icon(stage.status),
                    )
                    continue
                for index, reviewer in enumerate(stage.reviewers, start=1):
                    label = stage.label
                    if len(stage.reviewers) > 1:
                        label = f"{stage.label} {index} of {len(stage.reviewers)}"
                    _timeline_node(
                        label,
                        reviewer.label,
                        reviewer.email,
                        reviewer.status,
                        _timeline_icon(reviewer.status),
                    )


def _open_workflow_timeline(timeline: WorkflowTimeline) -> None:
    dialog = ui.dialog()
    with dialog, ui.card().classes("workflow-timeline-dialog"):
        with ui.row().classes("items-start justify-between w-full gap-4"):
            with ui.column().classes("gap-0"):
                ui.label("Approval workflow").classes("report-dialog__title")
                ui.label(
                    "See who reviews this report, the current stage, and completed approvals."
                ).classes("report-dialog__subtitle")
            ui.button(icon="close", on_click=dialog.close).props(
                'flat round dense aria-label="Close approval path"'
            )
        _render_workflow_timeline(timeline)
        with ui.row().classes("w-full justify-end"):
            ui.button("Close", on_click=dialog.close).props("flat no-caps")
    dialog.open()


def _timeline_node(
    stage: str,
    person: str,
    email: str,
    status: str,
    icon: str,
) -> None:
    normalized = status.casefold().replace(" ", "-")
    with ui.element("div").classes(f"approval-timeline__node is-{normalized}"):
        ui.icon(icon).classes("approval-timeline__icon")
        with ui.column().classes("gap-0 min-w-0"):
            ui.label(stage).classes("approval-timeline__stage")
            ui.label(person).classes("approval-timeline__person")
            if email:
                ui.label(email).classes("approval-timeline__email")
        ui.label(status).classes("approval-timeline__status")


def _timeline_icon(status: str) -> str:
    return {
        "approved": "check_circle",
        "current": "radio_button_checked",
        "awaiting decision": "radio_button_checked",
        "returned": "assignment_return",
        "rejected": "cancel",
        "not required": "remove_circle_outline",
    }.get(status.casefold(), "schedule")


def _open_section_history(
    section_heading: str,
    changes: tuple[SectionChangeSummary, ...],
) -> None:
    dialog = ui.dialog()
    with dialog, ui.card().classes("section-history-dialog"):
        ui.label(f"Controlled wording history: {section_heading}").classes("report-dialog__title")
        ui.label(
            "The final report uses the latest accepted wording. Highlights are review-only."
        ).classes("report-dialog__subtitle")
        with ui.column().classes("w-full gap-3"):
            for change in changes:
                with ui.element("article").classes("section-history-entry"):
                    with ui.row().classes("items-center justify-between w-full"):
                        target_label = (
                            "Section heading" if change.target == "heading" else "Section wording"
                        )
                        ui.label(f"{target_label} edited by {change.actor_label}").classes(
                            "section-history-entry__actor"
                        )
                        ui.label(change.changed_at.strftime("%d %b %Y, %I:%M %p")).classes(
                            "section-history-entry__time"
                        )
                    ui.label(f"{change.actor_role} · {change.stage_label}").classes(
                        "section-history-entry__meta"
                    )
                    if change.reason:
                        ui.label(f"Reason: {change.reason}").classes(
                            "section-history-entry__reason"
                        )
                    with ui.element("div").classes("section-history-entry__diff"):
                        for change_type, text in _word_diff(
                            change.previous_content, change.new_content
                        ):
                            ui.label(text).classes(
                                f"inline section-diff section-diff--{change_type}"
                            )
        with ui.row().classes("w-full justify-end"):
            ui.button("Close", on_click=dialog.close).props("flat no-caps")
    dialog.open()


def _word_diff(previous: str, current: str) -> tuple[tuple[str, str], ...]:
    previous_tokens = _preview_tokens(previous)
    current_tokens = _preview_tokens(current)
    matcher = SequenceMatcher(a=previous_tokens, b=current_tokens, autojunk=False)
    result: list[tuple[str, str]] = []

    def append(kind: str, text: str) -> None:
        if not text:
            return
        if result and result[-1][0] == kind:
            previous_kind, previous_text = result[-1]
            result[-1] = previous_kind, previous_text + text
        else:
            result.append((kind, text))

    for operation, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if operation in {"equal", "delete", "replace"} and old_start < old_end:
            kind = "unchanged" if operation == "equal" else "deleted"
            append(kind, "".join(previous_tokens[old_start:old_end]))
        if operation in {"insert", "replace"} and new_start < new_end:
            append("inserted", "".join(current_tokens[new_start:new_end]))
    return tuple(result)


def _open_approve_dialog(report_id: str | None, actor_email: str) -> None:
    if report_id is None:
        return
    dialog = ui.dialog()
    with dialog, ui.card().classes("section-editor-dialog"):
        ui.label("Approve report").classes("report-dialog__title")
        ui.label("The report will move to the next assigned approval stage.").classes(
            "report-dialog__subtitle"
        )
        notes = ui.textarea(label="Approval note (optional)").props("outlined autogrow")

        async def approve() -> None:
            try:
                transition = await ApprovalService().approve(
                    report_id, actor_email, str(notes.value or "")
                )
            except (PermissionError, RuntimeError, ValueError) as error:
                ui.notify(str(error), type="negative", position="top-right")
                return
            dialog.close()
            destination = transition.active_stage or "final approval"
            ui.notify(
                f"Report approved and sent to {destination}.",
                type="positive",
                position="top-right",
            )
            ui.navigate.to("/dashboard")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            ui.button("Approve", icon="task_alt", on_click=approve).props(
                "unelevated no-caps"
            ).classes("primary-action")
    dialog.open()


def _open_return_dialog(
    report_id: str | None,
    actor_email: str,
    timeline: WorkflowTimeline | None,
) -> None:
    if report_id is None:
        return
    dialog = ui.dialog()
    with dialog, ui.card().classes("section-editor-dialog"):
        ui.label("Return for changes").classes("report-dialog__title")
        ui.label("A reason is required and will be recorded in the workflow history.").classes(
            "report-dialog__subtitle"
        )
        reason = ui.textarea(label="Required return reason").props("outlined autogrow")
        return_mode = ui.select(
            {
                "same": "Return to Maker, then back to me",
                "restart": "Return to Maker and restart from selected stage",
            },
            value="same",
            label="Resubmission route",
        ).props("outlined")
        stage_options: dict[str, str] = {}
        if timeline is not None:
            for stage in timeline.stages:
                if stage.status == "Not required":
                    continue
                stage_options[stage.stage_code] = stage.label
                if stage.status == "Current":
                    break
        restart_stage = (
            ui.select(
                stage_options,
                label="Restart approval from",
            )
            .props("outlined")
            .classes("w-full")
        )
        restart_stage.set_visibility(False)

        def mode_changed() -> None:
            restart_stage.set_visibility(return_mode.value == "restart")

        return_mode.on_value_change(lambda _: mode_changed())

        async def return_report() -> None:
            if return_mode.value == "restart" and not restart_stage.value:
                ui.notify(
                    "Select the approval stage from which review should restart.",
                    type="warning",
                    position="top-right",
                )
                return
            try:
                await ApprovalService().return_for_changes(
                    report_id,
                    actor_email,
                    str(reason.value or ""),
                    (
                        str(restart_stage.value)
                        if return_mode.value == "restart" and restart_stage.value
                        else None
                    ),
                )
            except (PermissionError, RuntimeError, ValueError) as error:
                ui.notify(str(error), type="negative", position="top-right")
                return
            dialog.close()
            ui.notify(
                "Report returned to its creator for changes.",
                type="positive",
                position="top-right",
            )
            ui.navigate.to("/dashboard")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            ui.button("Return Report", icon="assignment_return", on_click=return_report).props(
                "unelevated no-caps"
            ).classes("review-return-confirm")
    dialog.open()


def _render_page_error(title: str, message: str) -> None:
    with ui.element("main").classes("app-main"):
        with ui.column().classes("page-error items-center text-center"):
            ui.icon("error_outline")
            ui.label(title).classes("welcome-title")
            ui.label(message).classes("welcome-subtitle")
            ui.button("Return to dashboard", on_click=lambda: ui.navigate.to("/dashboard")).props(
                "outline no-caps"
            )
