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
import urllib.parse
import urllib.request
import ssl
import json
from datetime import datetime
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
@st.cache_resource
def _get_sf_conn():
    """Return a live Snowflake connection (cached per session)."""
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
        pem = base64.b64decode(key_b64)
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
    )


@st.cache_data(ttl=3600, show_spinner=False)
def _load_tdm_from_snowflake(reporter: str, product_code: str) -> dict:
    """Read TDM monthly data from Snowflake → {(year, month): tmt}."""
    conn = _get_sf_conn()
    if conn is None:
        return {}
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT year, month, tmt FROM TDM_MONTHLY "
            "WHERE reporter=%s AND product_code=%s",
            (reporter, product_code),
        )
        return {(int(r[0]), int(r[1])): float(r[2]) for r in cur.fetchall()}
    except Exception:
        return {}

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
        "country_colors": {
            "US":            "#f9a825",
            "Brazil":        "#1b7e35",
            "Argentina":     "#4fc3f7",
            "Ukraine":       "#fdd835",
            "TotalNonUS":    "#7e57c2",
            "MajorExporter": "#ef5350",
        },
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
        "country_colors": {
            "US":            "#f9a825",
            "Brazil":        "#1b7e35",
            "Argentina":     "#4fc3f7",
            "TotalNonUS":    "#7e57c2",
            "MajorExporter": "#ef5350",
            "ChinaImports":  "#e53935",
        },
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
        "country_colors": {
            "US":            "#f9a825",
            "Brazil":        "#43a047",
            "Argentina":     "#4fc3f7",
            "TotalNonUS":    "#8d6e63",
            "MajorExporter": "#ef6c00",
        },
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
        "country_colors": {
            "US":            "#f9a825",
            "Canada":        "#ef5350",
            "EU":            "#1565c0",
            "Russia":        "#78909c",
            "Ukraine":       "#fdd835",
            "India":         "#ff6d00",
            "China":         "#b71c1c",
            "Argentina":     "#4fc3f7",
            "Australia":     "#4db6ac",
            "Brazil":        "#43a047",
            "TotalNonUS":    "#7e57c2",
            "MajorExporter": "#ef6c00",
        },
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


def _add_chart_watermark(fig: go.Figure, logo_b64: str | None) -> go.Figure:
    if logo_b64 is None:
        return fig
    fig.add_layout_image(dict(
        source=logo_b64, xref="paper", yref="paper",
        x=0.5, y=0.5, sizex=0.30, sizey=0.30,
        xanchor="center", yanchor="middle",
        opacity=0.10, layer="above",
    ))
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def load_data(commodity: str) -> pd.DataFrame:
    """Build monthly export DataFrame from APIs (Census GATS/FGIS for US; TDM for non-US)."""
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
    current MY that have FGIS-filled data but no Census GATS data.
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


@st.cache_data(ttl=3600, show_spinner=False)
def _load_us_auto(commodity: str) -> dict:
    """Blended GATS + FGIS US monthly exports → {(year, month): tmt}.
    GATS is primary (official, ~6-8 wk lag); FGIS extends forward for recent months.
    FGIS is capped at the previous complete calendar month — the current month is
    always partial (FGIS reports weekly; mid-month only a fraction of weeks are in).
    """
    now = pd.Timestamp.now()
    # Last fully-complete calendar month (current month is always partial in FGIS)
    prev = now - pd.offsets.MonthBegin(1)
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
    overflow-x: auto; max-height: 580px; overflow-y: auto;
    border-radius: 6px; border: 1px solid #484f56;
    font-family: Arial, sans-serif; font-size: 12.5px;
    line-height: 1.35; margin-bottom: 8px;
}
.corn-tbl { border-collapse: collapse; width: max-content; min-width: 100%; }
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
    background: #141c18; border: 1px solid #253328;
    border-radius: 8px; padding: 14px 18px; margin-bottom: 6px;
}
.vl-kpi-label {
    font-size: 10px; font-weight: 600; color: #7a9485;
    text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px;
}
.vl-kpi-value {
    font-size: 26px; font-weight: 700; color: #e8ede9;
    line-height: 1.1; margin: 0;
}
.vl-kpi-sub { font-size: 11px; color: #7a9485; margin-top: 2px; }
.vl-sec-hdr {
    font-size: 11px; font-weight: 700; color: #7a9485;
    text-transform: uppercase; letter-spacing: 0.09em;
    border-bottom: 1px solid #253328; padding-bottom: 6px; margin: 22px 0 14px 0;
}
</style>
"""

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
def _base_layout(title_text, x_title, y_title) -> dict:
    axis = dict(
        gridcolor="#484f56", linecolor="#484f56",
        tickcolor="#aaaaaa", tickfont=dict(color="#cccccc"),
        title_font=dict(color="#ffffff"), zerolinecolor="#484f56",
    )
    return dict(
        title=dict(text=title_text, font=dict(color="#ffffff", size=15, family="Arial")),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#32373c",
        font=dict(family="Arial", color="#ffffff", size=12),
        legend=dict(bgcolor="rgba(30,33,36,0.92)", bordercolor="#484f56",
                    borderwidth=1, font=dict(size=11)),
        margin=dict(l=70, r=20, t=55, b=60),
        hovermode="x unified",
        xaxis=dict(**axis, title=x_title),
        yaxis=dict(**axis, title=y_title, tickformat=","),
    )


def _year_style(year, cy, ly, all_years) -> tuple:
    if year == cy:
        return "#0693e3", 4.5, 1.0
    if year == ly:
        return "#ffffff", 3.0, 0.92
    other = [y for y in all_years if y != cy and y != ly]
    idx   = other.index(year) if year in other else 0
    n     = max(len(other) - 1, 1)
    t     = idx / n
    return (f"rgb({int(100+80*t)},{int(120+80*t)},{int(155+65*t)})",
            round(1.0 + 0.4 * t, 1), round(0.18 + 0.45 * t, 2))


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
            fill="toself", fillcolor="rgba(6,147,227,0.10)",
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
            line=dict(color="#0693e3", width=2.5, dash="dash"),
            marker=dict(symbol="diamond", size=6, color="#0693e3"),
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
                    line=dict(color="#f9a825", width=width, dash="dash"),
                    marker=dict(size=5, color="#f9a825", symbol="diamond"),
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
                    line=dict(color="#f9a825", width=1, dash="dot"),
                )
                fig.add_annotation(
                    x=months[boundary], y=1.02,
                    xref="x", yref="paper",
                    text="Est →",
                    font=dict(color="#f9a825", size=10),
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
                marker=dict(size=5 if is_key else 3, color=color,
                            line=dict(width=1.2, color="#32373c") if year == ly else None),
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
                    fill="toself", fillcolor="rgba(255,193,7,0.18)",
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
                line=dict(color="#fdd835", width=2.5, dash="dot"),
                marker=dict(symbol="diamond-open", size=7, color="#fdd835",
                            line=dict(width=2, color="#fdd835")),
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
                    line=dict(color="#00e676", width=2.0, dash="dashdot"),
                    marker=dict(symbol="triangle-up-open", size=7, color="#00e676",
                                line=dict(width=2, color="#00e676")),
                    connectgaps=True, legendrank=6,
                    customdata=m2_hover,
                    hovertemplate="%{x}: %{customdata}<extra>Pace-Adj Fcst</extra>",
                ))

    fig.update_layout(**_base_layout(
        f"Seasonal {lbl}Shipments — {field_label} ({unit_short})",
        "Month", f"{lbl}Volume ({unit_short})",
    ))
    fig.update_layout(legend=dict(bgcolor="#ffffff", bordercolor="#c9ced3", borderwidth=1,
                                  font=dict(size=11, color="#32373c")))
    _add_chart_watermark(fig, logo_b64)
    return fig


_BAR_COLORS = [
    "#1565c0","#2e7d32","#6a1b9a","#ad1457",
    "#00695c","#e65100","#37474f","#4527a0",
    "#558b2f","#00838f","#4e342e","#283593",
]


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
        fill="toself", fillcolor="rgba(6,147,227,0.08)",
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
        line=dict(color="#0693e3", width=2.5, dash="dot"),
        marker=dict(symbol="diamond", size=7, color="#0693e3"),
        customdata=oly_hover,
        hovertemplate="%{x}: %{customdata}<extra>6-Yr Olympic Avg</extra>",
    ))

    color_idx = 0
    for year in selected_years:
        vals  = [data_pivot[m].get(year) for m in months]
        color = "#f9a825" if year == cy else _BAR_COLORS[color_idx % len(_BAR_COLORS)]
        if year != cy:
            color_idx += 1
        bar_hover = [
            f"{v:,.{dec}f} {unit_short}" if v is not None else "—"
            for v in vals
        ]
        fig.add_trace(go.Bar(
            x=months, y=vals, name=year,
            marker_color=color, opacity=0.85,
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
        return ["#4caf50" if (v is not None and v >= 0) else "#ef5350" for v in vals]

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
        name="% vs Olympic Avg",
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
        name="% vs Last Year",
        marker_color=_colors(pct_lys),
        marker_line_width=0,
        opacity=0.55,
        text=[f"LY  {v:+.1f}%" if v is not None else "LY  —" for v in pct_lys],
        textposition="outside",
        cliponaxis=False,
        customdata=hover_ly,
        hovertemplate="%{y}: %{customdata}<extra>% vs Last Year</extra>",
    ))

    # Single zero-reference line across the whole chart
    fig.add_vline(x=0, line_color="#8a9aaa", line_width=1.2)

    fig.update_layout(
        barmode="group",
        height=fig_h,
        title=dict(
            text=(f"{commodity_label} — Cumulative Shipment Snapshot"
                  f"  ·  {selected_year_label}"),
            font=dict(size=14, color="#ffffff", family="Arial"),
            x=0.01,
        ),
        paper_bgcolor="#181c20",
        plot_bgcolor="#1d2227",
        font=dict(family="Arial", color="#d0d8e0", size=11),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="left", x=0,
            font=dict(color="#aab4c0", size=11),
            bgcolor="rgba(0,0,0,0)",
            itemsizing="constant",
        ),
        margin=dict(l=10, r=120, t=80, b=20),
        bargap=0.28,
        bargroupgap=0.06,
    )
    fig.update_xaxes(
        ticksuffix="%",
        gridcolor="#2e353d", gridwidth=0.5,
        zeroline=False,
        tickfont=dict(size=10),
        range=x_range,
    )
    fig.update_yaxes(
        gridcolor="#2e353d", gridwidth=0.5,
        tickfont=dict(size=11),
    )

    # Annotation note at bottom explaining opacity difference
    fig.add_annotation(
        text="■ Solid = % vs Olympic Avg     ■ Light = % vs Last Year"
             "     🟢 Above reference     🔴 Below reference",
        xref="paper", yref="paper",
        x=0.0, y=-0.04,
        xanchor="left", yanchor="top",
        showarrow=False,
        font=dict(size=10, color="#5a6878", family="Arial"),
    )

    _add_chart_watermark(fig, logo_b64)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# STAT TILES
# ─────────────────────────────────────────────────────────────────────────────
def _compute_tile_stats(df, use_bushels, unit_factor, cfg,
                        arbr_local_my: bool = True,
                        us_local_my: bool = True) -> list:
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
            f'line-height:1.1;font-family:Arial;">{m_vol}</div>'
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
            f'line-height:1.1;font-family:Arial;">{c_vol}</div>'
            f'<div style="font-size:9px;color:#3d4a58;margin:2px 0 5px;">'
            f'{unit_short}&nbsp;·&nbsp;'
            f'<span style="color:#5a6878;">thru&nbsp;{t["latest_month"]}</span>'
            f'&nbsp;<span style="color:#3d4a58;">{t["cy"]}&nbsp;({t["my_label"]})</span></div>'
            f'<div style="font-size:9.5px;display:flex;gap:6px;justify-content:center;">'
            f'{_pct_chip(t["pct_ly_c"],"LY")}&nbsp;{_pct_chip(t["pct_oly_c"],"Avg")}'
            f'</div></div>'

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
        dot_col  = f"rgb({int(80+80*t)},{int(100+80*t)},{int(140+70*t)})"
        fig.add_trace(go.Scatter(
            x=[ytd_h], y=[final_h],
            mode="markers+text",
            marker=dict(size=9, color=dot_col,
                        line=dict(color="#1e2124", width=1)),
            text=[yr], textposition="top center",
            textfont=dict(size=9, color=dot_col),
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
        line=dict(color="#78909c", width=1.5, dash="dot"),
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
        font=dict(size=9, color=accent_color, family="Arial"),
        xanchor="left", yanchor="top",
        bgcolor="rgba(30,33,36,0.75)",
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
        font=dict(size=9, color=accent_color, family="Arial"),
        xanchor="left", yanchor="bottom",
        bgcolor="rgba(30,33,36,0.75)",
    )

    # Projected CY star on regression line
    fig.add_trace(go.Scatter(
        x=[cy_ytd], y=[projected],
        mode="markers",
        marker=dict(size=14, symbol="star",
                    color=accent_color,
                    line=dict(color="#ffffff", width=1.5)),
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
        _ref_lines.append((pi["usda_total"],  "USDA Total",  "#ff1744", "dash"))
    if pi.get("model1_total"):
        _ref_lines.append((pi["model1_total"], "M1 Seasonal", "#fdd835", "dot"))
    if pi.get("has_ytd") and pi.get("model2_total"):
        _ref_lines.append((pi["model2_total"], "M2 Pace Adj", "#00e676", "dashdot"))

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
            font=dict(size=9, color=ref_col, family="Arial"),
            xanchor="right", yanchor="bottom",
            bgcolor="rgba(30,33,36,0.75)",
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
        st.error(f"Error loading {cfg['label']} data: {exc}")
        st.stop()

    FIELDS         = cfg["fields"]
    MAR_FEB_FIELDS = cfg["mar_feb_fields"]

    # ── Stat tiles ────────────────────────────────────────────────────────
    tile_stats = _compute_tile_stats(df, use_bushels, unit_factor, cfg,
                                     arbr_local_my=arbr_local_my,
                                     us_local_my=us_local_my)
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
        _, _snap_all_years = build_pivot(df, _snap_ref_field)
    except Exception:
        _snap_all_years = []

    _snap_cy     = _snap_all_years[-1] if _snap_all_years else None
    _snap_prior  = list(reversed(_snap_all_years[:-1])) if len(_snap_all_years) > 1 else []
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
                value=True,
                key=f"{pfx}_snap_local_my",
                help=(
                    "**ON** (default) — US uses Sep–Aug; "
                    f"AR/BR use {cfg['arbr_label']} (local harvest MY).\n\n"
                    "**OFF** — USDA Oct–Sep for all countries."
                ),
            )

        _is_cy_snap = _snap_sel.startswith("Current Year")
        _sel_yr     = None if _is_cy_snap else _snap_sel
        _my_tag     = f"Local MY" if _snap_local_my else "USDA Oct–Sep"
        _snap_label = (f"CY {_snap_cy} (YTD, {_my_tag})"
                       if _is_cy_snap else f"Full MY {_snap_sel} ({_my_tag})")

        _snap_data = _compute_snapshot_data(df, cfg, use_bushels, unit_factor,
                                            selected_year=_sel_yr,
                                            use_local_my=_snap_local_my)
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
    _est_legend_item = (
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
        f'<div style="padding:10px 0;font-family:Arial;font-size:12px;color:#8a9aaa;">'
        f'<b style="color:#fdd835;">●</b> Dotted yellow line = <b>USDA Seasonal</b>'
        f' — distributes total via historical monthly share %<br>'
        f'<b style="color:#00e676;">●</b> Green dash-dot line = <b>Pace-Adjusted</b>'
        f' — shifts forecast based on YTD pace vs seasonal baseline'
        f' (activates once official data exists)<br>'
        f'<b style="color:#fdd835; opacity:0.5;">▓</b> Shaded band ='
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
                    f'<span style="font-size:24px;font-weight:700;color:#e8ede9;">'
                    f'{usda_total:,.0f}</span><br>'
                    f'<span style="color:#555;font-size:11px;">{_src_lbl}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption("No WASDE forecast available for this field.")
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
        accent_color=cfg["tile_accents"].get(field, JSA_CYAN),
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
        st.error(f"Error loading Wheat data: {exc}")
        st.stop()

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
        f'<div style="padding:10px 0;font-family:Arial;font-size:12px;color:#8a9aaa;">'
        f'<b style="color:#fdd835;">●</b> Dotted yellow = <b>USDA Seasonal</b> forecast<br>'
        f'<b style="color:#00e676;">●</b> Green dash-dot = <b>Pace-Adjusted</b>'
        f' (activates once official data exists)<br>'
        f'<b style="color:#fdd835; opacity:0.5;">▓</b> Shaded band = ±0.5σ likely range'
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
                        f'<span style="font-size:24px;font-weight:700;color:#e8ede9;">'
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
        accent_color=cfg["tile_accents"].get(field, JSA_CYAN),
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
    "United States": "#0693e3",
    "Brazil":        "#4caf50",
    "Argentina":     "#f9a825",
    "Ukraine":       "#ef5350",
    "Russia":        "#ab47bc",
    "Australia":     "#26c6da",
    "Canada":        "#ff7043",
}
_FALLBACK_COLORS = [
    "#78909c","#a5d6a7","#ffe082","#ef9a9a",
    "#ce93d8","#80deea","#ffab91","#b0bec5",
]


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_tdm_china(product_code: str, password: str) -> pd.DataFrame:
    """Fetch China import data from TDM; cached 1 hour."""
    import urllib.request
    url = (
        f"{TDM_BASE}?username={TDM_USER}&password={password}"
        f"&reporter=CN&periodBegin=201401&periodEnd=202601"
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
            line=dict(color="#8a9aaa", width=2, dash="dot"),
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
            color, lw, op = "#0693e3", 4.0, 1.0
        elif my_s == ly_start:
            color, lw, op = "#ffffff", 2.5, 0.92
        else:
            idx = sorted_chart_starts.index(my_s)
            t   = idx / max(len(sorted_chart_starts) - 1, 1)
            color = f"rgb({int(100+80*t)},{int(120+80*t)},{int(155+65*t)})"
            lw, op = 1.5, 0.22 + 0.42 * t

        fig1.add_trace(go.Scatter(
            x=x_vals, y=y_vals, mode="lines+markers", name=lbl,
            line=dict(color=color, width=lw), opacity=op,
            marker=dict(size=5 if my_s == cur_my_start else 3),
            customdata=[f"{v:,.0f} TMT" for v in y_vals],
            hovertemplate="%{x}: %{customdata}<extra>" + lbl + "</extra>",
        ))

    my_conv = "Oct–Sep" if not is_wheat else "Jul–Jun"
    fig1.update_layout(
        height=420,
        plot_bgcolor="#1a1e22", paper_bgcolor="#1a1e22",
        font=dict(color="#c8d4e0", family="Arial", size=11),
        xaxis=dict(gridcolor="#2e353d", tickfont=dict(size=10),
                   categoryorder="array", categoryarray=my_labels),
        yaxis=dict(gridcolor="#2e353d", title="TMT", tickformat=",.0f"),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        margin=dict(l=60, r=20, t=30, b=40),
        hovermode="x unified",
        title=dict(
            text=f"Marketing Year convention: {my_conv}",
            font=dict(size=10, color="#8a9aaa"), x=1, xanchor="right", y=0.01,
        ),
    )
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

    fig2.update_layout(
        barmode="stack", height=380,
        plot_bgcolor="#1a1e22", paper_bgcolor="#1a1e22",
        font=dict(color="#c8d4e0", family="Arial", size=11),
        xaxis=dict(gridcolor="#2e353d", tickfont=dict(size=10),
                   categoryorder="array", categoryarray=my_labels),
        yaxis=dict(gridcolor="#2e353d", title="TMT", tickformat=",.0f"),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        margin=dict(l=60, r=20, t=30, b=40),
        hovermode="x unified",
    )
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
# EXPORT INSPECTIONS TAB  (USDA FGIS Socrata – enhanced)
# ─────────────────────────────────────────────────────────────────────────────

_FGIS_PORT_GROUP = {
    "MISSISSIPPI R.": "Gulf", "S. TEXAS": "Gulf", "N. TEXAS": "Gulf",
    "COLUMBIA R.": "Pacific", "PUGET SOUND": "Pacific",
    "S. ATLANTIC": "Atlantic",
    "INTERIOR": "Interior", "TOLEDO": "Interior",
}
_PORT_GROUP_COLORS = {
    "Gulf": "#0693e3", "Pacific": "#4a6741",
    "Atlantic": "#f9a825", "Interior": "#78909c", "Other": "#ab47bc",
}
_CARRIER_COLORS = {
    "VESSEL": "#0693e3", "SHIP": "#0693e3",
    "CONTAINER": "#4caf50", "RAIL": "#f9a825",
    "TRUCK": "#ef5350", "BARGE": "#ab47bc",
}
_INSP_GRAINS = {"Corn": "CORN", "Soybeans": "SOYBEANS", "Wheat": "WHEAT"}


@st.cache_data(ttl=3600, show_spinner=False)
def _load_fgis_detail(grain_code: str) -> pd.DataFrame:
    """FGIS Socrata detailed inspections → DataFrame(year,month,week,dest,port,carrier,mt)."""
    import http.client
    out = []
    limit = 50_000
    for offset in range(0, 200_000, limit):
        params = urllib.parse.urlencode({
            "$select": "year,month,week,destination,port,type_carrier_text,sum(mt) as total_mt",
            "$where":  f"grain='{grain_code}'",
            "$group":  "year,month,week,destination,port,type_carrier_text",
            "$limit":  str(limit),
            "$offset": str(offset),
        })
        try:
            ctx  = ssl.create_default_context()
            conn = http.client.HTTPSConnection(_SOCRATA_HOST, 443, context=ctx, timeout=30)
            conn.request("GET", f"{_SOCRATA_PATH}?{params}",
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                         "Connection": "close"})
            resp = conn.getresponse()
            body = resp.read(10_000_000)
            conn.close()
            if resp.status != 200:
                break
            rows = json.loads(body.decode("utf-8"))
            if not rows:
                break
            for r in rows:
                try:
                    out.append({
                        "year":    int(r["year"]),
                        "month":   int(r["month"]),
                        "week":    int(r["week"]),
                        "dest":    r.get("destination", "OTHER").strip().title(),
                        "port":    r.get("port", "").strip(),
                        "carrier": r.get("type_carrier_text", "").strip().upper(),
                        "mt":      float(r["total_mt"]),
                    })
                except Exception:
                    pass
            if len(rows) < limit:
                break
        except Exception:
            break
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    df["port_group"] = df["port"].map(_FGIS_PORT_GROUP).fillna("Other")
    return df


def _fgis_add_my(df: pd.DataFrame, grain_label: str) -> pd.DataFrame:
    """Add 'my' (marketing year start) and 'my_week' (1-52/53) columns."""
    df = df.copy()
    is_wheat = grain_label.lower() == "wheat"
    if is_wheat:
        df["my"] = df.apply(lambda r: r["year"] if r["month"] >= 7 else r["year"] - 1, axis=1)
        my_months_map = {7:1,8:2,9:3,10:4,11:5,12:6,1:7,2:8,3:9,4:10,5:11,6:12}
    else:
        df["my"] = df.apply(lambda r: r["year"] if r["month"] >= 10 else r["year"] - 1, axis=1)
        my_months_map = {10:1,11:2,12:3,1:4,2:5,3:6,4:7,5:8,6:9,7:10,8:11,9:12}
    df["my_month_pos"] = df["month"].map(my_months_map)
    df["my_label"] = df["my"].apply(lambda y: f"{y}/{str(y+1)[-2:]}")
    return df


def _insp_tile(label, value, sub="", delta=None, accent=None):
    accent = accent or JSA_CYAN
    d = ""
    if delta is not None:
        c = "#4caf50" if delta >= 0 else "#ef5350"
        s = "+" if delta >= 0 else ""
        d = f'<div style="font-size:10px;color:{c};font-weight:700;margin-top:3px;">{s}{delta:.1f}%</div>'
    return (
        f'<div style="background:#1e2124;border:1px solid #2e353d;'
        f'border-top:3px solid {accent};border-radius:6px;'
        f'padding:12px 16px;text-align:center;flex:1;min-width:140px;">'
        f'<div style="font-size:9px;font-weight:700;color:#5a6878;'
        f'text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px;">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;color:{JSA_CYAN};'
        f'line-height:1.1;font-family:Arial;">{value}</div>'
        f'<div style="font-size:10px;color:#5a6878;margin-top:2px;">{sub}</div>'
        f'{d}</div>'
    )


def _run_export_inspections_tab():
    """Enhanced FGIS Export Inspections tab — full drill-down filters."""

    _DIV = '<div style="border-top:1px solid #2e353d;margin:6px 0 16px;"></div>'

    # ── Top controls ──────────────────────────────────────────────────────────
    c1, c2 = st.columns([2, 2])
    with c1:
        grain_label = st.selectbox("Commodity", list(_INSP_GRAINS.keys()),
                                   key="insp_grain")
    grain_code = _INSP_GRAINS[grain_label]

    with st.spinner(f"Loading {grain_label} inspection data…"):
        raw = _load_fgis_detail(grain_code)

    if raw.empty:
        st.warning("No FGIS inspection data available. Try refreshing.")
        return

    df = _fgis_add_my(raw, grain_label)

    all_my = sorted(df["my"].unique(), reverse=True)
    cur_my = all_my[0]
    my_labels = [
        f"Current MY YTD ({cur_my}/{str(cur_my+1)[-2:]})" if m == cur_my
        else f"{m}/{str(m+1)[-2:]}"
        for m in all_my
    ]
    with c2:
        my_sel_label = st.selectbox("Marketing Year", my_labels, key="insp_my")
    sel_my = all_my[my_labels.index(my_sel_label)]

    # ── Filter panel ──────────────────────────────────────────────────────────
    df_my_full = df[df["my"] == sel_my].copy()

    all_dests    = sorted(df_my_full["dest"].dropna().unique().tolist())
    all_ports    = sorted(df_my_full["port_group"].dropna().unique().tolist())
    all_carriers = sorted(df_my_full["carrier"].dropna().unique().tolist())
    all_weeks    = sorted(df_my_full["week"].dropna().unique().tolist())

    with st.expander("🔽  Filters", expanded=True):
        fa, fb, fc = st.columns(3)
        with fa:
            sel_dests = st.multiselect(
                "Destination", all_dests, default=[],
                key="insp_dest",
                help="Leave blank to include all destinations",
            )
        with fb:
            sel_ports = st.multiselect(
                "Port Region", all_ports, default=[],
                key="insp_port",
                help="Leave blank to include all port regions",
            )
        with fc:
            sel_carriers = st.multiselect(
                "Carrier Type", all_carriers, default=[],
                key="insp_carrier",
                help="Leave blank to include all carrier types",
            )
        fd, fe = st.columns(2)
        with fd:
            week_range = st.slider(
                "Week Range (ISO week)",
                min_value=int(min(all_weeks)) if all_weeks else 1,
                max_value=int(max(all_weeks)) if all_weeks else 52,
                value=(int(min(all_weeks)) if all_weeks else 1,
                       int(max(all_weeks)) if all_weeks else 52),
                key="insp_week_range",
            )
        with fe:
            top_n_dests = st.slider("Top N destinations", 5, 40, 20,
                                    key="insp_top_n")

    # Apply filters
    df_my = df_my_full.copy()
    if sel_dests:
        df_my = df_my[df_my["dest"].isin(sel_dests)]
    if sel_ports:
        df_my = df_my[df_my["port_group"].isin(sel_ports)]
    if sel_carriers:
        df_my = df_my[df_my["carrier"].isin(sel_carriers)]
    df_my = df_my[(df_my["week"] >= week_range[0]) & (df_my["week"] <= week_range[1])]

    active_filters = bool(sel_dests or sel_ports or sel_carriers
                          or week_range != (min(all_weeks, default=1),
                                            max(all_weeks, default=52)))
    if active_filters:
        st.caption(
            f"Filters active — showing {len(df_my):,} rows "
            f"({df_my['mt'].sum()/1000:,.0f} TMT)"
        )

    if df_my.empty:
        st.warning("No data matches current filters.")
        return

    # ── Stat tiles (always from unfiltered MY for context; filtered for value) ─
    latest_week = int(df_my_full["week"].max())
    prev_week   = latest_week - 1 if latest_week > 1 else None

    cur_wk_mt  = df_my[df_my["week"] == latest_week]["mt"].sum() / 1000
    prev_wk_mt_raw = df_my_full[df_my_full["week"] == prev_week]["mt"].sum() / 1000 if prev_week else None
    ytd_mt     = df_my["mt"].sum() / 1000

    prior_my   = sel_my - 1
    df_prior_full = df[(df["my"] == prior_my) & (df["week"] <= latest_week)]
    if sel_dests:
        df_prior_full = df_prior_full[df_prior_full["dest"].isin(sel_dests)]
    if sel_ports:
        df_prior_full = df_prior_full[df_prior_full["port_group"].isin(sel_ports)]
    if sel_carriers:
        df_prior_full = df_prior_full[df_prior_full["carrier"].isin(sel_carriers)]
    prior_ytd = df_prior_full["mt"].sum() / 1000 if not df_prior_full.empty else None

    wow_delta = ((cur_wk_mt / prev_wk_mt_raw - 1) * 100) if prev_wk_mt_raw else None
    ytd_delta = ((ytd_mt / prior_ytd - 1) * 100) if prior_ytd else None

    dest_totals = df_my.groupby("dest")["mt"].sum()
    top_dest    = dest_totals.idxmax() if not dest_totals.empty else "—"
    top_dest_mt = dest_totals.max() / 1000 if not dest_totals.empty else 0

    tiles_html = (
        _insp_tile("Latest Week", f"{cur_wk_mt:,.0f}", "TMT", wow_delta, JSA_CYAN) +
        _insp_tile("YTD Total", f"{ytd_mt:,.0f}", f"TMT · thru wk {latest_week}", ytd_delta, JSA_GREEN) +
        _insp_tile("Top Destination", top_dest, f"{top_dest_mt:,.0f} TMT YTD", accent="#f9a825") +
        _insp_tile("Weeks in Range", str(int(df_my["week"].nunique())),
                   f"wk {week_range[0]}–{week_range[1]}", accent="#78909c")
    )
    st.markdown(
        f'<div style="display:flex;gap:10px;margin:12px 0 18px;">{tiles_html}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(_DIV, unsafe_allow_html=True)

    # ── Monthly labels for x-axis ─────────────────────────────────────────────
    _my_month_labels = (
        ["Jul","Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun"]
        if grain_label == "Wheat"
        else ["Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep"]
    )

    # ── Charts row 1: Port breakdown + Carrier breakdown ──────────────────────
    col_port, col_carr = st.columns(2)

    with col_port:
        st.markdown("#### Port Region by Month")
        port_df = (
            df_my.groupby(["my_month_pos", "port_group"])["mt"]
            .sum().reset_index()
        )
        port_df["tmt"] = port_df["mt"] / 1000
        fig_port = go.Figure()
        for pg in ["Gulf", "Pacific", "Atlantic", "Interior", "Other"]:
            sub = port_df[port_df["port_group"] == pg].sort_values("my_month_pos")
            if sub.empty:
                continue
            fig_port.add_trace(go.Bar(
                x=sub["my_month_pos"], y=sub["tmt"],
                name=pg, marker_color=_PORT_GROUP_COLORS.get(pg, "#78909c"),
            ))
        fig_port.update_layout(
            barmode="stack",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cdd9e5", size=11),
            xaxis=dict(title="MY Month", gridcolor="#2e353d",
                       tickmode="array", tickvals=list(range(1, 13)),
                       ticktext=_my_month_labels),
            yaxis=dict(title="TMT", gridcolor="#2e353d", showgrid=True),
            legend=dict(bgcolor="rgba(0,0,0,0)"),
            margin=dict(l=10, r=10, t=10, b=10), height=320,
        )
        st.plotly_chart(fig_port, use_container_width=True)

    with col_carr:
        st.markdown("#### Carrier Type")
        carr_df = (
            df_my.groupby("carrier")["mt"].sum()
            .reset_index().sort_values("mt", ascending=False)
        )
        carr_df["tmt"] = carr_df["mt"] / 1000
        colors = [_CARRIER_COLORS.get(c, "#8a9aaa") for c in carr_df["carrier"]]
        fig_carr = go.Figure(go.Pie(
            labels=carr_df["carrier"], values=carr_df["tmt"],
            marker=dict(colors=colors, line=dict(color="#1e2124", width=2)),
            hole=0.45,
            textfont=dict(color="#cdd9e5", size=11),
            hovertemplate="%{label}: %{value:,.0f} TMT (%{percent})<extra></extra>",
        ))
        fig_carr.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cdd9e5", size=11),
            legend=dict(bgcolor="rgba(0,0,0,0)"),
            margin=dict(l=10, r=10, t=10, b=10), height=320,
        )
        st.plotly_chart(fig_carr, use_container_width=True)

    # ── Top destinations bar ──────────────────────────────────────────────────
    st.markdown("#### Top Destinations (YTD)")
    dest_df = (
        df_my.groupby("dest")["mt"].sum().reset_index()
        .sort_values("mt", ascending=True)
    )
    dest_df["tmt"] = dest_df["mt"] / 1000
    top_n = dest_df.tail(top_n_dests)

    fig_dest = go.Figure(go.Bar(
        x=top_n["tmt"], y=top_n["dest"],
        orientation="h", marker_color=JSA_CYAN,
        text=top_n["tmt"].apply(lambda v: f"{v:,.0f}"),
        textposition="outside",
        hovertemplate="%{y}: %{x:,.0f} TMT<extra></extra>",
    ))
    fig_dest.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#cdd9e5", size=11),
        xaxis=dict(title="TMT", gridcolor="#2e353d", showgrid=True),
        yaxis=dict(gridcolor="#2e353d", showgrid=False),
        margin=dict(l=10, r=10, t=10, b=10),
        height=max(300, len(top_n) * 22),
    )
    st.plotly_chart(fig_dest, use_container_width=True)

    # ── Destination × Port Region stacked bar ─────────────────────────────────
    st.markdown(_DIV, unsafe_allow_html=True)
    st.markdown("#### Destination by Port Region")

    dest_port_df = (
        df_my.groupby(["dest", "port_group"])["mt"].sum().reset_index()
    )
    dest_port_df["tmt"] = dest_port_df["mt"] / 1000
    # Restrict to top N dests
    top_dest_names = (
        dest_port_df.groupby("dest")["tmt"].sum()
        .nlargest(top_n_dests).index.tolist()
    )
    dest_port_df = dest_port_df[dest_port_df["dest"].isin(top_dest_names)]
    dest_order = (
        dest_port_df.groupby("dest")["tmt"].sum()
        .sort_values().index.tolist()
    )

    fig_dp = go.Figure()
    for pg in ["Gulf", "Pacific", "Atlantic", "Interior", "Other"]:
        sub = dest_port_df[dest_port_df["port_group"] == pg]
        sub = sub.set_index("dest").reindex(dest_order).reset_index()
        fig_dp.add_trace(go.Bar(
            x=sub["tmt"], y=sub["dest"],
            orientation="h", name=pg,
            marker_color=_PORT_GROUP_COLORS.get(pg, "#78909c"),
        ))
    fig_dp.update_layout(
        barmode="stack",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#cdd9e5", size=11),
        xaxis=dict(title="TMT", gridcolor="#2e353d", showgrid=True),
        yaxis=dict(gridcolor="#2e353d", showgrid=False),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=10, r=10, t=10, b=10),
        height=max(320, len(dest_order) * 22),
    )
    st.plotly_chart(fig_dp, use_container_width=True)

    # ── Seasonal cumulative chart ─────────────────────────────────────────────
    st.markdown(_DIV, unsafe_allow_html=True)
    st.markdown("#### Seasonal Cumulative Inspections")

    comp_my_opts = [f"{m}/{str(m+1)[-2:]}" for m in all_my if m != sel_my]
    comp_sel = st.multiselect(
        "Compare Marketing Years", comp_my_opts,
        default=comp_my_opts[:5], key="insp_comp_my"
    )
    comp_starts = [int(l.split("/")[0]) for l in comp_sel]
    all_chart_my = sorted(set([sel_my] + comp_starts))

    # Apply same dest/port/carrier filters to seasonal data too
    df_seas = df.copy()
    if sel_dests:
        df_seas = df_seas[df_seas["dest"].isin(sel_dests)]
    if sel_ports:
        df_seas = df_seas[df_seas["port_group"].isin(sel_ports)]
    if sel_carriers:
        df_seas = df_seas[df_seas["carrier"].isin(sel_carriers)]

    wk_df = (
        df_seas.groupby(["my", "week"])["mt"].sum().reset_index()
        .sort_values(["my", "week"])
    )
    wk_df["tmt"] = wk_df["mt"] / 1000

    _SEAS_PALETTE = [
        "#0693e3","#4a6741","#f9a825","#ef5350","#ab47bc",
        "#26c6da","#ff7043","#78909c","#a5d6a7","#ffe082",
    ]
    fig_seas = go.Figure()
    for idx, my_yr in enumerate(sorted(all_chart_my, reverse=True)):
        sub = wk_df[wk_df["my"] == my_yr].copy()
        if sub.empty:
            continue
        sub["cum_tmt"] = sub["tmt"].cumsum()
        is_cur = (my_yr == sel_my)
        my_lbl = f"{my_yr}/{str(my_yr+1)[-2:]}"
        color = JSA_CYAN if is_cur else _SEAS_PALETTE[idx % len(_SEAS_PALETTE)]
        fig_seas.add_trace(go.Scatter(
            x=sub["week"], y=sub["cum_tmt"],
            mode="lines", name=my_lbl,
            line=dict(color=color, width=2.5 if is_cur else 1.3,
                      dash="solid" if is_cur else "dot"),
            opacity=1.0 if is_cur else 0.7,
            hovertemplate=f"{my_lbl} · Wk %{{x}}: %{{y:,.0f}} TMT<extra></extra>",
        ))
    fig_seas.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#cdd9e5", size=11),
        xaxis=dict(title="ISO Week of Year", gridcolor="#2e353d", showgrid=True),
        yaxis=dict(title="Cumulative TMT", gridcolor="#2e353d", showgrid=True),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=10, r=10, t=10, b=10), height=400,
    )
    st.plotly_chart(fig_seas, use_container_width=True)

    # ── Detail table ──────────────────────────────────────────────────────────
    st.markdown(_DIV, unsafe_allow_html=True)
    st.markdown("#### Destination Detail Table")
    tbl_df = (
        df_my.groupby(["dest", "port_group", "carrier"])["mt"]
        .sum().reset_index().sort_values("mt", ascending=False)
    )
    tbl_df["tmt"] = (tbl_df["mt"] / 1000).round(1)
    tbl_df = tbl_df.rename(columns={
        "dest": "Destination", "port_group": "Port Region",
        "carrier": "Carrier", "tmt": "TMT",
    })[["Destination", "Port Region", "Carrier", "TMT"]]

    dl_col, _ = st.columns([1, 6])
    with dl_col:
        st.download_button(
            "⬇ CSV", data=tbl_df.to_csv(index=False),
            file_name=f"inspections_{grain_label}_{sel_my}.csv",
            mime="text/csv", key="dl_insp_tbl",
        )
    st.dataframe(tbl_df, use_container_width=True, height=360)


# ─────────────────────────────────────────────────────────────────────────────
# EXPORT SALES TAB  (USDA FAS ESR API)
# ─────────────────────────────────────────────────────────────────────────────

_ESR_GATEWAY = "https://api.fas.usda.gov"
_ESR_API_KEY = "6hcX4IEwu7kreNrxoGQnPhctd6ozXmoPQTjk6Ewi"
_ESR_COMMODITIES = {
    "Corn":         401,
    "Soybeans":     801,
    "Soybean Meal": 901,
    "Wheat - All":  107,
    "Sorghum":      701,
}
_ESR_BU_FACTOR = {401: 39.368, 801: 36.744, 107: 36.744, 701: 39.368}
# MT → MBU per 1 MT (multiply tmt by factor to get MBU)

# ── Vessel Lineup constants ───────────────────────────────────────────────────
_VL_GREEN      = "#5e7164"
_VL_GREEN_LT   = "#8db89a"
_VL_BG         = "#141c18"
_VL_BORDER     = "#253328"
_VL_TEXT       = "#e8ede9"
_VL_MUTED      = "#7a9485"
_VL_COL_POS    = "#8db89a"
_VL_COL_NEG    = "#e07070"
_VL_COL_AMB    = "#c4b456"
_VL_COL_BLUE   = "#6fa8c4"
_VL_COL_PURP   = "#9b89c4"
_VL_COL_ORG    = "#c4896a"
_VL_COL_TEAL   = "#6ac4b8"
_VL_COMM_COLORS = {
    "Corn":          _VL_COL_AMB,
    "Wheat":         "#e8c96a",
    "Soybeans/Meal": _VL_COL_POS,
    "Sorghum":       _VL_COL_ORG,
    "Dist. Grains":  _VL_COL_PURP,
    "Rice":          "#c46a8d",
    "Mixed Cargo":   _VL_COL_BLUE,
    "Other":         "#5a6660",
}
_VL_REGION_COLORS = {
    "USG": _VL_COL_BLUE,
    "PNW": _VL_COL_POS,
    "TXG": _VL_COL_AMB,
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
    plot_bgcolor="#1a2620",
    paper_bgcolor=_VL_BG,
    font=dict(color=_VL_MUTED, family="Arial"),
    xaxis=dict(gridcolor=_VL_BORDER, linecolor=_VL_BORDER,
               tickfont=dict(color=_VL_MUTED)),
    yaxis=dict(gridcolor=_VL_BORDER, linecolor=_VL_BORDER,
               tickfont=dict(color=_VL_MUTED)),
    legend=dict(bgcolor="#1a2620", bordercolor=_VL_BORDER, borderwidth=1,
                font=dict(color=_VL_TEXT)),
    margin=dict(l=10, r=10, t=36, b=10),
    title_font=dict(size=12, color=_VL_MUTED),
    title_x=0,
)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_esr_countries() -> dict:
    """ESR country code → description."""
    try:
        url = f"{_ESR_GATEWAY}/api/esr/countries?api_key={_ESR_API_KEY}"
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
        return {c["countryCode"]: c["countryDescription"].strip() for c in data}
    except Exception:
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def _load_esr_release_dates() -> list:
    try:
        url = f"{_ESR_GATEWAY}/api/esr/datareleasedates?api_key={_ESR_API_KEY}"
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def _load_esr_exports(commodity_code: int, market_year: int) -> list:
    """ESR weekly export sales for one commodity + market year → list of records."""
    try:
        url = (
            f"{_ESR_GATEWAY}/api/esr/exports/commodityCode/{commodity_code}"
            f"/allCountries/marketYear/{market_year}?api_key={_ESR_API_KEY}"
        )
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except Exception:
        return []


def _run_export_sales_tab(use_bushels=False, unit_short="TMT"):
    """Weekly Export Sales tab — USDA FAS ESR API with full drill-down filters."""

    _DIV = '<div style="border-top:1px solid #2e353d;margin:6px 0 16px;"></div>'
    dec  = 1 if use_bushels else 0

    # ── Connectivity check ────────────────────────────────────────────────────
    with st.spinner("Loading ESR release dates…"):
        release_dates = _load_esr_release_dates()
    if not release_dates:
        st.error("⚠️ Unable to reach the USDA FAS ESR API. Try refreshing.")
        return

    # ── Top controls: Commodity + Market Year ─────────────────────────────────
    c1, c2 = st.columns([2, 2])
    with c1:
        commodity = st.selectbox(
            "Commodity", list(_ESR_COMMODITIES.keys()), key="esr_commodity"
        )
    comm_code = _ESR_COMMODITIES[commodity]
    bu_factor = _ESR_BU_FACTOR.get(comm_code)

    comm_dates = sorted(
        [d for d in release_dates if d["commodityCode"] == comm_code],
        key=lambda d: d["marketYear"], reverse=True,
    )
    available_years = [d["marketYear"] for d in comm_dates]
    cur_my = available_years[0] if available_years else pd.Timestamp.now().year

    with c2:
        my_opts = [
            f"Current MY ({cur_my})" if y == cur_my else str(y)
            for y in available_years
        ]
        my_sel_label = st.selectbox("Market Year", my_opts, key="esr_my")
    sel_my_year = available_years[my_opts.index(my_sel_label)]

    # ── Fetch ─────────────────────────────────────────────────────────────────
    with st.spinner(f"Fetching {commodity} MY {sel_my_year}…"):
        records       = _load_esr_exports(comm_code, sel_my_year)
        country_names = _load_esr_countries()

    if not records:
        st.warning(f"No ESR data for {commodity} MY {sel_my_year}.")
        return

    def _cn(code):
        return country_names.get(code, str(code)).title()

    def _parse_esr(recs, my_year):
        rows = []
        for r in recs:
            try:
                rows.append({
                    "country_code": r["countryCode"],
                    "country":      _cn(r["countryCode"]),
                    "week_date":    pd.Timestamp(r["weekEndingDate"]),
                    "net_sales":    r.get("currentMYNetSales", 0) / 1000,
                    "exports":      r.get("weeklyExports", 0) / 1000,
                    "gross_sales":  r.get("grossNewSales", 0) / 1000,
                    "outstanding":  r.get("outstandingSales", 0) / 1000,
                    "ytd_exports":  r.get("accumulatedExports", 0) / 1000,
                    "ytd_commit":   r.get("currentMYTotalCommitment", 0) / 1000,
                    "next_sales":   r.get("nextMYNetSales", 0) / 1000,
                    "next_outstanding": r.get("nextMYOutstandingSales", 0) / 1000,
                    "my_year":      my_year,
                })
            except Exception:
                pass
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    df_full = _parse_esr(records, sel_my_year)
    if df_full.empty:
        st.warning("Could not parse ESR records.")
        return

    numeric_cols = ["net_sales","exports","gross_sales","outstanding",
                    "ytd_exports","ytd_commit","next_sales","next_outstanding"]
    if use_bushels and bu_factor:
        df_full[numeric_cols] = df_full[numeric_cols] * bu_factor

    all_countries = sorted(df_full["country"].dropna().unique().tolist())
    all_weeks     = sorted(df_full["week_date"].unique())
    latest_date   = df_full["week_date"].max()
    latest_date_str = latest_date.strftime("%b %d, %Y")

    # ── Metric definitions ────────────────────────────────────────────────────
    _ESR_METRICS = {
        "Net Sales":             "net_sales",
        "Gross Sales":           "gross_sales",
        "Weekly Exports":        "exports",
        "Outstanding Sales":     "outstanding",
        "YTD Exports":           "ytd_exports",
        "YTD Commitment":        "ytd_commit",
        "New Crop Net Sales":    "next_sales",
        "New Crop Outstanding":  "next_outstanding",
    }
    _METRIC_COLORS = {
        "net_sales":       JSA_CYAN,
        "gross_sales":     "#26c6da",
        "exports":         JSA_GREEN,
        "outstanding":     "#f9a825",
        "ytd_exports":     "#ab47bc",
        "ytd_commit":      "#ff7043",
        "next_sales":      "#78909c",
        "next_outstanding":"#a5d6a7",
    }

    # ── Filter panel ──────────────────────────────────────────────────────────
    with st.expander("🔽  Filters", expanded=True):
        fa, fb = st.columns([3, 2])
        with fa:
            sel_countries = st.multiselect(
                "Destination Country", all_countries, default=[],
                key="esr_countries",
                help="Leave blank to include all countries",
            )
        with fb:
            top_n_dest = st.slider("Top N destinations", 5, 40, 20,
                                   key="esr_top_n")

        fc, fd = st.columns(2)
        with fc:
            wk_metrics = st.multiselect(
                "Weekly chart — metrics to show",
                list(_ESR_METRICS.keys()),
                default=["Net Sales", "Weekly Exports"],
                key="esr_wk_metrics",
            )
        with fd:
            cum_metric_label = st.selectbox(
                "Cumulative chart — metric",
                ["YTD Exports", "YTD Commitment", "Net Sales", "Outstanding Sales"],
                key="esr_cum_metric",
            )

        fe, _ = st.columns([3, 1])
        with fe:
            comp_my_opts = [str(y) for y in available_years if y != sel_my_year]
            comp_my_sel = st.multiselect(
                "Compare additional Market Years (cumulative chart)",
                comp_my_opts,
                default=comp_my_opts[:2],
                key="esr_comp_my",
            )

    # Apply destination filter
    df = df_full.copy()
    if sel_countries:
        df = df[df["country"].isin(sel_countries)]

    if df.empty:
        st.warning("No data matches current filters.")
        return

    # ── Latest / prior week snapshots ─────────────────────────────────────────
    def _sum(frame, col):
        return float(frame[col].sum()) if not frame.empty else 0.0

    def _delta(cur, prev):
        return ((cur / prev - 1) * 100) if prev and prev != 0 else None

    latest    = df[df["week_date"] == latest_date]
    prior_dates = sorted(df["week_date"].unique())
    prev_date   = prior_dates[-2] if len(prior_dates) >= 2 else None
    prev        = df[df["week_date"] == prev_date] if prev_date else pd.DataFrame()

    wk_net  = _sum(latest, "net_sales")
    wk_exp  = _sum(latest, "exports")
    wk_out  = _sum(latest, "outstanding")
    wk_ytd  = _sum(latest, "ytd_exports")
    wk_comm = _sum(latest, "ytd_commit")

    pw_net  = _sum(prev, "net_sales")
    pw_exp  = _sum(prev, "exports")

    # Prior MY for YoY
    with st.spinner(""):
        prior_recs = _load_esr_exports(comm_code, sel_my_year - 1)
    df_py = _parse_esr(prior_recs, sel_my_year - 1)
    if use_bushels and bu_factor and not df_py.empty:
        df_py[numeric_cols] = df_py[numeric_cols] * bu_factor
    if sel_countries and not df_py.empty:
        df_py = df_py[df_py["country"].isin(sel_countries)]
    py_latest = df_py[df_py["week_date"] == latest_date] if not df_py.empty else pd.DataFrame()
    py_ytd    = _sum(py_latest, "ytd_exports")

    # ── Stat tiles ────────────────────────────────────────────────────────────
    def _esr_tile(label, val, sub="", pct=None, pct_label="WoW", acc=None):
        acc = acc or JSA_CYAN
        d = ""
        if pct is not None:
            c = "#4caf50" if pct >= 0 else "#ef5350"
            s = "+" if pct >= 0 else ""
            d = f'<div style="font-size:10px;color:{c};font-weight:700;margin-top:3px;">{s}{pct:.1f}% {pct_label}</div>'
        v = f"{val:,.{dec}f}"
        return (
            f'<div style="background:#1e2124;border:1px solid #2e353d;'
            f'border-top:3px solid {acc};border-radius:6px;'
            f'padding:12px 16px;text-align:center;flex:1;min-width:140px;">'
            f'<div style="font-size:9px;font-weight:700;color:#5a6878;'
            f'text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px;">{label}</div>'
            f'<div style="font-size:22px;font-weight:700;color:{JSA_CYAN};'
            f'line-height:1.1;font-family:Arial;">{v}</div>'
            f'<div style="font-size:10px;color:#5a6878;margin-top:2px;">{sub}</div>'
            f'{d}</div>'
        )

    tiles_html = (
        _esr_tile("Weekly Net Sales",  wk_net,  unit_short, _delta(wk_net, pw_net), "WoW", JSA_CYAN) +
        _esr_tile("Weekly Exports",    wk_exp,  unit_short, _delta(wk_exp, pw_exp), "WoW", JSA_GREEN) +
        _esr_tile("Outstanding Sales", wk_out,  unit_short, acc="#f9a825") +
        _esr_tile("YTD Exports",       wk_ytd,  unit_short, _delta(wk_ytd, py_ytd), "YoY", "#ab47bc") +
        _esr_tile("YTD Commitment",    wk_comm, unit_short, acc="#ff7043")
    )
    st.markdown(
        f'<div style="font-size:11px;color:#5a6878;margin-bottom:8px;">'
        f'Week ending: <b style="color:#cdd9e5;">{latest_date_str}</b>'
        f'&nbsp;·&nbsp;MY {sel_my_year}&nbsp;·&nbsp;{commodity}'
        + (f'&nbsp;·&nbsp;<span style="color:#f9a825;">Filtered: {", ".join(sel_countries[:3])}'
           + ("…" if len(sel_countries) > 3 else "") + "</span>"
           if sel_countries else "") +
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div style="display:flex;gap:10px;margin-bottom:18px;">{tiles_html}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(_DIV, unsafe_allow_html=True)

    # ── Weekly metric bar chart ───────────────────────────────────────────────
    st.markdown("#### Weekly Metrics")
    wk_totals = (
        df.groupby("week_date")[numeric_cols].sum().reset_index()
        .sort_values("week_date")
    )

    if wk_metrics:
        fig_wk = go.Figure()
        for ml in wk_metrics:
            col_key = _ESR_METRICS[ml]
            fig_wk.add_trace(go.Bar(
                x=wk_totals["week_date"], y=wk_totals[col_key],
                name=ml, marker_color=_METRIC_COLORS.get(col_key, JSA_CYAN),
                hovertemplate=f"Wk %{{x|%b %d}}: %{{y:,.{dec}f}} {unit_short}<extra>{ml}</extra>",
            ))
        fig_wk.update_layout(
            barmode="group",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cdd9e5", size=11),
            xaxis=dict(title="Week Ending", gridcolor="#2e353d", showgrid=True),
            yaxis=dict(title=unit_short, gridcolor="#2e353d", showgrid=True),
            legend=dict(bgcolor="rgba(0,0,0,0)"),
            margin=dict(l=10, r=10, t=10, b=10), height=360,
        )
        st.plotly_chart(fig_wk, use_container_width=True)
    else:
        st.info("Select at least one metric in Filters to show the weekly chart.")

    # ── Cumulative chart with multi-MY comparison ─────────────────────────────
    st.markdown(_DIV, unsafe_allow_html=True)
    cum_col = _ESR_METRICS.get(cum_metric_label, "ytd_exports")
    st.markdown(f"#### Cumulative {cum_metric_label} — MY Comparison")

    comp_years = [int(y) for y in comp_my_sel]
    all_cum_years = sorted(set([sel_my_year] + comp_years), reverse=True)

    fig_cum = go.Figure()
    _CUM_PAL = [JSA_CYAN, "#4a6741", "#f9a825", "#ef5350", "#ab47bc", "#26c6da", "#ff7043"]

    for idx, yr in enumerate(all_cum_years):
        if yr == sel_my_year:
            yr_df = df.copy()
        else:
            with st.spinner(""):
                yr_recs = _load_esr_exports(comm_code, yr)
            yr_df = _parse_esr(yr_recs, yr)
            if use_bushels and bu_factor and not yr_df.empty:
                yr_df[numeric_cols] = yr_df[numeric_cols] * bu_factor
            if sel_countries and not yr_df.empty:
                yr_df = yr_df[yr_df["country"].isin(sel_countries)]

        if yr_df.empty:
            continue

        # Build cumulative: sum across countries per week, then cumsum
        cum_wk = (
            yr_df.groupby("week_date")[cum_col].sum()
            .sort_index().cumsum().reset_index()
            .rename(columns={cum_col: "cum_val"})
        )
        is_sel = (yr == sel_my_year)
        color  = _CUM_PAL[idx % len(_CUM_PAL)]
        fig_cum.add_trace(go.Scatter(
            x=cum_wk["week_date"], y=cum_wk["cum_val"],
            mode="lines", name=f"MY {yr}",
            line=dict(color=color, width=2.5 if is_sel else 1.5,
                      dash="solid" if is_sel else "dot"),
            opacity=1.0 if is_sel else 0.75,
            hovertemplate=f"MY {yr} · %{{x|%b %d}}: %{{y:,.{dec}f}} {unit_short}<extra></extra>",
        ))

    fig_cum.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#cdd9e5", size=11),
        xaxis=dict(title="Week Ending", gridcolor="#2e353d", showgrid=True),
        yaxis=dict(title=f"Cumulative {unit_short}", gridcolor="#2e353d", showgrid=True),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=10, r=10, t=10, b=10), height=360,
    )
    st.plotly_chart(fig_cum, use_container_width=True)

    # ── Destination charts ────────────────────────────────────────────────────
    st.markdown(_DIV, unsafe_allow_html=True)

    dest_metric_label = st.selectbox(
        "Destination charts — show metric",
        ["YTD Exports", "Outstanding Sales", "Net Sales", "YTD Commitment",
         "Gross Sales", "Weekly Exports"],
        key="esr_dest_metric",
        help="Which metric to rank destinations by",
    )
    dest_col = _ESR_METRICS.get(dest_metric_label, "ytd_exports")

    dest_agg = (
        latest.groupby("country")[[dest_col, "outstanding", "ytd_exports",
                                   "net_sales", "ytd_commit"]].sum()
        .reset_index().sort_values(dest_col, ascending=True)
    )
    top_dest_df = dest_agg.tail(top_n_dest)

    col_bar, col_pie = st.columns(2)

    with col_bar:
        st.markdown(f"#### Top {top_n_dest} Destinations — {dest_metric_label}")
        fig_dest = go.Figure(go.Bar(
            x=top_dest_df[dest_col], y=top_dest_df["country"],
            orientation="h",
            marker_color=_METRIC_COLORS.get(dest_col, JSA_CYAN),
            text=top_dest_df[dest_col].apply(lambda v: f"{v:,.{dec}f}"),
            textposition="outside",
            hovertemplate="%{y}: %{x:,.0f} " + unit_short + "<extra></extra>",
        ))
        fig_dest.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cdd9e5", size=11),
            xaxis=dict(title=unit_short, gridcolor="#2e353d", showgrid=True),
            yaxis=dict(gridcolor="#2e353d", showgrid=False),
            margin=dict(l=10, r=10, t=10, b=10),
            height=max(320, len(top_dest_df) * 24),
        )
        st.plotly_chart(fig_dest, use_container_width=True)

    with col_pie:
        st.markdown(f"#### {dest_metric_label} Share (pie)")
        pie_df = top_dest_df[top_dest_df[dest_col] > 0]
        if not pie_df.empty:
            fig_pie = go.Figure(go.Pie(
                labels=pie_df["country"], values=pie_df[dest_col],
                hole=0.45,
                textfont=dict(color="#cdd9e5", size=10),
                hovertemplate="%{label}: %{value:,.0f} " + unit_short + " (%{percent})<extra></extra>",
            ))
            fig_pie.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#cdd9e5", size=11),
                legend=dict(bgcolor="rgba(0,0,0,0)"),
                margin=dict(l=10, r=10, t=10, b=10), height=max(320, len(pie_df) * 24),
            )
            st.plotly_chart(fig_pie, use_container_width=True)

    # ── All-metrics country table ─────────────────────────────────────────────
    st.markdown(_DIV, unsafe_allow_html=True)
    st.markdown("#### Country Detail — Latest Week (all metrics)")

    tbl = latest[["country","net_sales","gross_sales","exports","outstanding",
                  "ytd_exports","ytd_commit","next_sales","next_outstanding"]].copy()
    tbl = tbl[tbl[["net_sales","exports","outstanding","ytd_exports","ytd_commit"]]
              .abs().sum(axis=1) > 0]
    tbl = tbl.sort_values("ytd_exports", ascending=False).reset_index(drop=True)
    for c in ["net_sales","gross_sales","exports","outstanding",
               "ytd_exports","ytd_commit","next_sales","next_outstanding"]:
        tbl[c] = tbl[c].round(dec)
    tbl.columns = [
        "Country", "Net Sales", "Gross Sales", "Exports", "Outstanding",
        "YTD Exports", "YTD Commitment", "New Crop Net Sales", "New Crop Outstanding",
    ]
    dl_col, _ = st.columns([1, 6])
    with dl_col:
        st.download_button(
            "⬇ CSV", data=tbl.to_csv(index=False),
            file_name=f"esr_{commodity}_{sel_my_year}_{latest_date_str.replace(' ','')}.csv",
            mime="text/csv", key="dl_esr_tbl",
        )
    st.dataframe(tbl, use_container_width=True, height=400)

    # ── New crop callout ──────────────────────────────────────────────────────
    next_net = _sum(latest, "next_sales")
    next_out = _sum(latest, "next_outstanding")
    if abs(next_net) + abs(next_out) > 0.01:
        st.info(
            f"**New Crop MY {sel_my_year + 1}:** "
            f"Net Sales {next_net:,.{dec}f} {unit_short} · "
            f"Outstanding {next_out:,.{dec}f} {unit_short} "
            f"as of {latest_date_str}"
        )


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


def _vl_bar_comm(df, title):
    g = (df.groupby("COMM_GRP").size()
           .reset_index(name="Vessels")
           .sort_values("Vessels", ascending=False))
    fig = px.bar(g, x="COMM_GRP", y="Vessels",
                 color="COMM_GRP", color_discrete_map=_VL_COMM_COLORS,
                 title=title, labels={"COMM_GRP": "Commodity"})
    fig.update_layout(**_VL_BASE_LAYOUT, showlegend=False)
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
    fig.update_layout(**_VL_BASE_LAYOUT)
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
    t = trends_df[["COMMODITY", col]].copy().rename(columns={col: "MT Δ (kMT)"})
    total = pd.DataFrame([{"COMMODITY": "Total",
                            "MT Δ (kMT)": t["MT Δ (kMT)"].sum()}])
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
                    fmt["MT Δ (kMT)"] = fmt["MT Δ (kMT)"].apply(
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
    _vl_kpi(c3, "Volume Lined Up", f"{smry['LU_MT'].sum():,.0f} kMT",
            "known MT (excl. RVT)", _VL_COL_AMB)
    _vl_kpi(c4, "Volume Sailed", f"{smry['S_MT'].sum():,.0f} kMT",
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
        fig.update_layout(**_VL_BASE_LAYOUT)
        fig.update_traces(marker_line_width=0)
        st.plotly_chart(fig, use_container_width=True)
    with ch2:
        g2 = lu_all.groupby(["COMM_GRP", "REGION"]).size().reset_index(name="Vessels")
        fig2 = px.bar(g2, x="COMM_GRP", y="Vessels",
                      color="REGION", color_discrete_map=_VL_REGION_COLORS,
                      barmode="stack", title="Lined-Up Vessels by Commodity & Region",
                      labels={"COMM_GRP": "Commodity", "REGION": "Region"})
        fig2.update_layout(**_VL_BASE_LAYOUT)
        fig2.update_traces(marker_line_width=0)
        st.plotly_chart(fig2, use_container_width=True)

    st.dataframe(
        smry.rename(columns=dict(
            Lined_Up="Lined Up", LU_MT="Lined Up kMT",
            Sailed=f"Sailed ({n_months}mo)", S_MT="Sailed kMT",
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
            f"{lu_mt:,.0f} kMT known" if lu_mt > 0 else "MT not disclosed", _VL_COL_BLUE)
    _vl_kpi(c2, "Top Commodity (Lined Up)", top_lu, f"{top_lu_n} vessels", _VL_COL_AMB)
    _vl_kpi(c3, f"Vessels Sailed — last {n_months}mo", f"{len(sailed):,}",
            f"{s_mt:,.0f} kMT", _VL_COL_POS)
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
                   title="Shipped Volume by Month (kMT — excludes RVT)",
                   labels={"SAIL_MONTH": "Month", "MT_kMT": "kMT"},
                   color_discrete_sequence=[_VL_GREEN_LT])
    fig_v.update_layout(**_VL_BASE_LAYOUT)
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
    corn_tab, soy_tab, meal_tab, wheat_tab, china_tab, insp_tab, esr_tab, vessel_tab, ref_tab = st.tabs(
        ["🌽  Corn", "🫘  Soybeans", "🌾  Soybean Meal", "🌿  Wheat",
         "🇨🇳  China Imports", "🔍  Inspections", "📈  Export Sales",
         "🚢  Vessel Lineup", "📅  Marketing Years"]
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
        try:
            _run_export_inspections_tab()
        except Exception as _e:
            st.error(f"Inspections tab error: {_e}")

    with esr_tab:
        try:
            _run_export_sales_tab(use_bushels=use_bushels, unit_short=unit_short)
        except Exception as _e:
            st.error(f"Export Sales tab error: {_e}")

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
