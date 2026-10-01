"""Analyst layer for FindII lead exports.

After a scrape produces its CSV+XLSX sales kit, this module adds the
analyst + visual step:

  * analyst paper    — markdown: breakdowns, coverage, top leads, insights
  * visual dashboard — single self-contained HTML (charts embedded base64)
  * standalone PNGs  — district distribution, density/heatmap, coverage

The charts are rendered with matplotlib using Segoe UI so Persian /
Russian business names don't turn into tofu boxes. Everything is pure
local computation — no API keys.
"""
from __future__ import annotations

import base64
import io
import os
import re
from collections import Counter
from datetime import datetime
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from matplotlib import font_manager  # noqa: E402

# Segoe UI covers Cyrillic + Arabic script (Persian) on Windows.
_SEGOE = None
for _f in ("C:/Windows/Fonts/SegoeUI.ttf", "C:/Windows/Fonts/segoeui.ttf"):
    if os.path.exists(_f):
        try:
            _SEGOE = font_manager.FontProperties(fname=_f)
        except Exception:  # noqa: BLE001
            _SEGOE = None
        break
if _SEGOE is not None:
    plt.rcParams["font.family"] = _SEGOE.get_name()
plt.rcParams["axes.unicode_minus"] = False

BG = "#0d0d0f"
PANEL = "#1a1a1e"
GOLD = "#b98a2f"
FG = "#f5f5f7"
MUT = "#8e8e93"


def _clamp(s: str, n: int = 44) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s if len(s) <= n else s[: n - 1] + "…"


def _cover(dark: bool = True) -> dict[str, dict[str, Any]]:
    if dark:
        return {"bg": BG, "panel": PANEL, "fg": FG, "mut": MUT, "grid": "#2c2c2e"}
    return {"bg": "#ffffff", "panel": "#f5f5f7", "fg": "#1d1d1f",
            "mut": "#6e6e73", "grid": "#e5e5ea"}


def _style_axes(ax, c: dict[str, Any]) -> None:
    ax.set_facecolor(c["panel"])
    for s in ax.spines.values():
        s.set_color(c["grid"])
    ax.tick_params(colors=c["mut"], labelsize=9)
    for lab in ax.get_xticklabels():
        lab.set_color(c["fg"])
    for lab in ax.get_yticklabels():
        lab.set_color(c["fg"])
    ax.title.set_color(c["fg"])
    ax.xaxis.label.set_color(c["mut"])
    ax.yaxis.label.set_color(c["mut"])
    ax.grid(axis="y", color=c["grid"], lw=0.6, alpha=0.6)


def _fig_to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


# ------------------------------------------------------------------ stats
def _stats(rows: list[dict]) -> dict[str, Any]:
    n = len(rows)
    with_phone = sum(1 for r in rows if (r.get("phone") or "").strip())
    with_web = sum(1 for r in rows if (r.get("website") or "").strip())
    named = sum(1 for r in rows if (r.get("name") or "").strip())
    by_district = Counter(
        (r.get("district") or "—").strip() or "—" for r in rows)
    by_group = Counter((r.get("group") or "—").strip() or "—" for r in rows)
    geo = sum(1 for r in rows if r.get("lat") is not None)
    return {"total": n, "with_phone": with_phone, "with_web": with_web,
            "named": named, "by_district": by_district, "by_group": by_group,
            "geo": geo,
            "phone_rate": (with_phone / n * 100) if n else 0.0,
            "web_rate": (with_web / n * 100) if n else 0.0,
            "geo_rate": (geo / n * 100) if n else 0.0,
            "top_districts": by_district.most_common(12),
            "top_groups": by_group.most_common(8)}


def _fmt_rate(v: float) -> str:
    return f"{v:.0f}%"


# ------------------------------------------------------------------ charts
def chart_districts(st: dict[str, Any], dark: bool = True,
                    title: str = "Leads by district") -> bytes:
    c = _cover(dark)
    dists, counts = [], []
    for d, n in st["top_districts"]:
        dists.append(_clamp(d, 26))
        counts.append(n)
    dists = dists[::-1]
    counts = counts[::-1]
    fig, ax = plt.subplots(figsize=(9, 4.6), facecolor=c["bg"])
    bars = ax.barh(dists, counts, color=GOLD, zorder=3)
    for b, v in zip(bars, counts):
        ax.text(v + max(counts) * 0.01, b.get_y() + b.get_height() / 2,
                str(v), va="center", color=c["fg"], fontsize=9)
    ax.set_title(title, color=c["fg"], fontsize=13, pad=12)
    _style_axes(ax, c)
    ax.grid(which="major", axis="both", color=c["grid"], lw=0.5, alpha=0.35)
    ax.set_xlim(0, max(counts) * 1.12)
    return _fig_to_png(fig)


def chart_groups(st: dict[str, Any], dark: bool = True,
                 title: str = "Mix by group") -> bytes:
    c = _cover(dark)
    labels = [_clamp(g, 20) for g, _ in st["top_groups"]]
    sizes = [n for _, n in st["top_groups"]]
    fig, ax = plt.subplots(figsize=(7.2, 4.6), facecolor=c["bg"])
    wedges, _t, autotexts = ax.pie(
        sizes, labels=labels, autopct="%1.0f%%", startangle=110,
        colors=[GOLD, "#d1a64a", "#8e6c22", "#f0d9a8", "#5c4a16",
                "#b98a2f", "#e7c06b", "#7a5f1f"],
        textprops={"color": c["fg"], "fontsize": 9})
    for at in autotexts:
        at.set_color("#1d1d1f")
        at.set_fontsize(8)
    ax.set_title(title, color=c["fg"], fontsize=13, pad=12)
    fig.set_facecolor(c["bg"])
    return _fig_to_png(fig)


def chart_density(rows: list[dict], dark: bool = True,
                  title: str = "Lead density map") -> bytes:
    c = _cover(dark)
    pts = [(r.get("lon"), r.get("lat")) for r in rows
           if r.get("lat") is not None and r.get("lon") is not None]
    if not pts:
        return b""
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    fig, ax = plt.subplots(figsize=(8.6, 6.2), facecolor=c["bg"])
    ax.set_facecolor(BG if dark else "#eef1f5")
    hexbin = ax.hexbin(lons, lats, gridsize=42, cmap="YlOrBr", mincnt=1)
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_title(title, color=c["fg"], fontsize=13, pad=12)
    _style_axes(ax, c)
    ax.grid(color=c["grid"], lw=0.4, alpha=0.3)
    if hexbin and not dark:
        cb = fig.colorbar(hexbin, ax=ax, fraction=0.046, pad=0.02)
        cb.ax.tick_params(colors=c["mut"], labelsize=8)
        cb.outline.set_edgecolor(c["grid"])
    return _fig_to_png(fig)


def chart_coverage(st: dict[str, Any], dark: bool = True) -> bytes:
    c = _cover(dark)
    labels = ["named", "with phone", "with website", "mapped (lat/lon)"]
    values = [st["named"], st["with_phone"], st["with_web"], st["geo"]]
    rates = [_fmt_rate(st[key]) for key in
             ("named", "phone_rate", "web_rate", "geo_rate")]
    fig, ax = plt.subplots(figsize=(7.2, 3.8), facecolor=c["bg"])
    bars = ax.bar(labels, values, color=[GOLD, "#d1a64a", "#8e6c22", "#e7c06b"],
                  zorder=3)
    for b, v, rt in zip(bars, values, rates):
        ax.text(b.get_x() + b.get_width() / 2, v + max(values) * 0.02,
                f"{v} · {rt}", ha="center", color=c["fg"], fontsize=9)
    ax.set_title("Contact & data coverage", color=c["fg"], fontsize=13, pad=12)
    _style_axes(ax, c)
    ax.set_ylim(0, max(values) * 1.18)
    return _fig_to_png(fig)


# ------------------------------------------------------------------ paper
def markdown_paper(query: dict[str, Any], rows: list[dict],
                   st: dict[str, Any], city_stats: dict[str, Any]) -> str:
    city = query.get("city") or city_stats.get("city") or ""
    cat = query.get("category") or ""
    qraw = query.get("raw") or query.get("category") or ""
    ndist = len(st["by_district"])
    phone = st["with_phone"]
    md = []
    md.append(f"# Analyst paper — {cat} in {city}")
    md.append("")
    md.append(f"Query: `{qraw}`")
    md.append(f"Generated: {datetime.now().isoformat(timespec='minutes')}")
    md.append("")
    md.append("## 1 · Market at a glance")
    md.append("")
    md.append("| metric | value |")
    md.append("|---|---|")
    md.append(f"| leads found | **{st['total']}** |")
    md.append(f"| map buildings scanned | {city_stats.get('map_buildings', '—'):,} |")
    md.append(f"| city | {city} |")
    md.append(f"| district filter | {query.get('district') or '—'} |")
    md.append(f"| hit rate | {st['total']/max(city_stats.get('map_buildings', 1), 1)*1000:.2f}‰ of map buildings |")
    md.append(f"| districts represented | {ndist} |")
    md.append(f"| with phone | {phone} ({_fmt_rate(st['phone_rate'])}) |")
    md.append(f"| with website | {st['with_web']} ({_fmt_rate(st['web_rate'])}) |")
    md.append(f"| with lat/lon | {st['geo']} ({_fmt_rate(st['geo_rate'])}) |")
    md.append("")
    md.append("## 2 · Where they are (districts)")
    md.append("")
    md.append("| district | leads | share |")
    md.append("|---|---|---|")
    for d, n in st["top_districts"]:
        md.append(f"| {d} | {n} | {n/st['total']*100:.0f}% |")
    md.append("")
    md.append("## 3 · Category / group mix")
    md.append("")
    md.append("| group | leads |")
    md.append("|---|---|")
    for g, n in st["top_groups"]:
        md.append(f"| {g} | {n} |")
    md.append("")
    md.append("## 4 · Top leads worth calling")
    md.append("")
    md.append("Ranked by contact completeness + name. The phone column is the "
              "gold — call these first.")
    md.append("")
    md.append("| # | name | district | phone | website |")
    md.append("|---|---|---|---|---|")
    ranked = sorted(rows,
                    key=lambda r: (1 if (r.get("phone") or "").strip() else 0,
                                   1 if (r.get("website") or "").strip() else 0,
                                   1 if (r.get("name") or "").strip() else 0),
                    reverse=True)[:20]
    for i, r in enumerate(ranked, start=1):
        name = _clamp(r.get("name") or "—", 30)
        ph = (r.get("phone") or "—").replace(",", " ")
        md.append(f"| {i} | {name} | {_clamp(r.get('district') or '—', 22)} | {ph} | "
                  f"{_clamp(r.get('website') or '—', 30)} |")
    md.append("")
    md.append("## 5 · Read")
    md.append("")
    if phone:
        md.append(f"- {_fmt_rate(st['phone_rate'])} of leads carry a phone — "
                  f"outreach is viable out of the box. Prioritise the "
                  f"largest district clusters first.")
    if st["with_web"]:
        md.append(f"- {st['with_web']} businesses have a website — you can "
                  f"enrich or contact them via their own channels.")
    top = st["top_districts"][0] if st["top_districts"] else None
    if top:
        md.append(f"- **{top[0]}** is the largest cluster ({top[1]} leads). "
                  f"That's your beachhead for a first campaign.")
    md.append(f"- Data source: OpenStreetMap city extract (offline, cached "
              f"locally). {city_stats.get('map_buildings', 0):,} buildings "
              f"scanned — every row is a geo-referenced, real place.")
    md.append("")
    md.append("---")
    md.append("FindII · scrape → filter → export → analyst → visual")
    return "\n".join(md)


# ------------------------------------------------------------------ visual
def _img_tag(data: bytes) -> str:
    b64 = base64.b64encode(data).decode("ascii")
    return f'<img class="ch" alt="chart" src="data:image/png;base64,{b64}"/>'


def visual_html(query: dict[str, Any], rows: list[dict], st: dict[str, Any],
                charts: dict[str, bytes], city_stats: dict[str, Any]) -> str:
    city = query.get("city") or city_stats.get("city") or ""
    cat = query.get("category") or ""
    qraw = query.get("raw") or query.get("category") or ""
    img = "".join(_img_tag(d) for d in charts.values() if d)
    top = st["top_districts"][0] if st["top_districts"] else None
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FindII · {cat} in {city}</title>
<style>
  body {{ margin:0; background:#0d0d0f; color:#f5f5f7;
    font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif; }}
  .wrap {{ max-width:980px; margin:0 auto; padding:28px 20px 60px; }}
  h1 {{ font-size:30px; margin:0 0 4px; letter-spacing:-.5px; }}
  h2 {{ font-size:20px; margin:34px 0 10px; color:#b98a2f; }}
  .sub {{ color:#8e8e93; font-size:13px; }}
  .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px;
    margin:18px 0 4px; }}
  .card {{ background:#1a1a1e; border-radius:12px; padding:14px; }}
  .card b {{ font-size:26px; display:block; color:#b98a2f; }}
  .card span {{ color:#8e8e93; font-size:12px; }}
  .chartbox {{ background:#1a1a1e; border-radius:16px; padding:14px;
    margin-top:12px; }}
  img.ch {{ width:100%; }}
  table {{ border-collapse:collapse; width:100%; margin-top:10px;
    background:#1a1a1e; border-radius:12px; overflow:hidden; }}
  th,td {{ text-align:left; padding:9px 12px; border-bottom:1px solid #2c2c2e; }}
  th {{ color:#b98a2f; font-size:12px; text-transform:uppercase; }}
  td {{ font-size:13px; }}
  .footer {{ color:#6e6e73; font-size:12px; margin-top:34px; text-align:center; }}
  @media(max-width:700px) {{ .cards {{ grid-template-columns:1fr 1fr; }} }}
</style></head><body><div class="wrap">
  <div class="eyebrow" style="color:#8e8e93;font-size:12px;letter-spacing:2px;text-transform:uppercase">FindII · analyst + visual</div>
  <h1>{cat} in {city}</h1>
  <div class="sub">query: <b>{qraw}</b> · generated {datetime.now().isoformat(timespec='minutes')} ·
  source OSM (offline, cached)</div>
  <div class="cards">
    <div class="card"><b>{st['total']}</b><span>leads found</span></div>
    <div class="card"><b>{_fmt_rate(st['phone_rate'])}</b><span>with phone</span></div>
    <div class="card"><b>{city_stats.get('map_buildings', 0):,}</b><span>map buildings</span></div>
    <div class="card"><b>{len(st['by_district'])}</b><span>districts</span></div>
  </div>
  <div class="chartbox">{img}</div>
  <h2>Top 15 leads</h2>
  <table><tr><th>#</th><th>name</th><th>district</th><th>phone</th><th>website</th></tr>{"".join(
    f"<tr><td>{i}</td><td>{(r.get('name') or '—')}</td><td>{(r.get('district') or '—')}</td>"
    f"<td>{(r.get('phone') or '—')}</td><td>{(r.get('website') or '—')}</td></tr>"
    for i, r in enumerate(sorted(rows, key=lambda r: (1 if (r.get('phone') or '').strip() else 0, 1 if (r.get('name') or '').strip() else 0), reverse=True)[:15], start=1))}</table>
  <h2>District table</h2>
  <table><tr><th>district</th><th>leads</th><th>share</th></tr>{"".join(
    f"<tr><td>{d}</td><td>{n}</td><td>{n/st['total']*100:.0f}%</td></tr>"
    for d, n in st["top_districts"])}</table>
  <div class="footer">FindII · {'beachhead: ' + top[0] if top else 'scrape → export → analyst → visual'}</div>
</div></body></html>"""


# ------------------------------------------------------------------ orbit
def build_analyst(query: dict[str, Any], rows: list[dict],
                  city_stats: dict[str, Any], out_dir: str,
                  base_name: str | None = None) -> dict[str, str]:
    """Generate the full analyst + visual set for a scrape result.

    Returns {md, html, png_districts, png_groups, png_density, png_coverage}.
    """
    os.makedirs(out_dir, exist_ok=True)
    st = _stats(rows)
    if not base_name:
        stmp = datetime.now().strftime("%Y%m%d_%H%M%S")
        cat = re.sub(r"[^a-z0-9]+", "_", (query.get("category") or "leads").lower())
        base_name = f"{cat}_{query.get('city') or ''}_{stmp}".strip("_")
    city_stats = city_stats or {"map_buildings": 0}

    png_d = chart_districts(st)
    png_g = chart_groups(st)
    png_density = chart_density(rows)
    png_cov = chart_coverage(st)
    charts = {"districts": png_d, "groups": png_g,
              "density": png_density, "coverage": png_cov}

    md_path = os.path.join(out_dir, f"{base_name}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown_paper(query, rows, st, city_stats))

    html_path = os.path.join(out_dir, f"{base_name}_report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(visual_html(query, rows, st, charts, city_stats))

    png_paths = {}
    for key, data in charts.items():
        if data:
            p = os.path.join(out_dir, f"{base_name}_{key}.png")
            with open(p, "wb") as f:
                f.write(data)
            png_paths[f"png_{key}"] = p
    return {"md": md_path, "html": html_path,
            **png_paths, "stats": st if False else ""}