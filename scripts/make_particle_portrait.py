#!/usr/bin/env python3
"""
make_particle_portrait.py
-------------------------
Samples a prepared grayscale portrait plate into a field of particles and emits
a single self-contained animated SVG.

    empty  ->  particles fade in, scattered  ->  converge inward
           ->  portrait resolves  ->  freeze (no loop)

Design notes
------------
* Density is *saliency weighted*: particles cluster on edges (eyes, jawline,
  hair boundary, lapel) and thin out across flat regions, so a modest particle
  budget still reads as a face.
* Radius and opacity both track local luminance, giving tonal depth without
  needing colour.
* Animation is CSS `@keyframes` inside the SVG (GitHub renders this fine
  through `<img>`; no JavaScript, no external stylesheet). Every particle
  shares ONE keyframe rule and supplies its own offset/delay via CSS custom
  properties — this keeps the file ~4x smaller than per-element SMIL.
* `animation-fill-mode: both` freezes the final frame. The circles also carry
  their final opacity as a plain attribute, so any renderer that ignores CSS
  animation still shows the finished portrait instead of a blank box.

Usage
    python scripts/make_particle_portrait.py
    python scripts/make_particle_portrait.py --particles 2400 --seed 7 --preview
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

# --------------------------------------------------------------------------- #
# Tunables — change these to restyle the portrait
# --------------------------------------------------------------------------- #

PARTICLE_COUNT = 5200  # target number of particles

CANVAS_W = 520  # SVG viewBox width
PAD = 26  # inner padding around the portrait

BG = "#0B0F12"  # panel background
BORDER = "#1C2429"  # panel border
GRID = "#131A1F"  # faint technical grid

# Monochrome ramp, dim -> bright. Index chosen by particle luminance.
RAMP = ["#2C3A42", "#4A5C66", "#72868F", "#9DB0B8", "#C9D8DE", "#EAF2F5"]
ACCENT = "#3FB950"  # green, used sparingly on the brightest particles
ACCENT_SHARE = 0.035  # fraction of top-band particles that take the accent

R_MIN, R_MAX = 0.50, 1.70  # particle radius range
BANDS = 7  # luminance quantisation -> one CSS class per band (keeps SVG small)

SCATTER_MIN, SCATTER_MAX = 34.0, 120.0  # start-offset distance range (px)
FADE_IN = 0.55  # per-particle opacity fade duration (s)
TRAVEL = 1.35  # per-particle convergence duration (s)
STAGGER = 1.70  # spread of start times across the field (s)
LEAD_IN = 0.30  # delay before the first particle moves (s)
EASE = "cubic-bezier(.16,.86,.28,1)"  # decelerating settle


# --------------------------------------------------------------------------- #
# Image analysis
# --------------------------------------------------------------------------- #


def _sobel(gray: np.ndarray) -> np.ndarray:
    """Edge magnitude, normalized to [0,1]."""
    g = gray.astype(np.float32) / 255.0
    kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
    ky = kx.T

    def conv(a: np.ndarray, k: np.ndarray) -> np.ndarray:
        p = np.pad(a, 1, mode="edge")
        out = np.zeros_like(a)
        for i in range(3):
            for j in range(3):
                out += k[i, j] * p[i : i + a.shape[0], j : j + a.shape[1]]
        return out

    mag = np.hypot(conv(g, kx), conv(g, ky))
    hi = np.percentile(mag, 99.0)
    return np.clip(mag / max(hi, 1e-6), 0, 1)


def _blur(gray: np.ndarray, radius: float) -> np.ndarray:
    """Gaussian blur returning a float [0,1] tone field."""
    im = Image.fromarray(gray).filter(ImageFilter.GaussianBlur(radius=radius))
    return np.asarray(im, dtype=np.float32) / 255.0


def _saliency(
    tone: np.ndarray, edge: np.ndarray, alpha: np.ndarray
) -> np.ndarray:
    """
    Per-pixel probability weight for placing a particle.

    Density tracks luminance, so dark features (eyes, brows, moustache, hair)
    become *voids* in the particle field — that negative space is what makes a
    face legible at this scale. The edge term then adds definition along
    structural boundaries.
    """
    w = 0.05 + 1.10 * (tone ** 1.45) + 0.40 * edge
    w *= (alpha > 0.5).astype(np.float32)
    return np.clip(w, 0.0, 1.0)


def band_style(band: int) -> tuple[str, float, float]:
    """Colour, radius and opacity for a luminance band. Single source of truth
    shared by the SVG writer and the PNG preview."""
    t = band / (BANDS - 1)
    color = RAMP[min(len(RAMP) - 1, int(t * (len(RAMP) - 1) + 0.5))]
    radius = R_MIN + (R_MAX - R_MIN) * (t ** 0.75)
    # Steep curve: shadows recede to a whisper, highlights carry the face.
    opacity = 0.12 + 0.86 * (t ** 1.35)
    return color, radius, opacity


# --------------------------------------------------------------------------- #
# Sampling
# --------------------------------------------------------------------------- #


def sample_particles(
    plate: Path, target: int, seed: int
) -> tuple[list[dict], int, int]:
    img = Image.open(plate).convert("RGBA")
    arr = np.asarray(img)
    gray = arr[..., 0].astype(np.uint8)
    alpha = arr[..., 3].astype(np.float32) / 255.0
    h, w = gray.shape

    # Tone is read from a blurred plate: raw pixels alternate light/dark along
    # edges, which would render as noise rather than shading.
    tone = _blur(gray, radius=max(1.1, min(h, w) / 420.0))
    # S-curve: pushes eye sockets, brows and the moustache toward void while
    # letting cheek/forehead highlights carry the light.
    tone = np.clip((tone - 0.44) * 1.5 + 0.46, 0.0, 1.0)
    edge = _sobel(gray)
    weight = _saliency(tone, edge, alpha)
    coverage = float((weight > 0).sum())
    if coverage == 0:
        raise SystemExit("ERROR: prepared plate is fully transparent — re-run prep_photo.py")

    # Jittered grid: even spatial distribution, no clumping, then reject by
    # saliency so the surviving points concentrate on structure.
    mean_w = float(weight.sum() / coverage)
    cells = target / max(mean_w, 1e-6)
    step = math.sqrt(coverage / max(cells, 1.0))
    step = max(step, 1.2)

    rng = random.Random(seed)
    nprng = np.random.default_rng(seed)

    pts: list[dict] = []
    y = step * 0.5
    while y < h:
        x = step * 0.5
        while x < w:
            jx = x + (rng.random() - 0.5) * step
            jy = y + (rng.random() - 0.5) * step
            ix, iy = int(jx), int(jy)
            if 0 <= ix < w and 0 <= iy < h:
                p = weight[iy, ix]
                if p > 0 and rng.random() < p:
                    pts.append({"px": jx, "py": jy, "lum": float(tone[iy, ix])})
            x += step
        y += step

    # Trim or pad toward the requested budget.
    if len(pts) > target:
        keep = nprng.choice(len(pts), size=target, replace=False)
        pts = [pts[i] for i in sorted(keep.tolist())]

    return pts, w, h


# --------------------------------------------------------------------------- #
# SVG emission
# --------------------------------------------------------------------------- #


def build_svg(pts: list[dict], iw: int, ih: int, seed: int) -> str:
    scale = (CANVAS_W - 2 * PAD) / iw
    canvas_h = int(round(ih * scale + 2 * PAD))
    rng = random.Random(seed + 1)

    # Convergence origin: the optical centre of the face sits high, so bias the
    # focus upward. Particles scatter outward from here and fall back inward.
    cx = CANVAS_W / 2.0
    cy = PAD + (ih * scale) * 0.42

    # Precompute layout so we can normalise stagger by radial distance.
    laid = []
    max_r = 1e-6
    for p in pts:
        x = PAD + p["px"] * scale
        y = PAD + p["py"] * scale
        d = math.hypot(x - cx, y - cy)
        max_r = max(max_r, d)
        laid.append((x, y, d, p["lum"]))

    body: list[str] = []
    for x, y, d, lum in laid:
        # ---- appearance (quantised into a CSS class) -----------------------
        band = min(BANDS - 1, int(lum * BANDS))
        _, radius, opac = band_style(band)
        cls = f"b{band}"
        if band == BANDS - 1 and rng.random() < ACCENT_SHARE:
            cls = f"a{band}"

        # ---- motion --------------------------------------------------------
        # Start offset points outward from the focus, jittered so the cloud
        # never looks like a clean starburst.
        ang = math.atan2(y - cy, x - cx) + rng.uniform(-0.55, 0.55)
        dist = SCATTER_MIN + (SCATTER_MAX - SCATTER_MIN) * (
            (d / max_r) ** 0.6
        ) * rng.uniform(0.6, 1.15)
        dx = math.cos(ang) * dist
        dy = math.sin(ang) * dist * 0.85

        # Inside-out bloom: centre particles land first, edges settle last.
        delay = LEAD_IN + STAGGER * ((d / max_r) ** 0.75) + rng.uniform(0, 0.22)

        body.append(
            f'<circle class="{cls}" cx="{x:.1f}" cy="{y:.1f}" r="{radius:.2f}"'
            f' style="--x:{dx:.0f}px;--y:{dy:.0f}px;--t:{delay:.2f}s"/>'
        )

    total = LEAD_IN + STAGGER + max(TRAVEL, FADE_IN)

    # One class + one keyframe per luminance band. Opacity is baked into the
    # keyframe rather than passed as a custom property: some non-browser SVG
    # rasterisers apply CSS but do not resolve var(), and `opacity: var(--o)`
    # makes them fail hard instead of degrading to a static image.
    band_css = []
    for b in range(BANDS):
        color, radius, opacity = band_style(b)
        for cls, fill in ((f"b{b}", color),) + (
            ((f"a{b}", ACCENT),) if b >= BANDS - 2 else ()
        ):
            band_css.append(
                f".{cls}{{r:{radius:.2f}px;fill:{fill};opacity:{opacity:.2f};"
                f"animation:settle {TRAVEL}s {EASE} var(--t) both,"
                f"em{b} {FADE_IN}s linear var(--t) both}}"
            )
        band_css.append(
            f"@keyframes em{b}{{from{{opacity:0}}to{{opacity:{opacity:.2f}}}}}"
        )
    bands = "".join(band_css)

    style = f"""
    {bands}
    @keyframes settle {{
      from {{ transform: translate(var(--x), var(--y)); }}
      to   {{ transform: translate(0, 0); }}
    }}
    .chrome {{ animation: chrome .9s ease-out both; }}
    @keyframes chrome {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
    @media (prefers-reduced-motion: reduce) {{
      .f circle, .chrome {{ animation: none; }}
    }}
    """

    ticks = 14
    corners = "".join(
        f'<path d="{d}" stroke="#2A353B" stroke-width="1" fill="none"/>'
        for d in (
            f"M10 {10 + ticks} V10 H{10 + ticks}",
            f"M{CANVAS_W - 10 - ticks} 10 H{CANVAS_W - 10} V{10 + ticks}",
            f"M10 {canvas_h - 10 - ticks} V{canvas_h - 10} H{10 + ticks}",
            f"M{CANVAS_W - 10 - ticks} {canvas_h - 10} H{CANVAS_W - 10} "
            f"V{canvas_h - 10 - ticks}",
        )
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS_W} {canvas_h}" '
        f'width="{CANVAS_W}" height="{canvas_h}" role="img" '
        f'aria-label="Particle portrait of Sheshakanth RA">'
        f"<style>{style}</style>"
        f'<defs><pattern id="g" width="32" height="32" patternUnits="userSpaceOnUse">'
        f'<path d="M32 0H0V32" fill="none" stroke="{GRID}" stroke-width="1"/>'
        f"</pattern></defs>"
        f'<rect width="{CANVAS_W}" height="{canvas_h}" fill="{BG}"/>'
        f'<rect width="{CANVAS_W}" height="{canvas_h}" fill="url(#g)" opacity=".55"/>'
        f'<g class="chrome">'
        f'<rect x=".5" y=".5" width="{CANVAS_W - 1}" height="{canvas_h - 1}" '
        f'fill="none" stroke="{BORDER}"/>{corners}</g>'
        f'<g class="f">{"".join(body)}</g>'
        f"</svg>"
    ), total


def write_preview(pts, iw, ih, out: Path, ss: int = 3) -> None:
    """Rasterise the frozen final frame (supersampled) to eyeball likeness."""
    from PIL import ImageDraw

    scale = (CANVAS_W - 2 * PAD) / iw
    h = int(round(ih * scale + 2 * PAD))
    bgc = tuple(int(BG[i : i + 2], 16) for i in (1, 3, 5))

    im = Image.new("RGB", (CANVAS_W * ss, h * ss), bgc)
    d = ImageDraw.Draw(im)
    for p in pts:
        x = (PAD + p["px"] * scale) * ss
        y = (PAD + p["py"] * scale) * ss
        band = min(BANDS - 1, int(p["lum"] * BANDS))
        color, r, op = band_style(band)
        r *= ss
        c = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
        c = tuple(int(bgc[k] + (c[k] - bgc[k]) * op) for k in range(3))
        d.ellipse([x - r, y - r, x + r, y + r], fill=c)

    im.resize((CANVAS_W, h), Image.LANCZOS).save(out)
    print(f"[portrait] preview -> {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the particle portrait SVG.")
    ap.add_argument("--input", default="assets/source/portrait-prepared.png")
    ap.add_argument("--output", default="assets/profile-particles.svg")
    ap.add_argument("--particles", type=int, default=PARTICLE_COUNT)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--preview", action="store_true", help="also write a PNG of the final frame")
    a = ap.parse_args()

    plate = Path(a.input)
    if not plate.exists():
        raise SystemExit(
            f"ERROR: prepared plate not found at {plate}\n"
            f"       Run: python scripts/prep_photo.py"
        )

    pts, iw, ih = sample_particles(plate, a.particles, a.seed)
    svg, total = build_svg(pts, iw, ih, a.seed)

    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")

    print(f"[portrait] particles : {len(pts)}")
    print(f"[portrait] timeline  : {total:.2f}s then frozen")
    print(f"[portrait] size      : {out.stat().st_size / 1024:.0f} KB")
    print(f"[portrait] wrote     : {out}")

    if a.preview:
        write_preview(pts, iw, ih, out.with_suffix(".preview.png"))


if __name__ == "__main__":
    main()
