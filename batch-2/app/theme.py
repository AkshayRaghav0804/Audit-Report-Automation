"""Central application theme values."""

from __future__ import annotations

from nicegui import ui

PRIMARY_COLOR = "#7CB542"
PRIMARY_HOVER = "#6DA337"
PRIMARY_DARK = "#557F2C"
PRIMARY_SOFT = "#F1F7E9"
SECONDARY_COLOR = "#374151"
DASHBOARD_SIDEBAR_COLOR = "#FFFFFF"
DASHBOARD_SIDEBAR_TEXT = "#1F2937"
DASHBOARD_SIDEBAR_HOVER = "#F4F8EF"
DASHBOARD_SIDEBAR_ACTIVE = "#E8F2DD"
DASHBOARD_SIDEBAR_BORDER = "#DCE4D5"


def configure_theme() -> None:
    ui.colors(
        primary=PRIMARY_DARK,
        secondary=SECONDARY_COLOR,
        accent=PRIMARY_COLOR,
        positive=PRIMARY_COLOR,
    )


def dashboard_theme_html() -> str:
    return (
        "<style>:root{"
        f"--qc-primary:{PRIMARY_COLOR};"
        f"--qc-primary-hover:{PRIMARY_HOVER};"
        f"--qc-primary-dark:{PRIMARY_DARK};"
        f"--qc-primary-soft:{PRIMARY_SOFT};"
        f"--dashboard-sidebar:{DASHBOARD_SIDEBAR_COLOR};"
        f"--dashboard-sidebar-text:{DASHBOARD_SIDEBAR_TEXT};"
        f"--dashboard-sidebar-hover:{DASHBOARD_SIDEBAR_HOVER};"
        f"--dashboard-sidebar-active:{DASHBOARD_SIDEBAR_ACTIVE};"
        f"--dashboard-sidebar-border:{DASHBOARD_SIDEBAR_BORDER};"
        "}</style>"
    )
