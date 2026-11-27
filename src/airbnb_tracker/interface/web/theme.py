"""Visual identity of the web UI: a clean marketplace look (coral accent, rounded cards).

Colors and fonts go through Streamlit's native theming (passed as CLI flags by
``airbnb-tracker ui``, so a theme change needs a restart); only a few details Streamlit
cannot theme are set with CSS, which also carries the compact density.
The look is inspired by modern stay marketplaces; no third-party logos are used.
"""

from __future__ import annotations

import streamlit as st

from airbnb_tracker.interface.web.preferences import Density, Theme

CORAL = "#FF385C"
INK = "#222222"
MUTED = "#8A8A8A"  # readable on light and dark backgrounds
TEAL = "#00A699"
BORDER = "#DDDDDD"
SURFACE = "#F7F7F7"
AREA_FILL = "#FF385C33"  # coral at 20% opacity: the map stays visible underneath

FONT_URL = "https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@400;600;700;800&display=swap"

# One palette per mode (Strategy): the preference picks which ones Streamlit receives.
LIGHT_PALETTE: dict[str, str] = {
    "primaryColor": CORAL,
    "backgroundColor": "#FFFFFF",
    "secondaryBackgroundColor": SURFACE,
    "textColor": INK,
    "linkColor": INK,
    "borderColor": BORDER,
    "dataframeBorderColor": "#EBEBEB",
    "dataframeHeaderBackgroundColor": SURFACE,
    "chartCategoricalColors": f'["{CORAL}","{INK}","#00A699","#FC642D","#767676"]',
}
DARK_PALETTE: dict[str, str] = {
    "primaryColor": CORAL,
    "backgroundColor": "#121212",
    "secondaryBackgroundColor": "#1E1E1E",
    "textColor": "#F2F2F2",
    "linkColor": "#F2F2F2",
    "borderColor": "#3A3A3A",
    "dataframeBorderColor": "#2E2E2E",
    "dataframeHeaderBackgroundColor": "#1E1E1E",
    "chartCategoricalColors": f'["{CORAL}","#F2F2F2","#00A699","#FC642D","#A0A0A0"]',
}
SHARED_THEME: dict[str, str] = {
    "font": f"Nunito Sans:{FONT_URL}",
    "headingFont": f"Nunito Sans:{FONT_URL}",
    "baseRadius": "12px",
    "buttonRadius": "8px",
    "showWidgetBorder": "true",
    "metricValueFontSize": "1.5rem",  # default is too large for a row of 5 KPIs
    "metricValueFontWeight": "700",
}
SERVER_OPTIONS: dict[str, str] = {
    "client.toolbarMode": "minimal",
    "server.address": "localhost",  # local tool: never listen on the network
    "browser.gatherUsageStats": "false",
}


def streamlit_options(theme: Theme = Theme.SYSTEM) -> dict[str, str]:
    """Theme for the chosen mode plus local-only server settings.

    *System* gives Streamlit both palettes and lets the browser choose; *light* or
    *dark* fixes the base and its palette.
    """
    options = {f"theme.{key}": value for key, value in SHARED_THEME.items()}
    if theme is Theme.SYSTEM:
        for mode, palette in (("light", LIGHT_PALETTE), ("dark", DARK_PALETTE)):
            options |= {f"theme.{mode}.{key}": value for key, value in palette.items()}
    else:
        palette = DARK_PALETTE if theme is Theme.DARK else LIGHT_PALETTE
        options |= {"theme.base": theme.value}
        options |= {f"theme.{key}": value for key, value in palette.items()}
    return options | SERVER_OPTIONS


_CSS = f"""
<style>
  .block-container {{ max-width: 1280px; padding-top: 2rem; }}
  h1, h2, h3 {{ letter-spacing: -0.02em; }}
  [data-testid="stVerticalBlockBorderWrapper"] {{
      box-shadow: 0 6px 16px rgba(0, 0, 0, 0.08);
  }}
  .stTabs [data-baseweb="tab-list"] {{ gap: 2rem; }}
  .stTabs [data-baseweb="tab"] {{ font-weight: 600; opacity: .7; }}
  .stTabs [aria-selected="true"] {{ opacity: 1; }}
  .app-header {{ display: flex; align-items: center; gap: .6rem; margin-bottom: .25rem; }}
  .app-header .mark {{
      width: 34px; height: 34px; border-radius: 10px; background: {CORAL};
      display: flex; align-items: center; justify-content: center; color: #fff;
      font-weight: 800; font-size: 1.1rem;
  }}
  .app-header .name {{ font-size: 1.5rem; font-weight: 800; color: {CORAL}; }}
  .app-subtitle {{ opacity: .7; margin-bottom: 1.5rem; }}
  .muted {{ opacity: .7; font-size: .9rem; }}
</style>
"""

_COMPACT_CSS = """
<style>
  .block-container { padding-top: 1rem; padding-bottom: 1rem; }
  [data-testid="stVerticalBlock"] { gap: .5rem; }
  [data-testid="stVerticalBlockBorderWrapper"] { padding: .25rem; }
  h1 { font-size: 1.8rem; } h2, h3 { font-size: 1.2rem; }
</style>
"""


def streamlit_flags(theme: Theme = Theme.SYSTEM) -> list[str]:
    """Command-line flags for ``streamlit run`` (the theme needs a restart to change)."""
    return [f"--{key}={value}" for key, value in streamlit_options(theme).items()]


def apply_page_style(density: Density = Density.COMFORTABLE) -> None:
    st.html(_CSS + (_COMPACT_CSS if density is Density.COMPACT else ""))


def header(title: str, subtitle: str) -> None:
    st.html(
        f'<div class="app-header"><div class="mark">C</div>'
        f'<div class="name">{title}</div></div>'
        f'<div class="app-subtitle">{subtitle}</div>'
    )
