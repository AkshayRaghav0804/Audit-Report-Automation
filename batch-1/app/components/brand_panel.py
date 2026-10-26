"""Brand introduction and decorative audit-review visual."""

from __future__ import annotations

from nicegui import ui


def brand_identity(*, compact: bool = False) -> None:
    suffix = " brand-identity--compact" if compact else ""
    with ui.column().classes(f"brand-identity{suffix}"):
        ui.image("/static/logo/kkc-placeholder.svg").props(
            'alt="KKC & Associates LLP, Chartered Accountants"'
        ).classes("brand-logo")
        ui.html(
            '<h1 class="brand-title">Audit Report<br class="desktop-break"> Review Tool</h1>',
            sanitize=False,
        )
        ui.label(
            "A controlled platform for preparing, reviewing, approving and finalising "
            "audit reports."
        ).classes("brand-description")


def brand_panel() -> None:
    with (
        ui.element("section")
        .classes("brand-panel")
        .props('aria-label="Audit Report Review Tool introduction"')
    ):
        with ui.column().classes("brand-panel__content"):
            brand_identity()
            audit_review_visual()
            with ui.row().classes("security-notice items-start no-wrap"):
                ui.icon("verified_user").props('aria-hidden="true"')
                ui.label(
                    "Authorised users only. Report activity, comments, changes and approvals "
                    "are recorded for audit purposes."
                )


def audit_review_visual() -> None:
    ui.html(
        """
        <div class="review-visual" aria-hidden="true">
          <div class="document-card">
            <div class="document-card__top">
              <span class="document-label">INDEPENDENT AUDITOR'S REPORT</span>
              <span class="page-count">01 / 12</span>
            </div>
            <div class="document-title-line"></div>
            <div class="document-meta-line"></div>
            <div class="report-section report-section--approved">
              <span class="section-number">01</span>
              <span class="section-copy"><b>Opinion</b><i></i><i></i></span>
              <span class="section-state">Approved</span>
            </div>
            <div class="report-section report-section--review">
              <span class="section-number">02</span>
              <span class="section-copy"><b>Basis for Opinion</b><i></i><i></i></span>
              <span class="section-state">In review</span>
            </div>
            <div class="report-section">
              <span class="section-number">03</span>
              <span class="section-copy"><b>Key Audit Matters</b><i></i><i></i></span>
              <span class="section-state">Draft</span>
            </div>
          </div>
          <div class="comment-card">
            <span class="comment-avatar">AR</span>
            <span class="comment-copy"><b>Review comment</b><i></i><i></i></span>
            <span class="comment-check">✓</span>
          </div>
          <div class="approval-card">
            <span class="approval-icon">✓</span>
            <span><b>Partner approval</b><small>Evidence recorded</small></span>
          </div>
        </div>
        """,
        sanitize=False,
    ).classes("review-visual-host")
