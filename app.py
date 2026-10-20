"""
JSA Global Export Dashboard — John Stewart & Associates
Monthly export shipments in thousands of metric tons (TMT).

Commodities:
  🌽 Corn     — US, Brazil, Argentina, Ukraine, Total Non-US, Major Exporters
  🫘 Soybeans — US, Brazil, Argentina, Total Non-US, Major Exporters, China Imports

Marketing Year conventions:
  Oct–Sep : US, Ukraine, Total Non-US, Major Exporters, China Imports
  Mar–Feb : Argentina, Brazil

Data sources:
  US  — U.S. Census Bureau GATS (HS10) + USDA FGIS inspections (Socrata)
  Non-US — Trade Data Monitor (TDM) monthly export API
"""

import streamlit as st
from pathlib import Path

_FAVICON = Path(__file__).parent / "jsa_favicon.png"

st.set_page_config(
    page_title="JSA Global Export Dashboard",
    page_icon=str(_FAVICON) if _FAVICON.exists() else "🌍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import base64
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import ssl
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from html import escape as _html_escape
from pathlib import Path
from dotenv import load_dotenv, dotenv_values

# Load .env (droplet deployment)
_own_env   = Path(__file__).parent / ".env"
_basis_env = Path("/opt/basis-tracker/.env")
load_dotenv(_own_env)
if _basis_env.exists():
    for k, v in dotenv_values(_basis_env).items():
        if k not in os.environ and v:
            os.environ[k] = v


# ── Snowflake connection ──────────────────────────────────────────────────────
@st.cache_resource(ttl=1800)
def _get_sf_conn():
    """Return a live Snowflake connection, rebuilt every 30 min. An idle session expires after a
    few hours, and a connection cached forever then fails every query for the life of the process."""
    import snowflake.connector
    from cryptography.hazmat.primitives.serialization import (
        load_pem_private_key, Encoding, PrivateFormat, NoEncryption,
    )
    # Secrets: prefer st.secrets, fall back to env
    def _s(k):
        try:
            return st.secrets[k]
        except Exception:
            return os.environ.get(k, "")

    key_path = _s("SNOWFLAKE_PRIVATE_KEY_PATH")
    key_b64  = _s("SNOWFLAKE_PRIVATE_KEY")
    key_pwd  = (_s("SNOWFLAKE_PRIVATE_KEY_PWD") or "").encode() or None

    if key_path:
        pem = Path(key_path).expanduser().read_bytes()
    elif key_b64:
        # Raw PEM text (what the admin portal's shared secret holds) or base64 of the PEM
        # (this app's own Cloud secret).
        _kb = key_b64.encode() if isinstance(key_b64, str) else key_b64
        pem = _kb.replace(b"\\n", b"\n") if b"-----BEGIN" in _kb else base64.b64decode(_kb)
    else:
        return None

    pk = load_pem_private_key(pem, password=key_pwd)
    pk_der = pk.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    return snowflake.connector.connect(
        account   = _s("SNOWFLAKE_ACCOUNT"),
        user      = _s("SNOWFLAKE_USER"),
        private_key = pk_der,
        role      = _s("SNOWFLAKE_ROLE") or "EXPORTER_ROLE",
        warehouse = _s("SNOWFLAKE_WAREHOUSE") or "COMPUTE_WH",
        database  = "EXPORTS",
        schema    = "PUBLIC",
        client_session_keep_alive = True,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def _load_tdm_from_snowflake(reporter: str, product_code: str) -> dict:
    """Read TDM monthly data from Snowflake → {(year, month): tmt}.

    Raises on failure instead of returning {}: st.cache_data would otherwise cache the empty
    result for an hour and the dashboard would quietly show US-only data. A dead cached
    connection is dropped and retried once."""
    for attempt in (1, 2):
        conn = _get_sf_conn()
        if conn is None:
            raise RuntimeError("Snowflake credentials are not configured for this app")
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT year, month, tmt FROM TDM_MONTHLY "
                "WHERE reporter=%s AND product_code=%s",
                (reporter, product_code),
            )
            return {(int(r[0]), int(r[1])): float(r[2]) for r in cur.fetchall()}
        except Exception:
            if attempt == 2:
                raise
            _get_sf_conn.clear()       # the cached connection may have expired: reconnect

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = (
    r"C:\Users\KoltenPostin\John Stewart and Associates"
    r"\JSA - Documents\Research Analyst\Dashboards"
    r"\Corn by Major Exports"
)

def _find(filename: str) -> str:
    local = os.path.join(_HERE, filename)
    if os.path.exists(local):
        return local
    return os.path.join(DASHBOARD_DIR, filename)

LOGO_FULL_PATH  = _find("jsa_logo_full.png")
LOGO_WHITE_PATH = _find("jsa_logo_white.png")
LOGO_50YR_PATH  = _find("jsa_logo_50yr.png")

# ─────────────────────────────────────────────────────────────────────────────
# PLOTLY CHART CONFIG  (PNG download button + clean toolbar on every chart)
# ─────────────────────────────────────────────────────────────────────────────
_CHART_CONFIG = {
    "displayModeBar":         True,
    "displaylogo":            False,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
    "toImageButtonOptions":   {"format": "png", "scale": 2, "filename": "jsa_export_chart"},
}
_orig_plotly_chart = st.plotly_chart
def _patched_plotly_chart(fig, **kw):
    kw.setdefault("config", _CHART_CONFIG)
    kw.setdefault("theme", None)        # the figures style themselves; Streamlit's theme would overrule their fonts and legend colours
    return _orig_plotly_chart(fig, **kw)
st.plotly_chart = _patched_plotly_chart

# ─────────────────────────────────────────────────────────────────────────────
# BRAND COLORS
# ─────────────────────────────────────────────────────────────────────────────
JSA_GREEN = "#4a6741"
JSA_CYAN  = "#0693e3"
JSA_DARK  = "#1e2124"
JSA_CHAR  = "#32373c"
JSA_MID   = "#2a2f35"

# ─────────────────────────────────────────────────────────────────────────────
# CHART THEME — the light sage palette of the CFTC COT dashboard, shared by every chart
# ─────────────────────────────────────────────────────────────────────────────
_CH_SURF, _CH_BORDER, _CH_TEXT, _CH_MUTED = "#ffffff", "#d7e2dc", "#32373c", "#5f7267"
_CH_SAGE, _CH_BLUE, _CH_TERRA, _CH_LAV, _CH_GOLD, _CH_MIST = "#5e7164", "#6fa8c4", "#c4785a", "#9b89c4", "#c4b456", "#b8c4bc"
_CH_POS, _CH_NEG = "#16a34a", "#dc2626"                          # above / below a reference
_CH_EST, _CH_M1, _CH_M2 = "#d9822b", "#c8920d", "#16a34a"        # estimate segment, Model 1 (USDA seasonal), Model 2 (pace)
_CH_PAL = [_CH_BLUE, _CH_TERRA, _CH_SAGE, _CH_LAV, _CH_GOLD, "#8fa398", "#c0707f", "#5aa59a", "#7b8fb5", "#9c7a62", "#8c9a52", _CH_MIST]
_CH_FONT = '"Source Sans", "Source Sans Pro", sans-serif'
_CH_COUNTRY = {                                                   # one colour per country in every comparison chart
    "US": _CH_TERRA, "Brazil": _CH_SAGE, "Argentina": _CH_BLUE, "Ukraine": _CH_GOLD, "TotalNonUS": _CH_LAV,
    "MajorExporter": "#9c7a62", "ChinaImports": "#b5586b", "Canada": "#c0707f", "EU": "#4a7ba6", "Russia": "#7b8fb5",
    "India": "#d9904a", "China": "#a8505a", "Australia": "#5aa59a",
}
_CH_AXIS = dict(gridcolor=_CH_BORDER, linecolor=_CH_BORDER, showgrid=True, zeroline=False,
                tickfont=dict(color=_CH_MUTED, size=11), title_font=dict(color=_CH_MUTED, size=11))


def _ch_axis(title: str | None = None) -> dict:
    """An axis in the shared style. The title goes in as a dict: a plain string would replace the title font set above."""
    ax = dict(_CH_AXIS)
    ax.pop("title_font")
    if title:
        ax["title"] = dict(text=title, font=dict(color=_CH_MUTED, size=11))
    return ax


def _ch_mix(c1: str, c2: str, t: float) -> str:
    """The colour a fraction t of the way from c1 to c2 (both '#rrggbb')."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(a[j] + (b[j] - a[j]) * t):02x}" for j in range(3))


def _ch_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"

# ─────────────────────────────────────────────────────────────────────────────
# MONTH LISTS
# ─────────────────────────────────────────────────────────────────────────────
OCT_SEP_MONTHS = ["Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep"]
_MO_ABB = {
    "01":"Jan","02":"Feb","03":"Mar","04":"Apr","05":"May","06":"Jun",
    "07":"Jul","08":"Aug","09":"Sep","10":"Oct","11":"Nov","12":"Dec",
}
MAR_FEB_MONTHS = ["Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec","Jan","Feb"]
APR_MAR_MONTHS = ["Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar"]
SEP_AUG_MONTHS = ["Sep","Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug"]
JUL_JUN_MONTHS = ["Jul","Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun"]
DEC_NOV_MONTHS = ["Dec","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov"]
JUN_MAY_MONTHS = ["Jun","Jul","Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar","Apr","May"]
AUG_JUL_MONTHS = ["Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun","Jul"]
ALL_MONTHS     = set(OCT_SEP_MONTHS)

# ─────────────────────────────────────────────────────────────────────────────
# COMMODITY CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
COMMODITY_CONFIG = {
    "corn": {
        "sheet":          "Corn",
        "emoji":          "🌽",
        "label":          "Corn",
        "col_names":      ["MarketYear","Date","Month","US","Brazil","Argentina",
                           "Ukraine","TotalNonUS","MajorExporter"],
        "numeric_cols":   ["US","Brazil","Argentina","Ukraine","TotalNonUS","MajorExporter"],
        "fields": {
            "US":            "United States",
            "Brazil":        "Brazil",
            "Argentina":     "Argentina",
            "Ukraine":       "Ukraine",
            "TotalNonUS":    "Total Non-US",
            "MajorExporter": "Major Exporters",
        },
        "mar_feb_fields":   {"Brazil","Argentina"},
        "arbr_months":      MAR_FEB_MONTHS,
        "arbr_last_month":  "Feb",
        "arbr_label":       "Mar–Feb",
        "arbr_prev_months": frozenset({"Jan","Feb"}),
        "us_local_months":      SEP_AUG_MONTHS,
        "us_local_last_month":  "Aug",
        "us_local_label":       "Sep–Aug",
        "us_local_prev_months": frozenset({"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug"}),
        "import_fields":  set(),
        "non_us_comps":   ["Brazil","Argentina","Ukraine"],
        "major_comps":    ["US","Brazil","Argentina","Ukraine"],
        "tile_order":     ["US","Brazil","Argentina","Ukraine","TotalNonUS","MajorExporter"],
        "tile_accents": {
            "US":            "#f9a825",
            "Brazil":        "#2e7d32",
            "Argentina":     "#29b6f6",
            "Ukraine":       "#fdd835",
            "TotalNonUS":    "#7e57c2",
            "MajorExporter": "#ef5350",
        },
        "country_colors": dict(_CH_COUNTRY),
        "bu_lbs": 56.0,   # lbs per bushel of corn
    },
    "soybeans": {
        "sheet":          "Soybeans",
        "emoji":          "🫘",
        "label":          "Soybeans",
        "col_names":      ["MarketYear","Date","Month","US","Brazil","Argentina",
                           "TotalNonUS","MajorExporter","ChinaImports"],
        "numeric_cols":   ["US","Brazil","Argentina","TotalNonUS","MajorExporter","ChinaImports"],
        "fields": {
            "US":            "United States",
            "Brazil":        "Brazil",
            "Argentina":     "Argentina",
            "TotalNonUS":    "Total Non-US",
            "MajorExporter": "Major Exporters",
            "ChinaImports":  "China Imports",
        },
        "mar_feb_fields":   {"Brazil","Argentina"},
        "arbr_months":      APR_MAR_MONTHS,
        "arbr_last_month":  "Mar",
        "arbr_label":       "Apr–Mar",
        "arbr_prev_months": frozenset({"Jan","Feb","Mar"}),
        "us_local_months":      SEP_AUG_MONTHS,
        "us_local_last_month":  "Aug",
        "us_local_label":       "Sep–Aug",
        "us_local_prev_months": frozenset({"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug"}),
        "import_fields":  {"ChinaImports"},
        "non_us_comps":   ["Brazil","Argentina"],
        "major_comps":    ["US","Brazil","Argentina"],
        "tile_order":     ["US","Brazil","Argentina","TotalNonUS","MajorExporter","ChinaImports"],
        "tile_accents": {
            "US":            "#f9a825",
            "Brazil":        "#2e7d32",
            "Argentina":     "#29b6f6",
            "TotalNonUS":    "#7e57c2",
            "MajorExporter": "#ef5350",
            "ChinaImports":  "#e53935",
        },
        "country_colors": dict(_CH_COUNTRY),
        "bu_lbs": 60.0,   # lbs per bushel of soybeans
    },
    "soybeanmeal": {
        "sheet":          "Meal",
        "emoji":          "🌾",
        "label":          "Soybean Meal",
        # 6 raw columns only — TotalNonUS & MajorExporter are computed
        "col_names":      ["MarketYear","Date","Month","US","Brazil","Argentina"],
        "numeric_cols":   ["US","Brazil","Argentina"],
        "fields": {
            "US":            "United States",
            "Brazil":        "Brazil",
            "Argentina":     "Argentina",
            "TotalNonUS":    "Total Non-US",
            "MajorExporter": "Major Exporters",
        },
        "mar_feb_fields":   {"Brazil","Argentina"},
        "arbr_months":      APR_MAR_MONTHS,
        "arbr_last_month":  "Mar",
        "arbr_label":       "Apr–Mar",
        "arbr_prev_months": frozenset({"Jan","Feb","Mar"}),
        "us_local_months":      SEP_AUG_MONTHS,
        "us_local_last_month":  "Aug",
        "us_local_label":       "Sep–Aug",
        "us_local_prev_months": frozenset({"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug"}),
        "import_fields":    set(),
        "non_us_comps":     ["Brazil","Argentina"],
        "major_comps":      ["US","Brazil","Argentina"],
        "tile_order":       ["US","Brazil","Argentina","TotalNonUS","MajorExporter"],
        "tile_accents": {
            "US":            "#f9a825",
            "Brazil":        "#43a047",
            "Argentina":     "#29b6f6",
            "TotalNonUS":    "#8d6e63",
            "MajorExporter": "#ef6c00",
        },
        "country_colors": dict(_CH_COUNTRY),
        "bu_lbs":  None,   # meal is not measured in bushels
    },
    "wheat": {
        "sheet":        "Wheat",
        "emoji":        "🌾",
        "label":        "Wheat",
        # Adjust col_names to match your Excel column order exactly.
        # Remove India / China / Brazil if those columns are not yet present.
        # No MarketYear column needed — wheat pivots are built from the Date column.
        "col_names":    ["Date","Month","US","Canada","EU","Russia","Ukraine",
                         "India","China","Argentina","Australia","Brazil",
                         "TotalNonUS","MajorExporter"],
        "numeric_cols": ["US","Canada","EU","Russia","Ukraine",
                         "India","China","Argentina","Australia","Brazil",
                         "TotalNonUS","MajorExporter"],
        "fields": {
            "US":            "United States",
            "Canada":        "Canada",
            "EU":            "EU",
            "Russia":        "Russia",
            "Ukraine":       "Ukraine",
            "India":         "India",
            "China":         "China",
            "Argentina":     "Argentina",
            "Australia":     "Australia",
            "Brazil":        "Brazil",
            "TotalNonUS":    "Total Non-US",
            "MajorExporter": "Major Exporters",
        },
        # Per-field MY convention — all wheat uses year_offset=0 in build_arbr_pivot.
        # Aggregates (TotalNonUS, MajorExporter) are aligned to Jul-Jun (dominant NH).
        "field_my": {
            "US":            dict(months=JUN_MAY_MONTHS,
                                  prev=frozenset({"Jan","Feb","Mar","Apr","May"}),
                                  last="May", label="Jun–May", hemisphere="North"),
            "Canada":        dict(months=AUG_JUL_MONTHS,
                                  prev=frozenset({"Jan","Feb","Mar","Apr","May","Jun","Jul"}),
                                  last="Jul", label="Aug–Jul", hemisphere="North"),
            "EU":            dict(months=JUL_JUN_MONTHS,
                                  prev=frozenset({"Jan","Feb","Mar","Apr","May","Jun"}),
                                  last="Jun", label="Jul–Jun", hemisphere="North"),
            "Russia":        dict(months=JUL_JUN_MONTHS,
                                  prev=frozenset({"Jan","Feb","Mar","Apr","May","Jun"}),
                                  last="Jun", label="Jul–Jun", hemisphere="North"),
            "Ukraine":       dict(months=JUL_JUN_MONTHS,
                                  prev=frozenset({"Jan","Feb","Mar","Apr","May","Jun"}),
                                  last="Jun", label="Jul–Jun", hemisphere="North"),
            "India":         dict(months=APR_MAR_MONTHS,
                                  prev=frozenset({"Jan","Feb","Mar"}),
                                  last="Mar", label="Apr–Mar", hemisphere="North"),
            "China":         dict(months=JUN_MAY_MONTHS,
                                  prev=frozenset({"Jan","Feb","Mar","Apr","May"}),
                                  last="May", label="Jun–May", hemisphere="North"),
            "Argentina":     dict(months=DEC_NOV_MONTHS,
                                  prev=frozenset({"Jan","Feb","Mar","Apr","May","Jun",
                                                  "Jul","Aug","Sep","Oct","Nov"}),
                                  last="Nov", label="Dec–Nov", hemisphere="South"),
            "Australia":     dict(months=DEC_NOV_MONTHS,
                                  prev=frozenset({"Jan","Feb","Mar","Apr","May","Jun",
                                                  "Jul","Aug","Sep","Oct","Nov"}),
                                  last="Nov", label="Dec–Nov", hemisphere="South"),
            "Brazil":        dict(months=OCT_SEP_MONTHS,
                                  prev=frozenset({"Jan","Feb","Mar","Apr","May","Jun",
                                                  "Jul","Aug","Sep"}),
                                  last="Sep", label="Oct–Sep", hemisphere="South"),
            "TotalNonUS":    dict(months=JUL_JUN_MONTHS,
                                  prev=frozenset({"Jan","Feb","Mar","Apr","May","Jun"}),
                                  last="Jun", label="Jul–Jun", hemisphere="North"),
            "MajorExporter": dict(months=JUL_JUN_MONTHS,
                                  prev=frozenset({"Jan","Feb","Mar","Apr","May","Jun"}),
                                  last="Jun", label="Jul–Jun", hemisphere="North"),
        },
        # NH comparison window (Jul–Jun)
        "nh_months": JUL_JUN_MONTHS,
        "nh_prev":   frozenset({"Jan","Feb","Mar","Apr","May","Jun"}),
        "nh_last":   "Jun",
        "nh_label":  "Jul–Jun",
        # SH comparison window (Dec–Nov)
        "sh_months": DEC_NOV_MONTHS,
        "sh_prev":   frozenset({"Jan","Feb","Mar","Apr","May","Jun",
                                "Jul","Aug","Sep","Oct","Nov"}),
        "sh_last":   "Nov",
        "sh_label":  "Dec–Nov",
        "nh_fields": {"US","Canada","EU","Russia","Ukraine","India","China",
                      "TotalNonUS","MajorExporter"},
        "sh_fields": {"Argentina","Australia","Brazil"},
        "mar_feb_fields": set(),
        "import_fields":  set(),
        "non_us_comps":   ["Canada","EU","Russia","Ukraine","India","China",
                           "Argentina","Australia","Brazil"],
        "major_comps":    ["US","Canada","EU","Russia","Ukraine","India","China",
                           "Argentina","Australia","Brazil"],
        "tile_order":     ["US","Canada","EU","Russia","Ukraine","China",
                           "Argentina","Australia","Brazil","TotalNonUS","MajorExporter"],
        "tile_accents": {
            "US":            "#f9a825",
            "Canada":        "#ef5350",
            "EU":            "#1565c0",
            "Russia":        "#78909c",
            "Ukraine":       "#fdd835",
            "India":         "#ff6d00",
            "China":         "#b71c1c",
            "Argentina":     "#29b6f6",
            "Australia":     "#00897b",
            "Brazil":        "#43a047",
            "TotalNonUS":    "#7e57c2",
            "MajorExporter": "#ef6c00",
        },
        "country_colors": dict(_CH_COUNTRY),
        "bu_lbs": 60.0,   # lbs per bushel of wheat
    },
}


def _bu_conv_factor(cfg: dict) -> float:
    """TMT → Million Bushels conversion factor for this commodity.
    Returns 1.0 for commodities with no bushel equivalent (e.g. soybean meal)."""
    if not cfg.get("bu_lbs"):
        return 1.0
    return 1_000 * (2_204.62 / cfg["bu_lbs"]) / 1_000_000


def _apply_unit(pivot: dict, factor: float) -> dict:
    return {
        m: {y: (v * factor if v is not None else None) for y, v in yr.items()}
        for m, yr in pivot.items()
    }


# ─────────────────────────────────────────────────────────────────────────────
# FAVICON
# ─────────────────────────────────────────────────────────────────────────────
def _make_favicon():
    try:
        from PIL import Image
        img  = Image.open(LOGO_FULL_PATH).convert("RGBA")
        _, h = img.size
        icon = img.crop((0, 0, h, h))
        data = np.array(icon, dtype=np.uint8)
        mask = data[:, :, 3] > 10
        data[mask, 0], data[mask, 1], data[mask, 2] = 74, 103, 65
        return Image.fromarray(data, "RGBA")
    except Exception:
        return "📊"



# ─────────────────────────────────────────────────────────────────────────────
# LOGO UTILITIES
# ─────────────────────────────────────────────────────────────────────────────
def _load_logo_b64(path: str, _mtime: float = 0) -> str | None:
    """Load a logo file as a base64 data URI. Not cached so file changes are always picked up."""
    try:
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
        return f"data:image/png;base64,{encoded}"
    except FileNotFoundError:
        return None


@st.cache_data(show_spinner=False)
def _ch_watermark_uri() -> str | None:
    return _load_logo_b64(LOGO_50YR_PATH)


def _add_chart_watermark(fig: go.Figure, logo_b64: str | None = None) -> go.Figure:
    """The faint 50-year mark behind a chart. logo_b64 is accepted for the older call sites and ignored: the white
    logo they pass disappears on a light chart."""
    uri = _ch_watermark_uri()
    if uri is None:
        return fig
    fig.add_layout_image(dict(
        source=uri, xref="paper", yref="paper", x=0.5, y=0.5, sizex=0.42, sizey=0.42,
        xanchor="center", yanchor="middle", sizing="contain", opacity=0.09, layer="below",
    ))
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def load_data(commodity: str) -> pd.DataFrame:
    """Build monthly export DataFrame from APIs (Census GATS/FGIS for US; TDM for non-US).
    Non-US comes from Snowflake and raises if it cannot be read, so a failure is never cached."""
    cfg  = COMMODITY_CONFIG[commodity]
    now  = pd.Timestamp.now().replace(day=1)
    dates = pd.date_range("2010-01-01", now, freq="MS")
    df = pd.DataFrame({"Date": dates})
    df["Month"] = df["Date"].dt.strftime("%b")

    # Marketing year label
    if commodity == "wheat":
        df["MarketYear"] = df["Date"].apply(
            lambda d: f"{d.year}/{str(d.year+1)[-2:]}" if d.month >= 7
                      else f"{d.year-1}/{str(d.year)[-2:]}"
        )
    else:
        df["MarketYear"] = df["Date"].apply(
            lambda d: f"{d.year}/{str(d.year+1)[-2:]}" if d.month >= 10
                      else f"{d.year-1}/{str(d.year)[-2:]}"
        )

    # US: Census GATS (official, ~6-8 wk lag) + FGIS inspections (forward fill)
    us_data = _load_us_auto(commodity)
    df["US"] = df.apply(lambda r: us_data.get((r["Date"].year, r["Date"].month)), axis=1)

    # Non-US: read from Snowflake (ETL fetches TDM daily on the droplet)
    for field, (reporter, prod) in _TDM_REPORTERS.get(commodity, {}).items():
        country_data = _load_tdm_from_snowflake(reporter, prod)
        df[field] = df.apply(
            lambda r, d=country_data: d.get((r["Date"].year, r["Date"].month)), axis=1
        )

    # Ensure every expected column exists
    for col in cfg["numeric_cols"]:
        if col not in df.columns:
            df[col] = np.nan

    df = df[df["Month"].isin(ALL_MONTHS)].copy()
    return _enforce_aggregate_completeness(df, cfg)


def _enforce_aggregate_completeness(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Recompute TotalNonUS and MajorExporter from components.
    Only uses component columns that actually exist in the dataframe
    (handles wheat where some country columns may not be present yet).
    """
    df = df.copy()
    non_us_cols = [c for c in cfg["non_us_comps"] if c in df.columns]
    major_cols  = [c for c in cfg["major_comps"]  if c in df.columns]
    if non_us_cols:
        all_non_us = df[non_us_cols].notna().all(axis=1)
        df["TotalNonUS"] = np.where(all_non_us, df[non_us_cols].sum(axis=1), np.nan)
    if major_cols:
        all_major = df[major_cols].notna().all(axis=1)
        df["MajorExporter"] = np.where(all_major, df[major_cols].sum(axis=1), np.nan)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# OFFICIAL / ESTIMATE CONFIG
# ─────────────────────────────────────────────────────────────────────────────
_FULL_MONTH_TO_ABB = {
    "january":"Jan","february":"Feb","march":"Mar","april":"Apr",
    "may":"May","june":"Jun","july":"Jul","august":"Aug",
    "september":"Sep","october":"Oct","november":"Nov","december":"Dec",
    # already-abbreviated pass-through
    "jan":"Jan","feb":"Feb","mar":"Mar","apr":"Apr",
    "jun":"Jun","jul":"Jul","aug":"Aug",
    "sep":"Sep","oct":"Oct","nov":"Nov","dec":"Dec",
}

@st.cache_data(show_spinner=False)
def load_cutoff_config() -> dict[str, str]:
    """No static cutoffs — estimates are detected dynamically from FGIS vs GATS."""
    return {}


_MO_NAME_TO_NUM = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


@st.cache_data(ttl=86_400, show_spinner=False)
def _load_census_gats_keys(commodity: str) -> frozenset:
    """Return frozenset of (year, month) tuples where Census GATS data exists."""
    gats_df = _load_census_gats(commodity)
    if gats_df.empty:
        return frozenset()
    return frozenset(
        (int(r["year"]), int(r["month"])) for _, r in gats_df.iterrows()
    )


def _us_fgis_est_months(commodity: str, cy: str, months: list,
                         my_start_month: int = 10) -> set:
    """
    Return the set of MY month abbreviations (e.g. {"Jul", "Aug"}) in the
    current MY that Census GATS has not published yet (filled by an estimate where one exists).
    These are shown as EST — indicative only, not final Census figures.
    """
    gats_keys = _load_census_gats_keys(commodity)
    try:
        cy_start = int(cy.split("/")[0])
    except Exception:
        return set()

    fgis_months = set()
    for m in months:
        mo_num = _MO_NAME_TO_NUM.get(m)
        if mo_num is None:
            continue
        # Map MY month → calendar (year, month)
        cal_year = cy_start if mo_num >= my_start_month else cy_start + 1
        if (cal_year, mo_num) not in gats_keys:
            fgis_months.add(m)
    return fgis_months


def _cy_estimate_months(field: str, cutoffs: dict[str, str],
                        months_order: list[str]) -> set[str]:
    """Return the set of months in months_order that are estimates in the
    current marketing year for this field.  Empty set = all official."""
    last_off = cutoffs.get(field, "")
    if not last_off or last_off not in months_order:
        return set()
    cutoff_pos = months_order.index(last_off)
    return set(months_order[cutoff_pos + 1:])


# ─────────────────────────────────────────────────────────────────────────────
# FORECAST CONFIG & MODELS
# ─────────────────────────────────────────────────────────────────────────────
# USDA export forecasts come live from the PSD API (api.fas.usda.gov); see
# _load_wasde_forecasts below. _FORECAST_CONFIG is only a STALE fallback for when PSD
# cannot be reached. All values in thousand metric tons (TMT) per USDA convention.

_WASDE_AS_OF = "August 2025"   # ← update this string after each WASDE release

_FORECAST_CONFIG = {
    ("corn",        "US"):             55000,
    ("corn",        "Brazil"):         43000,
    ("corn",        "Argentina"):      37000,
    ("corn",        "Ukraine"):        22000,
    ("corn",        "TotalNonUS"):    102000,   # Brazil + Argentina + Ukraine
    ("corn",        "MajorExporter"): 157000,   # US + Brazil + Argentina + Ukraine
    ("soybeans",    "US"):             41912,
    ("soybeans",    "Brazil"):        115000,
    ("soybeans",    "Argentina"):       4600,
    ("soybeans",    "TotalNonUS"):    119600,   # Brazil + Argentina
    ("soybeans",    "MajorExporter"): 161512,   # US + Brazil + Argentina
    ("soybeanmeal", "US"):             17599,
    ("soybeanmeal", "Brazil"):         25500,
    ("soybeanmeal", "Argentina"):      29400,
    ("soybeanmeal", "TotalNonUS"):     54900,   # Brazil + Argentina
    ("soybeanmeal", "MajorExporter"):  72499,   # US + Brazil + Argentina
    ("wheat",       "US"):             24494,
    ("wheat",       "Canada"):         29000,
    ("wheat",       "EU"):             30500,
    ("wheat",       "Russia"):         44500,
    ("wheat",       "Ukraine"):        12500,
    ("wheat",       "Argentina"):      19500,
    ("wheat",       "Australia"):      26500,
}


_PSD_CODES = {"corn": "0440000", "soybeans": "2222000", "soybeanmeal": "0813100", "wheat": "0410000"}
# PSD uses FIPS-style country codes (Ukraine = UP, EU = E4), not ISO.
_PSD_COUNTRY = {
    "US": "US", "Brazil": "BR", "Argentina": "AR", "Ukraine": "UP",
    "Canada": "CA", "Russia": "RS", "EU": "E4", "Australia": "AS",
}
_PSD_NON_US = {
    "corn": ["Brazil", "Argentina", "Ukraine"],
    "soybeans": ["Brazil", "Argentina"],
    "soybeanmeal": ["Brazil", "Argentina"],
}
_PSD_MAJOR = {
    "corn": ["US", "Brazil", "Argentina", "Ukraine"],
    "soybeans": ["US", "Brazil", "Argentina"],
    "soybeanmeal": ["US", "Brazil", "Argentina"],
}


@st.cache_data(ttl=3600, show_spinner=False)
def _load_wasde_forecasts(my_year: int, use_ty: bool = True) -> tuple:
    """USDA PSD export forecasts for marketing year ``my_year`` (2026 = 2026/27), in TMT.

    Returns ({(commodity, field): TMT}, source). source is "live:<Month YYYY>" (the WASDE the
    figures come from) or "fallback:<reason>" when PSD cannot be reached and the stale static
    config is returned instead.

    use_ty=True  -> trade-year (Oct-Sep) exports where PSD publishes them (corn, wheat), which is
                    what the Oct-Sep USDA view and the TotalNonUS / MajorExporter sums compare to.
    use_ty=False -> marketing-year exports (each country's own year), for the local-MY views.
    "<field>_Local" keys always hold the marketing-year figure.
    """
    result: dict = {}
    as_of = (0, 0)
    try:
        for comm, code in _PSD_CODES.items():
            url = (f"{_ESR_GATEWAY}/api/psd/commodity/{code}/country/all/year/{my_year}"
                   f"?{urllib.parse.urlencode({'api_key': _ESR_API_KEY})}")
            with urllib.request.urlopen(url, timeout=30) as r:
                rows = json.loads(r.read())
            vals: dict = {}
            for x in rows:
                if x["attributeId"] in (88, 113):
                    vals[(x["countryCode"], x["attributeId"])] = float(x["value"])
                    as_of = max(as_of, (int(x["calendarYear"]), int(x["month"])))
            for field, cc in _PSD_COUNTRY.items():
                my_val = vals.get((cc, 88))
                val = (vals.get((cc, 113)) if use_ty else None) or my_val
                if val:
                    result[(comm, field)] = val
                if my_val:
                    result[(comm, f"{field}_Local")] = my_val
            for agg_key, comps in (("TotalNonUS", _PSD_NON_US.get(comm)),
                                   ("MajorExporter", _PSD_MAJOR.get(comm))):
                if comps and all((comm, c) in result for c in comps):
                    result[(comm, agg_key)] = sum(result[(comm, c)] for c in comps)
    except Exception as _e:
        return dict(_FORECAST_CONFIG), f"fallback:{_e}"

    if not result:
        return dict(_FORECAST_CONFIG), "fallback:no data"
    return result, f"live:{datetime(as_of[0], as_of[1], 1).strftime('%B %Y')}"


def _psd_year(cy) -> int:
    """USDA marketing-year start year from a label like "2026/27"; the current year if unparseable."""
    try:
        return int(str(cy).split("/")[0])
    except (ValueError, TypeError):
        now = datetime.now()
        return now.year if now.month >= 10 else now.year - 1


def load_forecast_config(cy=None, use_ty: bool = True) -> dict:
    data, _ = _load_wasde_forecasts(_psd_year(cy), use_ty)
    return data


def _wasde_api_source(cy=None, use_ty: bool = True) -> str:
    _, src = _load_wasde_forecasts(_psd_year(cy), use_ty)
    return src


def _wasde_source_label(cy=None, use_ty: bool = True) -> str:
    src = _wasde_api_source(cy, use_ty)
    if src.startswith("live:"):
        return f"USDA PSD (live) · {src[5:]} WASDE"
    return f"STALE static fallback (WASDE {_WASDE_AS_OF}) — USDA PSD unreachable"


# ─────────────────────────────────────────────────────────────────────────────
# US EXPORT DATA — Census GATS + FGIS Inspections (auto-fill for US column)
# ─────────────────────────────────────────────────────────────────────────────
# Primary: U.S. Census Bureau International Trade API (official GATS monthly data)
#   Endpoint: https://api.census.gov/data/timeseries/intltrade/exports/hs
#   No CTY_CODE in GET → returns world-aggregate totals per HS10 code.
# Extension: USDA FGIS grain inspections via Socrata (no key needed).
#   Used to fill months not yet published in Census (approx. 6–8 wk lag).
# When the Excel file already has US data for a month, it takes precedence.

CENSUS_API_KEY = "e92f7717c349de7f48c07ecb27af5cc1fea15121"

# Schedule B HS10 codes, confirmed from api.census.gov
# Unit "T" = metric tons; "KG" = kilograms (÷1000 → MT)
_CENSUS_HS10 = {
    "corn": [
        ("1005902020", "T"), ("1005902030", "T"), ("1005902035", "T"),
        ("1005902045", "T"), ("1005902070", "T"), ("1005904055", "T"),
        ("1005904065", "T"),
        ("1005100010", "KG"), ("1005100090", "KG"), ("1005904049", "KG"),
    ],
    "soybeans": [
        ("1201900095", "T"),
        ("1201100000", "KG"), ("1201900005", "KG"),
    ],
    "soybeanmeal": [
        ("2304000000", "KG"),
    ],
    "wheat": [
        ("1001190000", "T"), ("1001992015", "T"), ("1001992055", "T"),
        ("1001110000", "KG"), ("1001910000", "KG"),
    ],
}

_SOCRATA_HOST  = "agtransport.usda.gov"
_SOCRATA_PATH  = "/resource/sruw-w49i.json"
_SOCRATA_GRAIN = {
    "corn":     "CORN",
    "soybeans": "SOYBEANS",
    "wheat":    "WHEAT",
}


@st.cache_data(ttl=86_400, show_spinner=False)
def _load_census_gats(commodity: str) -> pd.DataFrame:
    """World-total monthly US exports from Census HS10 codes → DataFrame(year,month,tmt)."""
    import http.client
    codes = _CENSUS_HS10.get(commodity)
    if not codes or not CENSUS_API_KEY:
        return pd.DataFrame()
    start_yr = 2010
    end_yr   = pd.Timestamp.now().year
    mt_by_ym: dict = {}
    for code, _unit in codes:
        params = urllib.parse.urlencode({
            "get":       "E_COMMODITY,QTY_1_MO,UNIT_QY1",
            "COMM_LVL":  "HS10",
            "E_COMMODITY": code,
            "time":      f"from {start_yr}-01 to {end_yr}-12",
            "key":       CENSUS_API_KEY,
        })
        try:
            ctx  = ssl.create_default_context()
            conn = http.client.HTTPSConnection("api.census.gov", 443, context=ctx, timeout=25)
            conn.request("GET",
                f"/data/timeseries/intltrade/exports/hs?{params}",
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                         "Connection": "close"})
            resp = conn.getresponse()
            body = resp.read(500_000).decode("utf-8", errors="ignore")
            conn.close()
            if resp.status != 200 or body.strip().startswith("<"):
                continue
            rows = json.loads(body)
            h        = rows[0]
            qty_idx  = h.index("QTY_1_MO")
            unit_idx = h.index("UNIT_QY1")
            time_idx = h.index("time")
            for row in rows[1:]:
                q = row[qty_idx]
                if q in (None, "null", "", "0"):
                    continue
                q    = float(q)
                if q <= 0:
                    continue
                unit = row[unit_idx]
                mt   = q if unit == "T" else q / 1000.0
                ts   = row[time_idx]
                parts = ts.split("-")
                if len(parts) < 2:
                    continue
                key = (int(parts[0]), int(parts[1]))
                mt_by_ym[key] = mt_by_ym.get(key, 0.0) + mt
        except Exception:
            continue
    if not mt_by_ym:
        return pd.DataFrame()
    out = []
    for (yr, mo), mt in sorted(mt_by_ym.items()):
        mo_str = f"{mo:02d}"
        mo_abb = _MO_ABB.get(mo_str)
        if not mo_abb:
            continue
        out.append({"year": yr, "month": mo, "tmt": mt / 1000.0})
    return pd.DataFrame(out) if out else pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _load_fgis_inspections(commodity: str) -> pd.DataFrame:
    """USDA FGIS grain inspections via Socrata → DataFrame(year,month,tmt). No key needed."""
    import http.client
    grain = _SOCRATA_GRAIN.get(commodity)
    if not grain:
        return pd.DataFrame()
    try:
        params = urllib.parse.urlencode({
            "$select": "year,month,sum(mt) as total_mt",
            "$where":  f"grain='{grain}'",
            "$group":  "year,month",
            "$limit":  "600",
        })
        ctx  = ssl.create_default_context()
        conn = http.client.HTTPSConnection(_SOCRATA_HOST, 443, context=ctx, timeout=15)
        conn.request("GET", f"{_SOCRATA_PATH}?{params}",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                     "Connection": "close"})
        resp = conn.getresponse()
        body = resp.read(200_000)
        conn.close()
        if resp.status != 200:
            return pd.DataFrame()
        rows = json.loads(body.decode("utf-8"))
        out  = [{"year": int(r["year"]), "month": int(r["month"]),
                 "tmt": float(r["total_mt"]) / 1000}
                for r in rows]
        return pd.DataFrame(out) if out else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


# USDA ESR commodity codes used to estimate US months Census has not published yet: the same
# set and method as the daily email (etl.us_series), so the two show the same US number.
_US_ESR_CODE = {"corn": 401, "soybeans": 801, "soybeanmeal": 901}


@st.cache_data(ttl=3600, show_spinner=False)
def _esr_us_monthly(esr_code: int) -> tuple:
    """US weekly export shipments (USDA ESR) spread evenly over the days each week covers.
    Returns ({(year, month): TMT}, date of the latest week-ending or None)."""
    yr = datetime.now().year
    weeks: dict = {}
    for my in (yr - 1, yr, yr + 1):
        for x in _load_esr_exports(esr_code, my):
            try:
                wk = datetime.fromisoformat(str(x["weekEndingDate"])[:19]).date()
                weeks[wk] = weeks.get(wk, 0.0) + float(x.get("weeklyExports") or 0)
            except (KeyError, ValueError, TypeError):
                continue
    if not weeks:
        return {}, None
    out: dict = {}
    for end, total in weeks.items():
        for i in range(7):
            d = end - timedelta(days=i)
            out[(d.year, d.month)] = out.get((d.year, d.month), 0.0) + total / 7 / 1000.0
    return out, max(weeks)


def _us_estimates(esr_code: int, census: dict) -> dict:
    """Months after Census's latest: ESR shipments scaled by the last 3 months' Census/ESR ratio,
    only for months ESR has fully reported (identical to the email's etl.us_series)."""
    esr, last_wk = _esr_us_monthly(esr_code)
    if not esr or not last_wk or not census:
        return {}
    cal = [k for k in sorted(census) if k in esr][-3:]
    if len(cal) < 3:
        return {}
    ratio = sum(census[k] for k in cal) / sum(esr[k] for k in cal)
    if not 0.7 <= ratio <= 1.3:
        return {}
    est: dict = {}
    y, m = max(census)
    while True:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        month_end = datetime(y + (m == 12), m % 12 + 1, 1).date() - timedelta(days=1)
        if (y, m) not in esr or month_end > last_wk:
            break
        est[(y, m)] = esr[(y, m)] * ratio
    return est


@st.cache_data(ttl=3600, show_spinner=False)
def _load_us_auto(commodity: str) -> dict:
    """US monthly exports → {(year, month): tmt}.

    Census GATS is official (~5-6 week lag). For corn, soybeans and meal, months Census has not
    published yet are estimated from USDA ESR export shipments scaled to Census, the same method
    as the daily email, so the dashboard and the email agree on the US number.
    Wheat, and any case where Census is unavailable, keeps the older FGIS-inspections fill below.

    FGIS is capped at the previous complete calendar month — the current month is
    always partial (FGIS reports weekly; mid-month only a fraction of weeks are in).
    """
    esr_code = _US_ESR_CODE.get(commodity)
    if esr_code:
        census = {(int(r["year"]), int(r["month"])): float(r["tmt"])
                  for _, r in _load_census_gats(commodity).iterrows()}
        if census:
            return {**_us_estimates(esr_code, census), **census}

    now = pd.Timestamp.now()
    # Last fully-complete calendar month (current month is always partial in FGIS)
    prev = now.replace(day=1) - pd.Timedelta(days=1)      # last day of the previous month
    fgis_cap = (int(prev.year), int(prev.month))

    records: dict = {}
    fgis_df = _load_fgis_inspections(commodity)
    for _, row in fgis_df.iterrows():
        key = (int(row["year"]), int(row["month"]))
        if key <= fgis_cap:
            records[key] = float(row["tmt"])
    gats_df = _load_census_gats(commodity)
    for _, row in gats_df.iterrows():
        records[(int(row["year"]), int(row["month"]))] = float(row["tmt"])
    return records


def _compute_seasonal_shares(monthly_pivot: dict, complete_years: list,
                              months: list) -> dict:
    """Compute robust monthly share distributions from complete historical years.

    Returns {month: {'mean': float, 'olympic': float, 'std': float}}
    where values are percentage of marketing-year total (0-100).
    Uses olympic average (drop high/low) when ≥4 years are available.
    """
    if len(complete_years) < 2:
        return {}
    # MY total per year
    year_totals = {}
    for yr in complete_years:
        total = sum((monthly_pivot[m].get(yr) or 0) for m in months)
        if total > 0:
            year_totals[yr] = total

    shares_by_month: dict[str, list] = {m: [] for m in months}
    for yr, total in year_totals.items():
        for m in months:
            val = monthly_pivot[m].get(yr)
            if val is not None:
                shares_by_month[m].append(val / total * 100.0)

    result = {}
    for m in months:
        vals = shares_by_month[m]
        if len(vals) < 2:
            continue
        mean_v = float(np.mean(vals))
        std_v  = float(np.std(vals, ddof=1))
        oly_v  = float(np.mean(sorted(vals)[1:-1])) if len(vals) >= 4 else mean_v
        result[m] = {"mean": mean_v, "olympic": oly_v, "std": std_v}
    return result


def _build_forecast_pivots(monthly_pivot: dict, all_years: list, cy: str,
                            months: list, shares: dict,
                            usda_total: float | None,
                            cy_est_months: set) -> tuple:
    """Build Model 1 (USDA Seasonal) and Model 2 (Pace-Adjusted) forecast pivots.

    Returns: (model1_pivot, model2_pivot, pace_info)
      model1_pivot  – full pivot, CY non-official months filled so implied total = USDA total
      model2_pivot  – same but implied total = USDA × pace-adjustment factor
      pace_info     – dict of KPIs for the forecast panel

    Budget-constrained logic:
      Model 1: remaining_budget = usda_total − ytd_actual
               Each non-official month gets remaining_budget × (its_share / Σ remaining_shares)
               → implied total always equals usda_total exactly.
      Model 2: model2_target = usda_total × adj_factor  (40 % of YTD pace deviation)
               remaining_budget_m2 = model2_target − ytd_actual
               Same proportional distribution → implied total = model2_target.
    """
    if not usda_total or not shares:
        return None, None, {}

    # Official = has data AND not an estimate
    official_months = {m for m in months
                       if monthly_pivot[m].get(cy) is not None
                       and m not in cy_est_months}

    # All non-official = estimate + blank (forecast line + model fill)
    non_official_months = [m for m in months
                           if m in cy_est_months or monthly_pivot[m].get(cy) is None]

    # YTD actual (official only) & YTD seasonal expected
    ytd_actual   = sum((monthly_pivot[m].get(cy) or 0) for m in official_months)
    ytd_share    = sum(shares[m]["olympic"] for m in official_months if m in shares)
    ytd_expected = usda_total * ytd_share / 100.0 if ytd_share > 0 else 0.0

    # Pace ratio: how far above/below the seasonal baseline we are YTD
    pace_ratio = (ytd_actual / ytd_expected) if ytd_expected > 0 else 1.0

    # Remaining share denominator (for proportional allocation)
    remaining_share_sum = sum(shares[m]["olympic"]
                               for m in non_official_months if m in shares)

    # Model 2 target = USDA × pace-adjustment (40 % persistence)
    PERSISTENCE   = 0.40
    adj_factor    = 1.0 + PERSISTENCE * (pace_ratio - 1.0)
    model2_target = usda_total * adj_factor

    # Budget-constrained pivot builder
    def _make_pivot(remaining_budget: float) -> dict:
        piv = {m: dict(monthly_pivot[m]) for m in months}
        if remaining_share_sum > 0:
            for m in non_official_months:
                if m in shares:
                    piv[m][cy] = (remaining_budget
                                  * shares[m]["olympic"] / remaining_share_sum)
        return piv

    m1_remaining_budget = usda_total    - ytd_actual
    m2_remaining_budget = model2_target - ytd_actual

    model1_pivot = _make_pivot(m1_remaining_budget)
    model2_pivot = _make_pivot(m2_remaining_budget) if official_months else None

    # Uncertainty: ±1σ on the non-official portion, scaled to remaining budget
    sigma_remaining = (
        sum(
            (m1_remaining_budget * shares[m]["std"] / remaining_share_sum) ** 2
            for m in non_official_months if m in shares
        ) ** 0.5
        if remaining_share_sum > 0 else 0.0
    )

    pace_info = {
        "usda_total":      usda_total,
        "cy":              cy,
        "ytd_actual":      ytd_actual,
        "ytd_expected":    ytd_expected,
        "pace_ratio":      pace_ratio,
        "pace_pct":        (pace_ratio - 1.0) * 100.0,
        "has_ytd":         bool(official_months),
        "adj_factor":      adj_factor,
        "model1_total":    usda_total,        # always pins to USDA total
        "model2_total":    model2_target,     # pace-adjusted total
        "sigma_remaining": sigma_remaining,
        "forecast_months": non_official_months,
    }
    return model1_pivot, model2_pivot, pace_info


# ─────────────────────────────────────────────────────────────────────────────
# PIVOT BUILDERS
# ─────────────────────────────────────────────────────────────────────────────
def build_pivot(df: pd.DataFrame, field: str) -> tuple[dict, list[str]]:
    months = OCT_SEP_MONTHS
    pivot  = {m: {} for m in months}
    for _, row in df.iterrows():
        m, y = row["Month"], row["MarketYear"]
        if m not in pivot:
            continue
        # Normalise year to str so sorted() never sees mixed int/float/str
        try:
            y = str(int(float(y)))
        except (ValueError, TypeError):
            y = str(y)
        val = row[field]
        pivot[m][y] = None if pd.isna(val) else float(val)
    all_years = sorted({y for md in pivot.values() for y in md})
    return pivot, all_years


def build_arbr_pivot(df: pd.DataFrame, field: str,
                     months_list=None,
                     prev_months=None,
                     year_offset: int = -1) -> tuple[dict, list[str]]:
    """
    Build a marketing-year pivot keyed by derived year label.

    months_list  : ordered month list for this MY convention
    prev_months  : months that belong to the *previous* MY label
    year_offset  : controls the year-label formula:
                     -1  Southern Hemisphere planting convention (AR/BR default)
                         non-prev → year-1,  prev → year-2
                      0  Standard convention (US Sep-Aug)
                         non-prev → year,    prev → year-1
    """
    if months_list is None:
        months_list = MAR_FEB_MONTHS
    if prev_months is None:
        prev_months = frozenset({"Jan", "Feb"})
    pivot = {m: {} for m in months_list}
    for _, row in df.iterrows():
        month = row["Month"]
        if month not in pivot:
            continue
        date = row["Date"]
        if pd.isna(date):
            continue
        val   = row[field]
        year  = date.year
        start = (year + year_offset - 1) if month in prev_months \
                else (year + year_offset)
        label = f"{start}/{str(start + 1)[-2:]}"
        if label not in pivot[month]:
            pivot[month][label] = None if pd.isna(val) else float(val)
    all_years = sorted({y for md in pivot.values() for y in md})
    return pivot, all_years


def build_cumulative_pivot(monthly_pivot: dict, years: list[str],
                            months: list[str]) -> dict:
    cum = {m: {} for m in months}
    for year in years:
        running = 0.0
        for month in months:
            val = monthly_pivot[month].get(year)
            if val is not None:
                running += val
                cum[month][year] = running
            else:
                cum[month][year] = None
    return cum


# ─────────────────────────────────────────────────────────────────────────────
# STATISTICS
# ─────────────────────────────────────────────────────────────────────────────
def get_complete_years(pivot: dict, last_month: str) -> list[str]:
    return sorted(y for y, v in pivot.get(last_month, {}).items() if v is not None)


def olympic_avg(values: list) -> float | None:
    valid = [v for v in values if v is not None and not np.isnan(v)]
    if len(valid) < 3:
        return None
    s = sorted(valid)
    return sum(s[1:-1]) / len(s[1:-1])


def compute_stats(data_pivot, all_years, complete_years, cy, ly,
                  months, is_cumulative=False) -> dict:
    oly_years  = sorted(complete_years)[-6:]
    hist_years = sorted(complete_years)
    stats: dict = {}

    for month in months:
        hist_vals  = [data_pivot[month].get(y) for y in hist_years]
        clean_hist = [v for v in hist_vals if v is not None]
        oly_vals   = [data_pivot[month].get(y) for y in oly_years]
        cy_val     = data_pivot[month].get(cy)
        ly_val     = data_pivot[month].get(ly) if ly else None
        oly = olympic_avg(oly_vals)
        stats[month] = dict(
            oly_avg    = oly,
            min        = min(clean_hist) if clean_hist else None,
            max        = max(clean_hist) if clean_hist else None,
            pct_vs_ly  = _pct(cy_val, ly_val),
            pct_vs_oly = _pct(cy_val, oly),
        )

    totals: dict[str, float | None] = {}
    for year in all_years:
        yr_vals  = [data_pivot[m].get(year) for m in months]
        non_none = [v for v in yr_vals if v is not None]
        totals[year] = (non_none[-1] if non_none else None) if is_cumulative \
                       else (sum(non_none) if non_none else None)

    hist_totals = [totals.get(y) for y in hist_years]
    clean_ht    = [v for v in hist_totals if v is not None]
    oly_t       = olympic_avg([totals.get(y) for y in oly_years])

    # Find the latest month CY has reported, then compare CY / LY / Oly Avg
    # all at that same reference month — always apples-to-apples mid-year.
    cy_reported = [m for m in months if data_pivot[m].get(cy) is not None]
    latest_m    = cy_reported[-1] if cy_reported else None
    m_idx       = months.index(latest_m) if latest_m is not None else -1
    months_thru = months[: m_idx + 1]

    if latest_m is not None:
        if is_cumulative:
            # For cumulative pivot, value at latest_m IS the running total
            cy_ref  = data_pivot[latest_m].get(cy)
            ly_ref  = data_pivot[latest_m].get(ly) if ly else None
            oly_ref = olympic_avg([data_pivot[latest_m].get(y) for y in oly_years])
        else:
            # For monthly pivot, sum only the months CY has reported
            def _sum_thru(year):
                nv = [v for m in months_thru
                      if (v := data_pivot[m].get(year)) is not None]
                return sum(nv) if nv else None
            cy_ref  = _sum_thru(cy)
            ly_ref  = _sum_thru(ly) if ly else None
            oly_ref = olympic_avg([_sum_thru(y) for y in oly_years])
    else:
        cy_ref = ly_ref = oly_ref = None

    stats["TOTAL"] = dict(
        oly_avg    = oly_t,
        min        = min(clean_ht) if clean_ht else None,
        max        = max(clean_ht) if clean_ht else None,
        pct_vs_ly  = _pct(cy_ref, ly_ref),
        pct_vs_oly = _pct(cy_ref, oly_ref),
    )
    stats["_totals"] = totals
    return stats


def _pct(a, b) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return (a - b) / abs(b) * 100


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTING
# ─────────────────────────────────────────────────────────────────────────────
def fmt_num(val, decimals: int = 0) -> str:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "—"
    return f"{val:,.{decimals}f}"


def fmt_pct(val) -> str:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "—"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def pct_color(val) -> str:
    if val is None:
        return "#555555"
    return "#1b7e35" if val > 0 else "#c0392b" if val < 0 else "#555555"


# ─────────────────────────────────────────────────────────────────────────────
# TABLE RENDERER
# ─────────────────────────────────────────────────────────────────────────────
_TABLE_CSS = """
<style>
.corn-wrap {
    /* direction:rtl makes a scroll box open scrolled to its RIGHT end, so the most recent years sit next to the
       pinned current-year columns; the table inside is set back to ltr below, so nothing else changes */
    direction: rtl;
    overflow-x: auto; max-height: 580px; overflow-y: auto;
    border-radius: 6px; border: 1px solid #484f56;
    font-family: Arial, sans-serif; font-size: 12.5px;
    line-height: 1.35; margin-bottom: 8px;
}
.corn-tbl { direction: ltr; border-collapse: collapse; width: max-content; min-width: 100%; }
.corn-tbl th {
    background: #1e2124; color: #ffffff; font-weight: 600;
    text-align: center; padding: 7px 10px; white-space: nowrap;
    position: sticky; top: 0; z-index: 3;
    border-right: 1px solid #484f56; border-bottom: 2px solid #0d0f11;
}
.corn-tbl th.stat-hdr  { background: #2a2f35; color: #ffffff; z-index: 5; }
.corn-tbl th.cy-hdr    { background: #0555a0; color: #ffffff; }
.corn-tbl td {
    padding: 5px 10px; text-align: right; white-space: nowrap;
    border-bottom: 1px solid #484f56; border-right: 1px solid #484f56;
    background: #32373c; color: #ffffff;
}
.corn-tbl tr:hover td  { filter: brightness(1.2); }
.corn-tbl td.m-cell    { text-align: left; font-weight: 700;
                          background: #1e2124 !important; color: #ffffff !important; }
.corn-tbl td.s-cell    { background: #f4f6f8 !important; color: #000000 !important; }
.corn-tbl td.p-cell    { background: #f4f6f8 !important; font-weight: 700; }
.corn-tbl td.left-divider { border-left: 2px solid #0693e3 !important; }
.corn-tbl th.left-divider { border-left: 2px solid #0693e3 !important; }
.corn-tbl td.cy-cell   { background: #0693e3 !important; font-weight: 600; color: #ffffff !important; }
.corn-tbl tr.total-row td {
    background: #232729 !important; font-weight: 700;
    border-top: 2px solid #0693e3; color: #ffffff;
}
.corn-tbl tr.total-row td.cy-cell { background: #0555a0 !important; color: #ffffff !important; }
.corn-tbl tr.total-row td.s-cell  { background: #2a2f35 !important; color: #ffffff !important; }
.corn-tbl tr.total-row td.p-cell  { background: #2a2f35 !important; }
.corn-tbl td.est-cy-cell  { background: #7a5800 !important; color: #ffe082 !important;
                             font-weight: 600 !important; border-top: 1px dashed #f9a825 !important;
                             border-bottom: 1px dashed #f9a825 !important; }
.corn-tbl tr.total-row td.est-cy-cell { background: #5a4000 !important; color: #ffe082 !important; }
.est-badge { background:#7a5800; border:1px dashed #f9a825; padding:2px 8px;
             border-radius:3px; color:#ffe082; font-weight:600; }
.corn-tbl th.m1-hdr { background: #3d3100 !important; color: #fdd835 !important; }
.corn-tbl th.m2-hdr { background: #003d1a !important; color: #00e676 !important; }
.corn-tbl td.m1-cell { background: #3d3100 !important; color: #fdd835 !important;
                        font-weight: 600 !important; border-top: 1px dotted #fdd835 !important;
                        border-bottom: 1px dotted #fdd835 !important; }
.corn-tbl td.m2-cell { background: #003d1a !important; color: #00e676 !important;
                        font-weight: 600 !important; border-top: 1px dashed #00e676 !important;
                        border-bottom: 1px dashed #00e676 !important; }
.corn-tbl tr.total-row td.m1-cell { background: #2a2200 !important; color: #fdd835 !important; }
.corn-tbl tr.total-row td.m2-cell { background: #002a10 !important; color: #00e676 !important; }
.corn-tbl td.m-dash { background: #1e2124 !important; color: #4a5568 !important; }

/* ── Vessel Lineup tab ── */
.vl-kpi-wrap {
    background: #ffffff; border: 1px solid #d7e2dc;
    border-radius: 10px; padding: 14px 18px; margin-bottom: 6px;
}
.vl-kpi-label {
    font-size: 10px; font-weight: 600; color: #5f7267;
    text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px;
}
.vl-kpi-value {
    font-size: 26px; font-weight: 700; color: #32373c;
    line-height: 1.1; margin: 0;
}
.vl-kpi-sub { font-size: 11px; color: #5f7267; margin-top: 2px; }
.vl-sec-hdr {
    font-size: 11px; font-weight: 700; color: #5f7267;
    text-transform: uppercase; letter-spacing: 0.09em;
    border-bottom: 1px solid #d7e2dc; padding-bottom: 6px; margin: 22px 0 14px 0;
}
</style>
"""

_TITLE_COMMODITY = {"Soybeans": "Soybean"}      # "United States Soybean Exports", not "Soybeans Exports"


def _view_title_html(field_label: str, commodity_label: str, is_import: bool, unit_short: str) -> str:
    """The title above a country / category view, e.g. "United States Corn Exports (TMT)": the country that is
    filtered, the commodity, exports (imports for China's import field) and the unit the tables are listed in."""
    flow = "Imports" if is_import else "Exports"
    who = field_label[:-len(" Imports")] if is_import and field_label.endswith(" Imports") else field_label
    if who == "Major Exporters":
        who += "'"                                    # Major Exporters' Corn Exports
    what = _TITLE_COMMODITY.get(commodity_label, commodity_label)
    return (
        f'<div style="margin:18px 0 12px 0;padding-bottom:8px;border-bottom:2px solid {JSA_CYAN};'
        f'font-family:Arial,sans-serif;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;">'
        f'<span style="font-size:27px;font-weight:700;color:{JSA_DARK};letter-spacing:0.2px;line-height:1.2;">'
        f'{who} {what} {flow}</span>'
        f'<span style="font-size:17px;font-weight:600;color:#6b7280;">({unit_short})</span></div>'
    )


def _show_table(pivot, html: str, key: str) -> None:
    """Render a styled HTML table with a CSV download button above it."""
    btn_col, _ = st.columns([1, 6])
    with btn_col:
        try:
            # pivot is {month: {year: value}} — transpose so years are columns
            csv_data = pd.DataFrame(pivot).T.to_csv(index_label="Month")
        except Exception:
            csv_data = ""
        if csv_data:
            st.download_button(
                "⬇ CSV",
                data=csv_data,
                file_name=f"{key}.csv",
                mime="text/csv",
                key=f"dl_{key}",
            )
    st.markdown(html, unsafe_allow_html=True)


def render_table_html(data_pivot, stats, all_years, cy, ly, months,
                      decimals: int = 0,
                      cy_est_months: set | None = None,
                      model1_pivot: dict | None = None,
                      model2_pivot: dict | None = None,
                      drop_oldest: int = 2) -> str:
    """Render the data table as HTML.

    cy_est_months : set of month names in the current MY that are Estimates.
    model1_pivot  : USDA Seasonal forecast pivot; adds M1 Fcst column when provided.
    model2_pivot  : Pace-Adjusted forecast pivot; adds M2 Fcst column when provided.
    drop_oldest   : number of oldest marketing years to hide from the table.
    """
    fn = lambda v: fmt_num(v, decimals)
    W  = dict(month=65, stat=94, pct=112, year=90, fcst=90)
    est_months = cy_est_months or set()
    has_fcst   = model1_pivot is not None

    # Years to display: drop the oldest N to make room for M1/M2 columns
    display_years = sorted(all_years)
    if drop_oldest > 0 and len(display_years) > drop_oldest:
        display_years = display_years[drop_oldest:]

    def sticky_left(w):
        return f"position:sticky;left:0;min-width:{w}px;z-index:2;"

    # Build sticky-right offset array.
    # Columns right→left: % vs Oly, % vs LY, Max, Min, Oly Avg,
    #   [M2 Fcst, M1 Fcst if has_fcst], CY
    R = [0]
    R.append(R[-1] + W["pct"])   # R[1]
    R.append(R[-1] + W["pct"])   # R[2]
    R.append(R[-1] + W["stat"])  # R[3]
    R.append(R[-1] + W["stat"])  # R[4]  — Oly Avg
    R.append(R[-1] + W["stat"])  # R[5]  — M2 or CY (no-fcst)
    if has_fcst:
        R.append(R[-1] + W["fcst"])  # R[6]  — M1
        R.append(R[-1] + W["fcst"])  # R[7]  — CY
    cy_r  = 7 if has_fcst else 5
    m2_r  = 5
    m1_r  = 6

    def sticky_right(r_idx, w):
        return f"position:sticky;right:{R[r_idx]}px;min-width:{w}px;z-index:5;"

    # ── Header ───────────────────────────────────────────────────────────────
    hdr = (f'<th class="stat-hdr" '
           f'style="{sticky_left(W["month"])};text-align:left;z-index:10;">Month</th>')
    for year in display_years:
        if year == cy:
            continue
        hdr += f'<th style="min-width:{W["year"]}px;">{year}</th>'

    sticky_cols = [
        (cy_r, W["year"], "cy-hdr left-divider", cy),
        (4,    W["stat"], "stat-hdr",            "6-Yr<br>Oly Avg"),
        (3,    W["stat"], "stat-hdr",            "Min"),
        (2,    W["stat"], "stat-hdr",            "Max"),
        (1,    W["pct"],  "stat-hdr",            "% Chg<br>CY vs LY"),
        (0,    W["pct"],  "stat-hdr",            "% Chg CY<br>vs Oly Avg"),
    ]
    if has_fcst:
        sticky_cols.insert(1, (m1_r, W["fcst"], "m1-hdr", "M1<br>Fcst"))
        sticky_cols.insert(2, (m2_r, W["fcst"], "m2-hdr", "M2<br>Fcst"))
    for r_idx, w, cls, lbl in sticky_cols:
        hdr += f'<th class="{cls}" style="{sticky_right(r_idx, w)}">{lbl}</th>'

    # ── Row builder ──────────────────────────────────────────────────────────
    def build_row(label, s, year_data, is_total=False):
        # Top/bottom highlights only across visible years
        valid = [(y, year_data[y]) for y in display_years
                 if y != cy and year_data.get(y) is not None]
        srt  = sorted(valid, key=lambda x: x[1])
        n    = len(srt)
        bot2 = {y for y, _ in srt[:2]}  if n >= 2 else set()
        top2 = {y for y, _ in srt[-2:]} if n >= 2 else set()
        bot2 -= top2

        row = f'<td class="m-cell" style="{sticky_left(W["month"])}">{label}</td>'
        for year in display_years:
            if year == cy:
                continue
            val = year_data.get(year)
            if year in top2:
                row += f'<td style="background:#2e7d32;color:#fff;font-weight:600;">{fn(val)}</td>'
            elif year in bot2:
                row += f'<td style="background:#c62828;color:#fff;font-weight:600;">{fn(val)}</td>'
            else:
                row += f'<td>{fn(val)}</td>'

        pc_ly  = s["pct_vs_ly"]
        pc_oly = s["pct_vs_oly"]
        cy_val = year_data.get(cy)

        # CY cell — estimate styling if past the official cutoff
        is_est = (label in est_months) or (is_total and bool(est_months))
        cy_cls = "est-cy-cell left-divider" if is_est else "cy-cell left-divider"

        # M1 / M2 forecast values — show for non-official months and TOTAL row
        if has_fcst:
            is_nonoff = (label in est_months) or (cy_val is None and not is_total)
            if is_total:
                # Implied full-MY totals: sum all months from model pivots
                m1_val = (sum(v for m in months
                              if (v := model1_pivot[m].get(cy)) is not None)
                          if model1_pivot else None)
                m2_val = (sum(v for m in months
                              if (v := model2_pivot[m].get(cy)) is not None)
                          if model2_pivot else None)
                m1_cls = "m1-cell"
                m2_cls = "m2-cell"
            elif is_nonoff:
                m1_val = model1_pivot[label].get(cy) if model1_pivot else None
                m2_val = model2_pivot[label].get(cy) if model2_pivot else None
                m1_cls = "m1-cell"
                m2_cls = "m2-cell"
            else:
                m1_val = m2_val = None
                m1_cls = m2_cls = "m-dash"

        stat_cols = [
            (cy_r, W["year"], cy_cls,   fn(cy_val),       ""),
            (4,    W["stat"], "s-cell", fn(s["oly_avg"]), ""),
            (3,    W["stat"], "s-cell", fn(s["min"]),     ""),
            (2,    W["stat"], "s-cell", fn(s["max"]),     ""),
            (1,    W["pct"],  "p-cell", fmt_pct(pc_ly),  f"color:{pct_color(pc_ly)};"),
            (0,    W["pct"],  "p-cell", fmt_pct(pc_oly), f"color:{pct_color(pc_oly)};"),
        ]
        if has_fcst:
            stat_cols.insert(1, (m1_r, W["fcst"], m1_cls, fn(m1_val), ""))
            stat_cols.insert(2, (m2_r, W["fcst"], m2_cls, fn(m2_val), ""))

        for r_idx, w, cls, val_str, xtra in stat_cols:
            row += (f'<td class="{cls}" style="{sticky_right(r_idx, w)}{xtra}">'
                    f'{val_str}</td>')

        tag = 'class="total-row"' if is_total else ""
        return f"<tr {tag}>{row}</tr>"

    rows_html = ""
    for month in months:
        rows_html += build_row(month, stats[month],
                               {y: data_pivot[month].get(y) for y in all_years})
    rows_html += build_row("TOTAL", stats["TOTAL"], stats["_totals"], is_total=True)

    return (_TABLE_CSS
            + '<div class="corn-wrap"><table class="corn-tbl">'
            + f'<thead><tr>{hdr}</tr></thead>'
            + f'<tbody>{rows_html}</tbody>'
            + '</table></div>')


# ─────────────────────────────────────────────────────────────────────────────
# CHARTS
# ─────────────────────────────────────────────────────────────────────────────
def _ch_title(title: str, subtitle: str | None = None) -> dict:
    """A left-aligned chart title (with an optional muted second line), pinned 14 px below the top edge."""
    text = f"<b>{title}</b>" + (f"<br><span style='font-size:12px;color:{_CH_MUTED}'>{subtitle}</span>" if subtitle else "")
    return dict(text=text, x=0, xanchor="left", y=1, yanchor="top", pad=dict(t=14), font=dict(size=15, color=_CH_TEXT))


def _ch_layout(title: str | None = None, subtitle: str | None = None, x_title: str | None = None,
               y_title: str | None = None, height: int | None = None, legend: bool = True,
               hover: str = "x unified", margin: dict | None = None) -> dict:
    """Layout for the light COT look: white surface, sage gridlines, muted tick text. Every chart starts from this."""
    lay = dict(
        template="plotly_white", paper_bgcolor=_CH_SURF, plot_bgcolor=_CH_SURF,
        font=dict(family=_CH_FONT, color=_CH_TEXT, size=12), hovermode=hover,
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=_CH_BORDER, font=dict(color=_CH_TEXT, size=12)),
        showlegend=legend,
        legend=dict(bgcolor="rgba(255,255,255,0.92)", bordercolor=_CH_BORDER, borderwidth=1,
                    font=dict(size=11, color=_CH_MUTED)),
        margin=margin or dict(l=70, r=20, t=(88 if subtitle else 64) if title else 24, b=60),
        xaxis=_ch_axis(x_title), yaxis=_ch_axis(y_title),
    )
    if title:
        lay["title"] = _ch_title(title, subtitle)
    if height:
        lay["height"] = height
    return lay


def _base_layout(title_text, x_title, y_title) -> dict:
    lay = _ch_layout(title_text, None, x_title, y_title)
    lay["yaxis"]["tickformat"] = ","
    return lay


def _year_style(year, cy, ly, all_years) -> tuple:
    """(colour, width, opacity) of a marketing-year line: this year blue, last year terracotta, earlier years in sage
    shades that darken toward the present."""
    if year == cy:
        return _CH_BLUE, 4.5, 1.0
    if year == ly:
        return _CH_TERRA, 3.0, 1.0
    other = [y for y in all_years if y != cy and y != ly]
    idx   = other.index(year) if year in other else 0
    t     = idx / max(len(other) - 1, 1)
    return _ch_mix("#cdd9d1", "#5f7267", t), round(1.0 + 0.6 * t, 1), 1.0


def make_seasonal_chart(data_pivot, all_years, cy, complete_years,
                        field_label, is_cumulative, months,
                        logo_b64=None, unit_short="TMT",
                        cy_est_months: set | None = None,
                        model1_pivot: dict | None = None,
                        model2_pivot: dict | None = None,
                        shares: dict | None = None,
                        usda_total: float | None = None,
                        pace_info: dict | None = None) -> go.Figure:
    """Build the seasonal shipments chart.

    cy_est_months : set of month names classified as Estimate in the current MY.
                    When provided the CY trace is split into an Official segment
                    (solid line) and an Estimate segment (dashed, amber tint).
    """
    lbl = "Cumulative " if is_cumulative else ""
    dec = 1 if unit_short == "Mbu" else 0          # decimal places for volumes
    fig = go.Figure()
    ly  = complete_years[-1] if complete_years else None
    est_months = cy_est_months or set()

    hist_8 = sorted(complete_years)[-8:]
    if len(hist_8) >= 2:
        band_upper, band_lower = [], []
        for m in months:
            vals  = [data_pivot[m].get(y) for y in hist_8]
            clean = [v for v in vals if v is not None]
            if len(clean) >= 2:
                mu, sig = float(np.mean(clean)), float(np.std(clean, ddof=1))
                band_upper.append(mu + sig)
                band_lower.append(max(0.0, mu - sig))
            else:
                band_upper.append(None)
                band_lower.append(None)
        fig.add_trace(go.Scatter(
            x=months + months[::-1], y=band_upper + band_lower[::-1],
            fill="toself", fillcolor=_ch_rgba(_CH_SAGE, 0.12),
            line=dict(color="rgba(0,0,0,0)"),
            name=f"±1 Std Dev ({len(hist_8)} yr)",
            hoverinfo="skip", showlegend=True,
            legendrank=900,
        ))

    oly_6 = sorted(complete_years)[-6:]
    if len(oly_6) >= 3:
        oly_vals = [olympic_avg([data_pivot[m].get(y) for y in oly_6]) for m in months]
        oly_hover = [
            f"{v:,.{dec}f} {unit_short}" if v is not None else "—"
            for v in oly_vals
        ]
        fig.add_trace(go.Scatter(
            x=months, y=oly_vals, mode="lines+markers",
            name="6-Yr Olympic Avg",
            line=dict(color=_CH_SAGE, width=2.5, dash="dash"),
            marker=dict(symbol="diamond", size=6, color=_CH_SAGE),
            legendrank=800,
            customdata=oly_hover,
            hovertemplate="%{x}: %{customdata}<extra>6-Yr Olympic Avg</extra>",
        ))

    # Build year draw order (oldest first for rendering so newer lines sit on top)
    draw_order = [y for y in all_years if y != cy and y != ly]
    if ly:
        draw_order.append(ly)
    draw_order.append(cy)

    # Legend rank: CY=1 (top), LY=2, then historical newest→oldest (3, 4, 5…)
    hist_years_newest_first = [y for y in reversed(all_years) if y != cy and y != ly]
    legend_rank = {cy: 1}
    if ly:
        legend_rank[ly] = 2
    for rank, yr in enumerate(hist_years_newest_first, start=3):
        legend_rank[yr] = rank

    for year in draw_order:
        color, width, opacity = _year_style(year, cy, ly, all_years)
        is_key = year in (cy, ly)

        if year == cy and est_months:
            # ── CY: split into Official (solid) + Estimate (dashed) segments ──
            # Find the boundary index: first month in est_months
            boundary = None
            for idx, m in enumerate(months):
                if m in est_months:
                    boundary = idx
                    break

            # Official segment: months[0..boundary] inclusive of the boundary
            # so the line connects cleanly.  We include the boundary month
            # with its value in the official trace (it's the last known point).
            if boundary is not None:
                # Official portion — ends at the last official month
                off_months = months[:boundary]
                off_vals   = [data_pivot[m].get(cy) for m in off_months]
                off_hover  = [f"{v:,.{dec}f} {unit_short}" if v is not None else "—"
                              for v in off_vals]
                if off_months:
                    fig.add_trace(go.Scatter(
                        x=off_months, y=off_vals,
                        mode="lines+markers", name=f"{cy} (Official)",
                        line=dict(color=color, width=width),
                        marker=dict(size=5, color=color),
                        opacity=opacity, connectgaps=False,
                        legendrank=1,
                        customdata=off_hover,
                        hovertemplate="%{x}: %{customdata}<extra>" + f"{cy} Official</extra>",
                    ))

                # Estimate portion — starts one month before boundary to connect
                est_months_seq = months[max(boundary - 1, 0):]
                est_vals = [data_pivot[m].get(cy) for m in est_months_seq]
                est_hover = [f"{v:,.{dec}f} {unit_short}" if v is not None else "—"
                             for v in est_vals]
                fig.add_trace(go.Scatter(
                    x=est_months_seq, y=est_vals,
                    mode="lines+markers", name=f"{cy} (Estimate)",
                    line=dict(color=_CH_EST, width=width, dash="dash"),
                    marker=dict(size=5, color=_CH_EST, symbol="diamond"),
                    opacity=opacity, connectgaps=False,
                    legendrank=2,
                    customdata=est_hover,
                    hovertemplate="%{x}: %{customdata}<extra>" + f"{cy} Estimate</extra>",
                ))

                # Vertical annotation at the boundary (add_shape works with
                # categorical x-axes; add_vline does not)
                fig.add_shape(
                    type="line",
                    x0=months[boundary], x1=months[boundary],
                    y0=0, y1=1,
                    xref="x", yref="paper",
                    line=dict(color=_CH_EST, width=1, dash="dot"),
                )
                fig.add_annotation(
                    x=months[boundary], y=1.02,
                    xref="x", yref="paper",
                    text="Est →",
                    font=dict(color=_CH_EST, size=10),
                    showarrow=False,
                    xanchor="left", yanchor="bottom",
                )
            else:
                # All months are official — draw normal CY line
                vals     = [data_pivot[m].get(cy) for m in months]
                yr_hover = [f"{v:,.{dec}f} {unit_short}" if v is not None else "—"
                            for v in vals]
                fig.add_trace(go.Scatter(
                    x=months, y=vals, mode="lines+markers", name=cy,
                    line=dict(color=color, width=width),
                    marker=dict(size=5, color=color),
                    opacity=opacity, connectgaps=False,
                    legendrank=1,
                    customdata=yr_hover,
                    hovertemplate="%{x}: %{customdata}<extra>%{fullData.name}</extra>",
                ))
        else:
            vals     = [data_pivot[m].get(year) for m in months]
            yr_hover = [f"{v:,.{dec}f} {unit_short}" if v is not None else "—"
                        for v in vals]
            fig.add_trace(go.Scatter(
                x=months, y=vals, mode="lines+markers", name=year,
                line=dict(color=color, width=width),
                marker=dict(size=5 if is_key else 3, color=color),
                opacity=opacity, connectgaps=False,
                legendrank=legend_rank.get(year, 500),
                customdata=yr_hover,
                hovertemplate="%{x}: %{customdata}<extra>%{fullData.name}</extra>",
            ))

    # ── Forecast traces (Model 1 & Model 2) ──────────────────────────────
    pi = pace_info or {}
    fcst_months_set = set(pi.get("forecast_months", []))

    if model1_pivot and fcst_months_set and usda_total:
        dec_ = 1 if unit_short == "Mbu" else 0

        # Find the last OFFICIAL CY data point to connect forecast to actuals.
        # Must exclude estimate months — they also have data but are not the
        # anchor; using them causes Plotly to draw a backwards diagonal line.
        conn_m = conn_v = None
        for m in months:
            if data_pivot[m].get(cy) is not None and m not in est_months:
                conn_m, conn_v = m, data_pivot[m][cy]

        # ±0.5σ forecast uncertainty band — center pulled from budget-constrained
        # model1_pivot so the band is consistent with the M1 line.
        # Sigma uses the coefficient of variation (std/olympic) scaled to center.
        if shares and model1_pivot:
            band_x, band_hi, band_lo = [], [], []
            for m in months:
                if m in fcst_months_set and m in shares:
                    center = model1_pivot[m].get(cy) or 0.0
                    oly    = shares[m]["olympic"]
                    sigma  = (center * shares[m]["std"] / oly * 0.5
                               if oly > 0 else 0.0)
                    band_x.append(m)
                    band_hi.append(center + sigma)
                    band_lo.append(max(0.0, center - sigma))
            if band_x:
                fig.add_trace(go.Scatter(
                    x=band_x + band_x[::-1],
                    y=band_hi + band_lo[::-1],
                    fill="toself", fillcolor=_ch_rgba(_CH_M1, 0.16),
                    line=dict(color="rgba(0,0,0,0)"),
                    name="Forecast ±0.5σ",
                    hoverinfo="skip", showlegend=True, legendrank=950,
                ))

        # Model 1 — USDA Seasonal
        m1_x = ([conn_m] if conn_m else []) + [m for m in months if m in fcst_months_set]
        m1_y = ([conn_v] if conn_m else []) + [
            model1_pivot[m].get(cy) for m in months if m in fcst_months_set
        ]
        m1_hover = [
            f"{v:,.{dec_}f} {unit_short}" if v is not None else "—"
            for v in m1_y
        ]
        if len(m1_x) > 1:
            fig.add_trace(go.Scatter(
                x=m1_x, y=m1_y, mode="lines+markers",
                name="Forecast — USDA Seasonal",
                line=dict(color=_CH_M1, width=2.5, dash="dot"),
                marker=dict(symbol="diamond-open", size=7, color=_CH_M1,
                            line=dict(width=2, color=_CH_M1)),
                connectgaps=True, legendrank=5,
                customdata=m1_hover,
                hovertemplate="%{x}: %{customdata}<extra>USDA Seasonal Fcst</extra>",
            ))

        # Model 2 — Pace-Adjusted (only when YTD actuals exist)
        if model2_pivot and pi.get("has_ytd"):
            m2_x = m1_x
            m2_y = ([conn_v] if conn_m else []) + [
                model2_pivot[m].get(cy) for m in months if m in fcst_months_set
            ]
            m2_hover = [
                f"{v:,.{dec_}f} {unit_short}" if v is not None else "—"
                for v in m2_y
            ]
            adj_pct = (pi.get("adj_factor", 1.0) - 1.0) * 100.0
            adj_lbl = f"{adj_pct:+.1f}% pace adj"
            if len(m2_x) > 1:
                fig.add_trace(go.Scatter(
                    x=m2_x, y=m2_y, mode="lines+markers",
                    name=f"Forecast — Pace Adj ({adj_lbl})",
                    line=dict(color=_CH_M2, width=2.0, dash="dashdot"),
                    marker=dict(symbol="triangle-up-open", size=7, color=_CH_M2,
                                line=dict(width=2, color=_CH_M2)),
                    connectgaps=True, legendrank=6,
                    customdata=m2_hover,
                    hovertemplate="%{x}: %{customdata}<extra>Pace-Adj Fcst</extra>",
                ))

    fig.update_layout(**_base_layout(
        f"Seasonal {lbl}Shipments — {field_label} ({unit_short})",
        "Month", f"{lbl}Volume ({unit_short})",
    ))
    _add_chart_watermark(fig, logo_b64)
    return fig


_BAR_COLORS = _CH_PAL[1:]          # the selected year takes the lead blue; the others follow in palette order


def make_column_chart(data_pivot, stats, selected_years, cy,
                      field_label, is_cumulative, months,
                      logo_b64=None, unit_short="TMT") -> go.Figure:
    lbl = "Cumulative " if is_cumulative else ""
    dec = 1 if unit_short == "Mbu" else 0
    vol_fmt = f",.{dec}f"
    fig = go.Figure()

    mins = [stats[m]["min"] for m in months]
    maxs = [stats[m]["max"] for m in months]
    fig.add_trace(go.Scatter(
        x=months + months[::-1], y=maxs + mins[::-1],
        fill="toself", fillcolor=_ch_rgba(_CH_SAGE, 0.12),
        line=dict(color="rgba(0,0,0,0)"),
        name="Hist. Min–Max Range", hoverinfo="skip",
    ))
    olys = [stats[m]["oly_avg"] for m in months]
    oly_hover = [
        f"{v:,.{dec}f} {unit_short}" if v is not None else "—"
        for v in olys
    ]
    fig.add_trace(go.Scatter(
        x=months, y=olys, mode="lines+markers", name="6-Yr Olympic Avg",
        line=dict(color=_CH_SAGE, width=2.5, dash="dot"),
        marker=dict(symbol="diamond", size=7, color=_CH_SAGE),
        customdata=oly_hover,
        hovertemplate="%{x}: %{customdata}<extra>6-Yr Olympic Avg</extra>",
    ))

    color_idx = 0
    for year in selected_years:
        vals  = [data_pivot[m].get(year) for m in months]
        color = _CH_BLUE if year == cy else _BAR_COLORS[color_idx % len(_BAR_COLORS)]
        if year != cy:
            color_idx += 1
        bar_hover = [
            f"{v:,.{dec}f} {unit_short}" if v is not None else "—"
            for v in vals
        ]
        fig.add_trace(go.Bar(
            x=months, y=vals, name=year,
            marker_color=color, opacity=0.92,
            customdata=bar_hover,
            hovertemplate="%{x}: %{customdata}<extra>%{fullData.name}</extra>",
        ))

    fig.update_layout(
        barmode="group",
        **_base_layout(
            f"{lbl}Monthly Volume Comparison — {field_label} ({unit_short})",
            "Month", f"{lbl}Volume ({unit_short})",
        ),
    )
    _add_chart_watermark(fig, logo_b64)
    return fig


def make_snapshot_chart(snap_data, commodity_label, selected_year_label,
                        unit_short, logo_b64=None) -> go.Figure:
    """
    Single grouped horizontal bar chart.
    For each country:
      • Top bar  — % vs Olympic Avg  (solid, full opacity)
      • Bottom bar — % vs Last Year  (same colour family, 55% opacity)
    Countries sorted highest → lowest by % vs Avg.
    Green = above reference, Red = below.
    """
    valid = [d for d in snap_data
             if d.get("pct_avg") is not None or d.get("pct_ly") is not None]
    if not valid:
        return go.Figure()

    # Sort ascending → highest ends up at the top of the horizontal chart
    sorted_data = sorted(
        valid,
        key=lambda d: (d["pct_avg"] if d.get("pct_avg") is not None else -9999),
    )

    labels   = [d["label"]       for d in sorted_data]
    pct_avgs = [d.get("pct_avg") for d in sorted_data]
    pct_lys  = [d.get("pct_ly")  for d in sorted_data]

    def _colors(vals):
        return [_CH_POS if (v is not None and v >= 0) else _CH_NEG for v in vals]

    n     = len(labels)
    fig_h = max(400, n * 58 + 160)

    all_vals = [v for v in pct_avgs + pct_lys if v is not None]
    if all_vals:
        mx      = max(abs(v) for v in all_vals)
        pad     = mx * 0.30 + 8
        x_range = [-(mx + pad), mx + pad]
    else:
        x_range = [-30, 30]

    fig = go.Figure()

    # Pre-format hover strings in Python (avoids d3 format flag issues)
    hover_avg = [f"{v:+.1f}%" if v is not None else "N/A" for v in pct_avgs]
    hover_ly  = [f"{v:+.1f}%" if v is not None else "N/A" for v in pct_lys]

    # ── Trace 1: % vs Olympic Avg (solid — drawn first so it appears on TOP) ──
    fig.add_trace(go.Bar(
        x=pct_avgs, y=labels,
        orientation="h",
        name="% vs Olympic Avg", showlegend=False,
        marker_color=_colors(pct_avgs),
        marker_line_width=0,
        opacity=1.0,
        text=[f"Avg {v:+.1f}%" if v is not None else "Avg —" for v in pct_avgs],
        textposition="outside",
        cliponaxis=False,
        customdata=hover_avg,
        hovertemplate="%{y}: %{customdata}<extra>% vs Olympic Avg</extra>",
    ))

    # ── Trace 2: % vs Last Year (lighter — appears BELOW in grouped chart) ────
    fig.add_trace(go.Bar(
        x=pct_lys, y=labels,
        orientation="h",
        name="% vs Last Year", showlegend=False,
        marker_color=_colors(pct_lys),
        marker_line_width=0,
        opacity=0.55,
        text=[f"LY  {v:+.1f}%" if v is not None else "LY  —" for v in pct_lys],
        textposition="outside",
        cliponaxis=False,
        customdata=hover_ly,
        hovertemplate="%{y}: %{customdata}<extra>% vs Last Year</extra>",
    ))

    # The bars are green / red by sign, so the legend swatches are neutral (scatter traces: they take no bar slot)
    for nm, op in (("% vs Olympic Avg", 1.0), ("% vs Last Year", 0.45)):
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=nm, hoverinfo="skip",
                                 marker=dict(symbol="square", size=11, color=_CH_SAGE, opacity=op)))

    # Single zero-reference line across the whole chart
    fig.add_vline(x=0, line_color=_CH_MUTED, line_width=1.2)

    fig.update_layout(**_ch_layout(f"{commodity_label} — Cumulative Shipment Snapshot", selected_year_label,
                                   height=fig_h, hover="closest", margin=dict(l=10, r=120, t=88, b=50)))
    fig.update_layout(
        barmode="group", bargap=0.28, bargroupgap=0.06,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                    font=dict(color=_CH_MUTED, size=11), bgcolor="rgba(0,0,0,0)",
                    bordercolor="rgba(0,0,0,0)", itemsizing="constant"),
    )
    fig.update_xaxes(ticksuffix="%", zeroline=False, tickfont=dict(size=10, color=_CH_MUTED), range=x_range)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11, color=_CH_TEXT))

    # Annotation note at bottom explaining opacity difference
    fig.add_annotation(
        text="■ Solid = % vs Olympic Avg     ■ Light = % vs Last Year"
             "     🟢 Above reference     🔴 Below reference",
        xref="paper", yref="paper",
        x=0.0, y=-0.04,
        xanchor="left", yanchor="top",
        showarrow=False,
        font=dict(size=10, color=_CH_MUTED),
    )

    _add_chart_watermark(fig, logo_b64)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# STAT TILES
# ─────────────────────────────────────────────────────────────────────────────
def _aggregate_progress(df, cfg, field, year, use_bushels, unit_factor,
                        us_est_months=frozenset()):
    """Where an Oct–Sep aggregate (Total Non-US / Major Exporters) really stands for `year`.

    The aggregate only counts a month once EVERY component has reported, so one late reporter
    (Argentina's TDM data lags a month) makes the total stop a month early and read like a full
    year. Returns None for a non-aggregate field or a year with no data, else a dict:
        strict_thru / strict_cum   last month all components reported, and the total through it
        thru / cum                 the same plus the months only some components have reported
                                   (summing just the components that have)
        missing                    components absent from those extra months ([] = nothing pending)
        est                        True when a pending month leans on an estimated US month
    """
    comps = {"TotalNonUS": cfg.get("non_us_comps"),
             "MajorExporter": cfg.get("major_comps")}.get(field)
    if not comps or field not in df.columns or any(c not in df.columns for c in comps):
        return None

    def _pv(f):
        pv, _ = build_pivot(df, f)
        return _apply_unit(pv, unit_factor) if use_bushels else pv

    agg = _pv(field)
    comp = {c: _pv(c) for c in comps}
    strict_thru, strict_cum = None, 0.0
    for m in OCT_SEP_MONTHS:
        v = agg[m].get(year)
        if v is not None:
            strict_thru, strict_cum = m, strict_cum + v
    if strict_thru is None:
        return None
    cum, thru, missing, est = strict_cum, strict_thru, [], False
    for m in OCT_SEP_MONTHS[OCT_SEP_MONTHS.index(strict_thru) + 1:]:
        have = {c: comp[c][m].get(year) for c in comps if comp[c][m].get(year) is not None}
        if not have:
            break        # nothing reported for this month, so no later month is pending either
        cum += sum(have.values())
        thru = m
        missing += [c for c in comps if c not in have and c not in missing]
        est = est or ("US" in have and m in us_est_months)
    return dict(strict_thru=strict_thru, strict_cum=strict_cum, thru=thru, cum=cum,
                missing=missing, est=est)


def _ly_context_note(df, cfg, field, ly, commodity, use_bushels, unit_factor,
                     unit_short, unit_decimals) -> str:
    """One line under the USDA forecast of an Oct–Sep aggregate: where last year actually stands, so
    the forecast is read against the right base and a late reporter isn't mistaken for a full year."""
    if not ly or field not in ("TotalNonUS", "MajorExporter"):
        return ""
    try:
        est = _us_fgis_est_months(commodity, ly, OCT_SEP_MONTHS, my_start_month=10)
    except Exception:
        est = set()
    pg = _aggregate_progress(df, cfg, field, ly, use_bushels, unit_factor, us_est_months=est)
    if not pg:
        return ""
    fn = lambda v: fmt_num(v, unit_decimals)
    if pg["strict_thru"] == "Sep":
        base = f"{fn(pg['strict_cum'])} {unit_short} (full year)"
    elif pg["missing"]:
        base = (f"{fn(pg['cum'])} {unit_short} thru {pg['thru']}, excl. "
                f"{', '.join(pg['missing'])} {pg['thru']} (pending)")
    else:
        base = f"{fn(pg['strict_cum'])} {unit_short} thru {pg['strict_thru']}"
    try:
        raw = load_forecast_config(ly).get((commodity, field))
        usda_ly = (float(raw) * unit_factor if use_bushels else float(raw)) if raw else None
    except Exception:
        usda_ly = None
    tail = f" · USDA est. {fn(usda_ly)}" if usda_ly else ""
    return (f'<div style="font-family:Arial;font-size:11px;color:#5f6b76;margin-top:6px;">'
            f'Last year ({ly}): <b style="color:#32373c;">{base}</b>{tail}</div>')


def _compute_tile_stats(df, use_bushels, unit_factor, cfg,
                        arbr_local_my: bool = True,
                        us_local_my: bool = True,
                        commodity: str | None = None) -> list:
    cutoffs = load_cutoff_config()   # cached — no performance cost
    tiles = []
    for field in cfg["tile_order"]:
        # Determine marketing year convention for this field
        if field == "US" and us_local_my:
            months_list = cfg["us_local_months"]
            last_month  = cfg["us_local_last_month"]
            my_label    = f"Local ({cfg['us_local_label']})"
            pivot, all_years = build_arbr_pivot(
                df, field,
                months_list=cfg["us_local_months"],
                prev_months=cfg["us_local_prev_months"],
                year_offset=0,
            )
        elif field in cfg["mar_feb_fields"] and arbr_local_my:
            months_list = cfg["arbr_months"]
            last_month  = cfg["arbr_last_month"]
            my_label    = f"Local ({cfg['arbr_label']})"
            pivot, all_years = build_arbr_pivot(
                df, field,
                months_list=cfg["arbr_months"],
                prev_months=cfg["arbr_prev_months"],
            )
        else:
            months_list = OCT_SEP_MONTHS
            last_month  = "Sep"
            my_label    = "USDA (Oct–Sep)"
            pivot, all_years = build_pivot(df, field)
        if not all_years:
            tiles.append(None)
            continue

        # If the latest MY has no official data yet (just rolled over), use prev MY
        _cy_candidate = all_years[-1]
        _has_data = any(
            pivot[m].get(_cy_candidate) is not None
            for m in months_list
        )
        if not _has_data and len(all_years) >= 2:
            all_years = all_years[:-1]

        cy = all_years[-1]
        ly = all_years[-2] if len(all_years) >= 2 else None
        complete_years = [y for y in get_complete_years(pivot, last_month) if y != cy]
        oly_years      = sorted(complete_years)[-6:]

        if use_bushels:
            pivot = _apply_unit(pivot, unit_factor)

        # Months in the current MY that are estimates — skip these on tiles
        est_months = _cy_estimate_months(field, cutoffs, months_list)

        # US months Census hasn't published yet are filled by an estimate. They stay on the tile
        # (it is the newest read) but are flagged, instead of passing as official.
        us_est = set()
        if field == "US" and commodity:
            try:
                us_est = _us_fgis_est_months(commodity, cy, months_list,
                                             my_start_month=9 if us_local_my else 10)
            except Exception:
                us_est = set()

        latest_month = latest_val = None
        for m in reversed(months_list):
            if m in est_months:          # only show official data on tiles
                continue
            v = pivot[m].get(cy)
            if v is not None:
                latest_month, latest_val = m, v
                break

        if latest_month is None:
            tiles.append(None)
            continue

        ly_m    = pivot[latest_month].get(ly) if ly else None
        oly_m   = olympic_avg([pivot[latest_month].get(y) for y in oly_years])
        cum_piv = build_cumulative_pivot(pivot, all_years, months_list)
        cy_cum  = cum_piv[latest_month].get(cy)
        ly_cum  = cum_piv[latest_month].get(ly) if ly else None
        oly_cum = olympic_avg([cum_piv[latest_month].get(y) for y in oly_years])

        # An aggregate stops at the last month EVERY component has reported. Say what is still
        # pending, so e.g. Oct–Aug doesn't read like a full Oct–Sep year.
        pending = None
        if field in ("TotalNonUS", "MajorExporter") and my_label == "USDA (Oct–Sep)":
            try:
                _agg_est = (_us_fgis_est_months(commodity, cy, OCT_SEP_MONTHS, my_start_month=10)
                            if commodity else set())
                pending = _aggregate_progress(df, cfg, field, cy, use_bushels, unit_factor,
                                              us_est_months=_agg_est)
            except Exception:
                pending = None

        tiles.append(dict(
            field        = field,
            label        = cfg["fields"][field],
            my_label     = my_label,
            cy           = cy,
            latest_month = latest_month,
            accent       = cfg["tile_accents"].get(field, JSA_CYAN),
            is_import    = field in cfg["import_fields"],
            monthly_val  = latest_val,
            pct_ly_m     = _pct(latest_val, ly_m),
            pct_oly_m    = _pct(latest_val, oly_m),
            cum_val      = cy_cum,
            pct_ly_c     = _pct(cy_cum, ly_cum),
            pct_oly_c    = _pct(cy_cum, oly_cum),
            est          = latest_month in us_est,
            pending      = pending,
        ))
    return tiles


def _pct_chip(val, label) -> str:
    if val is None:
        return f'<span style="color:#3d4a58;">{label}&thinsp;—</span>'
    color = "#4caf50" if val >= 0 else "#ef5350"
    sign  = "+" if val >= 0 else ""
    return f'<span style="color:{color};font-weight:700;">{label}&thinsp;{sign}{val:.1f}%</span>'


def _render_tile_grid(tiles, unit_short, unit_decimals) -> str:
    """
    Render stat tiles as unified per-country cards (Monthly + MYTD seamlessly
    stacked inside one card), all cards flush against each other and centred.
    """
    cards_html = ""
    total      = sum(1 for t in tiles if t is not None)
    idx        = 0   # position among non-None tiles

    for t in tiles:
        if t is None:
            continue

        accent   = t.get("accent", JSA_CYAN)
        imp_note = (' <span style="font-size:9px;color:#8a9aaa;font-weight:400;">'
                    '(imports)</span>'
                    if t.get("is_import") else "")

        # rounded corners only on the outer edges of the first/last card
        br_tl = "6px" if idx == 0 else "0"
        br_bl = "6px" if idx == 0 else "0"
        br_tr = "6px" if idx == total - 1 else "0"
        br_br = "6px" if idx == total - 1 else "0"
        border_radius = f"{br_tl} {br_tr} {br_br} {br_bl}"

        # right border only on non-last cards (avoids double border)
        right_border = "" if idx == total - 1 else "border-right:1px solid #2e353d;"

        m_vol = fmt_num(t["monthly_val"], unit_decimals) if t["monthly_val"] is not None else "—"
        c_vol = fmt_num(t["cum_val"],     unit_decimals) if t["cum_val"]     is not None else "—"

        # "2025/26 (USDA (Oct–Sep))" wraps on a narrow card and has nested brackets: show "(Oct–Sep)"
        my_disp = t["my_label"].replace("USDA (Oct–Sep)", "Oct–Sep")

        # EST: the newest month is an estimate, not an official figure (US months Census hasn't published)
        est_tag = ('&nbsp;<span style="background:#7a5800;border:1px dashed #f9a825;padding:0 4px;'
                   'border-radius:3px;color:#ffe082;font-size:8px;font-weight:700;">EST</span>'
                   if t.get("est") else "")

        # Aggregates stop at the last month every component reported: say what is still pending
        pend      = t.get("pending") or {}
        pend_html = ""
        if pend.get("missing"):
            pend_html = (
                f'<div style="margin:7px 2px 0;padding:4px 5px;background:#2d2615;'
                f'border:1px dashed #f9a825;border-radius:4px;font-size:9px;line-height:1.4;'
                f'color:#ffe082;text-align:center;">'
                f'<b>{fmt_num(pend["cum"], unit_decimals)}</b> {unit_short} thru {pend["thru"]}'
                f'<br>ex-{", ".join(pend["missing"])} {pend["thru"]} (pending)'
                f'{"<br>incl. US estimate" if pend.get("est") else ""}</div>'
            )

        card = (
            # ── outer card ──────────────────────────────────────────────────
            f'<div style="flex:1;min-width:140px;background:#21262c;'
            f'border-top:3px solid {accent};'
            f'border-bottom:1px solid #2e353d;border-left:1px solid #2e353d;'
            f'{right_border}'
            f'border-radius:{border_radius};overflow:hidden;">'

            # ── country header ───────────────────────────────────────────────
            f'<div style="text-align:center;font-size:10.5px;font-weight:700;'
            f'color:#ffffff;text-transform:uppercase;letter-spacing:0.7px;'
            f'padding:6px 8px 5px;background:#1a1e22;'
            f'border-bottom:1px solid #2e353d;">'
            f'{t["label"]}{imp_note}</div>'

            # ── monthly section ──────────────────────────────────────────────
            f'<div style="padding:9px 10px 8px;text-align:center;">'
            f'<div style="font-size:8.5px;font-weight:700;color:#5a6878;'
            f'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px;">Monthly</div>'
            f'<div style="color:{JSA_CYAN};font-size:19px;font-weight:700;'
            f'line-height:1.1;font-family:Arial;">{m_vol}{est_tag}</div>'
            f'<div style="font-size:9px;color:#3d4a58;margin:2px 0 5px;">'
            f'{unit_short}&nbsp;·&nbsp;'
            f'<span style="color:#5a6878;">{t["latest_month"]}</span>'
            f'&nbsp;<span style="color:#3d4a58;">{t["cy"]}</span></div>'
            f'<div style="font-size:9.5px;display:flex;gap:6px;justify-content:center;">'
            f'{_pct_chip(t["pct_ly_m"],"LY")}&nbsp;{_pct_chip(t["pct_oly_m"],"Avg")}'
            f'</div></div>'

            # ── divider ──────────────────────────────────────────────────────
            f'<div style="border-top:1px solid #2e353d;margin:0;"></div>'

            # ── MYTD section ─────────────────────────────────────────────────
            f'<div style="padding:9px 10px 8px;text-align:center;">'
            f'<div style="font-size:8.5px;font-weight:700;color:{accent};'
            f'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px;">MYTD</div>'
            f'<div style="color:{JSA_CYAN};font-size:19px;font-weight:700;'
            f'line-height:1.1;font-family:Arial;">{c_vol}{est_tag}</div>'
            f'<div style="font-size:9px;color:#3d4a58;margin:2px 0 5px;">'
            f'{unit_short}&nbsp;·&nbsp;'
            f'<span style="color:#5a6878;">thru&nbsp;{t["latest_month"]}</span>'
            f'&nbsp;<span style="color:#3d4a58;">{t["cy"]}&nbsp;({my_disp})</span></div>'
            f'<div style="font-size:9.5px;display:flex;gap:6px;justify-content:center;">'
            f'{_pct_chip(t["pct_ly_c"],"LY")}&nbsp;{_pct_chip(t["pct_oly_c"],"Avg")}'
            f'</div>{pend_html}</div>'

            f'</div>'  # close card
        )
        cards_html += card
        idx += 1

    return (
        f'<div style="font-family:Arial;margin-bottom:14px;'
        f'display:flex;justify-content:center;">'
        f'<div style="display:flex;flex-wrap:nowrap;width:fit-content;">'
        f'{cards_html}'
        f'</div></div>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# SNAPSHOT DATA HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _compute_snapshot_data(df, cfg, use_bushels, unit_factor,
                            selected_year=None, use_local_my=False) -> list:
    """
    Compute per-country snapshot stats.
    use_local_my=False (default) → USDA Oct–Sep for all countries.
    use_local_my=True            → US uses Sep–Aug, AR/BR use Mar–Feb or Apr–Mar.
    selected_year=None  → CY MYTD (latest reported month per country).
    selected_year=str   → full MY total for that prior year.
    Returns list of {field, label, pct_avg, pct_ly, cum_val, year}.
    """
    result = []
    for field in cfg["tile_order"]:
        try:
            if use_local_my and field == "US":
                pivot, all_years = build_arbr_pivot(
                    df, field,
                    months_list=cfg["us_local_months"],
                    prev_months=cfg["us_local_prev_months"],
                    year_offset=0,
                )
                months_list = cfg["us_local_months"]
                last_month  = cfg["us_local_last_month"]
            elif use_local_my and field in cfg["mar_feb_fields"]:
                pivot, all_years = build_arbr_pivot(
                    df, field,
                    months_list=cfg["arbr_months"],
                    prev_months=cfg["arbr_prev_months"],
                )
                months_list = cfg["arbr_months"]
                last_month  = cfg["arbr_last_month"]
            else:
                pivot, all_years = build_pivot(df, field)
                months_list = OCT_SEP_MONTHS
                last_month  = "Sep"
        except Exception:
            continue
        if not all_years:
            continue
        # If the latest MY just rolled over with no data yet, fall back to prev MY
        _cy_cand = all_years[-1]
        if (not any(pivot[m].get(_cy_cand) is not None for m in months_list)
                and len(all_years) >= 2):
            all_years = all_years[:-1]
        if use_bushels:
            pivot = _apply_unit(pivot, unit_factor)
        cum_piv  = build_cumulative_pivot(pivot, all_years, months_list)
        cy       = all_years[-1]
        ly       = all_years[-2] if len(all_years) >= 2 else None
        complete = [y for y in get_complete_years(pivot, last_month) if y != cy]

        if selected_year is None:
            # Current year: find latest reported cumulative month
            year     = cy
            ref_ly   = ly
            latest_m = None
            for m in reversed(months_list):
                if cum_piv[m].get(cy) is not None:
                    latest_m = m
                    break
            if latest_m is None:
                continue
            cum_val = cum_piv[latest_m].get(cy)
            ly_val  = cum_piv[latest_m].get(ly) if ly else None
            oly_yrs = sorted(complete)[-6:]
            oly_val = olympic_avg([cum_piv[latest_m].get(y) for y in oly_yrs])
        else:
            year = selected_year
            if year not in all_years:
                continue
            yr_idx = all_years.index(year)
            ref_ly = all_years[yr_idx - 1] if yr_idx > 0 else None
            # Full MY total at last month of this MY; fallback to latest available
            cum_val = cum_piv[last_month].get(year)
            if cum_val is None:
                for m in reversed(months_list):
                    v = cum_piv[m].get(year)
                    if v is not None:
                        cum_val = v
                        break
            if cum_val is None:
                continue
            ly_val  = cum_piv[last_month].get(ref_ly) if ref_ly else None
            prior   = [y for y in complete if y != year]
            oly_yrs = sorted(prior)[-6:]
            oly_val = olympic_avg([cum_piv[last_month].get(y) for y in oly_yrs])

        result.append(dict(
            field   = field,
            label   = cfg["fields"][field],
            year    = year,
            cum_val = cum_val,
            pct_avg = _pct(cum_val, oly_val),
            pct_ly  = _pct(cum_val, ly_val),
        ))
    return result


def _compute_wheat_snapshot_data(df, cfg, use_bushels, unit_factor,
                                  nh_compare, sh_compare,
                                  selected_year=None) -> list:
    """
    Per-country wheat snapshot using each country's own MY convention.
    selected_year=None  → newest available data per country (MYTD).
    selected_year=str   → full MY total for that prior year.
    """
    result = []
    for field in cfg["tile_order"]:
        if field not in df.columns:
            continue
        months, prev_months, last_month, _ = _get_wheat_field_my(
            field, cfg, nh_compare, sh_compare
        )
        try:
            pivot, all_years = build_arbr_pivot(
                df, field,
                months_list=months, prev_months=prev_months, year_offset=0,
            )
        except Exception:
            continue
        if not all_years:
            continue
        if use_bushels:
            pivot = _apply_unit(pivot, unit_factor)
        cum_piv  = build_cumulative_pivot(pivot, all_years, months)
        complete = get_complete_years(pivot, last_month)

        if selected_year is None:
            # Newest year with any data (countries report at different times)
            year     = None
            latest_m = None
            for yr in reversed(all_years):
                for m in reversed(months):
                    if cum_piv[m].get(yr) is not None:
                        year, latest_m = yr, m
                        break
                if year:
                    break
            if year is None:
                continue
            yr_idx = all_years.index(year)
            ref_ly = all_years[yr_idx - 1] if yr_idx > 0 else None
            complete_for = [y for y in complete if y != year]
            cum_val = cum_piv[latest_m].get(year)
            ly_val  = cum_piv[latest_m].get(ref_ly) if ref_ly else None
            oly_yrs = sorted(complete_for)[-6:]
            oly_val = olympic_avg([cum_piv[latest_m].get(y) for y in oly_yrs])
        else:
            year = selected_year
            if year not in all_years:
                continue
            yr_idx = all_years.index(year)
            ref_ly = all_years[yr_idx - 1] if yr_idx > 0 else None
            cum_val = cum_piv[last_month].get(year)
            if cum_val is None:
                for m in reversed(months):
                    v = cum_piv[m].get(year)
                    if v is not None:
                        cum_val = v
                        break
            if cum_val is None:
                continue
            ly_val = cum_piv[last_month].get(ref_ly) if ref_ly else None
            complete_for = [y for y in complete if y != year]
            oly_yrs = sorted(complete_for)[-6:]
            oly_val = olympic_avg([cum_piv[last_month].get(y) for y in oly_yrs])

        result.append(dict(
            field   = field,
            label   = cfg["fields"][field],
            year    = year,
            cum_val = cum_val,
            pct_avg = _pct(cum_val, oly_val),
            pct_ly  = _pct(cum_val, ly_val),
        ))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# FORECAST PANEL
# ─────────────────────────────────────────────────────────────────────────────
def _render_forecast_panel(pace_info: dict, unit_short: str,
                            unit_decimals: int, field_label: str) -> None:
    """Render the USDA Forecast pace tracker panel."""
    if not pace_info or not pace_info.get("usda_total"):
        return

    pi       = pace_info
    dec      = unit_decimals
    fn       = lambda v: fmt_num(v, dec) if v is not None else "—"
    has_ytd  = pi.get("has_ytd", False)
    pace_pct = pi.get("pace_pct", 0.0)
    pace_col = "#4caf50" if pace_pct >= 0 else "#ef5350"
    pace_sign= "+" if pace_pct >= 0 else ""
    adj_pct  = (pi.get("adj_factor", 1.0) - 1.0) * 100.0
    _cy_lbl  = pi.get("cy") or "the current marketing year"

    st.markdown("---")
    st.markdown(f"### 📈 Forecast — {field_label}")

    cols = st.columns(5)
    metrics = [
        ("USDA MY Total",          fn(pi["usda_total"]),    unit_short, JSA_CYAN),
        ("YTD Official",
         fn(pi["ytd_actual"]) if has_ytd else "—",
         unit_short if has_ytd else "",                      "#f9a825"),
        ("YTD Seasonal Expected",
         fn(pi["ytd_expected"]) if has_ytd else "—",
         unit_short if has_ytd else "",                      "#78909c"),
        ("Model 1 — USDA Seasonal",fn(pi["model1_total"]), unit_short, "#fdd835"),
        ("Model 2 — Pace Adjusted",
         fn(pi["model2_total"]) if has_ytd else "—",
         unit_short if has_ytd else "Needs 1st official month", "#00e676"),
    ]

    for col, (label, val, unit, accent) in zip(cols, metrics):
        with col:
            st.markdown(f"""
            <div style="background:#1e2124;border-top:3px solid {accent};
                        border-radius:6px;padding:12px 14px;text-align:center;">
              <div style="font-size:11px;color:#8a9aaa;font-family:Arial;
                          margin-bottom:4px;">{label}</div>
              <div style="font-size:20px;font-weight:700;color:#ffffff;
                          font-family:Arial;">{val}</div>
              <div style="font-size:11px;color:{accent};font-family:Arial;
                          margin-top:2px;">{unit}</div>
            </div>
            """, unsafe_allow_html=True)

    # Pace strip
    if has_ytd:
        m1_vs_usda = pi["model1_total"] - pi["usda_total"]
        m2_vs_usda = pi["model2_total"] - pi["usda_total"]
        m1_col = "#4caf50" if m1_vs_usda >= 0 else "#ef5350"
        m2_col = "#4caf50" if m2_vs_usda >= 0 else "#ef5350"
        m1_sign= "+" if m1_vs_usda >= 0 else ""
        m2_sign= "+" if m2_vs_usda >= 0 else ""
        st.markdown(f"""
        <div style="background:{JSA_MID};padding:8px 16px;border-radius:5px;
                    margin-top:10px;font-family:Arial;font-size:12px;
                    display:flex;gap:28px;flex-wrap:wrap;color:#d0d8e0;">
          <span>📊 <b style="color:#fff;">YTD Pace:</b>
            <span style="color:{pace_col};font-weight:700;">
              {pace_sign}{pace_pct:.1f}% vs seasonal baseline</span></span>
          <span>🟡 <b style="color:#fdd835;">Model 1</b> implies
            <span style="color:{m1_col};font-weight:600;">
              {m1_sign}{fn(m1_vs_usda)} {unit_short}</span>
            vs USDA total</span>
          <span>🟢 <b style="color:#00e676;">Model 2</b>
            ({adj_pct:+.1f}% pace adj) implies
            <span style="color:{m2_col};font-weight:600;">
              {m2_sign}{fn(m2_vs_usda)} {unit_short}</span>
            vs USDA total</span>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div style="background:{JSA_MID};padding:8px 16px;border-radius:5px;
                    margin-top:10px;font-family:Arial;font-size:12px;color:#8a9aaa;">
          ℹ️ Pace-Adjusted forecast (Model 2) needs at least one official month of
          {_cy_lbl}. The first official month normally arrives 5–9 weeks after the
          marketing year begins; until then Model 1 is the only forecast.
        </div>
        """, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# MYTD → FINAL REGRESSION SCATTER  (Model 3)
# ─────────────────────────────────────────────────────────────────────────────
def _render_ytd_scatter(
    monthly_pivot: dict,
    complete_years: list,
    cy: str,
    months: list,
    cy_est_months: set,
    field_label: str,
    unit_short: str,
    unit_decimals: int,
    logo_b64: str | None = None,
    accent_color: str = "#0693e3",
    pace_info: dict | None = None,
) -> None:
    """Scatter of historical MYTD vs final MY total with OLS regression.

    For each complete prior year the point (cumulative through the same
    marketing-year month we're currently at, full-year total) is plotted.
    A linear regression line is fitted and the current-year MYTD is projected
    onto it to produce a data-driven 'Model 3' implied full-year forecast.
    """
    # ── YTD anchor: months with official (non-estimate) CY data ──────────
    cy_official = [m for m in months
                   if monthly_pivot[m].get(cy) is not None
                   and m not in cy_est_months]
    if not cy_official:
        return   # no official data yet, nothing to anchor

    cy_ytd   = sum(monthly_pivot[m].get(cy) or 0.0 for m in cy_official)
    ytd_thru = cy_official[-1]   # last official month label for axis / titles

    # ── Historical (ytd, final) pairs ────────────────────────────────────
    points: list[tuple[str, float, float]] = []
    for yr in complete_years:
        ytd_h   = sum((monthly_pivot[m].get(yr) or 0.0) for m in cy_official)
        final_h = sum((monthly_pivot[m].get(yr) or 0.0) for m in months)
        if ytd_h > 0 and final_h > 0:
            points.append((yr, ytd_h, final_h))

    if len(points) < 3:
        return   # too sparse for a meaningful regression

    x_arr = np.array([p[1] for p in points])
    y_arr = np.array([p[2] for p in points])
    n     = len(points)

    # ── OLS: final = slope * ytd + intercept ─────────────────────────────
    slope, intercept = map(float, np.polyfit(x_arr, y_arr, 1))
    y_hat = slope * x_arr + intercept
    ss_res = float(np.sum((y_arr - y_hat) ** 2))
    ss_tot = float(np.sum((y_arr - float(np.mean(y_arr))) ** 2))
    r2     = max(0.0, 1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    projected   = slope * cy_ytd + intercept
    hist_avg    = float(np.mean(y_arr))
    proj_delta  = projected - hist_avg
    proj_col    = "#4caf50" if proj_delta >= 0 else "#ef5350"
    proj_sign   = "+" if proj_delta >= 0 else ""
    fit_label   = "Strong" if r2 >= 0.70 else ("Moderate" if r2 >= 0.40 else "Weak")

    dec = unit_decimals
    fn  = lambda v: fmt_num(v, dec)

    # ── Build figure ──────────────────────────────────────────────────────
    fig = go.Figure()

    # Historical dots — gradient from faded (oldest) to bright (newest)
    yr_sorted = sorted(yr for yr, *_ in points)
    for yr, ytd_h, final_h in points:
        idx      = yr_sorted.index(yr)
        t        = idx / max(n - 1, 1)   # 0 = oldest, 1 = newest
        dot_col  = _ch_mix(_CH_MIST, "#5f7267", t)
        fig.add_trace(go.Scatter(
            x=[ytd_h], y=[final_h],
            mode="markers+text",
            marker=dict(size=9, color=dot_col,
                        line=dict(color="#ffffff", width=1)),
            text=[yr], textposition="top center",
            textfont=dict(size=9, color=_CH_MUTED),
            hovertemplate=(
                f"<b>{yr}</b><br>"
                f"MYTD thru {ytd_thru}: <b>{fn(ytd_h)}</b> {unit_short}<br>"
                f"Final MY: <b>{fn(final_h)}</b> {unit_short}"
                "<extra></extra>"
            ),
            showlegend=False,
        ))

    # OLS regression line
    x_lo = max(0.0, float(np.min(x_arr)) * 0.85)
    x_hi = max(float(np.max(x_arr)), cy_ytd) * 1.10
    fig.add_trace(go.Scatter(
        x=[x_lo, x_hi],
        y=[slope * x_lo + intercept, slope * x_hi + intercept],
        mode="lines",
        name=f"OLS Fit  (R²={r2:.2f})",
        line=dict(color="#8fa398", width=1.5, dash="dot"),
        hoverinfo="skip",
        showlegend=True,
        legendrank=800,
    ))

    # CY MYTD vertical marker
    fig.add_shape(
        type="line",
        x0=cy_ytd, x1=cy_ytd, y0=0, y1=1, yref="paper",
        line=dict(color=accent_color, width=1.5, dash="dash"),
    )
    fig.add_annotation(
        x=cy_ytd, y=0.98, yref="paper",
        text=f"CY MYTD<br>{fn(cy_ytd)} {unit_short}",
        showarrow=False,
        font=dict(size=9, color=accent_color),
        xanchor="left", yanchor="top",
        bgcolor="rgba(255,255,255,0.88)",
    )

    # M3 projected final horizontal line — same accent color as MYTD vertical
    fig.add_shape(
        type="line",
        x0=0, x1=1, xref="paper",
        y0=projected, y1=projected,
        line=dict(color=accent_color, width=1.5, dash="dash"),
    )
    fig.add_annotation(
        x=0.0, xref="paper",
        y=projected,
        text=f"<b>M3 Projection</b> {fn(projected)}",
        showarrow=False,
        font=dict(size=9, color=accent_color),
        xanchor="left", yanchor="bottom",
        bgcolor="rgba(255,255,255,0.88)",
    )

    # Projected CY star on regression line
    fig.add_trace(go.Scatter(
        x=[cy_ytd], y=[projected],
        mode="markers",
        marker=dict(size=14, symbol="star",
                    color=accent_color,
                    line=dict(color=_CH_TEXT, width=1)),
        name=f"M3 Projection: {fn(projected)} {unit_short}",
        hovertemplate=(
            "<b>CY Model 3 Projection</b><br>"
            f"MYTD thru {ytd_thru}: {fn(cy_ytd)} {unit_short}<br>"
            f"Implied Final MY: <b>{fn(projected)}</b> {unit_short}<br>"
            f"R² = {r2:.3f}"
            "<extra></extra>"
        ),
        showlegend=True,
        legendrank=700,
    ))

    # ── Horizontal reference lines from Model 1 / Model 2 / USDA ─────────
    pi = pace_info or {}
    _ref_lines = []
    if pi.get("usda_total"):
        _ref_lines.append((pi["usda_total"],  "USDA Total",  _CH_NEG, "dash"))
    if pi.get("model1_total"):
        _ref_lines.append((pi["model1_total"], "M1 Seasonal", _CH_M1, "dot"))
    if pi.get("has_ytd") and pi.get("model2_total"):
        _ref_lines.append((pi["model2_total"], "M2 Pace Adj", _CH_M2, "dashdot"))

    for ref_val, ref_lbl, ref_col, ref_dash in _ref_lines:
        fig.add_shape(
            type="line",
            x0=0, x1=1, xref="paper",
            y0=ref_val, y1=ref_val,
            line=dict(color=ref_col, width=1.2, dash=ref_dash),
        )
        fig.add_annotation(
            x=1.0, xref="paper",
            y=ref_val,
            text=f"<b>{ref_lbl}</b> {fn(ref_val)}",
            showarrow=False,
            font=dict(size=9, color=ref_col),
            xanchor="right", yanchor="bottom",
            bgcolor="rgba(255,255,255,0.88)",
        )

    layout = _base_layout(
        f"MYTD through {ytd_thru} vs Final MY Total — {field_label}",
        f"MYTD through {ytd_thru}  ({unit_short})",
        f"Final MY Total  ({unit_short})",
    )
    fig.update_layout(**layout)
    _add_chart_watermark(fig, logo_b64)

    # ── Tiles ─────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown(f"### 📉 Model 3 — MYTD Regression Forecast  ·  {field_label}")

    c1, c2, c3, _ = st.columns([1, 1, 1, 1])
    with c1:
        st.markdown(
            f'<div style="background:#1e2124;border-top:3px solid {accent_color};'
            f'border-radius:6px;padding:12px 14px;text-align:center;">'
            f'<div style="font-size:11px;color:#8a9aaa;font-family:Arial;'
            f'margin-bottom:4px;">Model 3 — MYTD Regression</div>'
            f'<div style="font-size:20px;font-weight:700;color:#fff;'
            f'font-family:Arial;">{fn(projected)}</div>'
            f'<div style="font-size:11px;color:{accent_color};font-family:Arial;'
            f'margin-top:2px;">{unit_short}</div></div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div style="background:#1e2124;border-top:3px solid #78909c;'
            f'border-radius:6px;padding:12px 14px;text-align:center;">'
            f'<div style="font-size:11px;color:#8a9aaa;font-family:Arial;'
            f'margin-bottom:4px;">Fit Quality (R²)</div>'
            f'<div style="font-size:20px;font-weight:700;color:#fff;'
            f'font-family:Arial;">{r2:.3f}</div>'
            f'<div style="font-size:11px;color:#78909c;font-family:Arial;'
            f'margin-top:2px;">{fit_label}</div></div>',
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f'<div style="background:#1e2124;border-top:3px solid #f9a825;'
            f'border-radius:6px;padding:12px 14px;text-align:center;">'
            f'<div style="font-size:11px;color:#8a9aaa;font-family:Arial;'
            f'margin-bottom:4px;">MYTD Thru / Sample</div>'
            f'<div style="font-size:20px;font-weight:700;color:#fff;'
            f'font-family:Arial;">{ytd_thru}</div>'
            f'<div style="font-size:11px;color:#f9a825;font-family:Arial;'
            f'margin-top:2px;">{n} prior years</div></div>',
            unsafe_allow_html=True,
        )

    st.plotly_chart(fig, use_container_width=True)

    # Context note
    st.markdown(
        f'<div style="font-family:Arial;font-size:11px;color:#5a6878;'
        f'padding:4px 0 12px 2px;">'
        f'Each dot = one prior marketing year positioned at its cumulative '
        f'shipments through <b style="color:#8a9aaa;">{ytd_thru}</b> (x-axis) '
        f'vs its full MY total (y-axis). The OLS line projects where the '
        f'current year\'s MYTD of <b style="color:{accent_color};">'
        f'{fn(cy_ytd)} {unit_short}</b> implies a final total of '
        f'<b style="color:{accent_color};">{fn(projected)} {unit_short}</b> '
        f'(vs historical avg final of {fn(hist_avg)} {unit_short}).</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# COMMODITY TAB RENDERER
# ─────────────────────────────────────────────────────────────────────────────
def _run_commodity_tab(commodity: str, use_bushels: bool,
                       unit_short: str, unit_decimals: int, unit_long: str,
                       logo_white_b64: str | None):
    """Render the full dashboard for one commodity inside its top-level tab."""
    cfg = COMMODITY_CONFIG[commodity]
    pfx = commodity   # namespace all widget keys

    # Per-commodity unit factor — meal has no bushel equivalent so always TMT
    supports_bushels = bool(cfg.get("bu_lbs"))
    if not supports_bushels:
        use_bushels   = False
        unit_short    = "TMT"
        unit_long     = "Thousand Metric Tons (TMT)"
        unit_decimals = 0
    unit_factor = _bu_conv_factor(cfg) if use_bushels else 1.0

    # ── Marketing-year convention toggles ─────────────────────────────────
    tog_col1, tog_col2 = st.columns(2)
    with tog_col1:
        arbr_local_my: bool = st.toggle(
            f"🌎  AR/BR: Local MY ({cfg['arbr_label']})",
            value=True,
            key=f"{pfx}_arbr_local_my",
            help=(
                f"**ON** (default) — Argentina & Brazil use their native "
                f"**{cfg['arbr_label']}** marketing year for {cfg['label']}.\n\n"
                "**OFF** — Aligns them with the USDA **Oct–Sep** convention "
                "for apples-to-apples cross-country comparisons."
            ),
        )
    with tog_col2:
        us_local_my: bool = st.toggle(
            "🇺🇸  US: Local MY (Sep–Aug)",
            value=True,
            key=f"{pfx}_us_local_my",
            help=(
                "**ON** (default) — United States uses its native **Sep–Aug** "
                "marketing year.\n\n"
                "**OFF** — Aligns the US with the USDA **Oct–Sep** convention "
                "for apples-to-apples cross-country comparisons."
            ),
        )

    # ── Load data ─────────────────────────────────────────────────────────
    try:
        df = load_data(commodity)
    except Exception as exc:
        st.error(f"Error loading {cfg['label']} data: {exc}\n\nClick **🔄 Refresh Data** to retry.")
        return

    FIELDS         = cfg["fields"]
    MAR_FEB_FIELDS = cfg["mar_feb_fields"]

    # ── Stat tiles ────────────────────────────────────────────────────────
    # One year for every tile by default. On each market's own year the row mixes years (the US
    # already on 2026/27 while Brazil and Argentina are still on 2025/26), which reads as a
    # mismatch next to Total Non-US and Major Exporters, which are always Oct–Sep.
    _tile_basis = st.radio(
        "Tiles",
        ["USDA Oct–Sep — one year for every tile", "Each market's own marketing year"],
        index=0, horizontal=True, key=f"{pfx}_tile_basis",
        help=(
            "**USDA Oct–Sep** (default) puts every tile on the same year, so they compare with each "
            "other and with Total Non-US and Major Exporters, which are always Oct–Sep.\n\n"
            "**Each market's own year** follows the two toggles above (US Sep–Aug, Brazil/Argentina "
            f"{cfg['arbr_label']}), so the tiles can sit on different years."
        ),
    )
    _tiles_common = _tile_basis.startswith("USDA")
    tile_stats = _compute_tile_stats(df, use_bushels, unit_factor, cfg,
                                     arbr_local_my=arbr_local_my and not _tiles_common,
                                     us_local_my=us_local_my and not _tiles_common,
                                     commodity=commodity)
    st.markdown(_render_tile_grid(tile_stats, unit_short, unit_decimals),
                unsafe_allow_html=True)
    st.markdown(
        '<div style="border-top:1px solid #2e353d;margin:4px 0 16px;"></div>',
        unsafe_allow_html=True,
    )

    # ── Snapshot Chart ────────────────────────────────────────────────────
    st.markdown("### 📸 Export Snapshot — All Countries")

    # Build year list from USDA MY on first available tile field
    _snap_ref_field = cfg["tile_order"][0]
    try:
        _snap_ref_pivot, _snap_all_years = build_pivot(df, _snap_ref_field)
    except Exception:
        _snap_ref_pivot, _snap_all_years = {}, []

    # Right after a rollover the newest MY is an empty shell (2026/27 in October): the current
    # year is the latest one that has data, and only EARLIER years are offered as prior years.
    _snap_filled = [y for y in _snap_all_years
                    if any(_snap_ref_pivot[m].get(y) is not None for m in OCT_SEP_MONTHS)]
    _snap_cy     = (_snap_filled[-1] if _snap_filled
                    else (_snap_all_years[-1] if _snap_all_years else None))
    _snap_prior  = [y for y in reversed(_snap_all_years) if _snap_cy and y < _snap_cy]
    _snap_opts   = [f"Current Year YTD ({_snap_cy})"] + _snap_prior if _snap_cy else []

    if _snap_opts:
        _snap_ctrl1, _snap_ctrl2 = st.columns([3, 1])
        with _snap_ctrl1:
            _snap_sel = st.selectbox(
                "Marketing Year",
                options=_snap_opts,
                index=0,
                key=f"{pfx}_snap_year",
                help=(
                    "**Current Year YTD** — cumulative shipments through the most "
                    "recent reported month for each country.\n\n"
                    "**Prior years** — full marketing year total."
                ),
            )
        with _snap_ctrl2:
            _snap_local_my = st.toggle(
                "Local MY",
                value=False,
                key=f"{pfx}_snap_local_my",
                help=(
                    "**OFF** (default) — USDA Oct–Sep for all countries, so every bar is the same "
                    "year and they compare with Total Non-US and Major Exporters.\n\n"
                    "**ON** — each market's own year: US uses Sep–Aug; "
                    f"AR/BR use {cfg['arbr_label']} (local harvest MY). The bars then sit on different years."
                ),
            )

        _is_cy_snap = _snap_sel.startswith("Current Year")
        _sel_yr     = None if _is_cy_snap else _snap_sel
        _my_tag     = f"Local MY" if _snap_local_my else "USDA Oct–Sep"

        _snap_data = _compute_snapshot_data(df, cfg, use_bushels, unit_factor,
                                            selected_year=_sel_yr,
                                            use_local_my=_snap_local_my)
        # Title from the years actually drawn: on local years the bars can sit on different ones
        _snap_yrs   = sorted({d["year"] for d in _snap_data})
        if not _is_cy_snap:
            _snap_label = f"Full MY {_snap_sel} ({_my_tag})"
        elif len(_snap_yrs) == 1:
            _snap_label = f"CY {_snap_yrs[0]} (YTD, {_my_tag})"
        else:
            _snap_label = f"Latest year per market (YTD, {_my_tag})"
        if _snap_data:
            st.plotly_chart(
                make_snapshot_chart(_snap_data, cfg["label"], _snap_label,
                                    unit_short, logo_white_b64),
                use_container_width=True,
            )
        else:
            st.info("No snapshot data available for the selected year.")

    st.markdown("---")

    # ── Country / Category filter ─────────────────────────────────────────
    st.markdown("#### Select Country or Category")
    field_key = f"{pfx}_field"
    if field_key not in st.session_state:
        st.session_state[field_key] = "US"

    filter_cols = st.columns(len(FIELDS))
    for i, (fk, fl) in enumerate(FIELDS.items()):
        with filter_cols[i]:
            btn_type = "primary" if st.session_state[field_key] == fk else "secondary"
            if st.button(fl, key=f"{pfx}_btn_{fk}", type=btn_type,
                         use_container_width=True):
                st.session_state[field_key] = fk
                st.rerun()

    field       = st.session_state[field_key]
    field_label = FIELDS[field]
    st.markdown(_view_title_html(field_label, cfg["label"], field in cfg["import_fields"], unit_short),
                unsafe_allow_html=True)

    # Determine marketing year convention for the selected field
    if field == "US" and us_local_my:
        months     = cfg["us_local_months"]
        last_month = cfg["us_local_last_month"]
        my_label   = f"Local ({cfg['us_local_label']})"
    elif field in MAR_FEB_FIELDS and arbr_local_my:
        months     = cfg["arbr_months"]
        last_month = cfg["arbr_last_month"]
        my_label   = f"Local ({cfg['arbr_label']})"
    else:
        months     = OCT_SEP_MONTHS
        last_month = "Sep"
        my_label   = "USDA (Oct–Sep)"

    # ── Build pivots ──────────────────────────────────────────────────────
    if field == "US" and us_local_my:
        monthly_pivot, all_years = build_arbr_pivot(
            df, field,
            months_list=cfg["us_local_months"],
            prev_months=cfg["us_local_prev_months"],
            year_offset=0,
        )
    elif field in MAR_FEB_FIELDS and arbr_local_my:
        monthly_pivot, all_years = build_arbr_pivot(
            df, field,
            months_list=cfg["arbr_months"],
            prev_months=cfg["arbr_prev_months"],
        )
    else:
        monthly_pivot, all_years = build_pivot(df, field)

    if not all_years:
        st.warning("No valid marketing-year data found.")
        return

    cy = all_years[-1]
    ly = all_years[-2] if len(all_years) >= 2 else None

    complete_years = get_complete_years(monthly_pivot, last_month)
    complete_years = [y for y in complete_years if y != cy]
    oly_years  = sorted(complete_years)[-6:]
    oly_label  = " → ".join(oly_years) if oly_years else "N/A"

    if use_bushels:
        monthly_pivot = _apply_unit(monthly_pivot, unit_factor)

    cum_pivot     = build_cumulative_pivot(monthly_pivot, all_years, months)
    monthly_stats = compute_stats(monthly_pivot, all_years, complete_years,
                                  cy, ly, months, is_cumulative=False)
    cum_stats     = compute_stats(cum_pivot, all_years, complete_years,
                                  cy, ly, months, is_cumulative=True)

    # ── Official / Estimate classification ───────────────────────────────
    cutoffs       = load_cutoff_config()
    cy_est_months = _cy_estimate_months(field, cutoffs, months)

    # US field: months filled by FGIS (not yet in Census GATS) are estimates.
    # These are indicative only — Census is the authoritative source.
    # my_start_month: corn/soy/meal = 10 (Oct), wheat = 7 (Jul)
    if field == "US":
        _my_sm = 7 if commodity == "wheat" else 10
        cy_est_months = cy_est_months | _us_fgis_est_months(
            commodity, cy, months, my_start_month=_my_sm
        )

    has_estimates = bool(cy_est_months)

    # ── Forecast seasonal shares (computed from history, no input needed) ───
    # Use only the most recent 5 complete years so share distributions reflect
    # current competitive dynamics rather than older export patterns.
    forecast_cfg    = load_forecast_config(cy)
    st.caption(f"📋 USDA export forecasts for {cy}: **{_wasde_source_label(cy)}**")
    _share_years    = sorted(complete_years)[-5:] if len(complete_years) >= 5 else complete_years
    shares          = _compute_seasonal_shares(monthly_pivot, _share_years, months)

    # Local-MY detection for this field:
    #   Brazil/Argentina toggle ON  → read "Brazil_Local"/"Argentina_Local" row from Excel
    #                                 and use a separate session-state key (_local_)
    #   All other fields            → read the standard row, standard key
    # US uses a single key (Sep-Aug ≈ Oct-Sep difference is minimal).
    _is_local_field = bool((field in MAR_FEB_FIELDS and arbr_local_my)
                           or (field == "US" and us_local_my))
    _fc_lookup      = f"{field}_Local" if _is_local_field else field
    _usda_saved     = forecast_cfg.get((commodity, _fc_lookup))   # Excel default, may be None

    # ── Info strip ────────────────────────────────────────────────────────
    is_import_field = field in cfg["import_fields"]
    flow_type = "Import" if is_import_field else "Export"
    st.markdown(f"""
    <div style="background:{JSA_MID};padding:9px 18px;border-radius:6px;
                margin:10px 0 6px 0;font-family:Arial;font-size:13px;
                display:flex;gap:28px;flex-wrap:wrap;color:#d0d8e0;
                border-left:3px solid {JSA_GREEN};">
        <span>📊 <b style="color:#fff;">Showing:</b> {field_label}
          {"&nbsp;<span style='color:#e57373;font-size:11px;'>(import data)</span>"
           if is_import_field else ""}</span>
        <span>📐 <b style="color:#fff;">Units:</b>
              <span style="color:{JSA_CYAN};font-weight:700;">{unit_long}</span></span>
        <span>📅 <b style="color:#fff;">Marketing Year:</b> {my_label}</span>
        <span>📅 <b style="color:#fff;">Current Year (CY):</b>
              <span style="color:{JSA_CYAN};font-weight:700;">{cy}</span></span>
        <span>📅 <b style="color:#fff;">Last Year (LY):</b> {ly or "N/A"}</span>
        <span>📈 <b style="color:#fff;">Olympic Avg (prior yrs):</b> {oly_label}</span>
    </div>
    """, unsafe_allow_html=True)

    # ── Legend ────────────────────────────────────────────────────────────
    # Use inline styles for EST badge — CSS class (.est-badge) lives inside
    # _TABLE_CSS which is injected with the table, not available yet here.
    _est_badge_style = (
        "background:#7a5800;border:1px dashed #f9a825;padding:2px 8px;"
        "border-radius:3px;color:#ffe082;font-weight:600;"
    )
    _est_how = (" — USDA export shipments scaled to Census"
                if field == "US" and commodity in _US_ESR_CODE else "")
    _est_legend_item = (
        f'<span><span style="{_est_badge_style}">EST</span>'
        f'&nbsp;Estimate (not yet official{_est_how})</span>'
        if has_estimates else ""
    )
    st.markdown(
        f'<div style="font-family:Arial;font-size:12px;color:#aab4c0;'
        f'margin:6px 0 14px 0;display:flex;gap:20px;flex-wrap:wrap;'
        f'padding:7px 14px;background:#252a2f;border-radius:5px;">'
        f'<span><span style="background:{JSA_CYAN};padding:2px 8px;border-radius:3px;'
        f'color:#fff;font-weight:600;">CY</span>&nbsp;Current Year ({cy})</span>'
        f'{_est_legend_item}'
        f'<span><span style="background:#2e7d32;padding:2px 8px;border-radius:3px;'
        f'color:#fff;">&#9632;</span>&nbsp;2 Highest (prior yrs)</span>'
        f'<span><span style="background:#c62828;padding:2px 8px;border-radius:3px;'
        f'color:#fff;">&#9632;</span>&nbsp;2 Lowest (prior yrs)</span>'
        f'<span><span style="background:#f4f6f8;padding:2px 8px;border-radius:3px;'
        f'color:#000;">&#9632;</span>&nbsp;Stat Columns (prior yrs only)</span>'
        f'<span style="color:#4caf50;font-weight:600;">+x.x%</span>'
        f'&nbsp;Above reference&nbsp;'
        f'<span style="color:#ef5350;font-weight:600;">-x.x%</span>'
        f'&nbsp;Below reference</div>',
        unsafe_allow_html=True,
    )

    # ── USDA Forecast input ───────────────────────────────────────────────
    # Soybeans / Meal: all components share USDA Oct-Sep → safe to auto-derive
    #   the aggregate total by summing component country inputs.
    # Corn: each country uses a different marketing year (US Sep-Aug, Ukraine
    #   Oct-Sep, AR/BR Mar-Feb) → summing USDA totals is invalid.  Instead,
    #   TotalNonUS and MajorExporter get a direct manual entry field so the user
    #   can type in the USDA WASDE published aggregate (which USDA aligns to
    #   Oct-Sep in its supply/demand tables).
    # Wheat: aggregate forecast disabled (see wheat tab).

    # Soybeans/meal: all components share Oct-Sep → auto-derive aggregate total
    # by summing individual country inputs.
    # Corn: TotalNonUS/MajorExporter values are already aggregated correctly in
    # the spreadsheet (Oct-Sep), so M1/M2 just uses a plain manual USDA input —
    # same as any individual-country field.
    _SOY_AGGREGATE_COMPS = (
        {
            "TotalNonUS":    cfg.get("non_us_comps", []),
            "MajorExporter": cfg.get("major_comps",  []),
        }
        if commodity in ("soybeans", "soybeanmeal")
        else {}
    )
    _model_legend_html = (
        f'<div style="padding:10px 0;font-family:Arial;font-size:12px;color:{_CH_MUTED};">'
        f'<b style="color:{_CH_M1};">●</b> Dotted gold line = <b>USDA Seasonal</b>'
        f' — distributes total via historical monthly share %<br>'
        f'<b style="color:{_CH_M2};">●</b> Green dash-dot line = <b>Pace-Adjusted</b>'
        f' — shifts forecast based on YTD pace vs seasonal baseline'
        f' (activates once official data exists)<br>'
        f'<b style="color:{_CH_M1}; opacity:0.5;">▓</b> Shaded band ='
        f' <b>±0.5σ likely range</b> from historical variance in seasonal shares'
        f'</div>'
    )

    # ── WASDE forecast total — from live API or static config ────────────
    if field in _SOY_AGGREGATE_COMPS:
        derived_total = sum(
            (float(forecast_cfg.get((commodity, c)) or 0) * unit_factor if use_bushels
             else float(forecast_cfg.get((commodity, c)) or 0))
            for c in _SOY_AGGREGATE_COMPS[field]
            if forecast_cfg.get((commodity, c))
        )
        usda_total = derived_total if derived_total > 0 else None
    else:
        _raw = forecast_cfg.get((commodity, _fc_lookup)) or forecast_cfg.get((commodity, field))
        usda_total = (float(_raw) * unit_factor if use_bushels else float(_raw)) if _raw else None

    with st.expander(f"📈  USDA MY Forecast — {field_label}", expanded=bool(usda_total)):
        _fc1, _fc2 = st.columns([2, 3])
        with _fc1:
            if usda_total:
                _src_lbl = _wasde_source_label(cy)
                st.markdown(
                    f'<div style="font-family:Arial;font-size:13px;">'
                    f'<span style="color:#8a9aaa;">WASDE {cy} MY Total ({unit_short})</span><br>'
                    f'<span style="font-size:24px;font-weight:700;color:#1e2124;">'
                    f'{usda_total:,.0f}</span><br>'
                    f'<span style="color:#555;font-size:11px;">{_src_lbl}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption("No WASDE forecast available for this field.")
            # Where last year actually stands, so the forecast is read against the right base
            _ly_note = _ly_context_note(df, cfg, field, ly, commodity, use_bushels, unit_factor,
                                        unit_short, unit_decimals)
            if _ly_note:
                st.markdown(_ly_note, unsafe_allow_html=True)
        with _fc2:
            st.markdown(_model_legend_html, unsafe_allow_html=True)

    # Build forecast pivots from the live UI value
    model1_pivot, model2_pivot, pace_info = _build_forecast_pivots(
        monthly_pivot, all_years, cy, months, shares, usda_total, cy_est_months
    )

    # ── Monthly / Cumulative tabs ─────────────────────────────────────────
    tab1, tab2 = st.tabs(["📊  Monthly Shipments", "📈  Cumulative Shipments"])

    # Build cumulative forecast pivots
    cum_model1 = (build_cumulative_pivot(model1_pivot, all_years, months)
                  if model1_pivot else None)
    cum_model2 = (build_cumulative_pivot(model2_pivot, all_years, months)
                  if model2_pivot else None)

    with tab1:
        st.markdown(
            f"**Monthly — {field_label}** &nbsp;({unit_short}) &nbsp;|&nbsp; "
            f"{my_label} marketing year &nbsp;|&nbsp; "
            f"Stats reflect prior marketing years only.",
            unsafe_allow_html=True,
        )
        _show_table(
            monthly_pivot,
            render_table_html(monthly_pivot, monthly_stats, all_years,
                              cy, ly, months, decimals=unit_decimals,
                              cy_est_months=cy_est_months,
                              model1_pivot=model1_pivot,
                              model2_pivot=model2_pivot),
            f"{commodity}_{field_label}_monthly",
        )
        st.plotly_chart(
            make_seasonal_chart(monthly_pivot, all_years, cy, complete_years,
                                field_label, False, months,
                                logo_white_b64, unit_short=unit_short,
                                cy_est_months=cy_est_months,
                                model1_pivot=model1_pivot,
                                model2_pivot=model2_pivot,
                                shares=shares,
                                usda_total=usda_total,
                                pace_info=pace_info),
            use_container_width=True,
        )

    with tab2:
        st.markdown(
            f"**Cumulative — {field_label}** &nbsp;({unit_short}) &nbsp;|&nbsp; "
            f"{my_label} marketing year &nbsp;|&nbsp; "
            f"Stats reflect prior marketing years only.",
            unsafe_allow_html=True,
        )
        _show_table(
            cum_pivot,
            render_table_html(cum_pivot, cum_stats, all_years,
                              cy, ly, months, decimals=unit_decimals,
                              cy_est_months=cy_est_months,
                              model1_pivot=cum_model1,
                              model2_pivot=cum_model2),
            f"{commodity}_{field_label}_cumulative",
        )
        st.plotly_chart(
            make_seasonal_chart(cum_pivot, all_years, cy, complete_years,
                                field_label, True, months,
                                logo_white_b64, unit_short=unit_short,
                                cy_est_months=cy_est_months,
                                model1_pivot=cum_model1,
                                model2_pivot=cum_model2,
                                shares=None,          # no σ band on cumulative
                                usda_total=usda_total,
                                pace_info=pace_info),
            use_container_width=True,
        )

    # ── Forecast Panel ────────────────────────────────────────────────────
    _render_forecast_panel(pace_info, unit_short, unit_decimals, field_label)

    # ── Model 3 — MYTD Regression Scatter ────────────────────────────────
    _render_ytd_scatter(
        monthly_pivot, complete_years, cy, months, cy_est_months,
        field_label, unit_short, unit_decimals, logo_white_b64,
        accent_color=cfg["country_colors"].get(field, _CH_BLUE),
        pace_info=pace_info,
    )

    # ── Volume Comparison Column Chart ────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📊 Volume Comparison by Month")

    COUNTRY_COLORS = cfg["country_colors"]
    ctrl_ctry, ctrl_yr, ctrl_type = st.columns([2, 3, 1])

    with ctrl_ctry:
        cmp_countries = st.multiselect(
            "Countries / categories",
            options=list(FIELDS.keys()),
            default=[field],
            max_selections=2,
            format_func=lambda k: FIELDS[k],
            key=f"{pfx}_cmp_countries",
        )
    with ctrl_type:
        cmp_mode    = st.radio("Data type", ["Monthly", "Cumulative"],
                               key=f"{pfx}_cmp_mode", horizontal=False)
    use_cum_cmp = cmp_mode == "Cumulative"
    lbl_cum     = "Cumulative " if use_cum_cmp else ""

    multi_country = len(set(cmp_countries)) > 1

    # TWO-COUNTRY MODE
    if multi_country:
        with ctrl_yr:
            st.caption(
                "ℹ️ Two-country comparison uses **USDA (Oct–Sep)** marketing year "
                "for both to keep things consistent."
            )
            _, oct_sep_years = build_pivot(df, "US")
            cmp_single_year  = st.selectbox(
                "Marketing year", options=oct_sep_years[::-1],
                key=f"{pfx}_cmp_single_year",
            )

        fig = go.Figure()
        for fk in cmp_countries:
            p, _ = build_pivot(df, fk)
            if use_bushels:
                p = _apply_unit(p, unit_factor)
            if use_cum_cmp:
                p = build_cumulative_pivot(p, oct_sep_years, OCT_SEP_MONTHS)
            vals = [p[m].get(cmp_single_year) for m in OCT_SEP_MONTHS]
            fig.add_trace(go.Bar(
                x=OCT_SEP_MONTHS, y=vals, name=FIELDS[fk],
                marker_color=COUNTRY_COLORS.get(fk, "#aaaaaa"), opacity=0.88,
            ))
        fig.update_layout(
            barmode="group",
            **_base_layout(
                f"{lbl_cum}Country Comparison — {cmp_single_year}  (Oct–Sep MY)",
                "Month", f"{lbl_cum}Volume ({unit_short})",
            ),
        )
        _add_chart_watermark(fig, logo_white_b64)
        st.plotly_chart(fig, use_container_width=True)

    # SINGLE-COUNTRY MODE
    else:
        cmp_field = cmp_countries[0] if cmp_countries else field

        if cmp_field == field:
            c_pivot_m, c_pivot_c = monthly_pivot, cum_pivot
            c_all_years, c_cy, c_months = all_years, cy, months
            c_stats_m, c_stats_c = monthly_stats, cum_stats
        else:
            if cmp_field == "US" and us_local_my:
                c_months  = cfg["us_local_months"]
                c_last_m  = cfg["us_local_last_month"]
                c_pivot_m, c_all_years = build_arbr_pivot(
                    df, cmp_field,
                    months_list=cfg["us_local_months"],
                    prev_months=cfg["us_local_prev_months"],
                    year_offset=0,
                )
            elif cmp_field in MAR_FEB_FIELDS and arbr_local_my:
                c_months  = cfg["arbr_months"]
                c_last_m  = cfg["arbr_last_month"]
                c_pivot_m, c_all_years = build_arbr_pivot(
                    df, cmp_field,
                    months_list=cfg["arbr_months"],
                    prev_months=cfg["arbr_prev_months"],
                )
            else:
                c_months  = OCT_SEP_MONTHS
                c_last_m  = "Sep"
                c_pivot_m, c_all_years = build_pivot(df, cmp_field)
            c_cy      = c_all_years[-1]
            c_ly      = c_all_years[-2] if len(c_all_years) >= 2 else None
            c_complete= [y for y in get_complete_years(c_pivot_m, c_last_m) if y != c_cy]
            if use_bushels:
                c_pivot_m = _apply_unit(c_pivot_m, unit_factor)
            c_pivot_c = build_cumulative_pivot(c_pivot_m, c_all_years, c_months)
            c_stats_m = compute_stats(c_pivot_m, c_all_years, c_complete,
                                      c_cy, c_ly, c_months, is_cumulative=False)
            c_stats_c = compute_stats(c_pivot_c, c_all_years, c_complete,
                                      c_cy, c_ly, c_months, is_cumulative=True)

        with ctrl_yr:
            default_yrs = [y for y in [c_cy,
                           c_all_years[-2] if len(c_all_years) > 1 else None]
                           if y is not None]
            cmp_years = st.multiselect(
                "Marketing years to compare",
                options=c_all_years[::-1], default=default_yrs,
                key=f"{pfx}_cmp_years",
            )

        st.caption(
            f"Showing: **{FIELDS[cmp_field]}** &nbsp;|&nbsp; "
            "Dashed line = 6-yr Olympic Avg (prior years) &nbsp;|&nbsp; "
            "Faint band = historical Min–Max range (prior years)."
        )

        if cmp_years:
            c_pivot = c_pivot_c if use_cum_cmp else c_pivot_m
            c_stats = c_stats_c if use_cum_cmp else c_stats_m
            st.plotly_chart(
                make_column_chart(c_pivot, c_stats, cmp_years, c_cy,
                                  FIELDS[cmp_field], use_cum_cmp, c_months,
                                  logo_white_b64, unit_short=unit_short),
                use_container_width=True,
            )
        else:
            st.info("Select at least one marketing year above to display the chart.")


# ─────────────────────────────────────────────────────────────────────────────
# WHEAT TAB
# ─────────────────────────────────────────────────────────────────────────────
def _get_wheat_field_my(field: str, cfg: dict,
                        nh_compare: bool, sh_compare: bool) -> tuple:
    """Return (months, prev_months, last_month, my_label) for a wheat field."""
    fmy = cfg["field_my"][field]
    hem = fmy["hemisphere"]
    if hem == "North" and nh_compare:
        return (cfg["nh_months"], cfg["nh_prev"], cfg["nh_last"],
                f"NH Compare ({cfg['nh_label']})")
    if hem == "South" and sh_compare:
        return (cfg["sh_months"], cfg["sh_prev"], cfg["sh_last"],
                f"SH Compare ({cfg['sh_label']})")
    return fmy["months"], fmy["prev"], fmy["last"], fmy["label"]


def _compute_wheat_tile_stats(df, use_bushels, unit_factor, cfg,
                              nh_compare, sh_compare) -> list:
    tiles = []
    for field in cfg["tile_order"]:
        if field not in df.columns:
            tiles.append(None)
            continue
        months_list, prev_months, last_month, my_label = _get_wheat_field_my(
            field, cfg, nh_compare, sh_compare
        )
        pivot, all_years = build_arbr_pivot(
            df, field,
            months_list=months_list,
            prev_months=prev_months,
            year_offset=0,
        )
        if not all_years:
            tiles.append(None)
            continue

        if use_bushels:
            pivot = _apply_unit(pivot, unit_factor)

        # Search newest → oldest year to find the most recent data for this
        # country. Countries report at different times so the latest year label
        # may have no data yet for some exporters.
        active_cy    = None
        latest_month = None
        latest_val   = None
        for yr in reversed(all_years):
            for m in reversed(months_list):
                v = pivot[m].get(yr)
                if v is not None:
                    active_cy, latest_month, latest_val = yr, m, v
                    break
            if active_cy:
                break

        if active_cy is None:
            tiles.append(None)
            continue

        cy = active_cy
        cy_idx = all_years.index(cy)
        ly     = all_years[cy_idx - 1] if cy_idx > 0 else None

        complete_years = [y for y in get_complete_years(pivot, last_month) if y != cy]
        oly_years      = sorted(complete_years)[-6:]

        ly_m    = pivot[latest_month].get(ly) if ly else None
        oly_m   = olympic_avg([pivot[latest_month].get(y) for y in oly_years])
        cum_piv = build_cumulative_pivot(pivot, all_years, months_list)
        cy_cum  = cum_piv[latest_month].get(cy)
        ly_cum  = cum_piv[latest_month].get(ly) if ly else None
        oly_cum = olympic_avg([cum_piv[latest_month].get(y) for y in oly_years])

        tiles.append(dict(
            field        = field,
            label        = cfg["fields"][field],
            my_label     = my_label,
            cy           = cy,
            latest_month = latest_month,
            accent       = cfg["tile_accents"].get(field, JSA_CYAN),
            is_import    = False,
            monthly_val  = latest_val,
            pct_ly_m     = _pct(latest_val, ly_m),
            pct_oly_m    = _pct(latest_val, oly_m),
            cum_val      = cy_cum,
            pct_ly_c     = _pct(cy_cum, ly_cum),
            pct_oly_c    = _pct(cy_cum, oly_cum),
        ))
    return tiles


def _run_wheat_tab(use_bushels: bool, unit_short: str,
                   unit_decimals: int, unit_long: str,
                   logo_white_b64: str | None):
    """Render the full Wheat dashboard tab."""
    cfg         = COMMODITY_CONFIG["wheat"]
    pfx         = "wheat"
    unit_factor = _bu_conv_factor(cfg) if use_bushels else 1.0

    # ── Hemisphere comparison toggles ────────────────────────────────────
    tog1, tog2 = st.columns(2)
    with tog1:
        nh_compare: bool = st.toggle(
            "🌍  NH: Jul–Jun Comparison MY",
            value=False,
            key="wheat_nh_compare",
            help=(
                "**OFF** (default) — each NH country shows its own Local MY:\n"
                "US: Jun–May · Canada: Aug–Jul · EU/Russia/Ukraine: Jul–Jun · "
                "India: Apr–Mar · China: Jun–May\n\n"
                "**ON** — aligns all NH countries to **Jul–Jun** for "
                "apples-to-apples cross-country comparisons."
            ),
        )
    with tog2:
        sh_compare: bool = st.toggle(
            "🌏  SH: Dec–Nov Comparison MY",
            value=False,
            key="wheat_sh_compare",
            help=(
                "**OFF** (default) — each SH country shows its own Local MY:\n"
                "Argentina: Dec–Nov · Australia: Dec–Nov · Brazil: Oct–Sep\n\n"
                "**ON** — aligns all SH countries to **Dec–Nov** for "
                "apples-to-apples cross-country comparisons."
            ),
        )

    # ── Load data ────────────────────────────────────────────────────────
    try:
        df = load_data("wheat")
    except Exception as exc:
        st.error(f"Error loading Wheat data: {exc}\n\nClick **🔄 Refresh Data** to retry.")
        return

    # Only expose fields that actually exist in the loaded dataframe
    FIELDS = {k: v for k, v in cfg["fields"].items() if k in df.columns}

    if not FIELDS:
        st.error(
            f"**No country columns found in data.** "
            f"Columns returned: `{list(df.columns)}`\n\n"
            f"Expected one of: `{list(cfg['fields'].keys())}`"
        )
        st.stop()

    # ── Stat tiles ───────────────────────────────────────────────────────
    tile_stats = _compute_wheat_tile_stats(
        df, use_bushels, unit_factor, cfg, nh_compare, sh_compare
    )
    st.markdown(_render_tile_grid(tile_stats, unit_short, unit_decimals),
                unsafe_allow_html=True)
    st.markdown(
        '<div style="border-top:1px solid #2e353d;margin:4px 0 16px;"></div>',
        unsafe_allow_html=True,
    )

    # ── Wheat Snapshot Chart ──────────────────────────────────────────────
    st.markdown("### 📸 Export Snapshot — All Countries")

    # Build year list from the first available wheat field
    _wsnap_ref = next((f for f in cfg["tile_order"] if f in df.columns), None)
    _wsnap_all_years = []
    if _wsnap_ref:
        try:
            _wm, _wp, _, _ = _get_wheat_field_my(_wsnap_ref, cfg, nh_compare, sh_compare)
            _, _wsnap_all_years = build_arbr_pivot(
                df, _wsnap_ref, months_list=_wm, prev_months=_wp, year_offset=0
            )
        except Exception:
            _wsnap_all_years = []

    _wsnap_cy    = _wsnap_all_years[-1] if _wsnap_all_years else None
    _wsnap_prior = list(reversed(_wsnap_all_years[:-1])) if len(_wsnap_all_years) > 1 else []
    _wsnap_opts  = [f"Current Year YTD ({_wsnap_cy})"] + _wsnap_prior if _wsnap_cy else []

    if _wsnap_opts:
        _wsnap_sel = st.selectbox(
            "Marketing Year",
            options=_wsnap_opts,
            index=0,
            key="wheat_snap_year",
            help=(
                "**Current Year YTD** — cumulative shipments through the most recent "
                "reported month (each country uses its own marketing year).\n\n"
                "**Prior years** — full marketing year total per country."
            ),
        )
        _wis_cy     = _wsnap_sel.startswith("Current Year")
        _wsel_yr    = None if _wis_cy else _wsnap_sel
        _wsnap_lbl  = (f"CY {_wsnap_cy} (YTD, per-country MY)"
                       if _wis_cy else f"Full MY {_wsnap_sel} (per-country MY)")

        _wsnap_data = _compute_wheat_snapshot_data(
            df, cfg, use_bushels, unit_factor,
            nh_compare, sh_compare, selected_year=_wsel_yr,
        )
        if _wsnap_data:
            st.plotly_chart(
                make_snapshot_chart(_wsnap_data, "Wheat", _wsnap_lbl,
                                    unit_short, logo_white_b64),
                use_container_width=True,
            )
        else:
            st.info("No snapshot data available for the selected year.")

    st.markdown("---")

    # ── Country / Category filter ────────────────────────────────────────
    st.markdown("#### Select Country or Category")
    field_key = f"{pfx}_field"
    if field_key not in st.session_state or st.session_state[field_key] not in FIELDS:
        st.session_state[field_key] = next(iter(FIELDS))

    filter_cols = st.columns(len(FIELDS))
    for i, (fk, fl) in enumerate(FIELDS.items()):
        with filter_cols[i]:
            btn_type = "primary" if st.session_state[field_key] == fk else "secondary"
            if st.button(fl, key=f"{pfx}_btn_{fk}", type=btn_type,
                         use_container_width=True):
                st.session_state[field_key] = fk
                st.rerun()

    field       = st.session_state[field_key]
    field_label = FIELDS[field]
    st.markdown(_view_title_html(field_label, cfg["label"], field in cfg["import_fields"], unit_short),
                unsafe_allow_html=True)

    months, prev_months, last_month, my_label = _get_wheat_field_my(
        field, cfg, nh_compare, sh_compare
    )

    # ── Build pivots ─────────────────────────────────────────────────────
    monthly_pivot, all_years = build_arbr_pivot(
        df, field,
        months_list=months,
        prev_months=prev_months,
        year_offset=0,
    )
    if not all_years:
        st.warning("No valid marketing-year data found.")
        return

    cy = all_years[-1]
    ly = all_years[-2] if len(all_years) >= 2 else None

    complete_years = [y for y in get_complete_years(monthly_pivot, last_month) if y != cy]
    oly_years      = sorted(complete_years)[-6:]
    oly_label      = " → ".join(oly_years) if oly_years else "N/A"

    if use_bushels:
        monthly_pivot = _apply_unit(monthly_pivot, unit_factor)

    cum_pivot     = build_cumulative_pivot(monthly_pivot, all_years, months)
    monthly_stats = compute_stats(monthly_pivot, all_years, complete_years,
                                  cy, ly, months, is_cumulative=False)
    cum_stats     = compute_stats(cum_pivot, all_years, complete_years,
                                  cy, ly, months, is_cumulative=True)

    # ── Official / Estimate classification ───────────────────────────────
    cutoffs         = load_cutoff_config()
    cy_est_months   = _cy_estimate_months(field, cutoffs, months)
    if field == "US":
        cy_est_months = cy_est_months | _us_fgis_est_months(
            "wheat", cy, months, my_start_month=7
        )
    has_estimates   = bool(cy_est_months)

    # ── Forecast seasonal shares ──────────────────────────────────────────
    # Limit to recent 5 complete years for share computation.
    forecast_cfg    = load_forecast_config(cy, use_ty=False)
    st.caption(f"📋 USDA export forecasts for {cy}: **{_wasde_source_label(cy, use_ty=False)}**")
    _usda_saved_w   = forecast_cfg.get(("wheat", field))
    _share_years_w  = sorted(complete_years)[-5:] if len(complete_years) >= 5 else complete_years
    shares_w        = _compute_seasonal_shares(monthly_pivot, _share_years_w, months)

    # ── Info strip ───────────────────────────────────────────────────────
    st.markdown(f"""
    <div style="background:{JSA_MID};padding:9px 18px;border-radius:6px;
                margin:10px 0 6px 0;font-family:Arial;font-size:13px;
                display:flex;gap:28px;flex-wrap:wrap;color:#d0d8e0;
                border-left:3px solid {JSA_GREEN};">
        <span>📊 <b style="color:#fff;">Showing:</b> {field_label}</span>
        <span>📐 <b style="color:#fff;">Units:</b>
              <span style="color:{JSA_CYAN};font-weight:700;">{unit_long}</span></span>
        <span>📅 <b style="color:#fff;">Marketing Year:</b> {my_label}</span>
        <span>📅 <b style="color:#fff;">Current Year (CY):</b>
              <span style="color:{JSA_CYAN};font-weight:700;">{cy}</span></span>
        <span>📅 <b style="color:#fff;">Last Year (LY):</b> {ly or "N/A"}</span>
        <span>📈 <b style="color:#fff;">Olympic Avg (prior yrs):</b> {oly_label}</span>
    </div>
    """, unsafe_allow_html=True)

    # ── Legend ───────────────────────────────────────────────────────────
    _est_badge_style = (
        "background:#7a5800;border:1px dashed #f9a825;padding:2px 8px;"
        "border-radius:3px;color:#ffe082;font-weight:600;"
    )
    _est_legend_w_item = (
        f'<span><span style="{_est_badge_style}">EST</span>'
        f'&nbsp;Estimate (not yet official)</span>'
        if has_estimates else ""
    )
    st.markdown(
        f'<div style="font-family:Arial;font-size:12px;color:#aab4c0;'
        f'margin:6px 0 14px 0;display:flex;gap:20px;flex-wrap:wrap;'
        f'padding:7px 14px;background:#252a2f;border-radius:5px;">'
        f'<span><span style="background:{JSA_CYAN};padding:2px 8px;border-radius:3px;'
        f'color:#fff;font-weight:600;">CY</span>&nbsp;Current Year ({cy})</span>'
        f'{_est_legend_w_item}'
        f'<span><span style="background:#2e7d32;padding:2px 8px;border-radius:3px;'
        f'color:#fff;">&#9632;</span>&nbsp;2 Highest (prior yrs)</span>'
        f'<span><span style="background:#c62828;padding:2px 8px;border-radius:3px;'
        f'color:#fff;">&#9632;</span>&nbsp;2 Lowest (prior yrs)</span>'
        f'<span><span style="background:#f4f6f8;padding:2px 8px;border-radius:3px;'
        f'color:#000;">&#9632;</span>&nbsp;Stat Columns (prior yrs only)</span>'
        f'<span style="color:#4caf50;font-weight:600;">+x.x%</span>'
        f'&nbsp;Above reference&nbsp;'
        f'<span style="color:#ef5350;font-weight:600;">-x.x%</span>'
        f'&nbsp;Below reference</div>',
        unsafe_allow_html=True,
    )

    # ── USDA Forecast input ───────────────────────────────────────────────
    # Wheat aggregate tabs (TotalNonUS, MajorExporter) are skipped — too many
    # different marketing year conventions across wheat-exporting countries to
    # produce a meaningful aggregate forecast here.
    _W_SKIP_AGGREGATE = {"TotalNonUS", "MajorExporter"}

    _w_legend_html = (
        f'<div style="padding:10px 0;font-family:Arial;font-size:12px;color:{_CH_MUTED};">'
        f'<b style="color:{_CH_M1};">●</b> Dotted gold = <b>USDA Seasonal</b> forecast<br>'
        f'<b style="color:{_CH_M2};">●</b> Green dash-dot = <b>Pace-Adjusted</b>'
        f' (activates once official data exists)<br>'
        f'<b style="color:{_CH_M1}; opacity:0.5;">▓</b> Shaded band = ±0.5σ likely range'
        f'</div>'
    )

    if field in _W_SKIP_AGGREGATE:
        st.info(
            "M1/M2 forecasting is not available for wheat aggregate categories "
            "(Total Non-US, Major Exporters) due to mixed marketing year conventions "
            "across exporting countries.",
            icon="📋",
        )
        usda_total_w = None

    else:
        # ── WASDE forecast total — from live API or static config ─────────
        _raw_w = forecast_cfg.get(("wheat", field))
        usda_total_w = (float(_raw_w) * unit_factor if use_bushels else float(_raw_w)) if _raw_w else None

        with st.expander(f"📈  USDA MY Forecast — {field_label}", expanded=bool(usda_total_w)):
            _fw1, _fw2 = st.columns([2, 3])
            with _fw1:
                if usda_total_w:
                    _src_lbl_w = _wasde_source_label(cy, use_ty=False)
                    st.markdown(
                        f'<div style="font-family:Arial;font-size:13px;">'
                        f'<span style="color:#8a9aaa;">WASDE {cy} MY Total ({unit_short})</span><br>'
                        f'<span style="font-size:24px;font-weight:700;color:#1e2124;">'
                        f'{usda_total_w:,.0f}</span><br>'
                        f'<span style="color:#555;font-size:11px;">{_src_lbl_w}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("No WASDE forecast available for this field.")
            with _fw2:
                st.markdown(_w_legend_html, unsafe_allow_html=True)

    model1_pivot_w, model2_pivot_w, pace_info_w = _build_forecast_pivots(
        monthly_pivot, all_years, cy, months, shares_w, usda_total_w, cy_est_months
    )

    # ── Monthly / Cumulative tabs ─────────────────────────────────────────
    tab1, tab2 = st.tabs(["📊  Monthly Shipments", "📈  Cumulative Shipments"])

    with tab1:
        st.markdown(
            f"**Monthly — {field_label}** &nbsp;({unit_short}) &nbsp;|&nbsp; "
            f"{my_label} marketing year &nbsp;|&nbsp; "
            f"Stats reflect prior marketing years only.",
            unsafe_allow_html=True,
        )
        cum_model1_w = (build_cumulative_pivot(model1_pivot_w, all_years, months)
                        if model1_pivot_w else None)
        cum_model2_w = (build_cumulative_pivot(model2_pivot_w, all_years, months)
                        if model2_pivot_w else None)
        _show_table(
            monthly_pivot,
            render_table_html(monthly_pivot, monthly_stats, all_years,
                              cy, ly, months, decimals=unit_decimals,
                              cy_est_months=cy_est_months,
                              model1_pivot=model1_pivot_w,
                              model2_pivot=model2_pivot_w),
            f"wheat_{field_label}_monthly",
        )
        st.plotly_chart(
            make_seasonal_chart(monthly_pivot, all_years, cy, complete_years,
                                field_label, False, months,
                                logo_white_b64, unit_short=unit_short,
                                cy_est_months=cy_est_months,
                                model1_pivot=model1_pivot_w,
                                model2_pivot=model2_pivot_w,
                                shares=shares_w,
                                usda_total=usda_total_w,
                                pace_info=pace_info_w),
            use_container_width=True,
        )

    with tab2:
        st.markdown(
            f"**Cumulative — {field_label}** &nbsp;({unit_short}) &nbsp;|&nbsp; "
            f"{my_label} marketing year &nbsp;|&nbsp; "
            f"Stats reflect prior marketing years only.",
            unsafe_allow_html=True,
        )
        _show_table(
            cum_pivot,
            render_table_html(cum_pivot, cum_stats, all_years,
                              cy, ly, months, decimals=unit_decimals,
                              cy_est_months=cy_est_months,
                              model1_pivot=cum_model1_w,
                              model2_pivot=cum_model2_w),
            f"wheat_{field_label}_cumulative",
        )
        st.plotly_chart(
            make_seasonal_chart(cum_pivot, all_years, cy, complete_years,
                                field_label, True, months,
                                logo_white_b64, unit_short=unit_short,
                                cy_est_months=cy_est_months,
                                model1_pivot=cum_model1_w,
                                model2_pivot=cum_model2_w,
                                shares=None,
                                usda_total=usda_total_w,
                                pace_info=pace_info_w),
            use_container_width=True,
        )

    # ── Forecast Panel ────────────────────────────────────────────────────
    _render_forecast_panel(pace_info_w, unit_short, unit_decimals, field_label)

    # ── Model 3 — MYTD Regression Scatter ────────────────────────────────
    _render_ytd_scatter(
        monthly_pivot, complete_years, cy, months, cy_est_months,
        field_label, unit_short, unit_decimals, logo_white_b64,
        accent_color=cfg["country_colors"].get(field, _CH_BLUE),
        pace_info=pace_info_w,
    )

    # ── Volume Comparison ────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📊 Volume Comparison by Month")

    COUNTRY_COLORS = cfg["country_colors"]
    ctrl_ctry, ctrl_yr, ctrl_type = st.columns([2, 3, 1])

    with ctrl_ctry:
        cmp_countries = st.multiselect(
            "Countries / categories",
            options=[k for k in FIELDS],
            default=[field],
            max_selections=2,
            format_func=lambda k: FIELDS[k],
            key=f"{pfx}_cmp_countries",
        )
    with ctrl_type:
        cmp_mode    = st.radio("Data type", ["Monthly", "Cumulative"],
                               key=f"{pfx}_cmp_mode", horizontal=False)
    use_cum_cmp = cmp_mode == "Cumulative"
    lbl_cum     = "Cumulative " if use_cum_cmp else ""

    multi_country = len(set(cmp_countries)) > 1

    if multi_country:
        # Two-country comparison: each uses its own (or comparison-group) MY
        fa, fb = cmp_countries[0], cmp_countries[1]
        ma, pma, _, la = _get_wheat_field_my(fa, cfg, nh_compare, sh_compare)
        mb, pmb, _, _  = _get_wheat_field_my(fb, cfg, nh_compare, sh_compare)
        pa, years_a    = build_arbr_pivot(df, fa, months_list=ma, prev_months=pma, year_offset=0)
        if use_bushels:
            pa = _apply_unit(pa, unit_factor)
        with ctrl_yr:
            st.caption(f"ℹ️ Using **{la}** MY for year selection.")
            cmp_yr = st.selectbox("Marketing year", options=years_a[::-1],
                                  key=f"{pfx}_cmp_single_year")
        fig = go.Figure()
        for fk, mfk, pmfk in [(fa, ma, pma), (fb, mb, pmb)]:
            p, yrs = build_arbr_pivot(df, fk, months_list=mfk, prev_months=pmfk, year_offset=0)
            if use_bushels:
                p = _apply_unit(p, unit_factor)
            if use_cum_cmp:
                p = build_cumulative_pivot(p, yrs, mfk)
            vals = [p[m].get(cmp_yr) if m in p else None for m in ma]
            fig.add_trace(go.Bar(x=ma, y=vals, name=FIELDS[fk],
                                 marker_color=COUNTRY_COLORS.get(fk, "#aaa"), opacity=0.88))
        fig.update_layout(barmode="group",
                          **_base_layout(f"{lbl_cum}Country Comparison — {cmp_yr}",
                                         "Month", f"{lbl_cum}Volume ({unit_short})"))
        _add_chart_watermark(fig, logo_white_b64)
        st.plotly_chart(fig, use_container_width=True)

    else:
        cmp_field = cmp_countries[0] if cmp_countries else field

        if cmp_field == field:
            c_pivot_m, c_pivot_c = monthly_pivot, cum_pivot
            c_all_years, c_cy, c_months = all_years, cy, months
            c_stats_m, c_stats_c = monthly_stats, cum_stats
        else:
            c_months, c_pm, c_lm, _ = _get_wheat_field_my(
                cmp_field, cfg, nh_compare, sh_compare
            )
            c_pivot_m, c_all_years = build_arbr_pivot(
                df, cmp_field,
                months_list=c_months, prev_months=c_pm, year_offset=0,
            )
            c_cy      = c_all_years[-1]
            c_ly      = c_all_years[-2] if len(c_all_years) >= 2 else None
            c_complete= [y for y in get_complete_years(c_pivot_m, c_lm) if y != c_cy]
            if use_bushels:
                c_pivot_m = _apply_unit(c_pivot_m, unit_factor)
            c_pivot_c = build_cumulative_pivot(c_pivot_m, c_all_years, c_months)
            c_stats_m = compute_stats(c_pivot_m, c_all_years, c_complete,
                                      c_cy, c_ly, c_months, is_cumulative=False)
            c_stats_c = compute_stats(c_pivot_c, c_all_years, c_complete,
                                      c_cy, c_ly, c_months, is_cumulative=True)

        with ctrl_yr:
            default_yrs = [y for y in [c_cy,
                           c_all_years[-2] if len(c_all_years) > 1 else None]
                           if y is not None]
            cmp_years = st.multiselect(
                "Marketing years to compare",
                options=c_all_years[::-1], default=default_yrs,
                key=f"{pfx}_cmp_years",
            )

        st.caption(
            f"Showing: **{FIELDS[cmp_field]}** &nbsp;|&nbsp; "
            "Dashed line = 6-yr Olympic Avg (prior years) &nbsp;|&nbsp; "
            "Faint band = historical Min–Max range (prior years)."
        )

        if cmp_years:
            c_pivot = c_pivot_c if use_cum_cmp else c_pivot_m
            c_stats = c_stats_c if use_cum_cmp else c_stats_m
            st.plotly_chart(
                make_column_chart(c_pivot, c_stats, cmp_years, c_cy,
                                  FIELDS[cmp_field], use_cum_cmp, c_months,
                                  logo_white_b64, unit_short=unit_short),
                use_container_width=True,
            )
        else:
            st.info("Select at least one marketing year above to display the chart.")


# ─────────────────────────────────────────────────────────────────────────────
# CHINA IMPORTS TAB  — live TDM data
# ─────────────────────────────────────────────────────────────────────────────
TDM_BASE = "https://www1.tdmlogin.com/tdm/api/api.asp"
TDM_USER = "jpsi"

TDM_PRODUCTS = {
    "Corn (ex. seed)":     "205719",
    "Soybeans (ex. seed)": "205714",
    "Wheat (ex. seed)":    "205713",
    "Soybean Meal":        "205717",
}

# Partners to highlight by default (all others still available via filter)
TDM_KEY_PARTNERS = [
    "United States", "Brazil", "Argentina", "Ukraine",
    "Russia", "Australia", "Canada",
]

# TDM reporter → (country_code, product_code) for monthly export data by commodity
_TDM_REPORTERS = {
    "corn": {
        "Brazil":    ("BR", "205719"),
        "Argentina": ("AR", "205719"),
        "Ukraine":   ("UA", "205719"),
    },
    "soybeans": {
        "Brazil":    ("BR", "205714"),
        "Argentina": ("AR", "205714"),
    },
    "soybeanmeal": {
        "Brazil":    ("BR", "205717"),
        "Argentina": ("AR", "205717"),
    },
    "wheat": {
        "Canada":    ("CA", "205713"),
        "EU":        ("EU", "205713"),
        "Russia":    ("RU", "205713"),
        "Ukraine":   ("UA", "205713"),
        "India":     ("IN", "205713"),
        "China":     ("CN", "205713"),
        "Argentina": ("AR", "205713"),
        "Australia": ("AU", "205713"),
        "Brazil":    ("BR", "205713"),
    },
}


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_tdm_exports_cached(reporter: str, product_code: str, password: str) -> dict:
    """Monthly exports from TDM for one reporter country → {(year, month): tmt}."""
    import urllib.request
    now        = pd.Timestamp.now()
    period_end = f"{now.year}{now.month:02d}"
    url = (
        f"{TDM_BASE}?username={TDM_USER}&password={password}"
        f"&reporter={reporter}&periodBegin=201001&periodEnd={period_end}"
        f"&flow=E&partners=All&frequency=M&productCode={product_code}"
        f"&levelDetail=6&levelDetailGroup=P&currency=USD&includeUnits=UNIT1"
        f"&isoCountryCode=NONE&conv=1&separator=T&includeFlow=Y"
    )
    try:
        with urllib.request.urlopen(url, timeout=45) as r:
            raw = r.read()
        if len(raw) < 500:
            return {}
        data  = raw.decode("utf-16")
        lines = [l for l in data.strip().split("\n") if l.strip()]
        if len(lines) < 2:
            return {}
        header = lines[0].split("\t")
        rows   = [l.split("\t") for l in lines[1:]]
        df = pd.DataFrame(rows, columns=header)
        hup = [h.strip().upper() for h in df.columns]
        df.columns = hup
        if not {"YEAR", "MONTH", "QTY1"}.issubset(set(hup)):
            return {}
        df["YEAR"]  = pd.to_numeric(df["YEAR"],  errors="coerce")
        df["MONTH"] = pd.to_numeric(df["MONTH"], errors="coerce")
        df["QTY1"]  = pd.to_numeric(df["QTY1"],  errors="coerce")
        df = df.dropna(subset=["YEAR", "MONTH", "QTY1"])
        result: dict = {}
        for _, row in df.iterrows():
            key = (int(row["YEAR"]), int(row["MONTH"]))
            result[key] = result.get(key, 0.0) + float(row["QTY1"]) / 1000
        return result
    except Exception:
        return {}

# Consistent colors per partner
_PARTNER_COLORS = {
    "United States": _CH_BLUE,
    "Brazil":        _CH_SAGE,
    "Argentina":     _CH_GOLD,
    "Ukraine":       "#c0707f",
    "Russia":        _CH_LAV,
    "Australia":     "#5aa59a",
    "Canada":        _CH_TERRA,
}
_FALLBACK_COLORS = [
    "#8fa398", "#d7c98a", "#dba3ab", "#c3b8dc",
    "#9fcfc6", "#e0b49e", "#a9b6c4", _CH_MIST,
]


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_tdm_china(product_code: str, password: str) -> pd.DataFrame:
    """Fetch China import data from TDM; cached 1 hour."""
    import urllib.request
    now = pd.Timestamp.now()
    url = (
        f"{TDM_BASE}?username={TDM_USER}&password={password}"
        f"&reporter=CN&periodBegin=201401&periodEnd={now.year}{now.month:02d}"
        f"&flow=I&partners=All&frequency=M&productCode={product_code}"
        f"&levelDetail=6&levelDetailGroup=P&currency=USD&includeUnits=UNIT1"
        f"&isoCountryCode=NONE&conv=1&separator=T&includeFlow=Y"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            raw = r.read()
        data = raw.decode("utf-16")
        lines = [l for l in data.strip().split("\n") if l.strip()]
        if len(lines) < 2:
            return pd.DataFrame()
        header = lines[0].split("\t")
        rows   = [l.split("\t") for l in lines[1:]]
        df = pd.DataFrame(rows, columns=header)
        df["YEAR"]  = pd.to_numeric(df["YEAR"],  errors="coerce")
        df["MONTH"] = pd.to_numeric(df["MONTH"], errors="coerce")
        df["QTY1"]  = pd.to_numeric(df["QTY1"],  errors="coerce")
        df["TMT"]   = df["QTY1"] / 1000          # metric tonnes → TMT
        return df.dropna(subset=["YEAR", "MONTH", "QTY1"])
    except Exception as e:
        return pd.DataFrame()


def _cn_my_info(commodity: str) -> tuple[list[int], list[str]]:
    """Return (month_order, month_labels) for China MY based on commodity."""
    if commodity == "Wheat (ex. seed)":
        # Jul–Jun
        months = [7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6]
        labels = ["Jul","Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun"]
    else:
        # Oct–Sep  (Corn, Soybeans, Soybean Meal)
        months = [10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        labels = ["Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep"]
    return months, labels


def _cn_my_label(year: int, month: int, commodity: str) -> tuple[int, str]:
    """Return (my_start_year, 'YYYY/YY') for a TDM data point."""
    if commodity == "Wheat (ex. seed)":
        my_start = year if month >= 7 else year - 1
    else:
        my_start = year if month >= 10 else year - 1
    return my_start, f"{my_start}/{str(my_start + 1)[-2:]}"


def _run_china_imports_tab(logo_b64=None):
    """China Imports tab — styled to match other commodity tabs, MY-aware."""

    # ── Credentials ──────────────────────────────────────────────────────────
    try:
        pwd = st.secrets["TDM_PASSWORD"]
    except Exception:
        st.error(
            "⚠️ TDM credentials missing. Add `TDM_PASSWORD` to Streamlit secrets "
            "(share.streamlit.io → app → Settings → Secrets)."
        )
        return

    # ── Commodity + partner selectors (top row) ───────────────────────────────
    ctrl1, ctrl2 = st.columns([2, 5])
    with ctrl1:
        commodity = st.selectbox(
            "Commodity", list(TDM_PRODUCTS.keys()), key="cn_commodity"
        )
    product_code = TDM_PRODUCTS[commodity]
    is_wheat     = (commodity == "Wheat (ex. seed)")
    my_months, my_labels = _cn_my_info(commodity)

    with st.spinner(f"Loading {commodity} data from TDM…"):
        df = _fetch_tdm_china(product_code, pwd)

    if df.empty:
        st.warning("No data returned from TDM API. Check credentials or try again.")
        return

    # ── Attach MY columns ─────────────────────────────────────────────────────
    df = df.copy()
    df["MY_START"] = df.apply(
        lambda r: _cn_my_label(int(r["YEAR"]), int(r["MONTH"]), commodity)[0], axis=1
    )
    df["MY_LABEL"] = df.apply(
        lambda r: _cn_my_label(int(r["YEAR"]), int(r["MONTH"]), commodity)[1], axis=1
    )
    df["MY_POS"] = df["MONTH"].apply(
        lambda m: my_months.index(int(m)) + 1 if int(m) in my_months else None
    )

    all_partners = sorted(df["PARTNER"].dropna().unique().tolist())
    all_my_starts = sorted(df["MY_START"].dropna().unique().astype(int).tolist())
    cur_my_start  = max(all_my_starts)
    cur_my_label  = f"{cur_my_start}/{str(cur_my_start+1)[-2:]}"

    with ctrl2:
        default_partners = [p for p in TDM_KEY_PARTNERS if p in all_partners]
        sel_partners = st.multiselect(
            "Source Countries", all_partners,
            default=default_partners, key="cn_partners"
        )

    if not sel_partners:
        st.info("Select at least one source country above.")
        return

    # ── MY selector (matches snapshot selector pattern) ───────────────────────
    my_opts_raw   = list(reversed(all_my_starts))
    my_opt_labels = [
        f"Current MY YTD ({cur_my_label})" if s == cur_my_start
        else f"{s}/{str(s+1)[-2:]}"
        for s in my_opts_raw
    ]
    sel_col, yr_col = st.columns([4, 3])
    with sel_col:
        snap_sel = st.selectbox(
            "Marketing Year", my_opt_labels, index=0, key="cn_my_sel",
            help=(
                f"**Corn / Soybeans / Meal:** Oct–Sep marketing year\n\n"
                f"**Wheat:** Jul–Jun marketing year\n\n"
                "Current MY YTD = cumulative through latest available month."
            ),
        )
    sel_my_start = my_opts_raw[my_opt_labels.index(snap_sel)]
    sel_my_label = f"{sel_my_start}/{str(sel_my_start+1)[-2:]}"
    is_current_my = (sel_my_start == cur_my_start)

    with yr_col:
        hist_default = [
            f"{s}/{str(s+1)[-2:]}" for s in all_my_starts
            if s >= cur_my_start - 5 and s != sel_my_start
        ]
        all_my_labels_sorted = [f"{s}/{str(s+1)[-2:]}" for s in reversed(all_my_starts)]
        comp_years = st.multiselect(
            "Compare MYs (seasonal chart)", all_my_labels_sorted,
            default=hist_default, key="cn_comp_yrs"
        )

    # Convert selected MY labels → MY start years for charting
    def _label_to_start(lbl: str) -> int:
        return int(lbl.split("/")[0])

    chart_my_starts = sorted(set(
        [sel_my_start] + [_label_to_start(l) for l in comp_years]
    ))

    # ── Divider ───────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="border-top:1px solid #2e353d;margin:6px 0 16px;"></div>',
        unsafe_allow_html=True,
    )

    # ── MYTD Stat Tiles ───────────────────────────────────────────────────────
    # Per-partner MYTD totals for current MY vs Olympic avg and vs LY
    st.markdown("### 📊 Import Summary — Current MY YTD by Source Country")

    # Build full-MY dataset for all partners + all MY starts
    df_all = df[df["PARTNER"].isin(sel_partners)].copy()

    # Determine last reported MY month position (in current MY)
    cur_my_data = df_all[df_all["MY_START"] == cur_my_start]
    last_pos    = int(cur_my_data["MY_POS"].max()) if not cur_my_data.empty else len(my_months)

    # Get Olympic avg (6 complete MYs, trim high/low)
    complete_my_starts = sorted([s for s in all_my_starts if s < cur_my_start])[-8:]

    def _mytd(my_start: int, partner: str, through_pos: int) -> float | None:
        sub = df_all[
            (df_all["MY_START"] == my_start) &
            (df_all["PARTNER"] == partner) &
            (df_all["MY_POS"] <= through_pos)
        ]
        return float(sub["TMT"].sum()) if not sub.empty else None

    tiles_html = ""
    for partner in sel_partners:
        cy_val   = _mytd(cur_my_start, partner, last_pos)
        ly_start = cur_my_start - 1
        ly_val   = _mytd(ly_start,      partner, last_pos)

        oly_vals = [v for s in complete_my_starts
                    if (v := _mytd(s, partner, last_pos)) is not None]
        if len(oly_vals) >= 4:
            oly_trimmed = sorted(oly_vals)[1:-1]
            oly_val = float(np.mean(oly_trimmed))
        else:
            oly_val = float(np.mean(oly_vals)) if oly_vals else None

        pct_avg = ((cy_val / oly_val - 1) * 100) if cy_val and oly_val else None
        pct_ly  = ((cy_val / ly_val  - 1) * 100) if cy_val and ly_val  else None

        def _pct_color(v):
            return "#4caf50" if v is not None and v >= 0 else "#ef5350"

        def _fmt(v, dec=0):
            return f"{v:,.{dec}f}" if v is not None else "—"

        color = _PARTNER_COLORS.get(partner, "#8a9aaa")
        tiles_html += f"""
        <div style="background:#1e2124;border:1px solid #2e353d;border-top:3px solid {color};
                    border-radius:6px;padding:12px 16px;min-width:160px;flex:1;">
          <div style="font-family:Arial;font-size:11px;font-weight:700;color:#8a9aaa;
                      text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">
            {partner}
          </div>
          <div style="font-family:Arial;font-size:18px;font-weight:700;color:#fff;
                      margin-bottom:4px;">{_fmt(cy_val)} <span style="font-size:11px;color:#8a9aaa;">TMT</span></div>
          <div style="display:flex;gap:10px;margin-top:4px;">
            <span style="font-family:Arial;font-size:11px;color:{_pct_color(pct_avg)};">
              Avg: {('+' if pct_avg and pct_avg>=0 else '')}{_fmt(pct_avg,1)}%
            </span>
            <span style="font-family:Arial;font-size:11px;color:{_pct_color(pct_ly)};">
              LY: {('+' if pct_ly and pct_ly>=0 else '')}{_fmt(pct_ly,1)}%
            </span>
          </div>
        </div>"""

    st.markdown(
        f'<div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px;">'
        f'{tiles_html}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="border-top:1px solid #2e353d;margin:4px 0 16px;"></div>',
        unsafe_allow_html=True,
    )

    # ── Chart 1: Seasonal monthly imports (MY-ordered x-axis) ────────────────
    st.markdown(f"### 📈 Monthly Imports — Seasonal Comparison (TMT)")

    # Build per-MY monthly totals (all selected partners)
    df_chart = df[df["PARTNER"].isin(sel_partners)].copy()
    my_monthly = (
        df_chart.groupby(["MY_START", "MY_POS", "MONTH"])["TMT"].sum()
        .reset_index()
    )

    fig1 = go.Figure()

    # Olympic avg over complete MYs (trim high/low per position)
    oly_by_pos = {}
    for pos in range(1, len(my_months) + 1):
        vals = []
        for s in complete_my_starts:
            sub = my_monthly[(my_monthly["MY_START"] == s) & (my_monthly["MY_POS"] == pos)]
            if not sub.empty:
                vals.append(float(sub["TMT"].sum()))
        if len(vals) >= 4:
            oly_by_pos[pos] = float(np.mean(sorted(vals)[1:-1]))

    if oly_by_pos:
        oly_x = [my_labels[p-1] for p in sorted(oly_by_pos)]
        oly_y = [oly_by_pos[p]  for p in sorted(oly_by_pos)]
        fig1.add_trace(go.Scatter(
            x=oly_x, y=oly_y, mode="lines", name="Olympic Avg",
            line=dict(color=_CH_SAGE, width=2.2, dash="dash"),
            customdata=[f"{v:,.0f} TMT" for v in oly_y],
            hovertemplate="%{x}: %{customdata}<extra>6-Yr Olympic Avg</extra>",
        ))

    sorted_chart_starts = sorted(chart_my_starts)
    _prev_complete = [s for s in sorted_chart_starts if s < cur_my_start]
    ly_start = _prev_complete[-1] if _prev_complete else None

    for my_s in sorted_chart_starts:
        lbl  = f"{my_s}/{str(my_s+1)[-2:]}"
        sub  = my_monthly[my_monthly["MY_START"] == my_s].sort_values("MY_POS")
        if sub.empty:
            continue
        x_vals = [my_labels[int(p)-1] for p in sub["MY_POS"]]
        y_vals = sub["TMT"].tolist()

        if my_s == cur_my_start:
            color, lw, op = _CH_BLUE, 4.0, 1.0
        elif my_s == ly_start:
            color, lw, op = _CH_TERRA, 2.5, 1.0
        else:
            idx = sorted_chart_starts.index(my_s)
            t   = idx / max(len(sorted_chart_starts) - 1, 1)
            color = _ch_mix("#cdd9d1", "#5f7267", t)
            lw, op = 1.2 + 0.5 * t, 1.0

        fig1.add_trace(go.Scatter(
            x=x_vals, y=y_vals, mode="lines+markers", name=lbl,
            line=dict(color=color, width=lw), opacity=op,
            marker=dict(size=5 if my_s == cur_my_start else 3),
            customdata=[f"{v:,.0f} TMT" for v in y_vals],
            hovertemplate="%{x}: %{customdata}<extra>" + lbl + "</extra>",
        ))

    my_conv = "Oct–Sep" if not is_wheat else "Jul–Jun"
    fig1.update_layout(**_ch_layout("Monthly imports by marketing year (TMT)", f"Marketing year convention: {my_conv}",
                                    None, "TMT", height=420, margin=dict(l=60, r=20, t=88, b=40)))
    fig1.update_xaxes(tickfont=dict(size=10, color=_CH_MUTED), categoryorder="array", categoryarray=my_labels)
    fig1.update_yaxes(tickformat=",.0f")
    _add_chart_watermark(fig1, logo_b64)
    st.plotly_chart(fig1, use_container_width=True)

    # ── Chart 2: Stacked partner breakdown for selected MY ────────────────────
    st.markdown(f"### 🌐 Source Country Breakdown — {sel_my_label}")

    df_sel_my = df[
        (df["MY_START"] == sel_my_start) & (df["PARTNER"].isin(sel_partners))
    ].copy()

    fig2   = go.Figure()
    fb_idx = 0
    for partner in sel_partners:
        pdf = df_sel_my[df_sel_my["PARTNER"] == partner].sort_values("MY_POS")
        if pdf.empty:
            continue
        x_vals = [my_labels[int(p)-1] for p in pdf["MY_POS"]]
        y_vals = pdf["TMT"].tolist()
        color  = _PARTNER_COLORS.get(partner) or _FALLBACK_COLORS[fb_idx % len(_FALLBACK_COLORS)]
        if partner not in _PARTNER_COLORS:
            fb_idx += 1
        fig2.add_trace(go.Bar(
            name=partner, x=x_vals, y=y_vals,
            marker_color=color,
            customdata=[f"{v:,.0f} TMT" for v in y_vals],
            hovertemplate="%{x}: %{customdata}<extra>" + partner + "</extra>",
        ))

    fig2.update_layout(**_ch_layout(f"Imports by source country, {sel_my_label} (TMT)", None,
                                    None, "TMT", height=380, margin=dict(l=60, r=20, t=64, b=40)))
    fig2.update_layout(barmode="stack")
    fig2.update_xaxes(tickfont=dict(size=10, color=_CH_MUTED), categoryorder="array", categoryarray=my_labels)
    fig2.update_yaxes(tickformat=",.0f")
    _add_chart_watermark(fig2, logo_b64)
    st.plotly_chart(fig2, use_container_width=True)

    # ── MY Totals Table (matching corn-tbl style) ─────────────────────────────
    st.markdown("### 📋 Marketing Year Totals by Source Country (TMT)")

    # Build pivot: rows=partner, cols=MY label (newest first)
    df_tbl = df[df["PARTNER"].isin(sel_partners)].copy()
    ann = (
        df_tbl.groupby(["MY_LABEL", "MY_START", "PARTNER"])["TMT"].sum()
        .reset_index()
    )
    my_col_order = [
        f"{s}/{str(s+1)[-2:]}" for s in reversed(sorted(ann["MY_START"].unique()))
    ]
    ann_pivot = (
        ann.pivot_table(index="PARTNER", columns="MY_LABEL", values="TMT", aggfunc="sum")
        .reindex(columns=my_col_order)
        .fillna(0)
    )

    # Highlight current MY column
    def _cell(val, is_cur=False, bold=False):
        fmt = f"{val:,.0f}" if val else "—"
        bg  = "#0693e3" if is_cur else "#32373c"
        fw  = "700" if (is_cur or bold) else "400"
        return (f'<td style="padding:5px 10px;text-align:right;white-space:nowrap;'
                f'border-bottom:1px solid #484f56;border-right:1px solid #484f56;'
                f'background:{bg};color:#fff;font-weight:{fw};">{fmt}</td>')

    hdr_cells = "".join(
        f'<th style="background:{"#0555a0" if col==cur_my_label else "#1e2124"};'
        f'color:#fff;font-weight:600;text-align:center;padding:7px 10px;'
        f'white-space:nowrap;border-right:1px solid #484f56;'
        f'border-bottom:2px solid #0d0f11;font-family:Arial;font-size:12px;">'
        f'{col}</th>'
        for col in my_col_order
    )
    hdr = (
        f'<th style="background:#2a2f35;color:#fff;font-weight:600;text-align:left;'
        f'padding:7px 10px;white-space:nowrap;border-right:1px solid #484f56;'
        f'border-bottom:2px solid #0d0f11;font-family:Arial;font-size:12px;'
        f'position:sticky;left:0;z-index:3;">Country</th>'
        + hdr_cells
    )

    tbl_rows = ""
    totals   = {col: 0.0 for col in my_col_order}
    for i, (partner, row) in enumerate(ann_pivot.iterrows()):
        bg = "#32373c" if i % 2 == 0 else "#2a2f35"
        cells = ""
        for col in my_col_order:
            v = float(row.get(col, 0))
            totals[col] += v
            is_cur = (col == cur_my_label)
            cells += _cell(v, is_cur=is_cur)
        tbl_rows += (
            f'<tr>'
            f'<td style="padding:5px 10px;text-align:left;white-space:nowrap;'
            f'border-bottom:1px solid #484f56;border-right:1px solid #484f56;'
            f'background:{bg};color:#fff;font-weight:700;font-family:Arial;'
            f'font-size:12px;position:sticky;left:0;z-index:2;">{partner}</td>'
            f'{cells}</tr>'
        )

    # Total row
    tot_cells = "".join(
        _cell(totals[col], is_cur=(col == cur_my_label), bold=True)
        for col in my_col_order
    )
    tbl_rows += (
        f'<tr style="border-top:2px solid #0693e3;">'
        f'<td style="padding:5px 10px;text-align:left;white-space:nowrap;'
        f'border-bottom:1px solid #484f56;border-right:1px solid #484f56;'
        f'background:#232729;color:#fff;font-weight:700;font-family:Arial;'
        f'font-size:12px;position:sticky;left:0;z-index:2;">Total</td>'
        f'{tot_cells}</tr>'
    )

    st.markdown(
        f'<div style="overflow-x:auto;border-radius:6px;border:1px solid #484f56;'
        f'font-family:Arial;font-size:12px;margin-bottom:12px;">'
        f'<table style="border-collapse:collapse;width:max-content;min-width:100%;">'
        f'<thead><tr style="background:#1e2124;">{hdr}</tr></thead>'
        f'<tbody>{tbl_rows}</tbody>'
        f'</table></div>',
        unsafe_allow_html=True,
    )

    st.caption(
        f"Source: Trade Data Monitor (TDM) · China import declarations · "
        f"MY convention: {'Jul–Jun' if is_wheat else 'Oct–Sep'} · Volumes in TMT"
    )


# ─────────────────────────────────────────────────────────────────────────────
# MARKETING YEARS REFERENCE TAB
# ─────────────────────────────────────────────────────────────────────────────
def _render_my_reference_tab():
    """Render the Marketing Years Reference tab — one simple table per commodity."""

    def _ref_table(title, rows):
        """rows: list of (Country, Hemisphere, USDA MY, Local MY)"""
        hdr = "".join(
            f'<th style="padding:9px 16px;text-align:left;color:#aab4c0;'
            f'font-size:11px;text-transform:uppercase;letter-spacing:0.5px;'
            f'border-bottom:2px solid #0693e3;white-space:nowrap;">{h}</th>'
            for h in ["Country", "Hemisphere", "USDA MY", "Local MY"]
        )
        tbl_rows = ""
        for i, (country, hemi, usda_my, local_my) in enumerate(rows):
            bg = "#21262c" if i % 2 == 0 else "#1d2227"
            same = local_my == usda_my
            local_cell = (
                local_my if same else
                f'<b style="color:#f9a825;">{local_my}</b>'
            )
            tbl_rows += (
                f'<tr style="background:{bg};">'
                f'<td style="padding:8px 16px;color:#e0e8f0;font-size:12.5px;">{country}</td>'
                f'<td style="padding:8px 16px;color:#8a9aaa;font-size:12.5px;">{hemi}</td>'
                f'<td style="padding:8px 16px;color:#d0d8e0;font-size:12.5px;">{usda_my}</td>'
                f'<td style="padding:8px 16px;font-size:12.5px;">{local_cell}</td>'
                f'</tr>'
            )
        return f"""
        <div style="font-family:Arial;margin-bottom:24px;">
          <div style="font-size:13px;font-weight:700;color:#fff;
                      margin-bottom:8px;">{title}</div>
          <div style="border-radius:6px;border:1px solid #2e353d;overflow:hidden;">
            <table style="border-collapse:collapse;width:100%;">
              <thead><tr style="background:#151a1f;">{hdr}</tr></thead>
              <tbody>{tbl_rows}</tbody>
            </table>
          </div>
        </div>
        """

    st.markdown(
        '<p style="font-family:Arial;font-size:12.5px;color:#8a9aaa;margin-bottom:20px;">'
        'Local MY (highlighted in amber) differs from the USDA standard. '
        'Toggle <em>Local MY</em> off on any commodity tab to align all countries '
        'to <b style="color:#0693e3;">USDA Oct–Sep</b> for cross-country comparisons.'
        '</p>',
        unsafe_allow_html=True,
    )

    wheat_html = _ref_table("🌾 Wheat Marketing Year Structure", [
        ("United States", "North", "Jun–May", "Jun–May"),
        ("Canada",        "North", "Aug–Jul", "Aug–Jul"),
        ("EU",            "North", "Jul–Jun", "Jul–Jun"),
        ("Russia",        "North", "Jul–Jun", "Jul–Jun"),
        ("Ukraine",       "North", "Jul–Jun", "Jul–Jun"),
        ("India",         "North", "Apr–Mar", "Apr–Mar"),
        ("China",         "North", "Jun–May", "Jun–May"),
        ("Argentina",     "South", "Dec–Nov", "Dec–Nov"),
        ("Australia",     "South", "Dec–Nov", "Dec–Nov"),
        ("Brazil",        "South", "Oct–Sep", "Oct–Sep"),
        ("Total Non-US",  "—",     "Jul–Jun",  "Jul–Jun"),
        ("Major Exporters","—",    "Jul–Jun",  "Jul–Jun"),
    ])

    corn_html = _ref_table("🌽 Corn Marketing Year Structure", [
        ("United States",         "North", "Oct–Sep", "Sep–Aug"),
        ("Brazil",                "South", "Oct–Sep", "Mar–Feb"),
        ("Argentina",             "South", "Oct–Sep", "Mar–Feb"),
        ("Ukraine",               "North", "Oct–Sep", "Oct–Sep"),
        ("Total Non-US",          "—",     "Oct–Sep", "Oct–Sep"),
        ("Major Exporters",       "—",     "Oct–Sep", "Oct–Sep"),
    ])

    soy_html = _ref_table("🫘 Soybeans Marketing Year Structure", [
        ("United States",                              "North", "Oct–Sep", "Sep–Aug"),
        ("Brazil",                                     "South", "Oct–Sep", "Apr–Mar"),
        ("Argentina",                                  "South", "Oct–Sep", "Apr–Mar"),
        ("Total Non-US",                               "—",     "Oct–Sep", "Oct–Sep"),
        ("Major Exporters",                            "—",     "Oct–Sep", "Oct–Sep"),
        ("China Imports",                              "North", "Oct–Sep", "Oct–Sep"),
    ])

    meal_html = _ref_table("🌾 Soybean Meal Marketing Year Structure", [
        ("United States",   "North", "Oct–Sep", "Sep–Aug"),
        ("Brazil",          "South", "Oct–Sep", "Apr–Mar"),
        ("Argentina",       "South", "Oct–Sep", "Apr–Mar"),
        ("Total Non-US",    "—",     "Oct–Sep", "Oct–Sep"),
        ("Major Exporters", "—",     "Oct–Sep", "Oct–Sep"),
    ])

    st.markdown(wheat_html + corn_html + soy_html + meal_html, unsafe_allow_html=True)

    # ── USDA PSD link ─────────────────────────────────────────────────────
    st.markdown(f"""
    <div style="background:#1a1e22;border:1px solid #2e353d;
                border-left:4px solid {JSA_CYAN};border-radius:6px;
                padding:12px 18px;font-family:Arial;margin-top:4px;">
      <span style="color:#fff;font-weight:700;font-size:12.5px;">
        🌐 USDA PSD Database
      </span>
      <span style="color:#8a9aaa;font-size:12px;margin:0 12px;">
        — Authoritative source for marketing year definitions &amp; export data.
      </span>
      <a href="https://apps.fas.usda.gov/psdonline/app/index.html#/app/downloads"
         target="_blank"
         style="color:{JSA_CYAN};font-weight:600;font-size:12px;text-decoration:none;">
        Open USDA PSD App ↗
      </a>
    </div>
    """, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# US EXPORT INSPECTIONS TAB  (USDA FGIS "Grain Inspections", weekly)
#   Source: USDA AMS AgTransport Socrata dataset sruw-w49i = Federal Grain Inspection Service export inspections,
#   one row per certificate: week ending (Thursday), certificate month/year, carrier, class, destination, port,
#   region, tonnes, pounds. Layout follows jpsi.com/export-inspections-dashboard (filters, pie, origin, destination,
#   weekly series, multi-year overlay; its destination map is left out) plus this dashboard's pace comparisons and unit toggle.
#   Marketing years split a week that straddles the 1st by certificate date (as jpsi.com does), so MYTD = USDA's.
# ─────────────────────────────────────────────────────────────────────────────

# label -> (dataset grain, marketing-year start month, pounds per bushel or None when USDA defines none)
_INSP_GRAINS = {
    "Corn":      ("CORN",      9, 56),
    "Soybeans":  ("SOYBEANS",  9, 60),
    "Wheat":     ("WHEAT",     6, 60),
    "Sorghum":   ("SORGHUM",   9, 56),
    "Barley":    ("BARLEY",    6, 48),
    "Oats":      ("OATS",      6, 32),
    "Canola":    ("CANOLA",    8, 50),
    "Sunflower": ("SUNFLOWER", 9, None),
    "Flaxseed":  ("FLAXSEED",  6, 56),
}
_INSP_LBS_PER_MT = 2204.62262
_INSP_REGION = {"GULF": "Gulf", "PACIFIC": "Pacific", "INTERIOR": "Interior", "ATLANTIC": "Atlantic",
                "LAKES": "Great Lakes", "ST LAWR SWY": "St. Lawrence Seaway"}
_INSP_CLASS = {
    "YC": "Yellow", "WHC": "White", "XC": "Mixed", "YSB": "Yellow", "XSB": "Mixed",
    "HRW": "Hard red winter", "HRS": "Hard red spring", "SRW": "Soft red winter", "SWW": "Soft white",
    "DUWH": "Durum", "WW": "White (all)", "HDWH": "Hard white", "XWHT": "Mixed", "UNCL": "Unclassed",
    "S": "Sorghum", "YS": "Yellow", "WHS": "White", "XS": "Mixed", "TANS": "Tan",
    "BAR": "Barley", "MBLY": "Malting barley", "K": "Canola", "O": "Oats", "OATS": "Oats",
}

# FGIS destination label -> display name
_FGIS_DEST_TABLE = """
JAPAN|Japan
CHINA|China
MEXICO|Mexico
KOREA REP|South Korea
TAIWAN|Taiwan
EGYPT|Egypt
USSR|USSR (historical)
COLOMBIA|Colombia
NETHERLANDS|Netherlands
SPAIN|Spain
PHILIPPINES|Philippines
INDONESIA|Indonesia
CANADA|Canada
NIGERIA|Nigeria
VENEZUELA|Venezuela
ALGERIA|Algeria
ISRAEL|Israel
SAUDI ARABIA|Saudi Arabia
ITALY|Italy
PERU|Peru
MOROCCO|Morocco
DOMINICN REP|Dominican Republic
THAILAND|Thailand
GERMANY|Germany
COSTA RICA|Costa Rica
GUATEMALA|Guatemala
PORTUGAL|Portugal
BRAZIL|Brazil
PAKISTAN|Pakistan
BELGIUM|Belgium
EL SALVADOR|El Salvador
TURKEY|Turkey
BANGLADESH|Bangladesh
VIETNAM|Vietnam
HONDURAS|Honduras
IRAQ|Iraq
MALAYSIA|Malaysia
TUNISIA|Tunisia
YEMEN|Yemen
REP S AFRICA|South Africa
PANAMA|Panama
CHILE|Chile
JAMAICA|Jamaica
RUSSIA|Russia
UN KINGDOM|United Kingdom
ECUADOR|Ecuador
JORDAN|Jordan
SYRIA|Syria
ETHIOPIA|Ethiopia
SRI LANKA|Sri Lanka
TRINIDAD|Trinidad and Tobago
CUBA|Cuba
FRANCE|France
NICARAGUA|Nicaragua
SUDAN|Sudan
GERM FR (W)|West Germany (historical)
INDIA|India
IRAN|Iran
LEBANON|Lebanon
POLAND|Poland
KENYA|Kenya
MOZAMBIQUE|Mozambique
ROMANIA|Romania
HAITI|Haiti
GERM DR (E)|East Germany (historical)
GREECE|Greece
YUGOSLAVIA|Yugoslavia (historical)
BULGARIA|Bulgaria
NORWAY|Norway
GHANA|Ghana
UZBEKISTAN|Uzbekistan
UN ARAB EM|United Arab Emirates
CYPRUS|Cyprus
BARBADOS|Barbados
IRELAND|Ireland
SINGAPORE|Singapore
ARGENTINA|Argentina
BOLIVIA|Bolivia
HONG KONG|Hong Kong
UKRAINE|Ukraine
DENMARK|Denmark
ZAIRE|DR Congo
NORTH KOREA|North Korea
ERITREA|Eritrea
GEORGIA|Georgia
SOMALIA|Somalia
GUYANA|Guyana
AFGHANISTAN|Afghanistan
ZIMBABWE|Zimbabwe
LIBYA|Libya
ANGOLA|Angola
KUWAIT|Kuwait
AUSTRALIA|Australia
TANZANIA|Tanzania
NIGER|Niger
FINLAND|Finland
ST. VINCENT|St. Vincent and the Grenadines
ARMENIA|Armenia
CANARY IS|Canary Islands
MALTA|Malta
NEW ZEALAND|New Zealand
DJIBOUTI|Djibouti
SURINAME|Suriname
CAMEROON|Cameroon
ZAMBIA|Zambia
MALAWI|Malawi
BURMA|Myanmar
SENEGAL|Senegal
OMAN|Oman
YEMEN (SANA)|Yemen
TOGO|Togo
BELIZE|Belize
NEPAL|Nepal
KYRGYZSTAN|Kyrgyzstan
UGANDA|Uganda
NAMIBIA|Namibia
MAURITANIA|Mauritania
GRENADA|Grenada
MOLDOVA|Moldova
BOSNIA-HERC|Bosnia and Herzegovina
CONGO (KINS)|DR Congo
CHAD|Chad
BENIN|Benin
SLOVENIA|Slovenia
CONGO (BRAZ)|Republic of the Congo
CAPE VERDE|Cape Verde
LITHUANIA|Lithuania
SIERRA LEONE|Sierra Leone
LIBERIA|Liberia
ESTONIA|Estonia
TURKMENISTAN|Turkmenistan
RWANDA|Rwanda
MONGOLIA|Mongolia
COTE DIVOIRE|Cote d'Ivoire
TAJIKISTAN|Tajikistan
BELARUS|Belarus
NETH ANTIL|Netherlands Antilles
ALBANIA|Albania
HUNGARY|Hungary
ST. LUCIA|St. Lucia
MADAGASCAR|Madagascar
URUGUAY|Uruguay
CROATIA|Croatia
ICELAND|Iceland
SWITZERLAND|Switzerland
BURUNDI|Burundi
GUINEA|Guinea
BOTSWANA|Botswana
MALI|Mali
LATVIA|Latvia
LEE WIND IS|Leeward and Windward Islands
BAHRAIN|Bahrain
YEMEN (ADEN)|Yemen
OTH WST AFR|Other West Africa
MACEDONIA|North Macedonia
SOUTH SUDAN|South Sudan
CAMBODIA|Cambodia
GUADELOUPE|Guadeloupe
BURKINA|Burkina Faso
UNKNOWN|Unknown
GABON|Gabon
ESWATINI|Eswatini
AZERBAIJAN|Azerbaijan
LESOTHO|Lesotho
WEST BANK|West Bank
MAURITIUS|Mauritius
NEW GUINEA|Papua New Guinea
MARTINIQUE|Martinique
S WST AFRICA|South West Africa (historical)
COMOROS|Comoros
CZECH REP|Czech Republic
SWEDEN|Sweden
GAMBIA|Gambia
FR WST IND|French West Indies
QATAR|Qatar
FRENCH POLY|French Polynesia
UPPER VOLTA|Burkina Faso
SO PAC IS|South Pacific Islands
BAHAMAS|Bahamas
MAYOTTE|Mayotte
FR PAC IS|French Pacific Islands
OTHER PAC IS|Other Pacific Islands
CEN AFR REP|Central African Republic
OTH SO ASIA|Other South Asia
VANUATU|Vanuatu
BRUNEI|Brunei
FR GUIANA|French Guiana
AUSTRIA|Austria
BHUTAN|Bhutan
MACAO|Macao
LAOS|Laos
VATICAN CITY|Vatican City
MALDIVES|Maldives
COCOS IS|Cocos Islands
SAO TOME&PR|Sao Tome and Principe
ST.KITTS&NEV|St. Kitts and Nevis
FIJI|Fiji
VIRGIN ISLAN|Virgin Islands
REUNION|Reunion
TOKELAU|Tokelau
"""
_FGIS_DEST = dict(line.split("|") for line in _FGIS_DEST_TABLE.strip().splitlines())


def _insp_first_thu(my: int, start_month: int) -> pd.Timestamp:
    """Week ending (Thursday) of week 1 of a marketing year: the first Thursday on or after its first day."""
    d = pd.Timestamp(year=int(my), month=int(start_month), day=1)
    return d + pd.Timedelta(days=(3 - d.weekday()) % 7)


def _fgis_fetch_raw(grain_code: str) -> pd.DataFrame:
    """One grain's weekly FGIS inspections, grouped by week ending, certificate month, destination, region, port,
    carrier and class, straight from the AgTransport Socrata API (CSV, 50,000 rows a page). Raises on any failure."""
    import io
    import time
    import requests
    url = f"https://{_SOCRATA_HOST}{_SOCRATA_PATH.replace('.json', '.csv')}"
    group = "date, year, month, destination, ams_reg, port, type_carrier_text, class"
    select = "date as week_end, year, month, destination, ams_reg, port, type_carrier_text, class, sum(mt) as mt, sum(pounds) as lbs"
    page, parts, offset = 50_000, [], 0
    while True:
        params = {"$select": select, "$where": f"grain='{grain_code}'", "$group": group, "$order": group,
                  "$limit": str(page), "$offset": str(offset)}
        for attempt in (1, 2, 3):
            try:
                r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=(10, 120))
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(2 * attempt)
        part = pd.read_csv(io.StringIO(r.text))
        parts.append(part)
        if len(part) < page:
            break
        offset += page
    return pd.concat(parts, ignore_index=True)


def _fgis_prepare(raw: pd.DataFrame, start_month: int) -> pd.DataFrame:
    """Raw grouped rows -> week_end, my (marketing-year start year), my_week, dest, region, port, carrier,
    cls, mt, lbs. A marketing year runs from its start month by certificate date; its week n ends on the first
    Thursday on or after the 1st plus 7 x (n-1) days, so a straddling week is split between two years."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw.copy()
    df["week_end"] = pd.to_datetime(df["week_end"], errors="coerce").dt.normalize()
    for c in ("year", "month"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["mt"] = pd.to_numeric(df["mt"], errors="coerce").fillna(0.0)
    lbs = pd.to_numeric(df["lbs"], errors="coerce")
    df["lbs"] = lbs.where(lbs > 0, df["mt"] * _INSP_LBS_PER_MT)        # a handful of old rows carry no pounds
    df = df.dropna(subset=["week_end", "year", "month"])
    year, month = df["year"].astype(int), df["month"].astype(int)
    df["my"] = np.where(month >= start_month, year, year - 1)
    first = pd.to_datetime({"year": df["my"], "month": start_month, "day": 1})
    first_thu = first + pd.to_timedelta((3 - first.dt.weekday) % 7, unit="D")
    df["my_week"] = ((df["week_end"] - first_thu).dt.days // 7) + 1
    df = df[df["my_week"] >= 1].copy()
    dest = df["destination"].fillna("UNKNOWN").astype(str).str.upper().str.strip()
    df["dest"] = dest.map(lambda k: _FGIS_DEST.get(k, k.title()))
    reg = df["ams_reg"].fillna("").astype(str).str.upper().str.strip()
    df["region"] = reg.map(lambda k: _INSP_REGION.get(k, k.title() or "Other"))
    df["port"] = df["port"].fillna("").astype(str).str.strip().str.title().replace("", "Unknown")
    df["carrier"] = df["type_carrier_text"].fillna("OTHER").astype(str).str.strip().str.title()
    cls = df["class"].fillna("").astype(str).str.upper().str.strip()
    df["cls"] = cls.map(lambda k: _INSP_CLASS.get(k, k.title() if k else "Unclassed"))
    return df[["week_end", "my", "my_week", "dest", "region", "port", "carrier", "cls", "mt", "lbs"]].reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_inspections(grain_code: str, start_month: int) -> pd.DataFrame:
    """Prepared weekly inspections for one grain. Raises on a failed fetch, so st.cache_data never keeps an empty
    frame for an hour after a bad Socrata moment."""
    return _fgis_prepare(_fgis_fetch_raw(grain_code), start_month)


# ── presentation: the shared light theme (CHART THEME, above) plus this tab's own colour maps ──
_INSP_SURF, _INSP_BORDER, _INSP_TEXT, _INSP_MUTED, _INSP_SAGE = _CH_SURF, _CH_BORDER, _CH_TEXT, _CH_MUTED, _CH_SAGE
_INSP_BLUE, _INSP_TERRA, _INSP_LAV, _INSP_GOLD, _INSP_MIST = _CH_BLUE, _CH_TERRA, _CH_LAV, _CH_GOLD, _CH_MIST
_INSP_POS, _INSP_NEG = _CH_POS, _CH_NEG
_INSP_PAL = _CH_PAL[:8]
_INSP_REGION_COLOR = {"Gulf": _INSP_BLUE, "Pacific": _INSP_SAGE, "Interior": _INSP_TERRA, "Atlantic": _INSP_LAV,
                      "Great Lakes": _INSP_GOLD, "St. Lawrence Seaway": "#8fa398"}
_INSP_CARRIER_COLOR = {"Ship": _INSP_BLUE, "Rail": _INSP_TERRA, "Container": _INSP_LAV, "Barge": _INSP_SAGE,
                       "Truck": _INSP_GOLD, "Other": _INSP_MIST}
_INSP_SEG_COL = {"Origin": "region", "Destination": "dest", "Carrier": "carrier"}
_INSP_CSS = """
<style>
.insp-sec { color:#5f7267; font-size:.7rem; text-transform:uppercase; letter-spacing:.1em; padding:10px 0 4px;
            border-bottom:1px solid #d7e2dc; margin:24px 0 12px; }
.insp-tile { background:#fff; border:1px solid #d7e2dc; border-top:3px solid #5e7164; border-radius:10px;
             padding:14px 16px 12px; min-height:164px; }
.insp-tile-label { color:#5f7267; font-size:.68rem; text-transform:uppercase; letter-spacing:.09em; margin-bottom:6px; }
.insp-tile-value { color:#32373c; font-size:1.65rem; font-weight:700; line-height:1.1; }
.insp-tile-unit { color:#5f7267; font-size:.8rem; font-weight:600; margin-left:5px; }
.insp-line { color:#5f7267; font-size:.78rem; margin-top:5px; }
.insp-note { color:#5f7267; font-size:.78rem; line-height:1.5; margin-top:8px; }
/* the filter panel of the Inspections and Export Sales tabs: always open, tinted, with a sage spine */
.st-key-insp_filters, .st-key-esr_filters { background:#f3f7f4; border:1px solid #c9d8cf !important;
    border-left:5px solid #5e7164 !important; border-radius:10px; padding:2px 8px 8px; margin-bottom:4px; }
.st-key-insp_reset, .st-key-esr_reset { display:flex; justify-content:flex-end; }
.fp-title { color:#32373c; font-size:.92rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
.fp-sub { color:#5f7267; font-size:.82rem; margin-left:12px; }
.fp-group { color:#5f7267; font-size:.74rem !important; font-weight:700; letter-spacing:.1em; text-transform:uppercase; margin:8px 0 calc(1rem - 8px) !important; }
.fp-chips { margin:0 0 1rem !important; line-height:1.9; }
.fp-lead, .fp-none { color:#5f7267; font-size:.8rem; margin-right:8px; }
.fp-none { font-style:italic; }
.fp-chip { display:inline-block; background:#e1ece5; color:#2d4636; border:1px solid #b9cfc0; border-radius:999px;
    padding:1px 11px; margin:2px 6px 2px 0; font-size:.8rem; font-weight:600; }
/* the Export Sales summary table */
.esr-wrap { overflow-x:auto; }
.esr-sum { width:100%; border-collapse:collapse; font-size:.86rem; }
.esr-sum th { color:#5f7267; font-size:.66rem; text-transform:uppercase; letter-spacing:.06em; font-weight:600; text-align:right;
    padding:8px 10px; border-bottom:2px solid #d7e2dc; white-space:nowrap; }
.esr-sum td { text-align:right; padding:7px 10px; border-bottom:1px solid #e8eeea; color:#32373c; font-variant-numeric:tabular-nums; white-space:nowrap; }
.esr-sum th:first-child, .esr-sum td:first-child { text-align:left; }
.esr-sum tr.cur td { background:#eef4f0; font-weight:700; }
.esr-sum tr.chg td { color:#5f7267; }
.esr-sum tr.gap td { padding:3px; border:0; }
.esr-sum tr td.pos { color:#16a34a; font-weight:600; }
.esr-sum tr td.neg { color:#dc2626; font-weight:600; }
</style>
"""
_INSP_RESET_KEYS = ("insp_my_", "insp_week_", "insp_view", "insp_cls_", "insp_car_", "insp_reg_", "insp_dst_", "insp_seg")


def _fp_reset(prefixes: tuple) -> None:
    """on_click of a 'Reset filters' button: forget the widget state kept under these key prefixes, so every filter is back
    at its default on the rerun this click triggers."""
    for k in [k for k in st.session_state.keys() if str(k).startswith(prefixes)]:
        del st.session_state[k]


def _fp_group(text: str) -> None:
    st.markdown(f'<p class="fp-group">{text}</p>', unsafe_allow_html=True)


def _fp_chips(chips: list, none_text: str) -> str:
    """What is narrowing the page right now, as pills; one quiet line when nothing is."""
    if not chips:
        return f'<p class="fp-chips"><span class="fp-none">{_html_escape(none_text)}</span></p>'
    return ('<p class="fp-chips"><span class="fp-lead">Narrowed to</span>'
            + "".join(f'<span class="fp-chip">{_html_escape(c)}</span>' for c in chips) + "</p>")


def _insp_pct(a, b):
    if a is None or b is None or pd.isna(a) or pd.isna(b) or b == 0:
        return None
    return (a / b - 1.0) * 100.0


def _insp_fmt(v, unit: str) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:,.{_insp_dec(unit, v)}f}"


def _insp_dec(unit: str, ref) -> int:
    """Decimals that suit the size of the numbers: volumes run from thousands of TMT down to a few kilograms of oats."""
    ref = 0.0 if ref is None or pd.isna(ref) else abs(float(ref))
    steps = ((100, 0), (1, 1), (0.1, 2), (0.01, 3)) if unit == "TMT" else ((10, 1), (1, 2), (0.1, 3), (0.01, 4))
    return next((d for floor, d in steps if ref >= floor), steps[-1][1] + 1 if ref > 0 else steps[1][1])


def _insp_plot(fig: go.Figure, key: str) -> None:
    """st.plotly_chart across Streamlit versions: width="stretch" where it exists, else use_container_width."""
    try:
        st.plotly_chart(fig, width="stretch", theme=None, key=key)
    except TypeError:
        st.plotly_chart(fig, use_container_width=True, theme=None, key=key)


def _insp_table(data, height: int) -> None:
    try:
        st.dataframe(data, width="stretch", height=height)
    except TypeError:
        st.dataframe(data, use_container_width=True, height=height)


def _insp_sec(text: str) -> None:
    st.markdown(f'<div class="insp-sec">{text}</div>', unsafe_allow_html=True)


def _insp_delta_line(p, text: str) -> str:
    if p is None:
        return f'<div class="insp-line">— {text}</div>'
    col, arrow = (_INSP_POS, "▲") if p >= 0 else (_INSP_NEG, "▼")
    return f'<div class="insp-line"><b style="color:{col}">{arrow} {abs(p):.1f}%</b> {text}</div>'


def _insp_tile(label: str, value: str, unit: str, lines: list) -> str:
    return (f'<div class="insp-tile"><div class="insp-tile-label">{label}</div>'
            f'<div class="insp-tile-value">{value}<span class="insp-tile-unit">{unit}</span></div>'
            f'{"".join(lines)}</div>')


def _insp_style(fig: go.Figure, height: int, title: str | None = None, subtitle: str | None = None,
                legend: bool = True, hover: str = "x unified", logo: str | None = None,
                x_title: str | None = None, y_title: str | None = None) -> go.Figure:
    """This tab's charts: the shared light layout with the legend laid along the top, and the 50-year mark when asked."""
    fig.update_layout(**_ch_layout(title, subtitle, x_title, y_title, height=height, legend=legend, hover=hover,
                                   margin=dict(l=10, r=10, t=(88 if subtitle else 64) if title else 24, b=10)))
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                                  bgcolor="rgba(0,0,0,0)", bordercolor="rgba(0,0,0,0)"))
    if logo:
        _add_chart_watermark(fig)
    return fig


def _insp_seg_labels(frame: pd.DataFrame, seg: str, top: int = 7) -> pd.Series:
    """The stacking label of each row: region / carrier as they are, destinations folded to a top few plus 'Other'."""
    s = frame[_INSP_SEG_COL[seg]]
    if seg == "Destination":
        keep = frame.groupby("dest")["v"].sum().nlargest(top).index
        s = s.where(s.isin(keep), "Other")
    return s


def _insp_seg_color(name: str, seg: str, i: int) -> str:
    if name == "Other":
        return _INSP_MIST
    if seg == "Origin":
        return _INSP_REGION_COLOR.get(name, _INSP_PAL[i % len(_INSP_PAL)])
    if seg == "Carrier":
        return _INSP_CARRIER_COLOR.get(name, _INSP_PAL[i % len(_INSP_PAL)])
    return _INSP_PAL[i % len(_INSP_PAL)]


def _insp_matrices(df_all: pd.DataFrame, df_f: pd.DataFrame):
    """Weekly (M) and cumulative (C) volume, one row per marketing year, columns = marketing-year week. Weeks a year
    never reached are NaN; weeks it reached with nothing inspected (after filtering) are 0."""
    last_w = df_all.groupby("my")["my_week"].max().sort_index()
    cols = list(range(1, int(last_w.max()) + 1))
    wk = df_f.groupby(["my", "my_week"])["v"].sum().unstack("my_week")
    M = wk.reindex(index=last_w.index, columns=cols)
    in_range = pd.DataFrame(np.arange(1, len(cols) + 1)[None, :] <= last_w.to_numpy()[:, None],
                            index=last_w.index, columns=cols)
    M = M.where(~in_range | M.notna(), 0.0).where(in_range)
    C = M.fillna(0.0).cumsum(axis=1).where(in_range)
    return M, C


def _insp_olympic(mat: pd.DataFrame, years: list, week: int):
    return olympic_avg([float(mat.at[y, week]) for y in years if week in mat.columns and pd.notna(mat.at[y, week])])


def _insp_month_ticks(first_thu: pd.Timestamp, n_weeks: int):
    """Marketing-year week numbers that open a calendar month, labelled with the month, for the week-number axis."""
    vals, text, prev = [], [], None
    for n in range(1, n_weeks + 1):
        d = first_thu + pd.Timedelta(weeks=n - 1)
        if d.month != prev:
            vals.append(n)
            text.append(f"{d:%b}")
            prev = d.month
    return vals, text


def _insp_fig_weekly(dff, M, C, sel_my, sel_week, last_w, first_thu, view, seg, unit, grain, my_label, logo):
    """Weekly bars (or cumulative lines) for the selected marketing year, against last year and the 6-year Olympic
    average; stacked by the chosen segment."""
    weeks = list(range(1, last_w + 1))
    dates = [first_thu + pd.Timedelta(weeks=n - 1) for n in weeks]
    mat = M if view == "Weekly" else C
    prior6 = [y for y in mat.index if y < sel_my][-6:]
    cur_row = mat.loc[sel_my].reindex(weeks)
    ly_row = mat.loc[sel_my - 1].reindex(weeks) if (sel_my - 1) in mat.index else pd.Series(np.nan, index=weeks)
    oly = [_insp_olympic(mat, prior6, n) for n in weeks]
    dec = _insp_dec(unit, cur_row.max())
    hov = lambda name: f"Wk %{{customdata}}: %{{y:,.{dec}f}} {unit}<extra>{name}</extra>"
    cur = dff[dff["my"] == sel_my]
    fig = go.Figure()

    if view == "Weekly":
        if seg == "None":
            fig.add_trace(go.Bar(x=dates, y=cur_row.fillna(0.0), name=my_label, customdata=weeks, hovertemplate=hov(my_label),
                                 marker_color=["#3b7fa3" if n == sel_week else _INSP_BLUE for n in weeks]))
        else:
            labels = _insp_seg_labels(cur, seg)
            pv = (cur.assign(seg=labels).groupby(["seg", "my_week"])["v"].sum().unstack("my_week")
                  .reindex(columns=weeks).fillna(0.0))
            for i, name in enumerate(pv.sum(axis=1).sort_values(ascending=False).index):
                fig.add_trace(go.Bar(x=dates, y=pv.loc[name], name=name, customdata=weeks, hovertemplate=hov(name),
                                     marker_color=_insp_seg_color(name, seg, i)))
            fig.update_layout(barmode="stack")
        fig.add_trace(go.Scatter(x=dates, y=ly_row, mode="lines", name="Last year", customdata=weeks,
                                 line=dict(color=_INSP_TERRA, width=1.8), hovertemplate=hov("Last year")))
        fig.add_trace(go.Scatter(x=dates, y=oly, mode="lines", name="6-yr Olympic avg", customdata=weeks,
                                 line=dict(color=_INSP_SAGE, width=2.2, dash="dash"), hovertemplate=hov("6-yr Olympic avg")))
        sub = "Weekly, week ending Thursday. Last year and the 6-year Olympic average are the same marketing-year week."
    else:
        rng = [i for i, n in enumerate(weeks)
               if sum(1 for y in prior6 if n in C.columns and pd.notna(C.at[y, n])) >= 2]
        if rng:
            hi = [max(C.at[y, weeks[i]] for y in prior6 if pd.notna(C.at[y, weeks[i]])) for i in rng]
            lo = [min(C.at[y, weeks[i]] for y in prior6 if pd.notna(C.at[y, weeks[i]])) for i in rng]
            xs = [dates[i] for i in rng]
            fig.add_trace(go.Scatter(x=xs + xs[::-1], y=hi + lo[::-1], fill="toself", mode="lines", line=dict(width=0),
                                     fillcolor=_ch_rgba(_INSP_SAGE, 0.13), name="6-yr range", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=dates, y=oly, mode="lines", name="6-yr Olympic avg", customdata=weeks,
                                 line=dict(color=_INSP_SAGE, width=2.2, dash="dash"), hovertemplate=hov("6-yr Olympic avg")))
        fig.add_trace(go.Scatter(x=dates, y=ly_row, mode="lines", name="Last year", customdata=weeks,
                                 line=dict(color=_INSP_TERRA, width=1.8), hovertemplate=hov("Last year")))
        if seg == "None":
            fig.add_trace(go.Scatter(x=dates, y=cur_row, mode="lines", name=my_label, customdata=weeks,
                                     line=dict(color=_INSP_BLUE, width=3.4), hovertemplate=hov(my_label)))
        else:
            labels = _insp_seg_labels(cur, seg)
            pv = (cur.assign(seg=labels).groupby(["seg", "my_week"])["v"].sum().unstack("my_week")
                  .reindex(columns=weeks).fillna(0.0).cumsum(axis=1))
            for i, name in enumerate(pv.iloc[:, -1].sort_values(ascending=False).index):
                col = _insp_seg_color(name, seg, i)
                fig.add_trace(go.Scatter(x=dates, y=pv.loc[name], mode="lines", name=name, stackgroup="seg",
                                         customdata=weeks, hovertemplate=hov(name), line=dict(width=0.6, color=col),
                                         fillcolor=_ch_rgba(col, 0.75)))
        sub = "Marketing year to date by week ending Thursday, against last year and the 6-year range."
    if sel_week < last_w:
        x = dates[sel_week - 1].strftime("%Y-%m-%d")
        fig.add_shape(type="line", x0=x, x1=x, y0=0, y1=1, xref="x", yref="paper",
                      line=dict(color=_INSP_SAGE, width=1, dash="dot"))
    word = "weekly" if view == "Weekly" else "cumulative"
    _insp_style(fig, 400, f"{grain} export inspections, {my_label}: {word} volume ({unit})", sub, logo=logo,
                y_title=unit if view == "Weekly" else f"Cumulative {unit}")
    if last_w <= 14:                                        # early in a marketing year: a tick for every week
        fig.update_xaxes(tickformat="%b %d", dtick=7 * 86_400_000, tickmode="linear", tick0=dates[0].strftime("%Y-%m-%d"),
                         hoverformat="%b %d, %Y")
    else:
        fig.update_xaxes(tickformat="%b %y", dtick="M1", hoverformat="%b %d, %Y")
    fig.update_yaxes(tickformat=",")
    return fig


def _insp_fig_multi(M, C, sel_my, view, years_mode, smooth, first_thu, unit, grain, start_month, logo):
    """One line per marketing year over the marketing-year weeks: the selected year in blue, last year terracotta,
    older years in sage shades (darker = more recent), plus the 6-year Olympic average."""
    mat = M if view == "Weekly" else C
    prior = [y for y in mat.index if y < sel_my]
    pick = {"5 prior years": prior[-5:], "10 prior years": prior[-10:], "All years": prior}.get(years_mode, prior[-5:]) + [sel_my]
    weeks = list(mat.columns)
    prior6 = [y for y in mat.index if y < sel_my][-6:]
    fig = go.Figure()
    older = sorted(y for y in pick if y not in (sel_my, sel_my - 1))
    dec = _insp_dec(unit, mat.loc[sel_my].max())
    for y in sorted(pick):
        row = mat.loc[y].dropna()
        if view == "Weekly" and smooth:
            row = row.rolling(4, min_periods=1).mean()
        lbl = f"{y}/{str(y + 1)[-2:]}"
        if y == sel_my:
            style = dict(color=_INSP_BLUE, width=3.6)
        elif y == sel_my - 1:
            style = dict(color=_INSP_TERRA, width=2.2)
        else:
            style = dict(color=_ch_mix("#d7e2dc", "#5f7267", older.index(y) / max(len(older) - 1, 1)), width=1.3)
        fig.add_trace(go.Scatter(x=row.index, y=row.values, mode="lines", name=lbl, line=style,
                                 hovertemplate=f"Wk %{{x}}: %{{y:,.{dec}f}} {unit}<extra>{lbl}</extra>"))
    oly = [_insp_olympic(mat, prior6, n) for n in weeks]
    if view == "Weekly" and smooth:
        oly = pd.Series(oly, dtype="float").rolling(4, min_periods=1).mean().tolist()
    fig.add_trace(go.Scatter(x=weeks, y=oly, mode="lines", name="6-yr Olympic avg", line=dict(color=_INSP_SAGE, width=2.2, dash="dash"),
                             hovertemplate=f"Wk %{{x}}: %{{y:,.{dec}f}} {unit}<extra>6-yr Olympic avg</extra>"))
    vals, text = _insp_month_ticks(first_thu, len(weeks))
    word = "weekly (4-week average)" if (view == "Weekly" and smooth) else ("weekly" if view == "Weekly" else "cumulative")
    _insp_style(fig, 420, f"{grain} export inspections by marketing-year week: {word} ({unit})",
                f"Marketing years overlaid; months on the axis follow {sel_my}/{str(sel_my + 1)[-2:]}.", logo=logo,
                x_title="Marketing-year week", y_title=unit if view == "Weekly" else f"Cumulative {unit}")
    fig.update_xaxes(tickmode="array", tickvals=vals, ticktext=[f"{t}" for t in text], range=[0.5, len(weeks) + 0.5])
    fig.update_yaxes(tickformat=",")
    return fig


def _insp_fig_hbar(frame, by, seg, top_n, unit, title, subtitle, logo=None):
    """Horizontal bars of volume by `by` (a column of `frame`), largest on top, stacked by the segment dimension
    unless that is the same thing as `by`."""
    tot = frame.groupby(by)["v"].sum().sort_values(ascending=False).head(top_n)
    tot = tot[tot > 0]
    if tot.empty:
        return None
    order = list(tot.index[::-1])
    sub_frame = frame[frame[by].isin(order)]
    seg_on = seg != "None" and _INSP_SEG_COL.get(seg) != by
    fig = go.Figure()
    if seg_on:
        labels = _insp_seg_labels(sub_frame, seg, top=6)
        pv = sub_frame.assign(seg=labels).groupby([by, "seg"])["v"].sum().unstack("seg").reindex(order).fillna(0.0)
        for i, name in enumerate(pv.sum(axis=0).sort_values(ascending=False).index):
            fig.add_trace(go.Bar(x=pv[name], y=pv.index, orientation="h", name=name, marker_color=_insp_seg_color(name, seg, i),
                                 hovertemplate=f"%{{y}}: %{{x:,.1f}} {unit}<extra>{name}</extra>"))
        fig.update_layout(barmode="stack")
    else:
        if by == "region":
            colors = [_INSP_REGION_COLOR.get(o, _INSP_BLUE) for o in order]
        elif by == "port":                                  # a port wears its region's colour
            reg = frame.drop_duplicates("port").set_index("port")["region"]
            colors = [_INSP_REGION_COLOR.get(reg.get(o), _INSP_BLUE) for o in order]
        else:
            colors = _INSP_BLUE
        fig.add_trace(go.Bar(x=[tot[o] for o in order], y=order, orientation="h", marker_color=colors, name="Volume",
                             hovertemplate=f"%{{y}}: %{{x:,.1f}} {unit}<extra></extra>"))
    fig.add_trace(go.Scatter(x=[tot[o] for o in order], y=order, mode="text", text=[_insp_fmt(tot[o], unit) for o in order],
                             textposition="middle right", textfont=dict(size=11, color=_INSP_MUTED), showlegend=False,
                             hoverinfo="skip", cliponaxis=False))
    _insp_style(fig, max(300, 27 * len(order) + 110), title, subtitle, legend=seg_on, hover="closest", logo=logo,
                x_title=unit)
    fig.update_xaxes(range=[0, float(tot.max()) * 1.2], tickformat=",")
    fig.update_yaxes(showgrid=False, automargin=True)
    return fig


def _insp_fig_donut(frame, by, unit, title, subtitle, colors=None, top_n=8, min_label=0.0):
    tot = frame.groupby(by)["v"].sum().sort_values(ascending=False)
    tot = tot[tot > 0]
    if tot.empty:
        return None
    if len(tot) > top_n:
        tot = pd.concat([tot.head(top_n), pd.Series({"Other": tot.iloc[top_n:].sum()})])
    cols = [(colors or {}).get(n) or (_INSP_MIST if n == "Other" else _INSP_PAL[i % len(_INSP_PAL)])
            for i, n in enumerate(tot.index)]
    fig = go.Figure(go.Pie(labels=list(tot.index), values=tot.values, hole=0.56, sort=False, direction="clockwise",
                           marker=dict(colors=cols, line=dict(color="#ffffff", width=2)),
                           textinfo="label+percent" if not min_label else "text", textposition="outside",
                           text=None if not min_label else [f"{n}<br>{v / tot.sum() * 100:.0f}%" if v / tot.sum() * 100 >= min_label else ""
                                                            for n, v in tot.items()],
                           textfont=dict(size=11, color=_INSP_TEXT),
                           hovertemplate=f"%{{label}}: %{{value:,.1f}} {unit} (%{{percent}})<extra></extra>"))
    fig.add_annotation(text=f"<b>{_insp_fmt(float(tot.sum()), unit)}</b><br><span style='font-size:11px'>{unit}</span>",
                       x=0.5, y=0.5, showarrow=False, font=dict(size=16, color=_INSP_TEXT))
    _insp_style(fig, 360, title, subtitle, legend=False, hover="closest")
    fig.update_layout(margin=dict(l=40, r=40, t=96, b=20))
    return fig


def _insp_render(use_bushels: bool) -> None:
    st.markdown(_INSP_CSS, unsafe_allow_html=True)
    logo = _load_logo_b64(LOGO_50YR_PATH)

    # ── filters: always on show, and they apply to the tiles, the table and every chart ──────────────────────────────
    with st.container(border=True, key="insp_filters"):
        top_l, top_r = st.columns([5, 1.1])
        top_l.markdown('<span class="fp-title">Filters</span><span class="fp-sub">apply to every tile, chart and table below</span>',
                       unsafe_allow_html=True)
        top_r.button("Reset filters", key="insp_reset", on_click=_fp_reset, args=(_INSP_RESET_KEYS,))
        _fp_group("What to show")
        c_grain, c_my, c_wk, c_view = st.columns([1.1, 1.1, 3.0, 1.4])
        with c_grain:
            grain = st.selectbox("Grain", list(_INSP_GRAINS), key="insp_grain")
        code, start_month, lb = _INSP_GRAINS[grain]
        try:
            with st.spinner(f"Loading {grain.lower()} inspections…"):
                df = _load_inspections(code, start_month)
        except Exception as e:
            st.error(f"**The FGIS inspections could not be read.** `{e}` Click **Refresh Data** above to try again.")
            return
        if df.empty:
            st.info(f"No {grain.lower()} export inspections are on file.")
            return

        years = sorted((int(y) for y in df["my"].unique()), reverse=True)
        my_name = {y: f"{y}/{str(y + 1)[-2:]}" for y in years}
        with c_my:
            picked = st.selectbox("Marketing year", [my_name[y] for y in years], key=f"insp_my_{code}")
        sel_my = next(y for y in years if my_name[y] == picked)
        my_label = my_name[sel_my]
        last_w = int(df.loc[df["my"] == sel_my, "my_week"].max())
        first_thu = _insp_first_thu(sel_my, start_month)

        def week_end(n) -> pd.Timestamp:
            return first_thu + pd.Timedelta(weeks=int(n) - 1)

        with c_wk:
            if last_w > 1:
                sel_week = int(st.select_slider("Week (ending Thursday)", options=list(range(1, last_w + 1)), value=last_w,
                                                format_func=lambda n: f"Wk {n} · {week_end(n):%b %d}",
                                                key=f"insp_week_{code}_{sel_my}"))
            else:
                sel_week = 1                               # a slider needs two positions: only one week is in so far
                st.caption(f"Week 1 · {week_end(1):%b %d}: the only week reported so far in {my_label}.")
        with c_view:
            view = st.segmented_control("View", ["Weekly", "Cumulative"], default="Weekly", key="insp_view") or "Weekly"

        _fp_group("Narrow it down")
        by_dest = df.groupby("dest")["mt"].sum().sort_values(ascending=False)
        f1, f2, f3, f4 = st.columns(4)
        sel_cls = f1.multiselect("Class", sorted(df["cls"].unique()), key=f"insp_cls_{code}", placeholder="All classes")
        sel_car = f2.multiselect("Carrier", sorted(df["carrier"].unique()), key=f"insp_car_{code}", placeholder="All carriers")
        sel_reg = f3.multiselect("Origin region", sorted(df["region"].unique()), key=f"insp_reg_{code}", placeholder="All regions")
        sel_dst = f4.multiselect("Destination", list(by_dest.index), key=f"insp_dst_{code}", placeholder="All destinations")
        sg, _ = st.columns([2.4, 2.6])
        seg = sg.segmented_control("Split the charts by", ["None", "Origin", "Destination", "Carrier"], default="None",
                                   key="insp_seg") or "None"
        chips = [f"{lab}: {', '.join(chosen[:4])}" + (f" +{len(chosen) - 4} more" if len(chosen) > 4 else "") for lab, chosen in
                 (("Class", sel_cls), ("Carrier", sel_car), ("Origin", sel_reg), ("Destination", sel_dst)) if chosen]
        st.markdown(_fp_chips(chips, "No narrowing: every certificate is counted"), unsafe_allow_html=True)

    mask = pd.Series(True, index=df.index)
    for col, chosen in (("cls", sel_cls), ("carrier", sel_car), ("region", sel_reg), ("dest", sel_dst)):
        if chosen:
            mask &= df[col].isin(chosen)
    dff = df[mask].copy()

    bu_ok = use_bushels and lb is not None
    unit = "Mbu" if bu_ok else "TMT"
    dff["v"] = dff["lbs"] / lb / 1e6 if bu_ok else dff["mt"] / 1000.0
    notes = [f"Latest week in the data: week ending {df['week_end'].max():%a %b %d, %Y} (USDA FGIS posts each week on Monday)."]
    if use_bushels and lb is None:
        notes.append(f"USDA defines no bushel weight for {grain.lower()}, so it stays in TMT.")
    st.markdown(
        f'<div style="margin:14px 0 4px;padding-bottom:8px;border-bottom:2px solid {JSA_CYAN};font-family:Arial,sans-serif;'
        f'display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;">'
        f'<span style="font-size:27px;font-weight:700;color:{JSA_DARK};letter-spacing:0.2px;line-height:1.2;">'
        f'United States {_TITLE_COMMODITY.get(grain, grain)} Export Inspections</span>'
        f'<span style="font-size:17px;font-weight:600;color:#6b7280;">({unit})</span></div>',
        unsafe_allow_html=True)
    st.caption(" ".join(notes))

    # ── pace numbers ──────────────────────────────────────────────────────────
    M, C = _insp_matrices(df, dff)
    prior6 = [y for y in M.index if y < sel_my][-6:]

    def cell(mat, y, n):
        return None if (y not in mat.index or n not in mat.columns or pd.isna(mat.at[y, n])) else float(mat.at[y, n])

    w_cur, c_cur = cell(M, sel_my, sel_week), cell(C, sel_my, sel_week)
    w_prev = cell(M, sel_my, sel_week - 1) if sel_week > 1 else None
    w_ly, c_ly = cell(M, sel_my - 1, sel_week), cell(C, sel_my - 1, sel_week)
    w_oly, c_oly = _insp_olympic(M, prior6, sel_week), _insp_olympic(C, prior6, sel_week)

    cur = dff[dff["my"] == sel_my]
    wk = cur[cur["my_week"] == sel_week]
    ytd = cur[cur["my_week"] <= sel_week]
    view_df = wk if view == "Weekly" else ytd

    def top_lines(col):
        s = view_df.groupby(col)["v"].sum().sort_values(ascending=False)
        s = s[s > 0]
        tot = float(s.sum())
        if s.empty or tot <= 0:
            return "—", [f'<div class="insp-line">nothing inspected</div>']
        rest = " · ".join(f"{n} {v / tot * 100:.0f}%" for n, v in s.iloc[1:3].items() if v / tot >= 0.005)
        return s.index[0], [f'<div class="insp-line"><b>{s.iloc[0] / tot * 100:.0f}%</b> of the {"week" if view == "Weekly" else "marketing year to date"}</div>',
                            f'<div class="insp-line">{rest}</div>' if rest else ""]

    t_dest, l_dest = top_lines("dest")
    t_reg, l_reg = top_lines("region")
    tiles = [
        _insp_tile(f"Week {sel_week} · ending {week_end(sel_week):%b %d, %Y}", _insp_fmt(w_cur, unit), unit,
                   [_insp_delta_line(_insp_pct(w_cur, w_prev), "vs prior week"),
                    _insp_delta_line(_insp_pct(w_cur, w_ly), "vs same week last year"),
                    _insp_delta_line(_insp_pct(w_cur, w_oly), "vs 6-yr Olympic avg")]),
        _insp_tile(f"{my_label} to date · through week {sel_week}", _insp_fmt(c_cur, unit), unit,
                   [_insp_delta_line(_insp_pct(c_cur, c_ly), "vs last year"),
                    _insp_delta_line(_insp_pct(c_cur, c_oly), "vs 6-yr Olympic avg")]),
        _insp_tile("Top destination", f'<span style="font-size:1.35rem">{t_dest}</span>', "", l_dest),
        _insp_tile("Top origin region", f'<span style="font-size:1.35rem">{t_reg}</span>', "", l_reg),
    ]
    for col, html in zip(st.columns(4), tiles):
        col.markdown(html, unsafe_allow_html=True)

    when = (f"Week {sel_week} · ending {week_end(sel_week):%b %d, %Y}" if view == "Weekly"
            else f"{my_label} to date · through week {sel_week} ({week_end(sel_week):%b %d, %Y})")

    # ── weekly pace ───────────────────────────────────────────────────────────
    _insp_sec("Weekly pace")
    _insp_plot(_insp_fig_weekly(dff, M, C, sel_my, sel_week, last_w, first_thu, view, seg, unit, grain, my_label, logo),
               "insp_fig_weekly")

    y1, y2, _ = st.columns([2, 1.4, 3])
    with y1:
        years_mode = st.segmented_control("Compare with", ["5 prior years", "10 prior years", "All years"],
                                          default="5 prior years", key="insp_years") or "5 prior years"
    smooth = False
    if view == "Weekly":
        with y2:
            smooth = st.toggle("4-week average", key="insp_smooth", help="Smooths the weekly lines; weekly volumes swing with vessel timing.")
    _insp_plot(_insp_fig_multi(M, C, sel_my, view, years_mode, smooth, first_thu, unit, grain, start_month, logo),
               "insp_fig_multi")

    # ── where it went ─────────────────────────────────────────────────────────
    _insp_sec("Where it went")
    if view_df["v"].sum() <= 0:
        st.info("Nothing was inspected for these filters in the selected period.")
    else:
        s1, _ = st.columns([1.6, 3.4])
        top_n = s1.slider("Destinations shown", 5, 30, 12, key="insp_topn")
        cl, cr = st.columns([3, 2])
        fig = _insp_fig_hbar(view_df, "dest", seg, top_n, unit, f"Top destinations ({unit})", when, logo=logo)
        if fig is not None:
            _insp_plot(fig, "insp_fig_dest")
        fig = _insp_fig_donut(view_df, "dest", unit, "Share by destination", when)
        if fig is not None:
            _insp_plot(fig, "insp_fig_pie")

        # ── where it shipped from ─────────────────────────────────────────────
        _insp_sec("Where it shipped from")
        s2, _ = st.columns([1.6, 3.4])
        with s2:
            level = st.segmented_control("Origin by", ["Region", "Port"], default="Region", key="insp_lvl") or "Region"
        by = "region" if level == "Region" else "port"
        ol, orr = st.columns([3, 2])
        fig = _insp_fig_hbar(view_df, by, seg, 20, unit, f"Inspections by U.S. {level.lower()} ({unit})", when, logo=logo)
        if fig is not None:
            _insp_plot(fig, "insp_fig_origin")
        fig = _insp_fig_donut(view_df, "carrier", unit, "Share by carrier", when, colors=_INSP_CARRIER_COLOR)
        if fig is not None:
            _insp_plot(fig, "insp_fig_carrier")

    # ── detail ────────────────────────────────────────────────────────────────
    _insp_sec("Detail by destination")
    ly_ytd = dff[(dff["my"] == sel_my - 1) & (dff["my_week"] <= sel_week)]
    wk_col, ytd_col, ly_col = f"Week {sel_week}", f"{my_label} to date", "Same period last year"
    tbl = pd.DataFrame({wk_col: wk.groupby("dest")["v"].sum(), ytd_col: ytd.groupby("dest")["v"].sum(),
                        ly_col: ly_ytd.groupby("dest")["v"].sum()}).fillna(0.0)
    tbl = tbl[(tbl[ytd_col] > 0) | (tbl[ly_col] > 0)].sort_values(ytd_col, ascending=False)
    tbl["% vs last year"] = np.where(tbl[ly_col] > 0, (tbl[ytd_col] / tbl[ly_col].where(tbl[ly_col] > 0) - 1) * 100, np.nan)
    tbl["Share of year to date"] = tbl[ytd_col] / tbl[ytd_col].sum() * 100 if tbl[ytd_col].sum() > 0 else np.nan
    tbl.index.name = "Destination"
    st.download_button("Download CSV", data=tbl.round(3).to_csv(), file_name=f"us_{code.lower()}_inspections_{my_label.replace('/', '-')}.csv",
                       mime="text/csv", key="insp_dl")
    vf = "{:,.1f}" if unit == "Mbu" else "{:,.0f}"
    show = tbl.head(60).rename(columns={c: f"{c} ({unit})" for c in (wk_col, ytd_col, ly_col)})
    show["% vs last year"] = show["% vs last year"].map(lambda p: "—" if pd.isna(p) else f"{p:+.1f}%")   # a null cell would read "None"
    _insp_table(show.style.format({show.columns[0]: vf, show.columns[1]: vf, show.columns[2]: vf,
                                   "Share of year to date": "{:.1f}%"}, na_rep="—"), 420)

    mo = pd.Timestamp(year=2000, month=start_month, day=1).strftime("%B")
    st.markdown(
        f'<div class="insp-note">Source: USDA Federal Grain Inspection Service export inspections (AMS AgTransport, '
        f'"Grain Inspections"), by week ending Thursday. {grain} marketing years start on {mo} 1; a week that straddles '
        f'the start is split by certificate date, so year-to-date totals follow USDA\'s marketing year (this is also how '
        f'jpsi.com/export-inspections-dashboard counts them). Inspections are a leading indicator of exports, not a customs count: '
        f'only grain actually inspected is captured, and rail and truck shipments to Canada and Mexico are inspected in part. '
        f'"6-yr Olympic avg" drops the highest and lowest of the six prior marketing years, as elsewhere in this dashboard.</div>',
        unsafe_allow_html=True)


@st.fragment
def _run_export_inspections_tab(use_bushels: bool = False):
    """US export inspections tab. A fragment, so its filters rerun only this tab, not all ten."""
    try:
        _insp_render(use_bushels)
    except Exception as e:
        st.error(f"Inspections tab error: {e}")


# ── USDA FAS gateway: the Export Sales tab, the monthly US estimates and the PSD call all use it ──

_ESR_GATEWAY = "https://api.fas.usda.gov"
_ESR_API_KEY = "6hcX4IEwu7kreNrxoGQnPhctd6ozXmoPQTjk6Ewi"
# ── Vessel Lineup constants ───────────────────────────────────────────────────
_VL_GREEN      = _CH_SAGE
_VL_GREEN_LT   = _CH_BLUE
_VL_BG         = _CH_SURF
_VL_BORDER     = _CH_BORDER
_VL_TEXT       = _CH_TEXT
_VL_MUTED      = _CH_MUTED
_VL_COL_POS    = _CH_POS
_VL_COL_NEG    = _CH_NEG
_VL_COL_AMB    = _CH_GOLD
_VL_COL_BLUE   = _CH_BLUE
_VL_COL_PURP   = _CH_LAV
_VL_COL_ORG    = _CH_TERRA
_VL_COL_TEAL   = "#5aa59a"
_VL_COMM_COLORS = {
    "Corn":          _CH_GOLD,
    "Wheat":         "#d9904a",
    "Soybeans/Meal": _CH_SAGE,
    "Sorghum":       _CH_TERRA,
    "Dist. Grains":  _CH_LAV,
    "Rice":          "#c0707f",
    "Mixed Cargo":   _CH_BLUE,
    "Other":         _CH_MIST,
}
_VL_REGION_COLORS = {
    "USG": _CH_BLUE,
    "PNW": _CH_SAGE,
    "TXG": _CH_GOLD,
}
_VL_DATA_FILENAME  = "Vessel Lineup - US.xlsx"
_VL_REPO_FILE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), _VL_DATA_FILENAME
)
_VL_ONEDRIVE_PATH  = (
    r"C:\Users\KoltenPostin\John Stewart and Associates"
    r"\JSA - Documents\Research Analyst\Misc\Boat Lineup\Vessel Lineup - US.xlsx"
)
_VL_BASE_LAYOUT = dict(
    template="plotly_white",
    plot_bgcolor=_CH_SURF,
    paper_bgcolor=_CH_SURF,
    font=dict(color=_CH_MUTED, family=_CH_FONT, size=11),
    xaxis=dict(**_CH_AXIS),
    yaxis=dict(**_CH_AXIS),
    legend=dict(bgcolor="rgba(255,255,255,0.92)", bordercolor=_CH_BORDER, borderwidth=1,
                font=dict(color=_CH_MUTED, size=11)),
    hoverlabel=dict(bgcolor="#ffffff", bordercolor=_CH_BORDER, font=dict(color=_CH_TEXT, size=12)),
    margin=dict(l=10, r=10, t=44, b=10),
    title_font=dict(size=14, color=_CH_TEXT),
    title_x=0,
)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_esr_exports(commodity_code: int, market_year: int) -> list:
    """ESR weekly export sales for one commodity + market year → list of records (feeds the monthly US estimates;
    the Export Sales tab has its own loader, _esr_history)."""
    try:
        url = (
            f"{_ESR_GATEWAY}/api/esr/exports/commodityCode/{commodity_code}"
            f"/allCountries/marketYear/{market_year}?api_key={_ESR_API_KEY}"
        )
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# EXPORT SALES TAB  (USDA FAS Export Sales Reporting, weekly)
#   Source: the FAS ESR API, one call per commodity and marketing year, every destination, every week. Layout follows
#   jpsi.com/export-sales-dashboard (summary table, weekly series, marketing-year overlay, destinations, country table)
#   plus this dashboard's pace comparisons, filter panel and unit toggle; the destination map is left out.
#   A marketing year's week n ends on the first Thursday on or after the year's first day plus 7 x (n-1) days (checked
#   against the first report of every commodity), so "year ago" and the Olympic average are the same marketing-year week.
#   ESR labels a year by the calendar year it ENDS in (corn 2026/27 = 2027); beef and pork run January-December.
# ─────────────────────────────────────────────────────────────────────────────

# label -> (ESR commodity code, marketing-year start month, pounds per bushel or None where USDA has no bushel measure)
_ESR_COMMODITIES = {
    "Corn":          (401, 9, 56),
    "Soybeans":      (801, 9, 60),
    "Soybean meal":  (901, 10, None),
    "Soybean oil":   (902, 10, None),
    "Wheat - all":   (107, 6, 60),
    "Wheat - SRW":   (102, 6, 60),
    "Wheat - HRW":   (101, 6, 60),
    "Wheat - HRS":   (103, 6, 60),
    "Wheat - white": (104, 6, 60),
    "Sorghum":       (701, 9, 56),
    "Rice":          (1505, 8, None),
    "Beef":          (1701, 1, None),
    "Pork":          (1702, 1, None),
}
_ESR_START = {code: month for code, month, _lb in _ESR_COMMODITIES.values()}
_ESR_DEPTH = 11                                    # marketing years loaded per commodity: the current one and ten before it
_ESR_FIELDS = {                                    # ESR record field -> column (metric tons)
    "currentMYNetSales": "net", "grossNewSales": "gross", "weeklyExports": "exp", "accumulatedExports": "acc",
    "outstandingSales": "out", "currentMYTotalCommitment": "commit", "nextMYNetSales": "nxt_net",
    "nextMYOutstandingSales": "nxt_out",
}
_ESR_COLS = ["week", "cc", "my", "wk"] + list(_ESR_FIELDS.values())
# data type -> (column, True for a weekly flow that adds up over the year, False for a balance as of the week)
_ESR_METRICS = {
    "Net sales":                   ("net",     True),
    "Gross new sales":             ("gross",   True),
    "Weekly exports":              ("exp",     True),
    "Accumulated exports":         ("acc",     False),
    "Outstanding sales":           ("out",     False),
    "Total commitments":           ("commit",  False),
    "Net sales (next MY)":         ("nxt_net", True),
    "Outstanding sales (next MY)": ("nxt_out", False),
}
_ESR_LABEL = {col: label for label, (col, _flow) in _ESR_METRICS.items()}
_ESR_FLOW = {col: flow for _label, (col, flow) in _ESR_METRICS.items()}
_ESR_RESET_KEYS = ("esr_my_", "esr_week_", "esr_metric", "esr_reg_", "esr_dst_")
_ESR_NAME_FIX = {
    "KOREA, REPUBLIC OF": "South Korea", "KOREA, DEMOCRATIC PEOPLE'S REP": "North Korea",
    "CHINA, PEOPLES REPUBLIC OF": "China", "RUSSIAN FEDERATION": "Russia", "BYELARUS, REPUBLIC OF": "Belarus",
    "EUROPEAN UNION - 27": "European Union", "UNKNOWN": "Unknown destination", "TANZANIA, UNITED REPUBLIC OF": "Tanzania",
    "CONGO, DEMOCRATIC REPUBLIC OF": "DR Congo", "CONGO (BRAZZAVILLE)": "Congo (Brazzaville)",
    "BOSNIA-HERCEGOVINA": "Bosnia and Herzegovina", "MACEDONIA (SKOPJE)": "North Macedonia",
    "DJIBOUTI AFARS-ISSAS": "Djibouti", "MAURITIUS AND DEPENDENTS": "Mauritius", "MALTA & GOZO": "Malta",
    "WESTERN SAMOA": "Samoa", "SURINAM": "Suriname", "COTE D'IVOIRE": "Cote d'Ivoire",
    "VIRGIN ISLANDS OF THE U.S.": "US Virgin Islands", "OTHER PACIFIC ISLANDS, NEC": "Other Pacific Islands",
    "WESTERN AFRICA, NEC": "Western Africa", "US MINOR OUTLYING ISLAND": "US Minor Outlying Islands",
    "YEMEN (ADEN)": "Yemen", "ST. HELENA (BR W AFR)": "St. Helena", "FRENCH IND. OCEAN TERRITORY": "French Indian Ocean Territory",
}
_ESR_REGION_FIX = {"EUROPEAN UNION - 27": "European Union", "FORMER SOVIET UNION-12": "Former Soviet Union",
                   "OTHER ASIA AND OCEANIA": "Other Asia & Oceania"}
_ESR_TITLE = {"Soybeans": "Soybean", "Soybean meal": "Soybean Meal", "Soybean oil": "Soybean Oil", "Wheat - all": "Wheat",
              "Wheat - SRW": "SRW Wheat", "Wheat - HRW": "HRW Wheat", "Wheat - HRS": "HRS Wheat", "Wheat - white": "White Wheat"}


def _esr_name(raw: str) -> str:
    """An ESR destination as it reads best: 'KOREA, REPUBLIC OF' -> 'South Korea', 'BAHAMAS, THE' -> 'Bahamas'."""
    s = re.sub(r"\s+", " ", str(raw)).strip()
    fixed = _ESR_NAME_FIX.get(s.upper())
    if fixed:
        return fixed
    s = re.sub(r",\s*(REPUBLIC OF|THE)$", "", s, flags=re.I)
    s = s.title()
    return re.sub(r"(?<=\s)(And|Of|The)\b", lambda m: m.group(1).lower(), s)


def _esr_first_thu(code: int, my: int) -> pd.Timestamp:
    """Week ending of week 1 of ESR marketing year `my` (the calendar year it ends in) of commodity `code`."""
    month = _ESR_START[code]
    return _insp_first_thu(my if month == 1 else my - 1, month)


def _esr_my_label(code: int, my: int) -> str:
    return str(my) if _ESR_START[code] == 1 else f"{my - 1}/{str(my)[-2:]}"


def _esr_get(path: str):
    """GET one ESR endpoint -> parsed JSON. Retries a rate-limit or gateway hiccup, then raises a RuntimeError that never
    carries the URL (it holds the API key), so a bad moment is reported but never cached."""
    url = f"{_ESR_GATEWAY}/api/esr/{path}?api_key={_ESR_API_KEY}"
    why = "no answer"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            why = f"HTTP {e.code}"
            if e.code not in (429, 500, 502, 503, 504):
                break
        except Exception as e:                                  # timeouts, resets, a page that is not JSON
            why = type(e).__name__
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"the USDA export-sales service did not answer ({why}: {path.split('/')[0]})")


def _esr_prepare(rows: list, code: int, my: int) -> pd.DataFrame:
    """Raw ESR records of one commodity and marketing year -> week, cc (country code), my, wk (marketing-year week) and
    the eight measures in metric tons."""
    if not rows:
        return pd.DataFrame(columns=_ESR_COLS)
    raw = pd.DataFrame(rows)
    df = pd.DataFrame({"week": pd.to_datetime(raw["weekEndingDate"], errors="coerce").dt.normalize(),
                       "cc": pd.to_numeric(raw["countryCode"], errors="coerce")})
    for field, col in _ESR_FIELDS.items():
        df[col] = pd.to_numeric(raw[field], errors="coerce").fillna(0.0) if field in raw else 0.0
    df = df.dropna(subset=["week", "cc"]).copy()
    df["cc"] = df["cc"].astype(int)
    df["my"] = int(my)
    df["wk"] = ((df["week"] - _esr_first_thu(code, my)).dt.days // 7 + 1).astype(int)
    return df[df["wk"] >= 1][_ESR_COLS].reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def _esr_catalog() -> dict:
    """Destinations with their region, and each commodity's current marketing year. Raises on a failed call, so
    st.cache_data never keeps an empty answer for an hour."""
    regions = {int(r["regionId"]): _ESR_REGION_FIX.get(str(r["regionName"]).strip().upper(), str(r["regionName"]).strip().title())
               for r in _esr_get("regions")}
    country = {int(c["countryCode"]): (_esr_name(c["countryDescription"]), regions.get(c.get("regionId"), "Other"))
               for c in _esr_get("countries")}
    current: dict = {}
    for d in _esr_get("datareleasedates"):
        code = int(d["commodityCode"])
        current[code] = max(int(d["marketYear"]), current.get(code, 0))
    return {"country": country, "current": current}


@st.cache_resource(ttl=3600, show_spinner=False)
def _esr_store() -> dict:
    """Prepared marketing-year frames, kept for an hour: {(commodity code, marketing year): DataFrame}. A resource, not
    st.cache_data, so worker threads can fill it and a year fetched once is never fetched again when another is added."""
    return {"lock": threading.Lock(), "frames": {}, "bad": {}}


def _esr_fetch_my(code: int, my: int) -> pd.DataFrame:
    return _esr_prepare(_esr_get(f"exports/commodityCode/{code}/allCountries/marketYear/{my}"), code, my)


def _esr_history(code: int, current: int, depth: int = _ESR_DEPTH):
    """(frame, failed years): the `depth` marketing years ending at `current`, every destination. Years not held yet are
    fetched in parallel. A past year that fails is left out and tried again after five minutes (not on every click); the
    current year failing raises."""
    store = _esr_store()
    years = [current - i for i in range(depth)]
    now = time.time()
    with store["lock"]:
        todo = [y for y in years if (code, y) not in store["frames"] and now - store["bad"].get((code, y), (0.0, ""))[0] > 300]
    failed: dict = {y: store["bad"][(code, y)][1] for y in years if (code, y) not in store["frames"] and y not in todo
                    and (code, y) in store["bad"]}
    if todo:
        with ThreadPoolExecutor(max_workers=min(6, len(todo))) as pool:
            futures = {y: pool.submit(_esr_fetch_my, code, y) for y in todo}
        for y, fut in futures.items():                          # the with-block has waited for every call
            try:
                got = fut.result()
            except Exception as e:
                failed[y] = str(e)
                with store["lock"]:
                    store["bad"][(code, y)] = (time.time(), str(e))
            else:
                with store["lock"]:
                    store["frames"][(code, y)] = got
                    store["bad"].pop((code, y), None)
    if current in failed:
        raise RuntimeError(failed[current])
    with store["lock"]:
        frames = [store["frames"][(code, y)] for y in years if (code, y) in store["frames"]]
    frames = [f for f in frames if not f.empty]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=_ESR_COLS)
    return df, sorted(failed)


def _keep_selected(options: list, key: str) -> list:
    """`options` plus anything this widget already holds, so a selection never falls out of a list that shifted underneath it."""
    held = [v for v in (st.session_state.get(key) or []) if v not in options]
    return list(options) + held


def _esr_named(df: pd.DataFrame, cat: dict) -> pd.DataFrame:
    """Adds the destination's name and region."""
    out = df.copy()
    out["country"] = out["cc"].map({c: v[0] for c, v in cat["country"].items()})
    out["region"] = out["cc"].map({c: v[1] for c, v in cat["country"].items()})
    lost = out["country"].isna()
    if lost.any():                                              # a code the country list has not heard of
        out.loc[lost, "country"] = "Destination " + out.loc[lost, "cc"].astype(str)
        out.loc[lost, "region"] = "Other"
    return out


def _esr_matrices(df_all: pd.DataFrame, df_f: pd.DataFrame) -> dict:
    """{column: DataFrame} with one row per marketing year and one column per marketing-year week, for the eight measures
    of the filtered rows. Weeks a year never reached are NaN; weeks it reached with nothing in the filter are 0. The
    week grid comes from the unfiltered rows, so a destination filter never shortens a year."""
    last_w = df_all.groupby("my")["wk"].max().sort_index()
    cols = list(range(1, int(last_w.max()) + 1))
    in_range = pd.DataFrame(np.arange(1, len(cols) + 1)[None, :] <= last_w.to_numpy()[:, None],
                            index=last_w.index, columns=cols)
    g = df_f.groupby(["my", "wk"])[list(_ESR_FIELDS.values())].sum()
    out = {}
    for c in _ESR_FIELDS.values():
        pv = g[c].unstack("wk") if len(g) else pd.DataFrame(index=last_w.index, columns=cols, dtype=float)
        out[c] = pv.reindex(index=last_w.index, columns=cols).fillna(0.0).where(in_range)
    return out


def _esr_dated(mat: pd.DataFrame, code: int) -> pd.Series:
    """One value per report Thursday. Where two marketing years share a Thursday (the report that closes the old year
    and opens the new one) the newer year's figure is kept, as jpsi.com does."""
    parts = []
    for my, row in mat.iterrows():                              # oldest year first
        row = row.dropna()
        if row.empty:
            continue
        first = _esr_first_thu(code, int(my))
        parts.append(pd.Series(row.to_numpy(dtype=float), index=[first + pd.Timedelta(weeks=int(n) - 1) for n in row.index]))
    if not parts:
        return pd.Series(dtype=float)
    s = pd.concat(parts)
    return s[~s.index.duplicated(keep="last")].sort_index()


def _esr_compare(mat: pd.DataFrame, flow: bool, code: int, sel_my: int, wk: int, prior6: list) -> dict:
    """The selected week of one measure against last week, the same week a year ago, the last four weeks and the
    6-year Olympic average. A flow's 'last week' is the previous report whatever year it falls in; a balance is only
    compared with the previous week of its own year (a new year's balances start again from nothing)."""
    def cell(y, n):
        return None if (y not in mat.index or n not in mat.columns or pd.isna(mat.at[y, n])) else float(mat.at[y, n])

    cur = cell(sel_my, wk)
    if flow:
        ser = _esr_dated(mat, code)
        d = _esr_first_thu(code, sel_my) + pd.Timedelta(weeks=wk - 1)
        prev = ser.get(d - pd.Timedelta(weeks=1))
        prev = None if prev is None or pd.isna(prev) else float(prev)
        last4 = ser[(ser.index <= d) & (ser.index > d - pd.Timedelta(weeks=4))]
        avg4 = float(last4.mean()) if len(last4) else None
    else:
        prev = cell(sel_my, wk - 1) if wk > 1 else None
        vals = [v for v in (cell(sel_my, n) for n in range(max(1, wk - 3), wk + 1)) if v is not None]
        avg4 = float(np.mean(vals)) if vals else None
    return dict(cur=cur, prev=prev, ly=cell(sel_my - 1, wk), avg4=avg4,
                oly=_insp_olympic(mat, prior6, wk))


def _esr_delta_line(a, b, text: str, unit: str) -> str:
    """'▲ 12.3% vs last week'; against a base of zero or less (net sales can be negative) the change is in volume."""
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return f'<div class="insp-line">— {text}</div>'
    if b > 0:
        return _insp_delta_line((a / b - 1.0) * 100.0, text)
    d = a - b
    if abs(d) < 1e-12:
        return f'<div class="insp-line">— {text}</div>'
    col, arrow = (_INSP_POS, "▲") if d > 0 else (_INSP_NEG, "▼")
    return f'<div class="insp-line"><b style="color:{col}">{arrow} {abs(d):,.{_insp_dec(unit, d)}f} {unit}</b> {text}</div>'


def _esr_summary_html(comp: dict, unit: str, pct: bool, labels: dict) -> str:
    """The jpsi.com-style summary table: the selected week, last week, a year ago, the 4-week and 6-year averages, and
    the change of the selected week against each, for all eight measures."""
    cols = list(_ESR_LABEL)
    dec = {c: _insp_dec(unit, max([abs(v) for v in comp[c].values() if v is not None] or [0.0])) for c in cols}

    def num(v, c):
        return "—" if v is None or pd.isna(v) else f"{v:,.{dec[c]}f}"

    def chg(cur, base, c):
        if cur is None or base is None or pd.isna(cur) or pd.isna(base):
            return "—", ""
        if pct:
            if base <= 0:
                return "—", ""
            d = (cur / base - 1.0) * 100.0
            text = f"{d:+,.1f}%"
        else:
            d = cur - base
            text = f"{d:+,.{dec[c]}f}"
        return text, ("pos" if d > 1e-12 else "neg" if d < -1e-12 else "")

    head = "".join(f"<th>{_ESR_LABEL[c]}</th>" for c in cols)
    body = ""
    for key, lab in (("cur", labels["cur"]), ("prev", "Last week"), ("ly", labels["ly"]), ("avg4", "4-week average"),
                     ("oly", "6-yr Olympic average")):
        cells = "".join(f"<td>{num(comp[c][key], c)}</td>" for c in cols)
        body += f'<tr class="{"cur" if key == "cur" else ""}"><td>{lab}</td>{cells}</tr>'
    body += '<tr class="gap"><td colspan="9"></td></tr>'
    for key, lab in (("prev", "Change vs last week"), ("ly", "Change vs a year ago"), ("avg4", "Change vs 4-week average"),
                     ("oly", "Change vs 6-yr Olympic avg")):
        cells = ""
        for c in cols:
            text, cls = chg(comp[c]["cur"], comp[c][key], c)
            cells += f'<td class="{cls}">{text}</td>'
        body += f'<tr class="chg"><td>{lab}</td>{cells}</tr>'
    return f'<div class="esr-wrap"><table class="esr-sum"><thead><tr><th></th>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _esr_fig_series(mat, flow, code, sel_my, sel_wk, last_w, rng, kind, ma, unit, name, metric, logo):
    """Weekly series of one measure. 'Selected year': that marketing year's weeks against last year and the 6-year Olympic
    average in the same marketing-year week. Longer ranges lay consecutive marketing years on the calendar."""
    first = _esr_first_thu(code, sel_my)
    my_label = _esr_my_label(code, sel_my)
    single = rng == "Selected year"
    if single:
        weeks = list(range(1, last_w + 1))
        x = [first + pd.Timedelta(weeks=n - 1) for n in weeks]
        y = mat.loc[sel_my].reindex(weeks).to_numpy(dtype=float)
        prior6 = [m for m in mat.index if m < sel_my][-6:]
        ly = mat.loc[sel_my - 1].reindex(weeks) if (sel_my - 1) in mat.index else pd.Series(np.nan, index=weeks)
        oly = [_insp_olympic(mat, prior6, n) for n in weeks]
        label = my_label
    else:
        ser = _esr_dated(mat, code)
        k = {"2 years": 2, "5 years": 5}.get(rng)
        start = _esr_first_thu(code, sel_my - k + 1) if k else (ser.index.min() if len(ser) else first)
        ser = ser[(ser.index >= start) & (ser.index <= first + pd.Timedelta(weeks=last_w - 1))]
        x, y, weeks, label = list(ser.index), ser.to_numpy(dtype=float), None, f"{name} {metric.lower()}"
    finite = y[np.isfinite(y)] if len(y) else y
    dec = _insp_dec(unit, float(np.abs(finite).max()) if len(finite) else 0.0)
    hov = (lambda nm: f"Wk %{{customdata}}: %{{y:,.{dec}f}} {unit}<extra>{nm}</extra>") if single else \
          (lambda nm: f"%{{x|%b %d, %Y}}: %{{y:,.{dec}f}} {unit}<extra>{nm}</extra>")
    fig = go.Figure()
    if kind == "Bars":
        picked = "#3b7fa3"
        colors = [_INSP_NEG if (np.isfinite(v) and v < 0) else (picked if (single and weeks[i] == sel_wk) else _INSP_BLUE)
                  for i, v in enumerate(y)]
        fig.add_trace(go.Bar(x=x, y=np.nan_to_num(y), marker_color=colors, showlegend=False, name=label, customdata=weeks,
                             hovertemplate=hov(label)))
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", marker=dict(symbol="square", size=11, color=_INSP_BLUE),
                                 name=label))                    # a bar trace of mixed colours would lend its legend the first bar's
    else:
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=label, customdata=weeks, line=dict(color=_INSP_BLUE, width=2.8),
                                 hovertemplate=hov(label)))
    if single:
        fig.add_trace(go.Scatter(x=x, y=ly, mode="lines", name="Year ago", customdata=weeks,
                                 line=dict(color=_INSP_TERRA, width=1.8), hovertemplate=hov("Year ago")))
        fig.add_trace(go.Scatter(x=x, y=oly, mode="lines", name="6-yr Olympic avg", customdata=weeks,
                                 line=dict(color=_INSP_SAGE, width=2.2, dash="dash"), hovertemplate=hov("6-yr Olympic avg")))
    if ma and len(y):
        smooth = pd.Series(y).rolling(4, min_periods=1).mean().to_numpy()
        fig.add_trace(go.Scatter(x=x, y=smooth, mode="lines", name="4-week average", customdata=weeks,
                                 line=dict(color=_INSP_LAV, width=2.6), hovertemplate=hov("4-week average")))
    if single and sel_wk < last_w:
        xs = x[sel_wk - 1].strftime("%Y-%m-%d")
        fig.add_shape(type="line", x0=xs, x1=xs, y0=0, y1=1, xref="x", yref="paper", line=dict(color=_INSP_SAGE, width=1, dash="dot"))
    if single:
        title = f"{name} {metric.lower()}, {my_label} ({unit})"
        sub = "Weekly, week ending Thursday. Year ago and the 6-year Olympic average are the same marketing-year week."
    else:
        title = f"{name} {metric.lower()} ({unit})"
        sub = (f"Weekly, {x[0]:%b %d, %Y} to {x[-1]:%b %d, %Y}. On the Thursday a marketing year turns over, the new year's figure is shown."
               if len(x) else "No weeks in this range.")
    _insp_style(fig, 400, title, sub, logo=logo, y_title=unit)
    if single and last_w <= 14:                                 # early in a marketing year: a tick for every week
        fig.update_xaxes(tickformat="%b %d", dtick=7 * 86_400_000, tickmode="linear", tick0=x[0].strftime("%Y-%m-%d"),
                         hoverformat="%b %d, %Y")
    elif single:
        fig.update_xaxes(tickformat="%b %y", dtick="M1", hoverformat="%b %d, %Y")
    else:
        fig.update_xaxes(tickformat="%b %Y", dtick={"2 years": "M3", "5 years": "M6"}.get(rng, "M12"), hoverformat="%b %d, %Y")
    fig.update_yaxes(tickformat=",", zeroline=True, zerolinecolor=_INSP_BORDER)
    return fig


def _esr_fig_season(mat, flow, code, sel_my, sel_wk, last_w, years_mode, cumulative, smooth, unit, name, metric, logo):
    """One line per marketing year over the marketing-year weeks: the selected year in blue, the year before it terracotta,
    older years in sage shades (darker = more recent), plus the 6-year Olympic average."""
    cum = flow and cumulative
    M = mat.fillna(0.0).cumsum(axis=1).where(mat.notna()) if cum else mat
    prior = [y for y in M.index if y < sel_my]
    pick = (prior[-10:] if years_mode == "10 prior years" else prior[-5:]) + [sel_my]
    prior6 = prior[-6:]
    weeks = list(M.columns)
    sm = flow and smooth and not cum
    older = sorted(y for y in pick if y not in (sel_my, sel_my - 1))
    finite = M.loc[sel_my].dropna().to_numpy(dtype=float)
    dec = _insp_dec(unit, float(np.abs(finite).max()) if len(finite) else 0.0)
    fig = go.Figure()
    for y in sorted(pick):
        row = M.loc[y].dropna()
        if sm:
            row = row.rolling(4, min_periods=1).mean()
        lbl = _esr_my_label(code, y)
        if y == sel_my:
            style, mode = dict(color=_INSP_BLUE, width=3.6), "lines+markers" if len(row) <= 12 else "lines"
        elif y == sel_my - 1:
            style, mode = dict(color=_INSP_TERRA, width=2.2), "lines"
        else:
            style, mode = dict(color=_ch_mix("#d7e2dc", "#5f7267", older.index(y) / max(len(older) - 1, 1)), width=1.3), "lines"
        if len(row) <= 2 and mode == "lines":
            mode = "lines+markers"                              # a year with one or two weeks in is otherwise invisible
        fig.add_trace(go.Scatter(x=row.index, y=row.to_numpy(), mode=mode, name=lbl, line=style, marker=dict(size=6),
                                 hovertemplate=f"Wk %{{x}}: %{{y:,.{dec}f}} {unit}<extra>{lbl}</extra>"))
    oly = [_insp_olympic(M, prior6, n) for n in weeks]
    if sm:
        oly = pd.Series(oly, dtype="float").rolling(4, min_periods=1).mean().tolist()
    fig.add_trace(go.Scatter(x=weeks, y=oly, mode="lines", name="6-yr Olympic avg", line=dict(color=_INSP_SAGE, width=2.2, dash="dash"),
                             hovertemplate=f"Wk %{{x}}: %{{y:,.{dec}f}} {unit}<extra>6-yr Olympic avg</extra>"))
    if sel_wk < last_w:
        fig.add_shape(type="line", x0=sel_wk, x1=sel_wk, y0=0, y1=1, xref="x", yref="paper", line=dict(color=_INSP_SAGE, width=1, dash="dot"))
    vals, text = _insp_month_ticks(_esr_first_thu(code, sel_my), len(weeks))
    word = "cumulative " if cum else ("weekly (4-week average) " if sm else "")
    _insp_style(fig, 420, f"{name} {word}{metric.lower()} by marketing-year week ({unit})",
                f"Marketing years overlaid; months on the axis follow {_esr_my_label(code, sel_my)}.", logo=logo,
                x_title="Marketing-year week", y_title=f"Cumulative {unit}" if cum else unit)
    fig.update_xaxes(tickmode="array", tickvals=vals, ticktext=[f"{t}" for t in text], range=[0.5, len(weeks) + 0.5])
    fig.update_yaxes(tickformat=",", zeroline=True, zerolinecolor=_INSP_BORDER)
    return fig


def _esr_fig_dest(vals: pd.Series, top_n: int, unit: str, title: str, subtitle: str, logo=None):
    """Horizontal bars of one measure by destination: the `top_n` largest by size, positives on top. A net cancellation
    (negative) is drawn red, to the left of zero."""
    v = vals[vals.abs() > 1e-12]
    if v.empty:
        return None
    top = v.reindex(v.abs().sort_values(ascending=False).index).head(top_n).sort_values()
    order = list(top.index)
    dec = _insp_dec(unit, float(top.abs().max()))
    fig = go.Figure(go.Bar(x=top.to_numpy(), y=order, orientation="h", name="Volume",
                           marker_color=[_INSP_NEG if t < 0 else _INSP_BLUE for t in top.to_numpy()],
                           hovertemplate=f"%{{y}}: %{{x:,.{dec}f}} {unit}<extra></extra>"))
    fig.add_trace(go.Scatter(x=[max(t, 0.0) for t in top.to_numpy()], y=order, mode="text", text=[_insp_fmt(t, unit) for t in top.to_numpy()],
                             textposition="middle right", textfont=dict(size=11, color=_INSP_MUTED), showlegend=False,
                             hoverinfo="skip", cliponaxis=False))
    _insp_style(fig, max(300, 27 * len(order) + 110), title, subtitle, legend=False, hover="closest", logo=logo, x_title=unit)
    lo, hi = float(top.min()), float(top.max())
    fig.update_xaxes(range=[min(0.0, lo * 1.25), max(0.0, hi * 1.2)], tickformat=",", zeroline=True, zerolinecolor=_INSP_BORDER)
    fig.update_yaxes(showgrid=False, automargin=True)
    return fig


def _esr_render(use_bushels: bool) -> None:
    st.markdown(_INSP_CSS, unsafe_allow_html=True)
    logo = _load_logo_b64(LOGO_50YR_PATH)
    try:
        cat = _esr_catalog()
    except Exception as e:
        st.error(f"**The USDA export-sales service could not be reached.** `{e}` Click **Refresh Data** above to try again.")
        return

    # ── filters: always on show, and they apply to the tiles, the table and every chart ──────────────────────────────
    with st.container(border=True, key="esr_filters"):
        top_l, top_r = st.columns([5, 1.1])
        top_l.markdown('<span class="fp-title">Filters</span><span class="fp-sub">apply to every tile, chart and table below</span>',
                       unsafe_allow_html=True)
        top_r.button("Reset filters", key="esr_reset", on_click=_fp_reset, args=(_ESR_RESET_KEYS,))
        _fp_group("What to show")
        c_com, c_my, c_wk, c_met = st.columns([1.15, 1.0, 2.7, 1.6])
        commodity = c_com.selectbox("Commodity", list(_ESR_COMMODITIES), key="esr_comm")
        code, _month, lb = _ESR_COMMODITIES[commodity]
        cur_my = cat["current"].get(code)
        if cur_my is None:
            st.error(f"USDA lists no current marketing year for {commodity.lower()}.")
            return
        try:
            with st.spinner(f"Loading {commodity.lower()} export sales…"):
                df, failed = _esr_history(code, cur_my)
        except Exception as e:
            st.error(f"**The {commodity.lower()} export sales could not be read.** `{e}` Click **Refresh Data** above to try again.")
            return
        if df.empty:
            st.info(f"No {commodity.lower()} export sales are on file yet.")
            return
        df = _esr_named(df, cat)
        years = sorted((int(y) for y in df["my"].unique()), reverse=True)
        my_name = {y: _esr_my_label(code, y) for y in years}
        picked = c_my.selectbox("Marketing year", [my_name[y] for y in years], key=f"esr_my_{code}")
        sel_my = next(y for y in years if my_name[y] == picked)
        my_label = my_name[sel_my]
        last_w = int(df.loc[df["my"] == sel_my, "wk"].max())
        first_thu = _esr_first_thu(code, sel_my)

        def week_end(n) -> pd.Timestamp:
            return first_thu + pd.Timedelta(weeks=int(n) - 1)

        with c_wk:
            if last_w > 1:
                sel_week = int(st.select_slider("Week (ending Thursday)", options=list(range(1, last_w + 1)), value=last_w,
                                                format_func=lambda n: f"Wk {n} · {week_end(n):%b %d}",
                                                key=f"esr_week_{code}_{sel_my}"))
            else:
                sel_week = 1                                    # a slider needs two positions: only one week is in so far
                st.caption(f"Week 1 · {week_end(1):%b %d}: the only week reported so far in {my_label}.")
        metric = c_met.selectbox("Data type", list(_ESR_METRICS), key="esr_metric")
        col, flow = _ESR_METRICS[metric]

        _fp_group("Narrow it down")
        ref = df[(df["my"] == sel_my) & (df["wk"] == last_w)]
        by_reg = ref.groupby("region")["commit"].sum().sort_values(ascending=False)
        by_dst = ref.groupby("country")["commit"].sum().sort_values(ascending=False)
        reg_opts = list(by_reg.index) + sorted(set(df["region"]) - set(by_reg.index))
        dst_opts = list(by_dst.index) + sorted(set(df["country"]) - set(by_dst.index))
        f1, f2 = st.columns([1.3, 2.7])
        sel_reg = f1.multiselect("Region", _keep_selected(reg_opts, f"esr_reg_{code}"), key=f"esr_reg_{code}", placeholder="All regions")
        sel_dst = f2.multiselect("Destination", _keep_selected(dst_opts, f"esr_dst_{code}"), key=f"esr_dst_{code}",
                                 placeholder="All destinations")
        chips = []
        if sel_reg:
            chips.append("Region: " + ", ".join(sel_reg))
        if sel_dst:
            chips.append("Destination: " + ", ".join(sel_dst[:4]) + (f" +{len(sel_dst) - 4} more" if len(sel_dst) > 4 else ""))
        st.markdown(_fp_chips(chips, "No narrowing: every destination is counted"), unsafe_allow_html=True)

    mask = pd.Series(True, index=df.index)
    if sel_reg:
        mask &= df["region"].isin(sel_reg)
    if sel_dst:
        mask &= df["country"].isin(sel_dst)
    dff = df[mask]
    if dff.empty:
        st.info("No destination matches these filters.")
        return

    bu_ok = use_bushels and lb is not None
    unit = "Mbu" if bu_ok else "TMT"
    sc = (_INSP_LBS_PER_MT / lb) / 1e6 if bu_ok else 1e-3          # metric tons -> Mbu or TMT
    name = _ESR_TITLE.get(commodity, commodity)

    notes = [f"Data through the week ending {df['week'].max():%a %b %d, %Y}. USDA publishes on Thursdays at 8:30 a.m. ET, "
             f"for the week that ended the Thursday before."]
    if use_bushels and lb is None:
        notes.append(f"USDA defines no bushel weight for {commodity.lower()}, so it stays in TMT.")
    if code in (1701, 1702):
        notes.append("Fresh, chilled or frozen muscle cuts; the marketing year is the calendar year.")
    if failed:
        notes.append("Marketing year(s) " + ", ".join(_esr_my_label(code, y) for y in failed) +
                     " could not be read just now and are left out; Refresh Data tries again.")
    st.markdown(
        f'<div style="margin:14px 0 4px;padding-bottom:8px;border-bottom:2px solid {JSA_CYAN};font-family:Arial,sans-serif;'
        f'display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;">'
        f'<span style="font-size:27px;font-weight:700;color:{JSA_DARK};letter-spacing:0.2px;line-height:1.2;">'
        f'United States {name} Export Sales</span>'
        f'<span style="font-size:17px;font-weight:600;color:#6b7280;">({unit})</span></div>',
        unsafe_allow_html=True)
    st.caption(" ".join(notes))

    # ── pace numbers ──────────────────────────────────────────────────────────
    T = {c: m * sc for c, m in _esr_matrices(df, dff).items()}
    prior6 = [y for y in T["net"].index if y < sel_my][-6:]
    comp = {c: _esr_compare(T[c], _ESR_FLOW[c], code, sel_my, sel_week, prior6) for c in T}

    tiles = []
    for c, lab in (("net", f"Net sales · week {sel_week}"), ("exp", "Weekly exports"), ("out", "Outstanding sales"),
                   ("acc", "Exports to date"), ("commit", "Total commitments"), ("nxt_out", "Next-MY outstanding")):
        k = comp[c]
        tiles.append(_insp_tile(lab, _insp_fmt(k["cur"], unit), unit,
                                [_esr_delta_line(k["cur"], k["prev"], "vs last week", unit),
                                 _esr_delta_line(k["cur"], k["ly"], "vs a year ago", unit),
                                 _esr_delta_line(k["cur"], k["oly"], "vs 6-yr Olympic avg", unit)]))
    for cell_col, html_ in zip(st.columns(6), tiles):
        cell_col.markdown(html_, unsafe_allow_html=True)

    # ── summary table ─────────────────────────────────────────────────────────
    _insp_sec("Summary")
    ly_end = _esr_first_thu(code, sel_my - 1) + pd.Timedelta(weeks=sel_week - 1) if (sel_my - 1) in T["net"].index else None
    labels = {"cur": f"Week {sel_week} · {week_end(sel_week):%b %d, %Y}",
              "ly": f"A year ago · {ly_end:%b %d, %Y}" if ly_end is not None else "A year ago"}
    p1, _ = st.columns([1.4, 4.6])
    as_pct = p1.toggle("Show changes as %", key="esr_pct")
    st.markdown(_esr_summary_html(comp, unit, as_pct, labels), unsafe_allow_html=True)
    st.markdown(f'<div class="insp-note">All figures in {unit}. A year ago and the 6-yr Olympic average are the same marketing-year week. '
                f'Sales and exports compare with the previous report; balances (accumulated exports, outstanding sales, commitments) '
                f'with the previous week of the same marketing year, and the 4-week average of a balance is taken within it.</div>',
                unsafe_allow_html=True)

    # ── weekly pace ───────────────────────────────────────────────────────────
    _insp_sec("Weekly pace")
    r1, r2, r3, _ = st.columns([2.4, 1.4, 1.4, 2.0])
    rng = r1.segmented_control("Range", ["Selected year", "2 years", "5 years", "All loaded"], default="Selected year",
                               key="esr_range") or "Selected year"
    kind = r2.segmented_control("Chart", ["Bars", "Line"], default="Bars", key="esr_kind") or "Bars"
    ma = r3.toggle("4-week average", key="esr_ma", help="Smooths the weekly series; sales and exports swing with vessel timing.")
    _insp_plot(_esr_fig_series(T[col], flow, code, sel_my, sel_week, last_w, rng, kind, ma, unit, name, metric, logo), "esr_fig_series")

    _insp_sec("Marketing-year pace")
    s1, s2, s3, _ = st.columns([2.0, 1.3, 1.5, 2.4])
    years_mode = s1.segmented_control("Compare with", ["5 prior years", "10 prior years"], default="5 prior years",
                                      key="esr_years") or "5 prior years"
    cumulative = smooth = False
    if flow:
        cumulative = s2.toggle("Cumulative", key="esr_cum", help="Adds each week to the ones before it: the marketing year to date.")
        if not cumulative:
            smooth = s3.toggle("4-week average", key="esr_smooth")
    else:
        s2.caption("A balance: shown as it stood each week.")
    _insp_plot(_esr_fig_season(T[col], flow, code, sel_my, sel_week, last_w, years_mode, cumulative, smooth, unit, name, metric, logo),
               "esr_fig_season")

    # ── where it is going ─────────────────────────────────────────────────────
    _insp_sec("Where it is going")
    cur_f = dff[dff["my"] == sel_my]
    d1, d2, _ = st.columns([2.4, 1.8, 2.2])
    to_date = False
    if flow:
        to_date = (d1.segmented_control("Period", ["This week", "Marketing year to date"], default="This week",
                                        key="esr_period") or "This week") != "This week"
    else:
        d1.caption(f"A balance: shown as it stood at the end of week {sel_week}.")
    top_n = d2.slider("Destinations shown", 5, 30, 12, key="esr_topn")
    rows = cur_f[cur_f["wk"] <= sel_week] if to_date else cur_f[cur_f["wk"] == sel_week]
    vals = rows.groupby("country")[col].sum() * sc
    when = (f"{my_label} to date · through week {sel_week} ({week_end(sel_week):%b %d, %Y})" if to_date
            else f"Week {sel_week} · ending {week_end(sel_week):%b %d, %Y}")
    if vals.abs().sum() <= 1e-12:
        st.info(f"No destination has {metric.lower()} to show for this period and these filters.")
    else:
        cl, cr = st.columns([3, 2])
        with cl:
            fig = _esr_fig_dest(vals, top_n, unit, f"{metric} by destination ({unit})", when, logo=logo)
            if fig is not None:
                _insp_plot(fig, "esr_fig_dest")
        with cr:
            pos = vals[vals > 1e-12]
            fig = _insp_fig_donut(pd.DataFrame({"dest": pos.index, "v": pos.to_numpy()}), "dest", unit, "Share by destination",
                                  when + (" · net cancellations left out" if (vals < -1e-12).any() else ""), top_n=7, min_label=3)
            if fig is not None:
                _insp_plot(fig, "esr_fig_share")

    # ── detail ────────────────────────────────────────────────────────────────
    _insp_sec("Detail by destination")
    num_cols = list(_ESR_FIELDS.values())
    wk_rows = cur_f[cur_f["wk"] == sel_week]
    tbl = wk_rows.groupby("country")[num_cols].sum() * sc
    ly_commit = dff[(dff["my"] == sel_my - 1) & (dff["wk"] == sel_week)].groupby("country")["commit"].sum() * sc
    tbl = tbl.reindex(tbl.index.union(ly_commit[ly_commit.abs() > 1e-12].index)).fillna(0.0)
    tbl = tbl[(tbl[num_cols].abs() > 1e-12).any(axis=1)].sort_values("commit", ascending=False)
    ly_c = ly_commit.reindex(tbl.index).fillna(0.0)
    tbl["vs"] = np.where(ly_c > 0, (tbl["commit"] / ly_c.where(ly_c > 0) - 1.0) * 100.0, np.nan)
    tot = tbl["commit"].sum()
    tbl["share"] = tbl["commit"] / tot * 100.0 if tot > 0 else np.nan
    tbl.index.name = "Destination"
    nice = {c: f"{_ESR_LABEL[c].replace(' (next MY)', ', next MY')} ({unit})" for c in num_cols}
    nice.update({"vs": "Commitments vs a year ago", "share": "Share of commitments"})
    tbl = tbl.rename(columns=nice)
    st.download_button("Download CSV", data=tbl.round(4).to_csv(),
                       file_name=f"us_{commodity.lower().replace(' ', '_')}_export_sales_{my_label.replace('/', '-')}_wk{sel_week}.csv",
                       mime="text/csv", key="esr_dl")
    vf = "{:,.1f}" if unit == "TMT" else "{:,.2f}"
    show = tbl.head(80).copy()
    show["Commitments vs a year ago"] = show["Commitments vs a year ago"].map(lambda p: "—" if pd.isna(p) else f"{p:+.1f}%")   # a null cell would read "None"
    fmt = {nice[c]: vf for c in num_cols}
    fmt["Share of commitments"] = "{:.1f}%"
    _insp_table(show.style.format(fmt, na_rep="—"), 420)

    st.markdown(
        '<div class="insp-note">Source: USDA Foreign Agricultural Service, Export Sales Reporting (ESR), by week ending Thursday; '
        'the same weekly report as jpsi.com/export-sales-dashboard. <b>Net sales</b> are new sales less cancellations and can be negative; '
        '<b>outstanding sales</b> are sold and not yet shipped; <b>total commitments</b> are accumulated exports plus outstanding sales; '
        '<b>next MY</b> counts sales for the following marketing year. The first report of a marketing year covers only the days since it '
        'began, so its weekly exports run short. Sales are reported by exporters on contract date and are revised later. '
        '"6-yr Olympic avg" drops the highest and lowest of the six prior marketing years, as elsewhere in this dashboard.</div>',
        unsafe_allow_html=True)


@st.fragment
def _run_export_sales_tab(use_bushels=False, unit_short="TMT"):
    """Export sales tab. A fragment, so its filters rerun only this tab, not all ten. The unit comes from the global
    toggle through `use_bushels`; `unit_short` is kept for the caller's sake."""
    try:
        _esr_render(use_bushels)
    except Exception as e:
        st.error(f"Export Sales tab error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# VESSEL LINEUP HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _vl_comm(c):
    if pd.isna(c):
        return "Other"
    c = str(c).strip().upper()
    if "/" in c:
        return "Mixed Cargo"
    if c == "CORN" or c.startswith("CORN "):
        return "Corn"
    if any(x in c for x in ("SBM", "YSB", "SOY", "CANOLA")):
        return "Soybeans/Meal"
    if "WHT" in c or "WHEAT" in c:
        return "Wheat"
    if "SORGHUM" in c:
        return "Sorghum"
    if any(x in c for x in ("DDGS", "CGM", "CGFP", "GDDG")):
        return "Dist. Grains"
    if "RICE" in c:
        return "Rice"
    return "Other"


@st.cache_data(show_spinner=False)
def _vl_load_data(file_source, _mtime=None):
    import io as _io
    US_COLS = ["ELEVATOR", "VESSEL", "ATA", "STATUS", "MT",
               "COMMODITY", "DESTINATION", "SAIL DATE"]
    frames = {}
    for region in ("USG", "PNW", "TXG"):
        src = _io.BytesIO(file_source) if isinstance(file_source, bytes) else file_source
        df = pd.read_excel(src, sheet_name=region, usecols=list(range(8)), header=0)
        df.columns = US_COLS
        df = df.dropna(how="all")
        df["REGION"] = region
        df["IS_RVT"] = df["MT"].astype(str).str.strip().str.upper() == "RVT"
        df["MT_kMT"] = pd.to_numeric(df["MT"], errors="coerce")

        def _stat(s):
            if pd.isna(s):
                return "Unknown"
            s = str(s).strip().upper()
            if s.startswith("SAIL"):
                return "Sailed"
            if s in ("LOADING", "PART-LDD", "IN PORT"):
                return "Loading/In Port"
            if s == "FILED":
                return "Filed"
            if s.startswith("ETA") or s.startswith("L/R"):
                return "ETA"
            return "Other"

        df["STATUS_NORM"] = df["STATUS"].apply(_stat)
        df["SAILED"]      = df["STATUS_NORM"] == "Sailed"
        df["COMM_GRP"]    = df["COMMODITY"].apply(_vl_comm)
        df["ATA"]         = pd.to_datetime(df["ATA"], errors="coerce")
        df["SAIL_DT"]     = pd.to_datetime(df["SAIL DATE"], errors="coerce")
        df["SAIL_MONTH"]  = df["SAIL_DT"].dt.to_period("M").dt.to_timestamp()
        frames[region] = df
    return frames


@st.cache_data(show_spinner=False)
def _vl_load_trends(file_source, _mtime=None):
    import io as _io
    src = _io.BytesIO(file_source) if isinstance(file_source, bytes) else file_source
    df = pd.read_excel(src, sheet_name="Trends", header=None)
    header_row = df.iloc[0]
    weekly_col = monthly_col = latest_col = None
    latest_date = None
    for ci, val in enumerate(header_row):
        ci = int(ci)
        if isinstance(val, str):
            v = val.strip()
            if v == "Weekly":
                weekly_col = ci
            elif v == "Monthly":
                monthly_col = ci
        elif hasattr(val, "year"):
            val_ts = pd.Timestamp(val)
            if latest_date is None or val_ts > latest_date:
                latest_date = val_ts
                latest_col = ci

    TARGETS = {"USG", "PNW", "TXG"}

    def _float(v):
        if pd.isna(v):
            return 0.0
        try:
            return float(v)
        except Exception:
            return 0.0

    regions = {}
    i = 0
    while i < len(df):
        cell = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ""
        if cell in TARGETS:
            region = cell
            latest_val = df.iloc[i, latest_col] if latest_col else None
            if not isinstance(latest_val, (int, float)):
                rows = []
                i += 1
                if i < len(df) and str(df.iloc[i, 0]).strip() == "Commodity":
                    i += 1
                while i < len(df):
                    label = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ""
                    if label == region:
                        i += 1
                        break
                    if label and label != "nan":
                        rows.append({
                            "COMMODITY":  label.upper(),
                            "MT":         _float(df.iloc[i, latest_col]) if latest_col else 0.0,
                            "Weekly_MT":  _float(df.iloc[i, weekly_col]) if weekly_col else 0.0,
                            "Monthly_MT": _float(df.iloc[i, monthly_col]) if monthly_col else 0.0,
                        })
                    i += 1
                if rows:
                    regions[region] = pd.DataFrame(rows)
                continue
        i += 1
    return regions, latest_date


def _vl_cutoff(months):
    return pd.Timestamp.now().normalize() - pd.DateOffset(months=months)


def _vl_kpi(col, label, value, sub="", accent=None):
    accent = accent or _VL_GREEN
    col.markdown(
        f'<div class="vl-kpi-wrap" style="border-left:3px solid {accent};">'
        f'<div class="vl-kpi-label">{label}</div>'
        f'<div class="vl-kpi-value">{value}</div>'
        f'<div class="vl-kpi-sub">{sub}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _vl_sec(label):
    st.markdown(f'<div class="vl-sec-hdr">{label}</div>', unsafe_allow_html=True)


def _vl_style(fig: go.Figure, **extra) -> go.Figure:
    """The Vessel tab's light chart layout, with the title in bold like every other chart."""
    fig.update_layout(**_VL_BASE_LAYOUT, **extra)
    if fig.layout.title.text:
        fig.update_layout(title_text=f"<b>{fig.layout.title.text}</b>")
    return fig


def _vl_bar_comm(df, title):
    g = (df.groupby("COMM_GRP").size()
           .reset_index(name="Vessels")
           .sort_values("Vessels", ascending=False))
    fig = px.bar(g, x="COMM_GRP", y="Vessels",
                 color="COMM_GRP", color_discrete_map=_VL_COMM_COLORS,
                 title=title, labels={"COMM_GRP": "Commodity"})
    _vl_style(fig, showlegend=False)
    fig.update_traces(marker_line_width=0)
    return fig


def _vl_bar_monthly_stacked(df, title, color_col="COMM_GRP"):
    cmap = _VL_COMM_COLORS if color_col == "COMM_GRP" else _VL_REGION_COLORS
    lbl  = "Commodity" if color_col == "COMM_GRP" else "Region"
    g = (df.groupby(["SAIL_MONTH", color_col]).size()
           .reset_index(name="Vessels").sort_values("SAIL_MONTH"))
    fig = px.bar(g, x="SAIL_MONTH", y="Vessels",
                 color=color_col, color_discrete_map=cmap,
                 barmode="stack", title=title,
                 labels={"SAIL_MONTH": "Month", color_col: lbl})
    _vl_style(fig)
    fig.update_xaxes(tickformat="%b %Y")
    fig.update_traces(marker_line_width=0)
    return fig


def _vl_pivot_comm_month(df):
    p = (df.groupby(["COMM_GRP", "SAIL_MONTH"]).size().unstack(fill_value=0))
    p.columns = [c.strftime("%b %Y") if hasattr(c, "strftime") else str(c)
                 for c in p.columns]
    p["Total"] = p.sum(axis=1)
    return p.sort_values("Total", ascending=False)


def _vl_fmt_change(val):
    if pd.isna(val) or val == 0:
        return "0"
    return f"+{val:,.0f}" if val > 0 else f"{val:,.0f}"


def _vl_current_table(lined_df, trends_df):
    vc = lined_df.groupby("COMMODITY").size().reset_index(name="Vessels")
    vc["COMMODITY"] = vc["COMMODITY"].str.upper().str.strip()
    if trends_df is not None and not trends_df.empty:
        mt = trends_df[["COMMODITY", "MT"]].copy()
    else:
        mt = (lined_df.groupby("COMMODITY")["MT_kMT"].sum().reset_index()
              .rename(columns={"MT_kMT": "MT"}))
        mt["COMMODITY"] = mt["COMMODITY"].str.upper().str.strip()
    merged = vc.merge(mt, on="COMMODITY", how="left").fillna({"MT": 0})
    merged["MT"] = merged["MT"].round(0).astype(int)
    total = pd.DataFrame([{"COMMODITY": "Grand Total",
                            "Vessels": merged["Vessels"].sum(),
                            "MT": merged["MT"].sum()}])
    return pd.concat([merged, total], ignore_index=True).set_index("COMMODITY")


def _vl_change_table(trends_df, col):
    if trends_df is None or trends_df.empty:
        return pd.DataFrame()
    t = trends_df[["COMMODITY", col]].copy().rename(columns={col: "MT Δ (TMT)"})
    total = pd.DataFrame([{"COMMODITY": "Total",
                            "MT Δ (TMT)": t["MT Δ (TMT)"].sum()}])
    return pd.concat([t, total], ignore_index=True).set_index("COMMODITY")


def _vl_render_snapshot(frames, trends_regions, latest_date):
    date_str = latest_date.strftime("%m/%d/%Y") if latest_date else "latest"
    _vl_sec(f"📋  Commodity Snapshot — Lineup as of {date_str}")
    REGION_LABELS = {"USG": "🇺🇸 US Gulf", "PNW": "🌲 Pacific Northwest", "TXG": "⭐ Texas Gulf"}
    for region in ("USG", "PNW", "TXG"):
        lined  = frames.get(region)
        trends = trends_regions.get(region) if trends_regions else None
        st.markdown(
            f"<div style='font-size:14px;font-weight:700;color:{_VL_TEXT};"
            f"margin:14px 0 6px 0;'>{REGION_LABELS[region]}</div>",
            unsafe_allow_html=True,
        )
        col_cur, col_wk, col_mo = st.columns(3)
        with col_cur:
            st.markdown(
                f"<div style='font-size:11px;font-weight:600;color:{_VL_MUTED};"
                f"text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px;'>"
                f"Current Lineup</div>", unsafe_allow_html=True,
            )
            if lined is not None and len(lined[~lined["SAILED"]]) > 0:
                st.dataframe(_vl_current_table(lined[~lined["SAILED"]], trends),
                             use_container_width=True)
            else:
                st.info("No vessels lined up.")
        for col_w, col_key, col_label in [
            (col_wk, "Weekly_MT", "Weekly MT Change"),
            (col_mo, "Monthly_MT", "Monthly MT Change"),
        ]:
            with col_w:
                st.markdown(
                    f"<div style='font-size:11px;font-weight:600;color:{_VL_MUTED};"
                    f"text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px;'>"
                    f"{col_label}</div>", unsafe_allow_html=True,
                )
                chg = _vl_change_table(trends, col_key)
                if not chg.empty:
                    fmt = chg.copy()
                    fmt["MT Δ (TMT)"] = fmt["MT Δ (TMT)"].apply(
                        lambda v: _vl_fmt_change(v) if isinstance(v, (int, float)) else v
                    )
                    st.dataframe(fmt, use_container_width=True)
                else:
                    st.info("No Trends data.")
    st.markdown("---")


def _vl_page_summary(frames, n_months, trends_regions=None, latest_date=None):
    cut = _vl_cutoff(n_months)
    rows = []
    for r, df in frames.items():
        lu = df[~df["SAILED"]]
        s  = df[df["SAILED"] & (df["SAIL_MONTH"] >= cut)]
        rows.append(dict(Region=r,
                         Lined_Up=len(lu), LU_MT=lu["MT_kMT"].sum(),
                         Sailed=len(s), S_MT=s["MT_kMT"].sum()))
    smry = pd.DataFrame(rows)

    c1, c2, c3, c4 = st.columns(4)
    _vl_kpi(c1, "Total Vessels Lined Up", f"{smry['Lined_Up'].sum():,}",
            "all regions combined", _VL_COL_BLUE)
    _vl_kpi(c2, "Total Vessels Sailed", f"{smry['Sailed'].sum():,}",
            f"last {n_months} months", _VL_COL_POS)
    _vl_kpi(c3, "Volume Lined Up", f"{smry['LU_MT'].sum():,.0f} TMT",
            "known MT (excl. RVT)", _VL_COL_AMB)
    _vl_kpi(c4, "Volume Sailed", f"{smry['S_MT'].sum():,.0f} TMT",
            f"last {n_months} months", _VL_COL_PURP)
    st.markdown("---")

    if trends_regions:
        _vl_render_snapshot(frames, trends_regions, latest_date)

    _vl_sec("🟡  Current Lineup — All Regions")
    lu_all = pd.concat([df[~df["SAILED"]] for df in frames.values()], ignore_index=True)
    ch1, ch2 = st.columns(2)
    with ch1:
        g = lu_all.groupby(["REGION", "COMM_GRP"]).size().reset_index(name="Vessels")
        fig = px.bar(g, x="REGION", y="Vessels",
                     color="COMM_GRP", color_discrete_map=_VL_COMM_COLORS,
                     barmode="stack", title="Lined-Up Vessels by Region & Commodity",
                     category_orders={"REGION": ["USG", "PNW", "TXG"]},
                     labels={"REGION": "Region", "COMM_GRP": "Commodity"})
        _vl_style(fig)
        fig.update_traces(marker_line_width=0)
        st.plotly_chart(fig, use_container_width=True)
    with ch2:
        g2 = lu_all.groupby(["COMM_GRP", "REGION"]).size().reset_index(name="Vessels")
        fig2 = px.bar(g2, x="COMM_GRP", y="Vessels",
                      color="REGION", color_discrete_map=_VL_REGION_COLORS,
                      barmode="stack", title="Lined-Up Vessels by Commodity & Region",
                      labels={"COMM_GRP": "Commodity", "REGION": "Region"})
        _vl_style(fig2)
        fig2.update_traces(marker_line_width=0)
        st.plotly_chart(fig2, use_container_width=True)

    st.dataframe(
        smry.rename(columns=dict(
            Lined_Up="Lined Up", LU_MT="Lined Up TMT",
            Sailed=f"Sailed ({n_months}mo)", S_MT="Sailed TMT",
        )).set_index("Region"),
        use_container_width=True,
    )
    st.markdown("---")
    _vl_sec("✅  Shipped — All Regions by Month")
    s_all = pd.concat(
        [df[df["SAILED"] & (df["SAIL_MONTH"] >= cut)] for df in frames.values()],
        ignore_index=True,
    )
    if len(s_all) == 0:
        st.info("No sailed vessel data for the selected period.")
        return
    ch3, ch4 = st.columns(2)
    with ch3:
        st.plotly_chart(_vl_bar_monthly_stacked(s_all,
            "Shipped Vessels by Month & Region", color_col="REGION"),
            use_container_width=True)
    with ch4:
        st.plotly_chart(_vl_bar_monthly_stacked(s_all,
            "Shipped Vessels by Month & Commodity"),
            use_container_width=True)
    reg_month = (s_all.groupby(["REGION", "SAIL_MONTH"]).size().unstack(fill_value=0))
    reg_month.columns = [c.strftime("%b %Y") if hasattr(c, "strftime") else str(c)
                         for c in reg_month.columns]
    reg_month["Total"] = reg_month.sum(axis=1)
    st.dataframe(reg_month, use_container_width=True)


def _vl_page_region(df, region_label, n_months):
    lined  = df[~df["SAILED"]].copy()
    sailed = df[df["SAILED"] & (df["SAIL_MONTH"] >= _vl_cutoff(n_months))].copy()

    c1, c2, c3, c4 = st.columns(4)
    lu_mt  = lined["MT_kMT"].sum()
    s_mt   = sailed["MT_kMT"].sum()
    top_lu = lined["COMM_GRP"].value_counts().index[0] if len(lined) > 0 else "—"
    top_lu_n = lined["COMM_GRP"].value_counts().iloc[0] if len(lined) > 0 else 0
    top_s  = sailed["COMM_GRP"].value_counts().index[0] if len(sailed) > 0 else "—"
    top_s_n = sailed["COMM_GRP"].value_counts().iloc[0] if len(sailed) > 0 else 0

    _vl_kpi(c1, "Vessels Lined Up", f"{len(lined):,}",
            f"{lu_mt:,.0f} TMT known" if lu_mt > 0 else "MT not disclosed", _VL_COL_BLUE)
    _vl_kpi(c2, "Top Commodity (Lined Up)", top_lu, f"{top_lu_n} vessels", _VL_COL_AMB)
    _vl_kpi(c3, f"Vessels Sailed — last {n_months}mo", f"{len(sailed):,}",
            f"{s_mt:,.0f} TMT", _VL_COL_POS)
    _vl_kpi(c4, "Top Commodity (Sailed)", top_s, f"{top_s_n} vessels", _VL_COL_PURP)
    st.markdown("---")

    _vl_sec("🟡  Current Lineup — Vessels Waiting to Ship")
    if len(lined) == 0:
        st.info("No vessels currently in the lineup.")
    else:
        ch, tb = st.columns([1.5, 1])
        with ch:
            st.plotly_chart(_vl_bar_comm(lined, "Vessel Count by Commodity"),
                            use_container_width=True)
        with tb:
            st.markdown("<br>", unsafe_allow_html=True)
            piv = lined.groupby(["COMM_GRP", "STATUS_NORM"]).size().unstack(fill_value=0)
            piv["Total"] = piv.sum(axis=1)
            st.dataframe(piv.sort_values("Total", ascending=False),
                         use_container_width=True)
        with st.expander("📋 Full Lineup Detail"):
            cols = [c for c in ["ELEVATOR","VESSEL","ATA","STATUS","COMMODITY",
                                 "COMM_GRP","MT","DESTINATION"] if c in lined.columns]
            st.dataframe(lined[cols].sort_values("ATA", na_position="last"),
                         use_container_width=True, height=320)

    st.markdown("---")
    _vl_sec("✅  Shipped — Departed Vessels by Month")
    if len(sailed) == 0:
        st.info("No sailed vessel data for this period.")
        return

    st.plotly_chart(_vl_bar_monthly_stacked(sailed,
        "Shipped Vessels by Month & Commodity"), use_container_width=True)
    st.dataframe(_vl_pivot_comm_month(sailed), use_container_width=True)

    mv = sailed.groupby("SAIL_MONTH")["MT_kMT"].sum().reset_index()
    fig_v = px.bar(mv.sort_values("SAIL_MONTH"), x="SAIL_MONTH", y="MT_kMT",
                   title="Shipped Volume by Month (TMT — excludes RVT)",
                   labels={"SAIL_MONTH": "Month", "MT_kMT": "TMT"},
                   color_discrete_sequence=[_VL_GREEN_LT])
    _vl_style(fig_v)
    fig_v.update_xaxes(tickformat="%b %Y")
    fig_v.update_traces(marker_line_width=0)
    st.plotly_chart(fig_v, use_container_width=True)


def _run_vessel_lineup_tab():
    """Vessel Lineup tab — embedded from vessel-lineup-dashboard."""
    # ── File source ───────────────────────────────────────────────────────────
    file_source = None
    file_mtime  = None

    if os.path.exists(_VL_REPO_FILE_PATH):
        file_source = _VL_REPO_FILE_PATH
        try:
            file_mtime = os.path.getmtime(_VL_REPO_FILE_PATH)
            mod_str = datetime.fromtimestamp(file_mtime).strftime("%b %d · %I:%M %p")
        except Exception:
            mod_str = "Unknown"
        st.caption(f"📂 {_VL_DATA_FILENAME} · Last saved: {mod_str}")
    elif os.path.exists(_VL_ONEDRIVE_PATH):
        file_source = _VL_ONEDRIVE_PATH
        try:
            file_mtime = os.path.getmtime(_VL_ONEDRIVE_PATH)
            mod_str = datetime.fromtimestamp(file_mtime).strftime("%b %d · %I:%M %p")
        except Exception:
            mod_str = "Unknown"
        st.caption(f"📂 OneDrive · Last saved: {mod_str}")
    else:
        import io as _io
        st.info("Upload the Vessel Lineup Excel file to load this tab.")
        uploaded = st.file_uploader(
            "Vessel Lineup - US.xlsx", type=["xlsx"], key="vl_upload"
        )
        if uploaded is None:
            return
        file_source = uploaded.read()

    # ── Controls ──────────────────────────────────────────────────────────────
    ctl1, ctl2, _ = st.columns([2, 1, 5])
    with ctl1:
        n_months = st.slider("Shipped history (months)", 3, 24, 12,
                             key="vl_n_months")
    with ctl2:
        if st.button("🔄 Reload", key="vl_reload", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # ── Load data ─────────────────────────────────────────────────────────────
    with st.spinner("Loading vessel data…"):
        frames = _vl_load_data(file_source, _mtime=file_mtime)
        trends_regions, latest_date = _vl_load_trends(file_source, _mtime=file_mtime)

    # ── Sub-tabs ──────────────────────────────────────────────────────────────
    sub_summary, sub_usg, sub_pnw, sub_txg = st.tabs(
        ["📊 Summary", "🇺🇸 USG", "🌲 PNW", "⭐ TXG"]
    )

    with sub_summary:
        _vl_page_summary(frames, n_months,
                         trends_regions=trends_regions, latest_date=latest_date)

    with sub_usg:
        st.markdown("<h3>US Gulf (USG)</h3>", unsafe_allow_html=True)
        _vl_page_region(frames["USG"], "USG", n_months)

    with sub_pnw:
        st.markdown("<h3>Pacific Northwest (PNW)</h3>", unsafe_allow_html=True)
        _vl_page_region(frames["PNW"], "PNW", n_months)

    with sub_txg:
        st.markdown("<h3>Texas Gulf (TXG)</h3>", unsafe_allow_html=True)
        _vl_page_region(frames["TXG"], "TXG", n_months)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# EXPORT DESTINATIONS
#   United States                   USDA FAS GATS / Census, cached by gats.py into Snowflake GATS_EXPORTS
#   Brazil / Argentina / Ukraine    TDM exports by destination, cached by tdm_partners.py into TDM_PARTNER_MONTHLY
#   China                           TDM imports by origin (same table, flow I)
# ─────────────────────────────────────────────────────────────────────────────
_GATS_COMMODITIES = {"Corn": "corn", "Soybeans": "soybeans", "Soybean Meal": "soybeanmeal", "Wheat": "wheat",
                    "Sorghum": "sorghum", "Canola": "canola"}
_GATS_MBU_PER_TMT = {"corn": 0.039368, "soybeans": 0.036744, "wheat": 0.036744,
                     "sorghum": 0.039368, "canola": 0.044092}   # soybean meal has no bushel unit
_GATS_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# tab selector -> where the numbers come from and which way the trade runs
_DEST_PLACES = {
    "United States":              dict(code="US", src="gats", owner="US", flow="E"),
    "Brazil":                     dict(code="BR", src="tdm", owner="Brazil", flow="E"),
    "Argentina":                  dict(code="AR", src="tdm", owner="Argentina", flow="E"),
    "Ukraine":                    dict(code="UA", src="tdm", owner="Ukraine", flow="E"),
    "China (imports, by origin)": dict(code="CN", src="tdm", owner="China", flow="I"),
}
_TDM_COMMODITY_OF = {"205719": "corn", "205714": "soybeans", "205717": "soybeanmeal", "205713": "wheat"}
_DEST_CONFIDENTIAL = "Confidential"        # TDM's name for shipments a country reports without a destination


class _DestNotLoaded(Exception):
    """The TDM partner table is missing or still empty. Raised, not returned, so st.cache_data never caches it."""


@st.cache_data(ttl=3600, show_spinner=False)
def _load_gats_destinations() -> pd.DataFrame:
    """Every loaded partner-month: commodity, code, country, year, month, tmt, value.
    Raises on failure (a cached empty frame would hide an outage for an hour); a dead cached
    connection is dropped and retried once."""
    for attempt in (1, 2):
        conn = _get_sf_conn()
        if conn is None:
            raise RuntimeError("Snowflake credentials are not configured for this app")
        try:
            cur = conn.cursor()
            cur.execute("SELECT commodity, partner_code, partner_name, year, month, tmt, value_usd FROM GATS_EXPORTS")
            df = pd.DataFrame(cur.fetchall(), columns=["commodity", "code", "country", "year", "month", "tmt", "value"])
            for c in ("year", "month"):
                df[c] = df[c].astype(int)
            for c in ("tmt", "value"):
                df[c] = df[c].astype(float)
            return df
        except Exception:
            if attempt == 2:
                raise
            _get_sf_conn.clear()


@st.cache_data(ttl=3600, show_spinner=False)
def _load_tdm_partner_months() -> pd.DataFrame:
    """Every loaded TDM partner-month: reporter, flow, commodity, code, country, year, month, tmt, value.
    Raises _DestNotLoaded until tdm_partners.py has created and filled the table; any other failure raises as
    it is (neither is cached)."""
    for attempt in (1, 2):
        conn = _get_sf_conn()
        if conn is None:
            raise RuntimeError("Snowflake credentials are not configured for this app")
        try:
            cur = conn.cursor()
            cur.execute("SELECT reporter, flow, product_code, partner_code, partner_name, year, month, tmt, value_usd "
                        "FROM TDM_PARTNER_MONTHLY")
            rows = cur.fetchall()
        except Exception as e:
            if "does not exist" in str(e).lower():
                raise _DestNotLoaded("TDM_PARTNER_MONTHLY does not exist yet")
            if attempt == 2:
                raise
            _get_sf_conn.clear()
            continue
        if not rows:
            raise _DestNotLoaded("TDM_PARTNER_MONTHLY is empty")
        df = pd.DataFrame(rows, columns=["reporter", "flow", "product", "code", "country", "year", "month", "tmt", "value"])
        df["commodity"] = df["product"].map(_TDM_COMMODITY_OF)
        for c in ("year", "month"):
            df[c] = df[c].astype(int)
        for c in ("tmt", "value"):
            df[c] = df[c].astype(float)
        return df.dropna(subset=["commodity"]).drop(columns="product")


def _run_destinations_tab(use_bushels: bool, unit_short: str, unit_decimals: int):
    """Where a country's corn / soybeans / soybean meal / wheat went (or, for China, came from), by country and
    month: the US from GATS (Census data), Brazil / Argentina / Ukraine / China from TDM."""
    c0, c1, c2 = st.columns(3)
    with c0:
        place_label = st.selectbox("Country", list(_DEST_PLACES), key="dest_place")
    place = _DEST_PLACES[place_label]
    owner, flow = place["owner"], place["flow"]
    verb = "exports" if flow == "E" else "imports"
    noun = "destination" if flow == "E" else "origin"
    Noun = noun.capitalize()

    if place["src"] == "gats":
        df_all = _load_gats_destinations()
        if df_all.empty:
            st.info("No GATS destination data has been loaded yet. Run `python gats.py seed`, then `backfill`, on the droplet.")
            return
    else:
        try:
            tdm = _load_tdm_partner_months()
        except _DestNotLoaded:
            st.info(f"The {owner} {noun} data has not been loaded yet. It is filled by `tdm_partners.py` on the droplet "
                    "once the TDM_PARTNER_MONTHLY table exists.")
            return
        df_all = tdm[(tdm["reporter"] == place["code"]) & (tdm["flow"] == flow)]
        if df_all.empty:
            st.info(f"No {owner} {verb} by {noun} are loaded yet.")
            return

    present = set(df_all["commodity"])
    com_options = {k: v for k, v in _GATS_COMMODITIES.items() if v in present}
    with c1:
        com_label = st.selectbox("Commodity", list(com_options), key=f"dest_commodity_{place['code']}")
    com = com_options[com_label]
    df = df_all[df_all["commodity"] == com].copy()

    factor = _GATS_MBU_PER_TMT.get(com) if use_bushels else None
    unit = unit_short if (not use_bushels or factor) else "TMT"      # meal has no bushel unit: stay in TMT
    dec = unit_decimals if unit == unit_short else 0
    scale = factor or 1.0
    df["vol"] = df["tmt"] * scale
    df["ym"] = df["year"] * 12 + df["month"] - 1                       # month index, so windows are plain ranges
    last_ym = int(df["ym"].max())
    first_ym = int(df["ym"].min())
    ly = last_ym // 12
    today = pd.Timestamp.now()
    if place["src"] == "gats" and last_ym < today.year * 12 + today.month - 1 - 3:    # newest loaded month is >3 months old
        st.warning("The destination history is still being loaded, so the figures below are incomplete. "
                   "This notice disappears once recent months are in.")

    def mon(ym):
        return f"{_GATS_MON[ym % 12]} {ym // 12}"

    options = {
        "Latest month": (last_ym, last_ym),
        "Last 3 months": (last_ym - 2, last_ym),
        "Last 12 months": (last_ym - 11, last_ym),
        f"{ly} year to date": (ly * 12, last_ym),
    }
    for y in range(ly - 1, first_ym // 12 - 1, -1):
        if y * 12 >= first_ym:
            options[f"{y} calendar year"] = (y * 12, y * 12 + 11)
    with c2:
        per_label = st.selectbox("Period", list(options), index=2, key=f"dest_period_{place['code']}_{com}")
    start, end = options[per_label]
    p_start, p_end = start - 12, end - 12                              # same months one year earlier
    have_prior = p_start >= first_ym

    cur = df[(df["ym"] >= start) & (df["ym"] <= end)]
    pri = df[(df["ym"] >= p_start) & (df["ym"] <= p_end)]
    by = cur.groupby("country").agg(vol=("vol", "sum"), value=("value", "sum")).reset_index()
    by["prior"] = by["country"].map(pri.groupby("country")["vol"].sum()).fillna(0.0)
    by = by[by["vol"] > 0].sort_values("vol", ascending=False)
    if by.empty:
        st.info(f"No {verb} in that period.")
        return
    total, prior_total = by["vol"].sum(), pri["vol"].sum()
    named = by[by["country"] != _DEST_CONFIDENTIAL]                   # 'Confidential' is a bucket, not a country
    conf_vol = float(by.loc[by["country"] == _DEST_CONFIDENTIAL, "vol"].sum())

    span = mon(start) if start == end else f"{mon(start)} – {mon(end)}"
    k1, k2, k3, k4 = st.columns([1.7, 1.2, 1.3, 0.8])             # the first label carries the period, so it gets the room
    k1.metric(f"{owner} {com_label.lower()} {verb} ({span})", f"{total:,.{dec}f} {unit}")
    if have_prior and prior_total > 0:
        k2.metric("vs same months a year earlier", f"{(total / prior_total - 1) * 100:+.1f}%",
                  help=f"{prior_total:,.{dec}f} {unit} in {mon(p_start)}" + ("" if p_start == p_end else f" – {mon(p_end)}"))
    if len(named):
        top = named.iloc[0]
        k3.metric(f"Largest {noun}", top["country"], f"{top['vol'] / total * 100:.0f}% of volume", delta_color="off")
    k4.metric(f"{Noun}s", f"{len(named)}", help="Countries that took any volume in the period" if flow == "E"
              else "Countries that supplied any volume in the period")
    if conf_vol > 0:
        st.caption(f"{conf_vol / total * 100:.0f}% of the volume is reported by {owner} as '{_DEST_CONFIDENTIAL}' "
                   f"(no {noun} given); it is shown as its own entry below and left out of the counts above.")

    top_n = st.slider(f"{Noun}s shown", 5, 25, 12, key="dest_topn")
    show = by.head(top_n)

    col_bar, col_trend = st.columns(2)
    with col_bar:
        st.markdown(f"#### Top {top_n} {noun}s — {span}")
        fig = go.Figure()
        if have_prior:
            fig.add_trace(go.Bar(y=show["country"], x=show["prior"], orientation="h", name="Year earlier",
                                 marker_color=_CH_MIST))
        fig.add_trace(go.Bar(y=show["country"], x=show["vol"], orientation="h", name=span, marker_color=_CH_BLUE,
                             text=show["vol"].apply(lambda v: f"{v:,.{dec}f}"), textposition="outside",
                             textfont=dict(color=_CH_MUTED, size=11), cliponaxis=False))
        fig.update_layout(**_ch_layout(None, None, unit, None, hover="closest", height=max(340, len(show) * 34),
                                       margin=dict(l=10, r=50, t=44, b=10)))
        fig.update_layout(barmode="group", legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                                                       bgcolor="rgba(0,0,0,0)", bordercolor="rgba(0,0,0,0)"))
        fig.update_xaxes(tickformat=",")
        fig.update_yaxes(autorange="reversed", showgrid=False, automargin=True)
        st.plotly_chart(fig, use_container_width=True)

    with col_trend:
        st.markdown(f"#### Monthly {verb} by {noun} — last 24 months")
        t_start = max(first_ym, last_ym - 23)
        win = df[df["ym"] >= t_start]
        lead = list(win.groupby("country")["vol"].sum().sort_values(ascending=False).head(7).index)
        win = win.assign(dest=win["country"].where(win["country"].isin(lead), "All other"))
        mo = win.groupby(["ym", "dest"])["vol"].sum().reset_index()
        fig2 = go.Figure()
        palette = _CH_PAL[:7] + [_CH_MIST]                     # the seven leading countries, then "All other"
        for i, d in enumerate(lead + ["All other"]):
            s = mo[mo["dest"] == d]
            if s.empty:
                continue
            fig2.add_trace(go.Bar(x=[mon(v) for v in s["ym"]], y=s["vol"], name=d, marker_color=palette[i]))
        fig2.update_layout(**_ch_layout(None, None, None, unit, height=max(340, len(show) * 34),
                                        margin=dict(l=10, r=10, t=10, b=10)))
        fig2.update_layout(barmode="stack", legend=dict(orientation="h", y=-0.2, bgcolor="rgba(0,0,0,0)", bordercolor="rgba(0,0,0,0)"))
        fig2.update_xaxes(categoryorder="array", categoryarray=[mon(v) for v in range(t_start, last_ym + 1)])
        fig2.update_yaxes(tickformat=",")
        st.plotly_chart(fig2, use_container_width=True)

    st.markdown(f"#### {Noun} detail — {span}")
    tbl = pd.DataFrame({
        Noun: by["country"],
        f"Volume ({unit})": by["vol"],
        "Share": by["vol"] / total * 100,
        f"Year earlier ({unit})": by["prior"] if have_prior else np.nan,
        "Change": ((by["vol"] / by["prior"] - 1) * 100).where(by["prior"] > 0) if have_prior else np.nan,
        "Value ($M)": by["value"] / 1e6,
        "$/MT": (by["value"] / (by["vol"] / scale * 1000)).where(by["vol"] > 0),
    })
    st.dataframe(
        tbl, hide_index=True, use_container_width=True,
        column_config={
            f"Volume ({unit})": st.column_config.NumberColumn(format=f"%,.{dec}f"),
            "Share": st.column_config.NumberColumn(format="%.1f%%"),
            f"Year earlier ({unit})": st.column_config.NumberColumn(format=f"%,.{dec}f"),
            "Change": st.column_config.NumberColumn(format="%+.1f%%"),
            "Value ($M)": st.column_config.NumberColumn(format="$%,.1f"),
            "$/MT": st.column_config.NumberColumn(format="$%,.0f"),
        },
    )
    if place["src"] == "gats":
        st.caption(
            f"Source: USDA FAS GATS (US Census Bureau monthly trade data), domestic exports by destination, data through {mon(last_ym)}. "
            "Corn = HS 1005 (incl. seed), soybeans = HS 1201, soybean meal = HS 2304, wheat = HS 1001 (incl. durum), sorghum = HS 1007, canola = HS 1205 (rapeseed/colza seed). "
            "Census revises months for up to a year, and countries are as Census reports them (e.g. EU members appear individually). "
            "Only destinations that have ever shown grain exports are loaded, so a brand-new destination can appear a month late."
        )
    else:
        st.caption(
            f"Source: Trade Data Monitor (TDM), {owner} customs {verb} by partner country, data through {mon(last_ym)}. "
            "Product groups: corn (ex. seed), soybeans (ex. seed), soybean meal, wheat (ex. seed). "
            "TDM revises recent months as customs data is finalised, and each country's own reporting lag differs. "
            + ("" if flow == "I" else f"Partners are the destination as {owner}'s customs record it; shipments to a "
               "transshipment hub (e.g. the Netherlands, Singapore) are not traced further.")
        )


def main():

    _white_mtime = os.path.getmtime(LOGO_WHITE_PATH) if os.path.exists(LOGO_WHITE_PATH) else 0
    _full_mtime  = os.path.getmtime(LOGO_FULL_PATH)  if os.path.exists(LOGO_FULL_PATH)  else 0
    logo_white_b64 = _load_logo_b64(LOGO_WHITE_PATH, _mtime=_white_mtime)
    logo_full_b64  = _load_logo_b64(LOGO_FULL_PATH,  _mtime=_full_mtime)

    # ── Header ───────────────────────────────────────────────────────────
    logo_img_tag = (
        f'<img src="{logo_full_b64}" '
        f'style="height:48px;width:auto;display:block;" alt="JSA Logo">'
        if logo_full_b64 else
        '<span style="font-size:22px;font-weight:700;color:#fff;'
        'font-family:Georgia,serif;">JSA</span>'
    )
    st.markdown(f"""
    <div style="background:#2c3e35;padding:14px 28px;margin-bottom:0;
                display:flex;align-items:center;gap:0;
                margin-left:-1rem;margin-right:-1rem;
                padding-left:2rem;padding-right:2rem;">
        <div style="flex-shrink:0;padding-right:24px;">
            {logo_img_tag}
        </div>
        <div style="border-left:1px solid rgba(255,255,255,0.25);
                    padding-left:24px;flex:1;">
            <div style="color:#ffffff;font-size:20px;font-weight:700;
                        font-family:Arial;letter-spacing:0.3px;line-height:1.2;">
                Global Agricultural Export Dashboard
            </div>
            <div style="color:rgba(255,255,255,0.55);font-size:10.5px;
                        font-family:Arial;letter-spacing:1px;
                        text-transform:uppercase;margin-top:3px;">
                John Stewart and Associates &nbsp;·&nbsp; Commodity Research Analytics
            </div>
        </div>
        <div style="flex-shrink:0;text-align:right;">
            <div style="color:rgba(255,255,255,0.9);font-size:11.5px;
                        font-weight:700;font-family:Arial;letter-spacing:0.3px;">
                Data Source
            </div>
            <div style="color:rgba(255,255,255,0.5);font-size:10.5px;
                        font-family:Arial;margin-top:2px;">
                USDA Foreign Agricultural Service
            </div>
        </div>
    </div>
    <div style="background:#243830;height:3px;
                margin-left:-1rem;margin-right:-1rem;
                margin-bottom:18px;"></div>
    """, unsafe_allow_html=True)

    # ── Refresh + Unit toggle ─────────────────────────────────────────────
    col_btn, col_toggle, col_note = st.columns([1, 1.6, 4.4])
    with col_btn:
        if st.button("🔄 Refresh Data", use_container_width=True):
            st.cache_data.clear()
            st.cache_resource.clear()
            st.toast("Data cache cleared — reloading…", icon="🔄")
            st.rerun()

    with col_toggle:
        use_bushels = st.toggle(
            "📐 Million Bushels (Mbu)",
            value=False,
            key="unit_toggle",
            help=(
                "Convert volumes to Million Bushels (Mbu).\n\n"
                "Corn: 56 lbs/bu  →  1 TMT ≈ 0.03937 Mbu\n"
                "Soybeans: 60 lbs/bu  →  1 TMT ≈ 0.03674 Mbu"
            ),
        )
    with col_note:
        st.caption(
            "Data sourced live from Census GATS, USDA FGIS, and TDM. "
            "Click **Refresh Data** to pull the latest figures."
        )

    unit_short    = "Mbu"  if use_bushels else "TMT"
    unit_long     = "Million Bushels (Mbu)" if use_bushels else "Thousand Metric Tons (TMT)"
    unit_decimals = 1      if use_bushels else 0

    # ── Top-level commodity tabs ──────────────────────────────────────────
    corn_tab, soy_tab, meal_tab, wheat_tab, china_tab, insp_tab, esr_tab, dest_tab, vessel_tab, ref_tab = st.tabs(
        ["🌽  Corn", "🫘  Soybeans", "🌾  Soybean Meal", "🌿  Wheat",
         "🇨🇳  China Imports", "🔍  US Inspections", "📈  Export Sales",
         "🧭  Destinations", "🚢  Vessel Lineup", "📅  Marketing Years"]
    )

    with corn_tab:
        _run_commodity_tab("corn", use_bushels, unit_short,
                           unit_decimals, unit_long, logo_white_b64)

    with soy_tab:
        _run_commodity_tab("soybeans", use_bushels, unit_short,
                           unit_decimals, unit_long, logo_white_b64)

    with meal_tab:
        _run_commodity_tab("soybeanmeal", use_bushels, unit_short,
                           unit_decimals, unit_long, logo_white_b64)

    with wheat_tab:
        try:
            _run_wheat_tab(use_bushels, unit_short, unit_decimals,
                           unit_long, logo_white_b64)
        except Exception as _e:
            st.error(f"Wheat tab error: {_e}")

    with china_tab:
        try:
            _run_china_imports_tab(logo_b64=logo_white_b64)
        except Exception as _e:
            st.error(f"China Imports tab error: {_e}")

    with insp_tab:
        _run_export_inspections_tab(use_bushels)

    with esr_tab:
        try:
            _run_export_sales_tab(use_bushels=use_bushels, unit_short=unit_short)
        except Exception as _e:
            st.error(f"Export Sales tab error: {_e}")

    with dest_tab:
        try:
            _run_destinations_tab(use_bushels, unit_short, unit_decimals)
        except Exception as _e:
            st.error(f"Destinations tab error: {_e}")

    with vessel_tab:
        try:
            _run_vessel_lineup_tab()
        except Exception as _e:
            st.error(f"Vessel Lineup tab error: {_e}")

    with ref_tab:
        try:
            _render_my_reference_tab()
        except Exception as _e:
            st.error(f"Reference tab error: {_e}")

    # ── Footer ────────────────────────────────────────────────────────────
    footer_logo = (
        f'<img src="{logo_white_b64}" style="height:36px;width:auto;opacity:0.85;" alt="JSA">'
        if logo_white_b64 else
        '<span style="font-family:Georgia,serif;font-size:18px;font-weight:700;'
        'color:#fff;">JSA</span>'
    )
    st.markdown(f"""
    <div style="background:{JSA_DARK};border-top:3px solid {JSA_GREEN};
                padding:22px 28px;border-radius:8px;margin-top:24px;
                font-family:Arial;display:flex;align-items:center;
                justify-content:space-between;flex-wrap:wrap;gap:16px;">
        <div style="display:flex;align-items:center;gap:18px;">
            {footer_logo}
            <div style="border-left:1px solid #484f56;padding-left:18px;">
                <div style="color:#fff;font-size:13px;font-weight:600;">
                    John Stewart and Associates</div>
                <div style="color:#7a8a9a;font-size:11px;margin-top:3px;">
                    Commodity Research &amp; Analytics</div>
            </div>
        </div>
        <div style="text-align:center;color:#5a6a7a;font-size:11px;line-height:1.6;">
            Sources: Census GATS · USDA FGIS · TDM · USDA FAS ESR<br>
            Stats reflect prior completed marketing years only — current year excluded.
        </div>
        <div style="text-align:right;color:#5a6a7a;font-size:11px;line-height:1.6;">
            <a href="https://www.jpsi.com" target="_blank"
               style="color:{JSA_CYAN};text-decoration:none;font-weight:600;">
               jpsi.com</a><br>info@jpsi.com
        </div>
    </div>
    """, unsafe_allow_html=True)
    _render_disclaimer()


def _render_disclaimer():
    st.markdown(
        f'<div style="font-family:inherit;margin-top:40px;padding:14px 20px;border-top:1px solid #2d3035;'
        f'color:#6b7280;font-size:inherit;line-height:1.6;">'
        f'Trading commodity futures, options on futures, cash commodities, and over-the-counter '
        f'derivative products involves substantial risk of loss and may not be suitable for all investors. '
        f'This communication is provided for informational purposes only and does not constitute investment '
        f'advice, a recommendation, or an offer or solicitation to buy or sell any futures, options, cash '
        f'commodities, or derivative products. John Stewart &amp; Associates, Inc. does not accept orders '
        f'to buy or sell any financial instruments via email. The information contained herein has been '
        f'obtained from sources believed to be reliable; however, its accuracy and completeness are not '
        f'guaranteed. Any opinions expressed are solely those of the author, are subject to change without '
        f'notice, and should not be relied upon as a basis for investment decisions. Past performance is '
        f'not indicative of future results. This message may contain confidential or proprietary '
        f'information intended solely for the use of the designated recipient. '
        f'&copy; John Stewart &amp; Associates, Inc. {datetime.now().year}'
        f'</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
