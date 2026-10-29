"""Download-as-PNG buttons for the dashboard's charts and tables.

One small component (a Streamlit CCv2 button, the kind the Vessel Lineup tab's Copy buttons are) sits above a chart or a table:

    chart  the Plotly figure as drawn on screen: its SVG layers laid on a canvas, at twice the resolution
    table  the table as drawn on screen: every cell's box, borders and text are read from the page (computed styles and the real
           text positions) and painted on a canvas, under a title and above a source line. A table whose cells are not in the page
           (an st.dataframe is a canvas grid) is handed over as HTML and measured off screen the same way.

Nothing is rendered on the server: what is saved is what is on screen. The button and the chart/table share a keyed container
(st-key-pngwrap_<key>) that the button's script uses to find them.

    png_tools.chart(plot, fig, kw)             draw a figure with plot(fig, **kw) (st.plotly_chart) under a button; app.py routes every
                                               st.plotly_chart call through it
    png_tools.table(key, name, title, ...)     a keyed container with the button on top: `with box:` then draw the table inside it
    png_tools.wrap(key) + png_tools.button()   the same in two steps, to put the button beside other controls (a CSV button, a toggle)
    png_tools.table_html(df, formats)          a DataFrame as plain HTML, for a table the page cannot measure

Streamlit Cloud keeps imported modules for the life of the process: after editing this file, Reboot the app.
"""

import datetime as _dt
import html as _html
import re

import streamlit as st

_CSS = """
.wrap { display:flex; justify-content:flex-end; }
button { font: 600 11px 'Source Sans 3','Source Sans Pro',system-ui,sans-serif; letter-spacing:.04em; text-transform:uppercase;
         color:#5f7267; background:#ffffff; border:1px solid #d7e2dc; border-radius:6px; padding:3px 9px; cursor:pointer;
         display:inline-flex; align-items:center; gap:5px; line-height:1.4; }
button:hover { color:#0693e3; border-color:#0693e3; }
button.ok { color:#16a34a; border-color:#16a34a; }
button.err { color:#dc2626; border-color:#dc2626; }
svg { width:12px; height:12px; }
"""

_HTML = """
<div class="wrap"><button type="button" title="Download as a PNG image">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>
<span class="lbl">PNG</span></button></div>
"""

_JS = r"""
const SCALE = 2;
const PAD = 22;
const OFF_CSS = `
.jsa-png-off table { border-collapse: collapse; font: 12.5px Arial, sans-serif; color: #32373c; background: #fff; }
.jsa-png-off th { background: #eef3ef; color: #5f7267; font-weight: 700; font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
                  padding: 7px 12px; border-bottom: 2px solid #d7e2dc; text-align: right; white-space: nowrap; }
.jsa-png-off td { padding: 6px 12px; border-bottom: 1px solid #e8eeea; text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.jsa-png-off th:first-child, .jsa-png-off td:first-child { text-align: left; }
`;

const PX = (v) => parseFloat(v) || 0;
const CLEAR = (c) => !c || c === 'transparent' || /rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*0\s*\)/.test(c);
const toBlob = (canvas) => new Promise((res) => canvas.toBlob(res, 'image/png'));

function download(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

// ── a Plotly chart: its SVG layers on a canvas ──────────────────────────────────────────────────────────────────────────
async function chartBlob(gd) {
  const rect = gd.getBoundingClientRect();
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(rect.width * SCALE); canvas.height = Math.round(rect.height * SCALE);
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  for (const svg of gd.querySelectorAll('svg.main-svg')) {
    const r = svg.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const clone = svg.cloneNode(true);
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');
    clone.setAttribute('width', r.width); clone.setAttribute('height', r.height);
    const url = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(clone));
    const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = url; });
    ctx.drawImage(img, (r.left - rect.left) * SCALE, (r.top - rect.top) * SCALE, r.width * SCALE, r.height * SCALE);
  }
  return await toBlob(canvas);
}

// ── a table: every box, border and line of text, read from the page ─────────────────────────────────────────────────────
function textLines(node) {
  // the text of one text node, line by line, each with the left / top / height of its first character
  const text = node.textContent, range = document.createRange(), out = [];
  let cur = null;
  for (let i = 0; i < text.length; i++) {
    range.setStart(node, i); range.setEnd(node, i + 1);
    const r = range.getClientRects()[0];
    if (!r) continue;                                           // collapsed whitespace has no box
    if (!cur || Math.abs(r.top - cur.top) > r.height * 0.6) {
      if (/\s/.test(text[i])) continue;                         // a line starts at its first visible character (a wrapped space has no width)
      cur = { left: r.left, top: r.top, height: r.height, text: '' }; out.push(cur);
    }
    cur.text += text[i];
  }
  return out;
}

function edge(ctx, x1, y1, x2, y2, w, color, style) {
  if (w <= 0 || style === 'none' || style === 'hidden' || CLEAR(color)) return;
  ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = w;
  if (style === 'dashed') ctx.setLineDash([4 * w, 2 * w]); else if (style === 'dotted') ctx.setLineDash([w, w]);
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); ctx.restore();
}

async function tableBlob(table, opt) {
  // sticky cells sit where the scroll left them: put each at its own place for the measuring, and back afterwards
  const stuck = [];
  table.querySelectorAll('th, td').forEach((c) => { if (getComputedStyle(c).position === 'sticky') { stuck.push([c, c.style.position]); c.style.position = 'static'; } });
  try {
    const tr = table.getBoundingClientRect();
    const W = Math.ceil(tr.width), H = Math.ceil(tr.height);
    const probe = document.createElement('canvas').getContext('2d');
    const TITLE_FONT = '700 21px Arial, sans-serif', SUB_FONT = '13px Arial, sans-serif', FOOT_FONT = '11px Arial, sans-serif';
    probe.font = TITLE_FONT; const wTitle = opt.title ? probe.measureText(opt.title).width : 0;
    probe.font = SUB_FONT; const wSub = opt.subtitle ? probe.measureText(opt.subtitle).width : 0;
    const width = Math.ceil(Math.max(W, wTitle, wSub)) + 2 * PAD;
    const headH = opt.title ? (opt.subtitle ? 74 : 52) : 0, footH = 46;
    const height = H + headH + footH + 2 * PAD;
    const canvas = document.createElement('canvas');
    canvas.width = width * SCALE; canvas.height = height * SCALE;
    const ctx = canvas.getContext('2d');
    ctx.scale(SCALE, SCALE);
    ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, width, height);
    const ox = PAD, oy = PAD + headH;                           // where the table starts on the canvas

    if (opt.title) {
      ctx.textBaseline = 'top'; ctx.textAlign = 'left';
      ctx.font = TITLE_FONT; ctx.fillStyle = '#1e2124'; ctx.fillText(opt.title, PAD, PAD);
      if (opt.subtitle) { ctx.font = SUB_FONT; ctx.fillStyle = '#5f7267'; ctx.fillText(opt.subtitle, PAD, PAD + 30); }
      ctx.fillStyle = '#0693e3'; ctx.fillRect(PAD, PAD + headH - 14, width - 2 * PAD, 2);
    }

    // boxes, outermost first, so a cell's colour covers its row's
    const boxes = [table, ...table.querySelectorAll('thead, tbody, tfoot, tr, th, td')];
    for (const el of boxes) {
      const bg = getComputedStyle(el).backgroundColor;
      if (CLEAR(bg)) continue;
      const r = el.getBoundingClientRect();
      ctx.fillStyle = bg; ctx.fillRect(ox + r.left - tr.left, oy + r.top - tr.top, r.width, r.height);
    }
    // borders (a row's own border only shows when the table's borders are collapsed)
    const collapsed = getComputedStyle(table).borderCollapse === 'collapse';
    for (const el of table.querySelectorAll('tr, th, td')) {
      if (el.tagName === 'TR' && !collapsed) continue;
      const cs = getComputedStyle(el), r = el.getBoundingClientRect();
      const x = ox + r.left - tr.left, y = oy + r.top - tr.top, w = r.width, h = r.height;
      const t = PX(cs.borderTopWidth), b = PX(cs.borderBottomWidth), l = PX(cs.borderLeftWidth), rt = PX(cs.borderRightWidth);
      edge(ctx, x, y + t / 2, x + w, y + t / 2, t, cs.borderTopColor, cs.borderTopStyle);
      edge(ctx, x, y + h - b / 2, x + w, y + h - b / 2, b, cs.borderBottomColor, cs.borderBottomStyle);
      edge(ctx, x + l / 2, y, x + l / 2, y + h, l, cs.borderLeftColor, cs.borderLeftStyle);
      edge(ctx, x + w - rt / 2, y, x + w - rt / 2, y + h, rt, cs.borderRightColor, cs.borderRightStyle);
    }
    // text, line by line at the position the browser gave it
    const walker = document.createTreeWalker(table, NodeFilter.SHOW_TEXT);
    ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent.trim()) continue;
      const cs = getComputedStyle(node.parentElement);
      if (cs.visibility === 'hidden' || cs.display === 'none') continue;
      ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
      ctx.fillStyle = cs.color;
      if ('letterSpacing' in ctx) ctx.letterSpacing = cs.letterSpacing === 'normal' ? '0px' : cs.letterSpacing;
      for (const ln of textLines(node)) {
        let s = ln.text.replace(/\s+$/, '');
        if (cs.textTransform === 'uppercase') s = s.toUpperCase();
        else if (cs.textTransform === 'lowercase') s = s.toLowerCase();
        else if (cs.textTransform === 'capitalize') s = s.replace(/\b\w/g, (c) => c.toUpperCase());
        ctx.fillText(s, ox + ln.left - tr.left, oy + ln.top - tr.top + ln.height / 2);
      }
    }
    if ('letterSpacing' in ctx) ctx.letterSpacing = '0px';

    // the source line and who made it
    ctx.textBaseline = 'top'; ctx.font = FOOT_FONT; ctx.fillStyle = '#5f7267';
    const fy = oy + H + 14;
    if (opt.source) ctx.fillText(opt.source, PAD, fy);
    const stamp = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
    ctx.fillText('John Stewart & Associates · jpsi.com · ' + stamp, PAD, fy + (opt.source ? 16 : 0));
    return await toBlob(canvas);
  } finally {
    stuck.forEach(([c, p]) => { c.style.position = p; });
  }
}

async function offscreenTableBlob(html, opt) {
  const host = document.createElement('div');
  host.className = 'jsa-png-off';
  host.style.cssText = 'position:fixed;left:-30000px;top:0;background:#fff;';
  host.innerHTML = '<style>' + OFF_CSS + '</style>' + html;
  document.body.appendChild(host);
  try {
    const table = host.querySelector('table');
    if (!table) throw new Error('no table');
    return await tableBlob(table, opt);
  } finally {
    host.remove();
  }
}

export default function (component) {
  const { data, parentElement } = component;
  const btn = parentElement.querySelector('button');
  const lbl = parentElement.querySelector('.lbl');
  if (!btn) return;
  btn._data = data || {};
  if (btn._wired) return;
  btn._wired = true;
  const flash = (cls, text) => {
    btn.className = cls; lbl.textContent = text;
    setTimeout(() => { btn.className = ''; lbl.textContent = 'PNG'; }, 1800);
  };
  btn.addEventListener('click', async () => {
    const d = btn._data;
    try {
      lbl.textContent = 'Saving…';
      const host = parentElement.host || parentElement;
      const wrap = host.closest('[class*="st-key-pngwrap_"]');
      let blob = null;
      if (d.kind === 'chart') {
        const gd = wrap && wrap.querySelector('.js-plotly-plot');
        if (!gd) { flash('err', 'No chart'); return; }
        blob = await chartBlob(gd);
      } else if (d.html) {
        blob = await offscreenTableBlob(d.html, d);
      } else {
        const table = wrap && wrap.querySelector('table');
        if (!table) { flash('err', 'No table'); return; }
        blob = await tableBlob(table, d);
      }
      if (!blob) { flash('err', 'Failed'); return; }
      download(blob, (d.name || 'jsa_export') + '.png');
      flash('ok', 'Saved');
    } catch (e) {
      console.error('png export failed', e);
      flash('err', 'Failed');
    }
  });
}
"""


def _define():
    return st.components.v2.component("jsa_png_button", html=_HTML, css=_CSS, js=_JS)


_PNG = _define()

# ── names and keys ──────────────────────────────────────────────────────────────────────────────────────────────────────────
_SEEN_FALLBACK: dict = {}


def slug(text: str, limit: int = 70) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", _html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).lower()).strip("_")
    return s[:limit].strip("_") or "chart"


def stamp() -> str:
    return _dt.date.today().isoformat()


def fig_title(fig) -> str:
    """The text of a figure's title, without its markup ('' when it has none)."""
    try:
        t = fig.layout.title.text or ""
    except Exception:
        return ""
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", str(t)))).strip()


def _seen() -> dict:
    """A dict that is new for every script or fragment run, so the same title drawn twice in a run can be told apart (and keys count
    from zero in each run). Outside a run it never resets, which only means the keys keep growing."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        ctx = get_script_run_ctx()
        marker = ctx.widget_ids_this_run                        # replaced by a new set at the start of every run
        if getattr(ctx, "_jsa_png_run", None) is not marker:
            ctx._jsa_png_run = marker                           # (holding it also keeps its id from being reused)
            ctx._jsa_png_seen = {}
        return ctx._jsa_png_seen
    except Exception:
        return _SEEN_FALLBACK


def unique_key(base: str) -> str:
    seen = _seen()
    n = seen.get(base, 0)
    seen[base] = n + 1
    return base if n == 0 else f"{base}_{n}"


# ── the button ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
def button(kind: str, key: str, name: str, title: str = "", subtitle: str = "", source: str = "", html: str = "",
           align: str = "right") -> None:
    """The PNG button. kind is 'chart' or 'table'; it finds its chart or table inside the keyed container pngwrap_<key>."""
    global _PNG
    data = {"kind": kind, "name": name, "title": title, "subtitle": subtitle, "source": source, "html": html}
    with st.container(horizontal=True, horizontal_alignment=align):
        try:
            _PNG(data=data, key=f"pngbtn_{key}", width="content")
        except Exception as e:                                   # a fresh server / test runtime has an empty component registry
            if "not registered" not in str(e):
                raise
            _PNG = _define()
            _PNG(data=data, key=f"pngbtn_{key}", width="content")


def chart(plot, fig, kw: dict, name: str | None = None):
    """Draw `fig` with plot(fig, **kw) (st.plotly_chart) under a PNG button. A button that cannot be drawn never costs the chart."""
    title = fig_title(fig)
    key = unique_key(slug(kw.get("key") or title or "chart"))
    with st.container(key=f"pngwrap_{key}"):
        try:
            button("chart", key, name or f"{slug(title or 'jsa_chart')}_{stamp()}")
        except Exception:
            pass
        return plot(fig, **kw)


def wrap(key: str):
    """(a keyed container, its key): put the button and the chart or table in it (button(..., key) finds them by the key)."""
    key = unique_key(slug(key, 90))
    return st.container(key=f"pngwrap_{key}"), key


def table(key: str, name: str, title: str = "", subtitle: str = "", source: str = "", html: str = "", align: str = "right"):
    """A keyed container for a table (or any block holding one): a PNG button on top, then whatever the caller draws inside it
    (`with box:` after this call). `html` is the table as HTML when its cells are not in the page (an st.dataframe); otherwise the
    table is read from the page."""
    box, key = wrap(key)
    with box:
        try:
            button("table", key, name, title, subtitle, source, html, align)
        except Exception:
            pass
    return box


def table_html(df, formats: dict | None = None, index: bool = True, index_label: str = "") -> str:
    """A DataFrame as plain HTML (formatted like the screen: `formats` maps a column to a format string or a function)."""
    formats = formats or {}

    def cell(col, v):
        if v is None or (isinstance(v, float) and v != v):
            return "—"
        f = formats.get(col)
        try:
            return f(v) if callable(f) else (f.format(v) if f else str(v))
        except Exception:
            return str(v)

    head = ("<th>" + _html.escape(str(index_label or (df.index.name or ""))) + "</th>" if index else "") + \
        "".join(f"<th>{_html.escape(str(c))}</th>" for c in df.columns)
    rows = []
    for idx, row in df.iterrows():
        tds = ("<td>" + _html.escape(str(idx)) + "</td>" if index else "") + \
            "".join(f"<td>{_html.escape(cell(c, row[c]))}</td>" for c in df.columns)
        rows.append(f"<tr>{tds}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
