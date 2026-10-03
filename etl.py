#!/usr/bin/env python3
"""
JSA Export ETL — runs daily on the droplet via cron.
Fetches TDM monthly export data for all configured countries/commodities
and upserts to Snowflake JSA-ANALYTICS EXPORTS.PUBLIC.

Also notifies jsagroup@jpsi.com when new months appear (via Microsoft Graph).
"""

import json, os, sys, base64
from datetime import datetime
from pathlib import Path

try:
    import urllib.request
    from dotenv import load_dotenv, dotenv_values
    import snowflake.connector
    import msal
    import requests as _requests
except ImportError as e:
    sys.exit(f"Missing dependency: {e}\nRun: .venv/bin/pip install -r requirements.txt")

# ── env ───────────────────────────────────────────────────────────────────────
_own_env   = Path(__file__).parent / ".env"
_basis_env = Path("/opt/basis-tracker/.env")
load_dotenv(_own_env)
if _basis_env.exists():
    for k, v in dotenv_values(_basis_env).items():
        if k not in os.environ and v:
            os.environ[k] = v

TDM_BASE     = "https://www1.tdmlogin.com/tdm/api/api.asp"
TDM_USER     = "jpsi"
TDM_PASSWORD = os.environ.get("TDM_PASSWORD", "")
RECIPIENT    = os.environ.get("RECIPIENT_EMAIL", "jsagroup@jpsi.com")
WATERMARK_FILE = Path(__file__).parent / "watermark.json"

# ── Snowflake connection ──────────────────────────────────────────────────────
def _sf_connect():
    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "")
    key_b64  = os.environ.get("SNOWFLAKE_PRIVATE_KEY", "")
    key_pwd  = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PWD", "").encode() or None
    from cryptography.hazmat.primitives.serialization import (
        load_pem_private_key, Encoding, PrivateFormat, NoEncryption,
    )
    if key_path:
        pem = Path(key_path).expanduser().read_bytes()
    elif key_b64:
        pem = base64.b64decode(key_b64)
    else:
        raise RuntimeError("No Snowflake private key configured (SNOWFLAKE_PRIVATE_KEY_PATH or SNOWFLAKE_PRIVATE_KEY)")
    pk = load_pem_private_key(pem, password=key_pwd)
    pk_der = pk.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    return snowflake.connector.connect(
        account   = os.environ["SNOWFLAKE_ACCOUNT"],
        user      = os.environ["SNOWFLAKE_USER"],
        private_key = pk_der,
        role      = os.environ.get("SNOWFLAKE_ROLE", "EXPORTER_ROLE"),
        warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database  = "EXPORTS",
        schema    = "PUBLIC",
    )

# ── brand ─────────────────────────────────────────────────────────────────────
JSA_DARK  = "#1e2124"
JSA_PANEL = "#23272a"
JSA_CYAN  = "#0693e3"

# ── month helpers ─────────────────────────────────────────────────────────────
MONTH_ABBR = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
MONTH_NUM  = {a: i+1 for i, a in enumerate(MONTH_ABBR)}

MAR_FEB = ["Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec","Jan","Feb"]
APR_MAR = ["Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar"]
OCT_SEP = ["Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep"]
AUG_JUL = ["Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun","Jul"]
JUL_JUN = ["Jul","Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar","Apr","May","Jun"]
DEC_NOV = ["Dec","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov"]

COMMODITIES = {
    "corn": {
        "label": "Corn", "emoji": "🌽",
        "countries": {
            "Brazil":    {"reporter":"BR","product":"205719","months":MAR_FEB,"prev_months":{"Jan","Feb"},"year_offset":-1,"last_month":"Feb","my_label":"Mar–Feb","color":"#2e7d32","wasde":43000},
            "Argentina": {"reporter":"AR","product":"205719","months":MAR_FEB,"prev_months":{"Jan","Feb"},"year_offset":-1,"last_month":"Feb","my_label":"Mar–Feb","color":"#29b6f6","wasde":37000},
            "Ukraine":   {"reporter":"UA","product":"205719","months":OCT_SEP,"prev_months":{"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep"},"year_offset":0,"last_month":"Sep","my_label":"Oct–Sep","color":"#ffd700","wasde":22000},
        },
    },
    "soybeans": {
        "label": "Soybeans", "emoji": "🫘",
        "countries": {
            "Brazil":    {"reporter":"BR","product":"205714","months":APR_MAR,"prev_months":{"Jan","Feb","Mar"},"year_offset":-1,"last_month":"Mar","my_label":"Apr–Mar","color":"#2e7d32","wasde":115000},
            "Argentina": {"reporter":"AR","product":"205714","months":APR_MAR,"prev_months":{"Jan","Feb","Mar"},"year_offset":-1,"last_month":"Mar","my_label":"Apr–Mar","color":"#29b6f6","wasde":4600},
        },
    },
    "soybeanmeal": {
        "label": "Soy Meal", "emoji": "🌿",
        "countries": {
            "Brazil":    {"reporter":"BR","product":"230400","months":APR_MAR,"prev_months":{"Jan","Feb","Mar"},"year_offset":-1,"last_month":"Mar","my_label":"Apr–Mar","color":"#2e7d32","wasde":25500},
            "Argentina": {"reporter":"AR","product":"230400","months":APR_MAR,"prev_months":{"Jan","Feb","Mar"},"year_offset":-1,"last_month":"Mar","my_label":"Apr–Mar","color":"#29b6f6","wasde":29400},
        },
    },
    "wheat": {
        "label": "Wheat", "emoji": "🌾",
        "countries": {
            "Canada":    {"reporter":"CA","product":"205713","months":AUG_JUL,"prev_months":{"Jan","Feb","Mar","Apr","May","Jun","Jul"},"year_offset":0,"last_month":"Jul","my_label":"Aug–Jul","color":"#ff9800","wasde":29000},
            "Russia":    {"reporter":"RU","product":"205713","months":JUL_JUN,"prev_months":{"Jan","Feb","Mar","Apr","May","Jun"},"year_offset":0,"last_month":"Jun","my_label":"Jul–Jun","color":"#f44336","wasde":44500},
            "Ukraine":   {"reporter":"UA","product":"205713","months":JUL_JUN,"prev_months":{"Jan","Feb","Mar","Apr","May","Jun"},"year_offset":0,"last_month":"Jun","my_label":"Jul–Jun","color":"#ffd700","wasde":12500},
            "Argentina": {"reporter":"AR","product":"205713","months":DEC_NOV,"prev_months":{"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov"},"year_offset":-1,"last_month":"Nov","my_label":"Dec–Nov","color":"#29b6f6","wasde":19500},
            "Australia": {"reporter":"AU","product":"205713","months":DEC_NOV,"prev_months":{"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov"},"year_offset":-1,"last_month":"Nov","my_label":"Dec–Nov","color":"#4db6ac","wasde":26500},
        },
    },
}


# ── TDM fetch ─────────────────────────────────────────────────────────────────
def fetch_tdm_raw(reporter: str, product_code: str) -> dict:
    """Returns {(year_int, month_int): tmt} — summed across all partner countries."""
    now = datetime.now()
    url = (
        f"{TDM_BASE}?username={TDM_USER}&password={TDM_PASSWORD}"
        f"&reporter={reporter}&periodBegin=201001"
        f"&periodEnd={now.year}{now.month:02d}"
        f"&flow=E&partners=All&frequency=M&productCode={product_code}"
        f"&levelDetail=6&levelDetailGroup=P&currency=USD&includeUnits=UNIT1"
        f"&isoCountryCode=NONE&conv=1&separator=T&includeFlow=Y"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read()
        # Decompress gzip if needed (TDM sometimes returns compressed)
        if raw[:2] == b'\x1f\x8b':
            import gzip
            raw = gzip.decompress(raw)
        if len(raw) < 50:
            print(f"  [{reporter}/{product_code}] empty response ({len(raw)} bytes)", file=sys.stderr)
            return {}
        data  = raw.decode("utf-16")
        lines = [l for l in data.strip().split("\n") if l.strip()]
        if len(lines) < 2:
            return {}
        header = [h.strip().upper() for h in lines[0].split("\t")]
        if not {"YEAR","MONTH","QTY1"}.issubset(set(header)):
            return {}
        yi = header.index("YEAR")
        mi = header.index("MONTH")
        qi = header.index("QTY1")
        result: dict = {}
        for line in lines[1:]:
            cols = line.split("\t")
            if len(cols) <= max(yi, mi, qi):
                continue
            try:
                key = (int(cols[yi]), int(cols[mi]))
                val = float(cols[qi])
                result[key] = result.get(key, 0.0) + val / 1000
            except (ValueError, IndexError):
                continue
        return result
    except Exception as e:
        print(f"  TDM error [{reporter}/{product_code}]: {e}", file=sys.stderr)
        return {}


# ── Snowflake upsert ──────────────────────────────────────────────────────────
def upsert_tdm(conn, reporter: str, product_code: str, data: dict) -> int:
    """Upsert TDM data rows into Snowflake. Returns count of rows written."""
    if not data:
        return 0
    rows = [(reporter, product_code, yr, mo, tmt) for (yr, mo), tmt in data.items()]
    cur = conn.cursor()
    cur.execute("""
        CREATE TEMPORARY TABLE IF NOT EXISTS TDM_MONTHLY_STAGE (
            reporter VARCHAR(10), product_code VARCHAR(20),
            year INTEGER, month INTEGER, tmt FLOAT
        )
    """)
    cur.executemany(
        "INSERT INTO TDM_MONTHLY_STAGE VALUES (%s,%s,%s,%s,%s)", rows
    )
    cur.execute("""
        MERGE INTO TDM_MONTHLY t
        USING TDM_MONTHLY_STAGE s
          ON t.reporter=s.reporter AND t.product_code=s.product_code
         AND t.year=s.year AND t.month=s.month
        WHEN MATCHED THEN UPDATE SET t.tmt=s.tmt, t.updated_at=CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (reporter,product_code,year,month,tmt)
             VALUES (s.reporter,s.product_code,s.year,s.month,s.tmt)
    """)
    cur.execute("DROP TABLE IF EXISTS TDM_MONTHLY_STAGE")
    cur.close()
    return len(rows)


# ── watermark ────────────────────────────────────────────────────────────────
def load_watermark() -> dict:
    if WATERMARK_FILE.exists():
        try:
            return json.loads(WATERMARK_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_watermark(wm: dict):
    WATERMARK_FILE.write_text(json.dumps(wm, indent=2))


def my_label_for(comm_key: str, country: str, year: int, month: int) -> str:
    cfg    = COMMODITIES[comm_key]["countries"][country]
    months = cfg["months"]
    prev   = cfg["prev_months"]
    offset = cfg["year_offset"]
    mo_abb = MONTH_ABBR[month - 1]
    adj    = -1 if mo_abb in prev else 0
    start  = year + offset + adj
    return f"{start}/{str(start+1)[-2:]}"


def latest_month(data: dict) -> tuple | None:
    if not data:
        return None
    return max(data.keys())


# ── email notification (same as notifier.py) ─────────────────────────────────
# ── shared HTML primitives ────────────────────────────────────────────────────
JPSI_DARK = "#32373c"
JPSI_BLUE = "#0693e3"
JPSI_LOGO = "https://www.jpsi.com/wp-content/themes/gate39media/img/logo-white.png"

_TH    = (f"padding:8px 10px;background:{JPSI_DARK};color:#ffffff;"
          "font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;")
_TD    = "padding:7px 10px;border-bottom:1px solid #e5e7eb;color:#374151;font-size:12px;"
_TD_A  = "padding:7px 10px;border-bottom:1px solid #e5e7eb;color:#374151;font-size:12px;background:#f9fafb;"
_TABLE = "border-collapse:collapse;width:100%;background:#ffffff;font-family:'Segoe UI',Arial,sans-serif"
_LABEL = ("color:#6b7280;font-size:11px;text-transform:uppercase;"
          "letter-spacing:.05em;margin:16px 0 6px;font-weight:600")


def _pct_span(pct: float) -> str:
    color = "#16a34a" if pct >= 0 else "#dc2626"
    sign  = "+" if pct >= 0 else ""
    return f"<span style='color:{color};font-weight:700'>{sign}{pct:.1f}%</span>"


def _all_years_map(comm_key: str, country: str, data: dict, cfg: dict) -> dict:
    months = cfg["months"]
    result: dict = {}
    for (yr, mo), tmt in data.items():
        mo_abb = MONTH_ABBR[mo - 1]
        if mo_abb not in months:
            continue
        my = my_label_for(comm_key, country, yr, mo)
        result.setdefault(my, {})[mo_abb] = tmt
    return result


def _ytd_sum(data: dict, months: list, latest_yr: int, latest_mo: int,
             offset_yrs: int = 0) -> float | None:
    mo_abb    = MONTH_ABBR[latest_mo - 1]
    mo_idx    = months.index(mo_abb) if mo_abb in months else -1
    if mo_idx < 0:
        return None
    ytd_months = months[:mo_idx + 1]
    total = 0.0
    for m in ytd_months:
        mo_n   = MONTH_NUM[m]
        cur_i  = months.index(m)
        yr_adj = (latest_yr - offset_yrs) if cur_i <= mo_idx else (latest_yr - 1 - offset_yrs)
        v = data.get((yr_adj, mo_n))
        if v is None:
            return None
        total += v
    return total


def _avg_pace(data: dict, months: list, latest_yr: int, latest_mo: int,
              n_years: int = 6) -> float | None:
    """Olympic average of MYTD for the same point in the past n_years marketing years."""
    vals = []
    for offset in range(1, n_years + 1):
        v = _ytd_sum(data, months, latest_yr, latest_mo, offset_yrs=offset)
        if v is not None:
            vals.append(v)
    if len(vals) < 3:
        return None
    # Olympic: drop high and low if enough years
    if len(vals) >= 4:
        vals = sorted(vals)[1:-1]
    return sum(vals) / len(vals)


def _snapshot_table_html(updates: list) -> str:
    rows = ""
    for i, (emoji, comm_label, country, comm_key, data, new_months, cfg) in enumerate(updates):
        if not new_months:
            continue
        latest_yr, latest_mo = max(new_months)
        latest_my  = my_label_for(comm_key, country, latest_yr, latest_mo)
        my_label   = cfg["my_label"]
        months     = cfg["months"]
        mo_abb     = MONTH_ABBR[latest_mo - 1]
        color      = cfg.get("color", JPSI_BLUE)

        ytd_cy  = _ytd_sum(data, months, latest_yr, latest_mo)
        ytd_ly  = _ytd_sum(data, months, latest_yr, latest_mo, offset_yrs=1)
        ytd_avg = _avg_pace(data, months, latest_yr, latest_mo)

        ly_pct  = ((ytd_cy - ytd_ly)  / ytd_ly  * 100) if ytd_cy and ytd_ly  else None
        avg_pct = ((ytd_cy - ytd_avg) / ytd_avg * 100) if ytd_cy and ytd_avg else None

        td = _TD_A if i % 2 else _TD
        rows += f"""<tr>
<td style="{td}"><span style="display:inline-block;width:4px;height:14px;background:{color};border-radius:2px;margin-right:7px;vertical-align:middle"></span><b>{country}</b></td>
<td style="{td}">{emoji} {comm_label}</td>
<td style="{td}">{latest_my} ({my_label})</td>
<td style="{td}">{mo_abb}</td>
<td style="{td};font-weight:600;color:{JPSI_DARK}">{f'{ytd_cy:,.0f} TMT' if ytd_cy else '—'}</td>
<td style="{td}">{_pct_span(ly_pct) if ly_pct is not None else '<span style="color:#9ca3af">—</span>'}</td>
<td style="{td}">{_pct_span(avg_pct) if avg_pct is not None else '<span style="color:#9ca3af">—</span>'}</td>
</tr>"""

    return f"""<table style="{_TABLE}">
<thead><tr>
<th style="{_TH};text-align:left">Country</th>
<th style="{_TH};text-align:left">Commodity</th>
<th style="{_TH};text-align:left">MY</th>
<th style="{_TH};text-align:left">Through</th>
<th style="{_TH};text-align:right">MYTD (TMT)</th>
<th style="{_TH};text-align:right">vs LY</th>
<th style="{_TH};text-align:right">vs 6yr Avg</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>"""


def _prose_summary(comm_key: str, country: str, data: dict, cfg: dict,
                   new_months: list) -> str:
    months   = cfg["months"]
    my_label = cfg["my_label"]
    paras    = []

    for (yr, mo) in sorted(new_months):
        mo_abb = MONTH_ABBR[mo - 1]
        tmt    = data.get((yr, mo))
        if tmt is None:
            continue
        my     = my_label_for(comm_key, country, yr, mo)
        mo_idx = months.index(mo_abb) if mo_abb in months else -1

        # vs prior month
        prev_part = ""
        if mo_idx > 0:
            prev_mo_abb = months[mo_idx - 1]
            prev_mo_num = MONTH_NUM[prev_mo_abb]
            prev_yr     = yr if prev_mo_num <= mo else yr - 1
            prev_tmt    = data.get((prev_yr, prev_mo_num))
            if prev_tmt:
                pct  = (tmt - prev_tmt) / prev_tmt * 100
                sign = "+" if pct >= 0 else ""
                prev_part = f", {sign}{pct:.1f}% from {prev_mo_abb} ({prev_tmt:,.0f} TMT)"

        # vs year-ago
        ly_part = ""
        ly_tmt  = data.get((yr - 1, mo))
        if ly_tmt:
            pct  = (tmt - ly_tmt) / ly_tmt * 100
            sign = "+" if pct >= 0 else ""
            ly_part = f" and {sign}{pct:.1f}% vs a year ago ({ly_tmt:,.0f} TMT)"

        # MYTD
        ytd_cy  = _ytd_sum(data, months, yr, mo)
        ytd_ly  = _ytd_sum(data, months, yr, mo, offset_yrs=1)
        ytd_avg = _avg_pace(data, months, yr, mo)
        months_in = mo_idx + 1

        ytd_part = ""
        if ytd_cy is not None:
            ytd_part = (
                f" MYTD ({my_label[:3]}–{mo_abb}) cumulative shipments stand at "
                f"<b>{ytd_cy:,.0f} TMT</b>"
            )
            if ytd_ly:
                pct  = (ytd_cy - ytd_ly) / ytd_ly * 100
                sign = "+" if pct >= 0 else ""
                ytd_part += f", {sign}{pct:.1f}% vs {my_label[:3]}-{mo_abb} last year ({ytd_ly:,.0f} TMT)"
            if ytd_avg:
                pct  = (ytd_cy - ytd_avg) / ytd_avg * 100
                sign = "+" if pct >= 0 else ""
                ytd_part += f" and {sign}{pct:.1f}% vs the 6-year Olympic average ({ytd_avg:,.0f} TMT)"
            ytd_part += "."

        para = (
            f"{country} shipments for {mo_abb} {my} came in at <b>{tmt:,.0f} TMT</b>"
            f"{prev_part}{ly_part}.{ytd_part}"
        )
        paras.append(f"<p style='color:#374151;font-size:13.5px;line-height:1.75;margin:0 0 14px'>{para}</p>")

    return "".join(paras)


def _shipments_table_html(comm_key: str, country: str, data: dict, cfg: dict) -> str:
    months   = cfg["months"]
    all_yrs  = _all_years_map(comm_key, country, data, cfg)
    recent   = sorted(all_yrs)[-6:]  # last 6 marketing years

    # Header: Month + 6 years + YoY%
    th_cells = f'<th style="{_TH}">Month</th>'
    for y in recent:
        th_cells += f'<th style="{_TH}">{y}</th>'
    th_cells += f'<th style="{_TH}">YoY</th>'

    rows_html = ""
    cy = recent[-1] if recent else None
    py = recent[-2] if len(recent) >= 2 else None
    for m in months:
        cells = f'<td style="{_TD}"><b>{m}</b></td>'
        for y in recent:
            v = all_yrs.get(y, {}).get(m)
            cells += f'<td style="{_TD}">{f"{v:,.0f} TMT" if v is not None else "—"}</td>'
        # YoY% between current and prior year
        cy_v = all_yrs.get(cy, {}).get(m) if cy else None
        py_v = all_yrs.get(py, {}).get(m) if py else None
        if cy_v is not None and py_v:
            pct = (cy_v - py_v) / py_v * 100
            cells += f'<td style="{_TD}">{_pct_span(pct)}</td>'
        else:
            cells += f'<td style="{_TD}">—</td>'
        rows_html += f"<tr>{cells}</tr>"

    return (
        f'<table style="{_TABLE}">'
        f"<thead><tr>{th_cells}</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        f"</table>"
    )


def _forecast_table_html(comm_key: str, country: str, data: dict, cfg: dict,
                         new_months: list) -> str:
    wasde    = cfg.get("wasde", 0)
    months   = cfg["months"]
    my_label = cfg["my_label"]

    if not new_months or not wasde:
        return ""

    latest_yr, latest_mo = max(new_months)
    latest_my  = my_label_for(comm_key, country, latest_yr, latest_mo)
    mo_abb     = MONTH_ABBR[latest_mo - 1]
    mo_idx     = (months.index(mo_abb) + 1) if mo_abb in months else 0
    months_in  = mo_idx

    ytd_cy = _ytd_sum(data, months, latest_yr, latest_mo)
    if ytd_cy is None:
        return ""

    pct_shipped    = ytd_cy / wasde * 100 if wasde else 0
    remaining      = max(wasde - ytd_cy, 0)
    months_left    = len(months) - months_in
    needed_per_mo  = remaining / months_left if months_left > 0 else 0

    # Historical avg shipped by this point
    hist_vals = []
    for offset in range(1, 7):
        v = _ytd_sum(data, months, latest_yr, latest_mo, offset_yrs=offset)
        if v is not None:
            hist_vals.append(v)
    hist_avg_pct = (sum(hist_vals) / len(hist_vals) / wasde * 100) if hist_vals else None

    rows = f"""
<tr><td style="{_TD}">WASDE Forecast</td>
    <td style="{_TD};font-weight:600;color:{JPSI_DARK}">{wasde:,} TMT</td>
    <td style="{_TD}">As of {datetime.now().strftime('%B %Y')}</td></tr>
<tr><td style="{_TD_A}">MYTD Shipments</td>
    <td style="{_TD_A};font-weight:600;color:{JPSI_DARK}">{ytd_cy:,.0f} TMT</td>
    <td style="{_TD_A}">{months_in} month{'s' if months_in != 1 else ''} in</td></tr>
<tr><td style="{_TD}">% of Forecast Shipped</td>
    <td style="{_TD};font-weight:700;color:{JPSI_BLUE}">{pct_shipped:.1f}%</td>
    <td style="{_TD}">{f'vs {hist_avg_pct:.1f}% hist. avg' if hist_avg_pct else ''}</td></tr>
<tr><td style="{_TD_A}">Remaining to Ship</td>
    <td style="{_TD_A};font-weight:600;color:{JPSI_DARK}">{remaining:,.0f} TMT</td>
    <td style="{_TD_A}">{months_left} months remaining</td></tr>
<tr><td style="{_TD}">Needed / Month (pace)</td>
    <td style="{_TD};font-weight:600;color:{JPSI_DARK}">{needed_per_mo:,.0f} TMT</td>
    <td style="{_TD}">to reach WASDE</td></tr>"""

    # vs WASDE: last year comparison
    all_yrs = _all_years_map(comm_key, country, data, cfg)
    if len(sorted(all_yrs)) >= 2:
        prev_my = sorted(all_yrs)[-2]
        prev_total = sum(all_yrs.get(prev_my, {}).values())
        if prev_total:
            pct = (wasde - prev_total) / prev_total * 100
            sign = "+" if pct >= 0 else ""
            rows += f"""
<tr><td style="{_TD}">LY Full-Year ({prev_my})</td>
    <td style="{_TD}">{prev_total:,.0f} TMT</td>
    <td style="{_TD}">{sign}{pct:.1f}% vs WASDE</td></tr>"""

    return (
        f'<table style="{_TABLE}">'
        f'<thead><tr>'
        f'<th style="{_TH}">Item</th>'
        f'<th style="{_TH}">Value</th>'
        f'<th style="{_TH}">Note</th>'
        f'</tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def build_email_html(updates: list) -> str:
    today = datetime.now().strftime("%B %d, %Y")

    comm_emojis = []
    seen = set()
    for emoji, comm_label, *_ in updates:
        if comm_label not in seen:
            comm_emojis.append(f"{emoji} {comm_label}")
            seen.add(comm_label)
    subtitle = " &nbsp;·&nbsp; ".join(comm_emojis) + f" &nbsp;·&nbsp; {today}"

    snapshot = _snapshot_table_html(updates)

    sections = ""
    for emoji, comm_label, country, comm_key, data, new_months, cfg in updates:
        if not new_months:
            continue
        latest_yr, latest_mo = max(new_months)
        mo_abb    = MONTH_ABBR[latest_mo - 1]
        latest_my = my_label_for(comm_key, country, latest_yr, latest_mo)
        color     = cfg.get("color", JPSI_BLUE)
        prose     = _prose_summary(comm_key, country, data, cfg, new_months)
        table     = _shipments_table_html(comm_key, country, data, cfg)
        forecast  = _forecast_table_html(comm_key, country, data, cfg, new_months)

        sections += f"""
<div style="margin-top:0;padding:20px 28px;border-top:3px solid {color};background:#ffffff">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
    <span style="display:inline-block;width:4px;height:22px;background:{color};border-radius:2px"></span>
    <h2 style="color:{JPSI_DARK};margin:0;font-size:15px;font-weight:700">
      {emoji} {comm_label} — {country}
      <span style="color:#6b7280;font-size:13px;font-weight:400"> &nbsp;{mo_abb} {latest_my}</span>
    </h2>
  </div>
  {prose}
  <p style="{_LABEL}">Monthly Shipments (TMT)</p>
  {table}
  { f'<p style="{_LABEL}">WASDE Forecast vs Pace</p>{forecast}' if forecast else '' }
</div>"""

    disclaimer = (
        "Trading commodity futures, options on futures, cash commodities, and over-the-counter "
        "derivative products involves substantial risk of loss and may not be suitable for all investors. "
        "This communication is provided for informational purposes only and does not constitute investment "
        "advice, a recommendation, or an offer or solicitation to buy or sell any futures, options, cash "
        "commodities, or derivative products. John Stewart &amp; Associates, Inc. does not accept orders "
        "to buy or sell any financial instruments via email. The information contained herein has been "
        "obtained from sources believed to be reliable; however, its accuracy and completeness are not "
        "guaranteed. Past performance is not indicative of future results. "
        "&copy; John Stewart &amp; Associates, Inc. 2026"
    )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:'Segoe UI',Arial,sans-serif">
<div style="max-width:780px;margin:24px auto;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08)">

  <!-- Header -->
  <div style="background:{JPSI_DARK};padding:20px 28px;display:flex;align-items:center;gap:16px">
    <img src="{JPSI_LOGO}" height="36" style="object-fit:contain" alt="JSA">
    <div>
      <div style="color:#ffffff;font-size:18px;font-weight:700">Global Export Data Update</div>
      <div style="color:#9ca3af;font-size:12px;margin-top:2px">{subtitle}</div>
    </div>
  </div>

  <!-- Dashboard CTA + snapshot -->
  <div style="padding:18px 28px;background:#f0f7ff;border-bottom:1px solid #e2e8f0">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:14px">
      <div style="font-size:13px;color:#374151">
        New monthly shipment data detected across <b>{len(updates)}</b> country/commodity combinations.
      </div>
      <a href="https://jsa-exportsdashboard.streamlit.app/"
         style="display:inline-block;background:{JPSI_BLUE};color:#ffffff;font-size:13px;font-weight:600;
                padding:9px 18px;border-radius:6px;text-decoration:none;white-space:nowrap">
        View Dashboard →
      </a>
    </div>
    <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;font-weight:600">
      Current MY Snapshot — All Countries
    </div>
    {snapshot}
  </div>

  <!-- Per-country sections -->
  {sections}

  <!-- Footer -->
  <div style="padding:16px 28px;background:#f9fafb;border-top:1px solid #e2e8f0;font-size:11px;color:#9ca3af;line-height:1.6">
    {disclaimer}
    &nbsp;·&nbsp;
    <a href="https://jsa-exportsdashboard.streamlit.app/" style="color:{JPSI_BLUE}">Live Dashboard</a>
  </div>

</div>
</body></html>"""


def send_email(subject: str, html: str) -> None:
    tenant = os.environ.get("GRAPH_TENANT_ID", "")
    client = os.environ.get("GRAPH_CLIENT_ID", "")
    secret = os.environ.get("GRAPH_CLIENT_SECRET", "")
    sender = os.environ.get("GRAPH_SENDER", "")
    if not (tenant and client and secret and sender):
        raise RuntimeError("Graph credentials not configured")
    app = msal.ConfidentialClientApplication(
        client_id=client,
        authority=f"https://login.microsoftonline.com/{tenant}",
        client_credential=secret,
    )
    tok    = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    access = tok.get("access_token")
    from_name = os.environ.get("GRAPH_FROM_NAME", "JSA Export Monitor")
    msg = {
        "subject": subject,
        "body":    {"contentType": "HTML", "content": html},
        "toRecipients": [{"emailAddress": {"address": RECIPIENT}}],
        "from":         {"emailAddress": {"address": sender, "name": from_name}},
    }
    resp = _requests.post(
        f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
        json={"message": msg, "saveToSentItems": True},
        timeout=30,
    )
    if resp.status_code not in (200, 202):
        raise RuntimeError(f"Graph sendMail failed [{resp.status_code}]: {resp.text[:300]}")


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    if not TDM_PASSWORD:
        sys.exit("TDM_PASSWORD not set")

    wm      = load_watermark()
    updates = []

    try:
        conn = _sf_connect()
        sf_ok = True
        print("Snowflake connected")
    except Exception as e:
        print(f"Snowflake unavailable: {e} — will skip DB writes", file=sys.stderr)
        conn  = None
        sf_ok = False

    for comm_key, comm_cfg in COMMODITIES.items():
        emoji = comm_cfg["emoji"]
        label = comm_cfg["label"]
        print(f"\n{emoji} {label}")

        for country, cfg in comm_cfg["countries"].items():
            reporter = cfg["reporter"]
            product  = cfg["product"]
            print(f"  Fetching {country}...", end=" ", flush=True)

            data = fetch_tdm_raw(reporter, product)
            if not data:
                print("no data")
                continue

            rows = len(data)
            print(f"{rows} rows", end="")

            # Upsert to Snowflake
            if sf_ok and conn:
                try:
                    written = upsert_tdm(conn, reporter, product, data)
                    print(f" → {written} rows upserted", end="")
                except Exception as e:
                    print(f" → SF error: {e}", end="", file=sys.stderr)

            # Check watermark for new months
            wm_key = f"{comm_key}:{country}"
            prev_latest = wm.get(wm_key)
            cur_latest  = latest_month(data)
            if cur_latest:
                cur_str = f"{cur_latest[0]}-{cur_latest[1]:02d}"
                if cur_str != prev_latest:
                    prev_dt = tuple(int(x) for x in prev_latest.split("-")) if prev_latest else (0, 0)
                    new_months = sorted(k for k in data if k > prev_dt)
                    if new_months:
                        print(f" → NEW: {[f'{MONTH_ABBR[m-1]} {y}' for y,m in new_months[-3:]]}")
                        updates.append((emoji, label, country, comm_key, data, new_months, cfg))
                    wm[wm_key] = cur_str
                else:
                    print(" → no new months")
            else:
                print()

    if conn:
        conn.close()
    save_watermark(wm)

    if updates:
        labels = ", ".join(f"{e} {l} {c}" for e, l, c, *_ in updates)
        subject = f"Export Update — {labels} — {datetime.now().strftime('%B %d, %Y')}"
        print(f"\nSending: {subject}")
        try:
            html = build_email_html(updates)
            send_email(subject, html)
            print("Graph sendMail → OK")
        except Exception as e:
            print(f"Email failed: {e}", file=sys.stderr)
    else:
        print("\nNo new months — no email sent.")


if __name__ == "__main__":
    main()
