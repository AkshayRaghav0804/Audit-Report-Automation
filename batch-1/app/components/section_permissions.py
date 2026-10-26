"""Per-report section locking and permission dialogs."""

from collections.abc import Awaitable, Callable
from uuid import UUID

from nicegui import ui
from nicegui.element import Element

from app.models.user import User
from app.services.section_access_service import SectionAccess, SectionAccessService


def section_permission_controls(
    public_id: str,
    template_key: str,
    section_key: str,
    heading: str,
    profile: User,
    state: SectionAccess,
    can_request: bool,
    refresh: Callable[[], Awaitable[None]],
    action_container: Element,
) -> None:
    service = SectionAccessService()

    async def change_lock() -> None:
        try:
            await service.set_lock(public_id, template_key, section_key, profile, not state.locked)
            await refresh()
        except (PermissionError, ValueError) as error:
            ui.notify(str(error), type="negative")

    def request_dialog() -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
            ui.label(f"Request fixed-wording access — {heading}").classes("text-h6")
            ui.label(
                "Only this section in this report. Access expires when the report is submitted."
            )
            reason = ui.textarea("Reason for changing fixed wording").classes("w-full")

            async def submit() -> None:
                try:
                    await service.request(
                        public_id, template_key, section_key, profile, reason.value
                    )
                    dialog.close()
                    await refresh()
                except (PermissionError, ValueError) as error:
                    ui.notify(str(error), type="negative")

            with ui.row():
                ui.button("Send request", on_click=submit)
                ui.button("Cancel", on_click=dialog.close).props("flat")
        dialog.open()

    async def review_dialog() -> None:
        try:
            requests = await service.pending(public_id, section_key, profile)
        except (PermissionError, ValueError) as error:
            ui.notify(str(error), type="negative")
            return
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
            ui.label(f"Editing requests — {heading}").classes("text-h6")
            if not requests:
                ui.label("No pending requests from other users for this section.")
            for request_id, name, request_reason in requests:
                with ui.card().classes("w-full"):
                    ui.label(name).classes("font-bold")
                    ui.label(request_reason)
                    decision_reason = ui.textarea("Decision note").classes("w-full")

                    async def decide(
                        approve: bool,
                        selected_id: UUID = request_id,
                        note: ui.textarea = decision_reason,
                    ) -> None:
                        try:
                            await service.decide(
                                public_id, selected_id, profile, approve, note.value
                            )
                            dialog.close()
                            await refresh()
                        except (PermissionError, ValueError) as error:
                            ui.notify(str(error), type="negative")

                    async def approve(action: Callable[..., Awaitable[None]] = decide) -> None:
                        await action(True)

                    async def reject(action: Callable[..., Awaitable[None]] = decide) -> None:
                        await action(False)

                    with ui.row():
                        ui.button("Approve editing", on_click=approve)
                        ui.button("Reject", on_click=reject).props("flat")
            ui.button("Close", on_click=dialog.close).props("flat")
        dialog.open()

    def help_dialog() -> None:
        with (
            ui.dialog() as dialog,
            ui.card().classes("section-permission-help-dialog w-full max-w-lg"),
        ):
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.icon("lock" if state.locked else "lock_open").classes(
                    "section-permission-help-dialog__icon"
                )
                ui.label(
                    "Why is this section locked?"
                    if state.locked
                    else "What does an unlocked section mean?"
                ).classes("text-h6")
            if state.locked:
                ui.label(
                    "Locked sections protect the approved report wording. Blue source fields "
                    "remain editable, while headings and fixed wording require an approved "
                    "access request or an administrator to unlock the section."
                )
            else:
                ui.label(
                    "This section is unlocked, so authorised editors can change its headings "
                    "and fixed wording as well as the blue source fields. Changes apply only to "
                    "this report and continue to be recorded in its history."
                )
            ui.button("Got it", on_click=dialog.close).props("flat no-caps").classes("self-end")
        dialog.open()

    lock_help = (
        "Locked: blue fields are editable; fixed wording needs approval or an unlock."
        if state.locked
        else "Unlocked: authorised editors can change all wording in this section."
    )
    with action_container:
        if can_request and state.locked and not state.can_edit:
            if state.request_status == "pending":
                with (
                    ui.button(icon="hourglass_top")
                    .props(
                        'flat round dense disable aria-label="Fixed-wording access request pending"'
                    )
                    .classes("preview-action section-permission-toolbar__request")
                    .mark(f"section-permission-request-{section_key}")
                ):
                    ui.tooltip("Fixed-wording access request pending")
            else:
                with (
                    ui.button(icon="edit_note", on_click=request_dialog)
                    .props('flat round dense aria-label="Request fixed-wording access"')
                    .classes("preview-action section-permission-toolbar__request")
                    .mark(f"section-permission-request-{section_key}")
                ):
                    ui.tooltip("Request fixed-wording access")
        if state.can_decide and state.pending_requests:
            review_label = f"Review editing requests ({state.pending_requests})"
            with (
                ui.button(icon="fact_check", on_click=review_dialog)
                .props(f'flat round dense aria-label="{review_label}"')
                .classes("preview-action section-permission-toolbar__review")
                .mark(f"section-permission-review-{section_key}")
            ):
                ui.tooltip(review_label)
        if state.can_manage:
            action_label = "Unlock section" if state.locked else "Lock section"
            with (
                ui.button(
                    icon="lock_open" if state.locked else "lock",
                    on_click=change_lock,
                )
                .props(f'flat round dense aria-label="{action_label}"')
                .classes("preview-action section-permission-toolbar__lock")
                .mark(f"section-lock-toggle-{section_key}")
            ):
                ui.tooltip(action_label)
        with (
            ui.button("?", on_click=help_dialog)
            .props('flat round dense aria-label="Explain section lock status"')
            .classes("preview-action section-permission-toolbar__help")
            .mark(f"section-lock-help-{section_key}")
        ):
            ui.tooltip(lock_help)
