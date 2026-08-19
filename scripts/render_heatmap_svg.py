#!/usr/bin/env python3
"""
render_heatmap_svg.py
---------------------
Reads `data/contributions.json` and writes `assets/contrib-heatmap.svg`.

A GitHub-shaped contribution calendar rendered as a self-contained animated
SVG: 53 week columns x 7 weekday rows, month labels, Mon/Wed/Fri rail,
Less -> More legend, and a stat line.

Animation: a diagonal wavefront sweeps across the grid, popping each cell in.
Plays once on load, then freezes — the delay comes from a per-cell CSS custom
property feeding one shared `@keyframes`, so there is no JavaScript and no
per-element animation markup.

Usage
    python scripts/render_heatmap_svg.py
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
# Geometry — tuned so the SVG sits comfortably at 860px in the README
# --------------------------------------------------------------------------- #

WIDTH = 860
PAD_X = 22
PAD_TOP = 20

RAIL_W = 30  # left gutter for Mon / Wed / Fri
MONTH_H = 18  # month label band
WEEKS = 53
DAYS = 7

# Derive the pitch from the canvas so the grid fills the full measure exactly,
# rather than leaving a ragged margin on the right.
PITCH = (WIDTH - 2 * PAD_X - RAIL_W) / WEEKS
GAP = 3.2
CELL = PITCH - GAP
RADIUS = 2.4

# --------------------------------------------------------------------------- #
# Palette — dark, monochrome-green
# --------------------------------------------------------------------------- #

BG = "#0B0F12"
BORDER = "#1C2429"
LEVELS = ["#151C21", "#0E4429", "#006D32", "#26A641", "#39D353"]

C_TEXT = "#C9D8DE"
C_MUTED = "#5A6B74"
C_DIM = "#41525B"
C_ACCENT = "#3FB950"

FONT = (
    "ui-monospace,'SF Mono',SFMono-Regular,Menlo,Consolas,"
    "'DejaVu Sans Mono','Liberation Mono',monospace"
)

# --------------------------------------------------------------------------- #
# Animation
# --------------------------------------------------------------------------- #

LEAD = 0.25
COL_STEP = 0.019  # delay added per column  -> horizontal sweep
ROW_STEP = 0.042  # delay added per row     -> tilts the wavefront diagonally
CELL_DUR = 0.42

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def build(payload: dict) -> tuple[str, float]:
    days = payload["days"]
    stats = payload["stats"]

    # ---- bucket days into (week, weekday) ---------------------------------
    first = datetime.strptime(days[0]["date"], "%Y-%m-%d").date()
    # GitHub grids start on Sunday; python weekday() has Monday=0.
    first_col_offset = (first.weekday() + 1) % 7

    grid: dict[tuple[int, int], dict] = {}
    for i, d in enumerate(days):
        slot = first_col_offset + i
        week, weekday = divmod(slot, 7)
        if week < WEEKS:
            grid[(week, weekday)] = d

    grid_x = PAD_X + RAIL_W
    grid_y = PAD_TOP + 26 + MONTH_H

    # ---- header ------------------------------------------------------------
    rng = payload["range"]
    head = (
        f'<text x="{PAD_X}" y="{PAD_TOP + 12}" class="h1">'
        f'{stats["total"]:,}</text>'
        f'<text x="{PAD_X + 12 + len(f"{stats['total']:,}") * 10}" '
        f'y="{PAD_TOP + 12}" class="h2">contributions in the last year</text>'
        f'<text x="{WIDTH - PAD_X}" y="{PAD_TOP + 12}" class="h3" '
        f'text-anchor="end">{rng["from"]} → {rng["to"]}</text>'
    )

    # ---- month labels ------------------------------------------------------
    months = []
    seen: set[str] = set()
    for (week, weekday), d in sorted(grid.items()):
        ym = d["date"][:7]
        if ym in seen:
            continue
        dt = datetime.strptime(d["date"], "%Y-%m-%d").date()
        # Only label a month once its first *full* column starts.
        if dt.day > 7:
            continue
        seen.add(ym)
        x = grid_x + week * PITCH
        if x > WIDTH - PAD_X - 24:
            continue
        months.append(
            f'<text x="{x:.1f}" y="{grid_y - 7:.1f}" class="mo">'
            f"{MONTHS[dt.month - 1]}</text>"
        )

    # ---- weekday rail ------------------------------------------------------
    rail = []
    for row, label in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        y = grid_y + row * PITCH + CELL * 0.78
        rail.append(
            f'<text x="{grid_x - 8:.1f}" y="{y:.1f}" class="wd" '
            f'text-anchor="end">{label}</text>'
        )

    # ---- cells -------------------------------------------------------------
    cells = []
    max_delay = 0.0
    for week in range(WEEKS):
        for weekday in range(DAYS):
            d = grid.get((week, weekday))
            if d is None:
                continue
            x = grid_x + week * PITCH
            y = grid_y + weekday * PITCH
            delay = LEAD + week * COL_STEP + weekday * ROW_STEP
            max_delay = max(max_delay, delay)
            cells.append(
                f'<rect class="c l{d["level"]}" x="{x:.1f}" y="{y:.1f}" '
                f'width="{CELL}" height="{CELL}" rx="{RADIUS}" '
                f'style="--t:{delay:.2f}s"><title>{d["count"]} on '
                f'{d["date"]}</title></rect>'
            )

    grid_bottom = grid_y + DAYS * PITCH

    # ---- footer: stats left, legend right ---------------------------------
    fy = grid_bottom + 20
    best = stats.get("best_day") or {"count": 0, "date": "—"}
    stat_bits = [
        ("STREAK", f'{stats["current_streak"]}d'),
        ("LONGEST", f'{stats["longest_streak"]}d'),
        ("BEST", f'{best["count"]}'),
        ("ACTIVE", f'{stats["active_days"]}d'),
    ]
    # Monospace advance widths: 10px label + 1.2px tracking, 11.5px value.
    LABEL_ADV, VALUE_ADV = 7.25, 7.0
    stats_svg = []
    sx = float(PAD_X)
    for k, v in stat_bits:
        stats_svg.append(f'<text x="{sx:.1f}" y="{fy}" class="sk">{k}</text>')
        sx += len(k) * LABEL_ADV + 9
        stats_svg.append(f'<text x="{sx:.1f}" y="{fy}" class="sv">{v}</text>')
        sx += len(v) * VALUE_ADV + 26

    legend = []
    lx = WIDTH - PAD_X - (len(LEVELS) * (CELL + 3) + 76)
    legend.append(f'<text x="{lx}" y="{fy}" class="lg">Less</text>')
    lx += 32
    for i, col in enumerate(LEVELS):
        legend.append(
            f'<rect x="{lx:.1f}" y="{fy - CELL + 2:.1f}" width="{CELL}" '
            f'height="{CELL}" rx="{RADIUS}" fill="{col}"/>'
        )
        lx += CELL + 3
    legend.append(f'<text x="{lx + 6:.1f}" y="{fy}" class="lg">More</text>')

    height = int(fy + 22)
    total_time = max_delay + CELL_DUR

    level_css = "".join(
        f".l{i}{{fill:{c}}}" for i, c in enumerate(LEVELS)
    )

    style = f"""
    text {{ font-family:{FONT}; }}
    .h1 {{ font-size:15px; fill:{C_TEXT}; font-weight:600; }}
    .h2 {{ font-size:12px; fill:{C_MUTED}; }}
    .h3 {{ font-size:11px; fill:{C_DIM}; letter-spacing:.4px; }}
    .mo {{ font-size:10.5px; fill:{C_MUTED}; letter-spacing:.5px; }}
    .wd {{ font-size:10px;   fill:{C_MUTED}; }}
    .sk {{ font-size:10px;   fill:{C_DIM}; letter-spacing:1.2px; }}
    .sv {{ font-size:11.5px; fill:{C_ACCENT}; font-weight:600; }}
    .lg {{ font-size:10.5px; fill:{C_MUTED}; }}
    {level_css}

    /* Diagonal wavefront: one keyframe, per-cell delay via --t, freeze at end. */
    .c {{
      transform-box: fill-box;
      transform-origin: center;
      animation: pop {CELL_DUR}s cubic-bezier(.2,.8,.3,1) var(--t) both;
    }}
    @keyframes pop {{
      from {{ opacity:0; transform:scale(.35); }}
      to   {{ opacity:1; transform:scale(1); }}
    }}
    .chrome {{ animation: fade .7s ease-out both; }}
    @keyframes fade {{ from {{opacity:0}} to {{opacity:1}} }}

    @media (prefers-reduced-motion: reduce) {{
      .c, .chrome {{ animation: none; }}
    }}
    """

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" '
        f'width="{WIDTH}" height="{height}" role="img" '
        f'aria-label="GitHub contribution heatmap: {stats["total"]} '
        f'contributions in the last year">'
        f"<style>{style}</style>"
        f'<rect width="{WIDTH}" height="{height}" fill="{BG}"/>'
        f'<rect x=".5" y=".5" width="{WIDTH - 1}" height="{height - 1}" '
        f'fill="none" stroke="{BORDER}"/>'
        f'<g class="chrome">{head}{"".join(months)}{"".join(rail)}'
        f'{"".join(stats_svg)}{"".join(legend)}</g>'
        f'{"".join(cells)}'
        f"</svg>"
    ), total_time


def main() -> None:
    ap = argparse.ArgumentParser(description="Render the contribution heatmap SVG.")
    ap.add_argument("--input", default="data/contributions.json")
    ap.add_argument("--output", default="assets/contrib-heatmap.svg")
    a = ap.parse_args()

    src = Path(a.input)
    if not src.exists():
        raise SystemExit(
            f"ERROR: {src} not found. Run: python scripts/fetch_contributions.py"
        )

    payload = json.loads(src.read_text(encoding="utf-8"))
    svg, total = build(payload)

    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")

    print(f"[heatmap] days      : {len(payload['days'])}")
    print(f"[heatmap] total     : {payload['stats']['total']}")
    print(f"[heatmap] timeline  : {total:.2f}s then frozen")
    print(f"[heatmap] wrote     : {out} ({out.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
