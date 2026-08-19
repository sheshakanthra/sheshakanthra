#!/usr/bin/env python3
"""
make_info_card.py
-----------------
Emits `assets/info-card.svg` — a neofetch-style terminal system panel that
reveals itself line by line, then freezes.

Everything is declared in the CONTENT block below. Edit that, re-run, done.

Typography note
---------------
An SVG loaded through `<img>` is a *sandboxed document*: it cannot fetch web
fonts. So the card asks for a generic monospace stack that resolves against the
viewer's own system fonts (SF Mono / Menlo / Consolas / DejaVu Sans Mono).
That is why this renders identically on GitHub without shipping a font file.

Usage
    python scripts/make_info_card.py
"""

from __future__ import annotations

import argparse
from pathlib import Path
from xml.sax.saxutils import escape

# --------------------------------------------------------------------------- #
# CONTENT — edit this
# --------------------------------------------------------------------------- #

PROMPT = "shesh@github"
SUBTITLE = "~/identity — system information"

ROWS: list[tuple[str, str] | tuple[None, None]] = [
    ("NAME", "Sheshakanth RA"),
    ("ROLE", "AI / ML × Full-Stack Engineer"),
    ("FOCUS", "Intelligent systems, applied ML"),
    (None, None),  # divider
    ("BUILDING", "FreshRoute"),
    ("STACK", "Python · FastAPI · React · Postgres"),
    ("INTERFACE", "Next.js · TypeScript"),
    (None, None),
    ("BASE", "Chennai, IN"),
    ("PORTFOLIO", "shesh-portfolio.vercel.app"),
    ("STATUS", "open to collaboration"),
]

FOOTER = "building · learning · shipping"

# --------------------------------------------------------------------------- #
# STYLE
# --------------------------------------------------------------------------- #

W, H = 600, 560
PAD = 30

BG = "#0B0F12"
BORDER = "#1C2429"
RULE = "#171F24"
GRID = "#131A1F"

C_LABEL = "#4E6470"  # dim slate — the key column
C_VALUE = "#D5E2E8"  # near-white — the data
C_ACCENT = "#3FB950"  # green, used only for prompt + accents
C_MUTED = "#5A6B74"

FONT = (
    "ui-monospace,'SF Mono',SFMono-Regular,Menlo,Consolas,"
    "'DejaVu Sans Mono','Liberation Mono',monospace"
)

ROW_H = 32
DIV_H = 30  # vertical space consumed by a divider row
LINE_STEP = 0.085  # stagger between successive lines (s)
LINE_DUR = 0.52  # per-line reveal duration (s)
LEAD = 0.18


def build() -> str:
    parts: list[str] = []
    idx = 0  # global reveal index, drives the stagger

    def line(inner: str, delay_index: int) -> str:
        return (
            f'<g class="ln" style="--t:{LEAD + delay_index * LINE_STEP:.2f}s">'
            f"{inner}</g>"
        )

    # ---- header ------------------------------------------------------------
    y = PAD + 26
    parts.append(
        line(
            f'<text x="{PAD}" y="{y}" class="pr">{escape(PROMPT)}'
            f'<tspan class="crt" dx="6">_</tspan></text>',
            idx,
        )
    )
    idx += 1

    y += 20
    parts.append(
        line(f'<text x="{PAD}" y="{y}" class="sub">{escape(SUBTITLE)}</text>', idx)
    )
    idx += 1

    y += 16
    parts.append(
        line(
            f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" class="rule"/>', idx
        )
    )
    idx += 1

    # ---- rows --------------------------------------------------------------
    # Measure first, then centre the block in the space between the header rule
    # and the footer rule, so the panel stays balanced whatever ROWS contains.
    header_end = y
    footer_top = H - PAD - 4 - 26
    block_h = sum(ROW_H if lbl is not None else DIV_H for lbl, _ in ROWS)
    y = header_end + max(0, (footer_top - header_end - block_h)) / 2.0

    label_x = PAD
    value_x = PAD + 132

    for label, value in ROWS:
        if label is None:
            y += DIV_H / 2
            parts.append(
                line(
                    f'<line x1="{PAD}" y1="{y:.0f}" x2="{W - PAD}" y2="{y:.0f}"'
                    f' class="rule"/>',
                    idx,
                )
            )
            y += DIV_H / 2
            idx += 1
            continue

        y += ROW_H
        parts.append(
            line(
                f'<text x="{label_x}" y="{y:.0f}" class="k">{escape(label)}</text>'
                f'<text x="{value_x}" y="{y:.0f}" class="v">{escape(value)}</text>',
                idx,
            )
        )
        idx += 1

    # ---- footer ------------------------------------------------------------
    fy = H - PAD - 4
    parts.append(
        line(
            f'<line x1="{PAD}" y1="{fy - 26}" x2="{W - PAD}" y2="{fy - 26}"'
            f' class="rule"/>'
            f'<circle cx="{PAD + 4}" cy="{fy - 5}" r="3.5" class="dot"/>'
            f'<text x="{PAD + 18}" y="{fy}" class="ft">{escape(FOOTER)}</text>',
            idx,
        )
    )
    idx += 1

    total = LEAD + idx * LINE_STEP + LINE_DUR

    tick = 14
    corners = "".join(
        f'<path d="{d}" class="corner"/>'
        for d in (
            f"M12 {12 + tick} V12 H{12 + tick}",
            f"M{W - 12 - tick} 12 H{W - 12} V{12 + tick}",
            f"M12 {H - 12 - tick} V{H - 12} H{12 + tick}",
            f"M{W - 12 - tick} {H - 12} H{W - 12} V{H - 12 - tick}",
        )
    )

    style = f"""
    text {{ font-family:{FONT}; }}
    .pr  {{ font-size:16px; fill:{C_ACCENT}; font-weight:600; letter-spacing:.4px; }}
    .crt {{ font-size:16px; fill:{C_ACCENT}; }}
    .sub {{ font-size:11.5px; fill:{C_MUTED}; letter-spacing:.6px; }}
    .k   {{ font-size:11.5px; fill:{C_LABEL}; letter-spacing:1.4px; }}
    .v   {{ font-size:13.5px; fill:{C_VALUE}; }}
    .ft  {{ font-size:11.5px; fill:{C_MUTED}; letter-spacing:1.6px; }}
    .rule   {{ stroke:{RULE}; stroke-width:1; }}
    .corner {{ stroke:#2A353B; stroke-width:1; fill:none; }}
    .dot    {{ fill:{C_ACCENT}; }}

    /* One rule, one keyframe: every line supplies its own delay via --t. */
    .ln {{ animation: rise {LINE_DUR}s cubic-bezier(.2,.7,.3,1) var(--t) both; }}
    @keyframes rise {{
      from {{ opacity:0; transform:translateY(7px); }}
      to   {{ opacity:1; transform:translateY(0); }}
    }}
    .frame {{ animation: rise {LINE_DUR}s ease-out both; }}
    /* Caret blinks while the panel fills, then rests lit.
       The final keyframe MUST be opacity:1 — with `both`, the animation holds
       its last frame, so ending on the dark half would leave the caret gone. */
    .crt {{ animation: blink .62s steps(1,end) 6 both; }}
    @keyframes blink {{ 0%,48%{{opacity:1}} 49%,98%{{opacity:0}} 99%,100%{{opacity:1}} }}

    @media (prefers-reduced-motion: reduce) {{
      .ln, .frame, .crt {{ animation: none; }}
    }}
    """

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" role="img" '
        f'aria-label="Terminal information card for Sheshakanth RA">'
        f"<style>{style}</style>"
        f'<defs><pattern id="ig" width="32" height="32" patternUnits="userSpaceOnUse">'
        f'<path d="M32 0H0V32" fill="none" stroke="{GRID}" stroke-width="1"/>'
        f"</pattern></defs>"
        f'<rect width="{W}" height="{H}" fill="{BG}"/>'
        f'<rect width="{W}" height="{H}" fill="url(#ig)" opacity=".55"/>'
        f'<g class="frame">'
        f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" fill="none" '
        f'stroke="{BORDER}"/>{corners}</g>'
        f"{''.join(parts)}"
        f"</svg>"
    ), total


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the terminal info card SVG.")
    ap.add_argument("--output", default="assets/info-card.svg")
    a = ap.parse_args()

    svg, total = build()
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    print(f"[info-card] rows     : {sum(1 for r in ROWS if r[0])}")
    print(f"[info-card] timeline : {total:.2f}s then frozen")
    print(f"[info-card] wrote    : {out} ({out.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
