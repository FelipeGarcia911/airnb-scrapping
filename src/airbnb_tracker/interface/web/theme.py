"""Visual identity of the web UI: a clean marketplace look (coral accent, rounded cards).

Colors and fonts go through Streamlit's native theming (passed as CLI flags by
``airbnb-tracker ui``); only a few details Streamlit cannot theme are set with CSS.
The look is inspired by modern stay marketplaces; no third-party logos are used.
"""

from __future__ import annotations

import streamlit as st

CORAL = "#FF385C"
INK = "#222222"
MUTED = "#717171"
BORDER = "#DDDDDD"
SURFACE = "#F7F7F7"
AREA_FILL = "#FF385C33"  # coral at 20% opacity: the map stays visible underneath

FONT_URL = "https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@400;600;700;800&display=swap"

STREAMLIT_OPTIONS: dict[str, str] = {
    "theme.base": "light",
    "theme.primaryColor": CORAL,
    "theme.backgroundColor": "#FFFFFF",
    "theme.secondaryBackgroundColor": SURFACE,
    "theme.textColor": INK,
    "theme.linkColor": INK,
    "theme.borderColor": BORDER,
    "theme.dataframeBorderColor": "#EBEBEB",
    "theme.dataframeHeaderBackgroundColor": SURFACE,
    "theme.font": f"Nunito Sans:{FONT_URL}",
    "theme.headingFont": f"Nunito Sans:{FONT_URL}",
    "theme.baseRadius": "12px",
    "theme.buttonRadius": "8px",
    "theme.showWidgetBorder": "true",
    "theme.metricValueFontSize": "1.5rem",  # default is too large for a row of 5 KPIs
    "theme.metricValueFontWeight": "700",
    "theme.chartCategoricalColors": f'["{CORAL}","{INK}","#00A699","#FC642D","#767676"]',
    "client.toolbarMode": "minimal",
    "server.address": "localhost",  # local tool: never listen on the network
    "browser.gatherUsageStats": "false",
}

_CSS = f"""
<style>
  .block-container {{ max-width: 1280px; padding-top: 2rem; }}
  h1, h2, h3 {{ letter-spacing: -0.02em; }}
  [data-testid="stVerticalBlockBorderWrapper"] {{
      box-shadow: 0 6px 16px rgba(0, 0, 0, 0.08);
  }}
  .stTabs [data-baseweb="tab-list"] {{ gap: 2rem; border-bottom: 1px solid {BORDER}; }}
  .stTabs [data-baseweb="tab"] {{ font-weight: 600; color: {MUTED}; }}
  .stTabs [aria-selected="true"] {{ color: {INK}; }}
  .app-header {{ display: flex; align-items: center; gap: .6rem; margin-bottom: .25rem; }}
  .app-header .mark {{
      width: 34px; height: 34px; border-radius: 10px; background: {CORAL};
      display: flex; align-items: center; justify-content: center; color: #fff;
      font-weight: 800; font-size: 1.1rem;
  }}
  .app-header .name {{ font-size: 1.5rem; font-weight: 800; color: {CORAL}; }}
  .app-subtitle {{ color: {MUTED}; margin-bottom: 1.5rem; }}
  .muted {{ color: {MUTED}; font-size: .9rem; }}
</style>
"""


def streamlit_flags() -> list[str]:
    """Command-line flags for ``streamlit run``: theme plus local-only server settings."""
    return [f"--{key}={value}" for key, value in STREAMLIT_OPTIONS.items()]


def apply_page_style() -> None:
    st.html(_CSS)


def header(title: str, subtitle: str) -> None:
    st.html(
        f'<div class="app-header"><div class="mark">C</div>'
        f'<div class="name">{title}</div></div>'
        f'<div class="app-subtitle">{subtitle}</div>'
    )
