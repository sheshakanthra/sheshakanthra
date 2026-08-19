#!/usr/bin/env python3
"""
prep_photo.py
-------------
Turns a raw portrait photograph into a clean, high-contrast, background-free
grayscale plate that make_particle_portrait.py can sample into particles.

Pipeline
    load  ->  auto-orient  ->  background matte  ->  crop to subject
          ->  grayscale    ->  local contrast (CLAHE)
          ->  tonal shaping / normalization
          ->  RGBA PNG (alpha = subject matte)

Background removal strategy (no network, no heavy models required):

    1. If `rembg` is installed, use it (best quality, U^2-Net matting).
    2. Otherwise fall back to a studio-white chroma matte:
       flood-fill from the image border through bright, low-saturation pixels,
       then feather the resulting alpha. This is tuned for the standard
       white-seamless headshot and degrades gracefully on busy backgrounds.

Usage
    python scripts/prep_photo.py \
        --input  assets/source/photo.jpg \
        --output assets/source/portrait-prepared.png

Options
    --size 720          long-edge of the prepared plate
    --no-bg-removal     keep the original background
    --white-thresh 210  brightness above which a pixel may be background
    --sat-thresh 38     max saturation for a pixel to count as background
    --debug             also write a *-debug-alpha.png matte preview
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

# --------------------------------------------------------------------------- #
# Background matting
# --------------------------------------------------------------------------- #


def _matte_with_rembg(img: Image.Image) -> np.ndarray | None:
    """Return a float alpha matte in [0,1] using rembg, or None if unavailable."""
    try:
        from rembg import remove  # type: ignore
    except Exception:
        return None
    try:
        cut = remove(img.convert("RGBA"))
        alpha = np.asarray(cut.split()[-1], dtype=np.float32) / 255.0
        print("  [bg] rembg matte")
        return alpha
    except Exception as exc:  # pragma: no cover - defensive
        print(f"  [bg] rembg failed ({exc}); falling back to chroma matte")
        return None


def _matte_white_studio(
    img: Image.Image, white_thresh: int, sat_thresh: int
) -> np.ndarray:
    """
    Flood-fill background matte for white/seamless studio backdrops.

    A pixel is *background-eligible* when it is bright and desaturated.
    We then keep only the eligible region that is connected to the image
    border, so bright shirts / collars in the middle of the subject survive.
    """
    from scipy import ndimage

    rgb = np.asarray(img.convert("RGB"), dtype=np.int16)
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    value = mx
    sat = mx - mn  # cheap saturation proxy, 0..255

    eligible = (value >= white_thresh) & (sat <= sat_thresh)

    # Connected components of the eligible region; keep border-touching ones.
    labels, n = ndimage.label(eligible)
    if n == 0:
        return np.ones(rgb.shape[:2], dtype=np.float32)

    border_labels = set(labels[0, :]) | set(labels[-1, :])
    border_labels |= set(labels[:, 0]) | set(labels[:, -1])
    border_labels.discard(0)

    background = np.isin(labels, list(border_labels))

    # Fill interior holes of the *subject* so gaps (e.g. between arm and torso
    # that never reach the border) stay opaque.
    subject = ~background
    subject = ndimage.binary_fill_holes(subject)

    # Drop stray specks, but keep every *significant* component.
    #
    # Important: a bright shirt collar often bridges the left and right
    # background all the way across, which severs the head from the torso.
    # Keeping only the largest component would silently delete the face, so
    # we keep anything at least `keep_frac` of the largest component.
    sub_labels, sn = ndimage.label(subject)
    if sn > 1:
        sizes = np.asarray(ndimage.sum(subject, sub_labels, range(1, sn + 1)))
        keep_frac = 0.03
        keep = np.where(sizes >= sizes.max() * keep_frac)[0] + 1
        subject = np.isin(sub_labels, keep)

    alpha = subject.astype(np.float32)

    # Feather the edge so particles do not terminate on a hard staircase.
    a_img = Image.fromarray((alpha * 255).astype(np.uint8))
    a_img = a_img.filter(ImageFilter.GaussianBlur(radius=1.6))
    alpha = np.asarray(a_img, dtype=np.float32) / 255.0

    print(f"  [bg] studio chroma matte  (coverage {alpha.mean() * 100:.1f}%)")
    return alpha


# --------------------------------------------------------------------------- #
# Tone processing
# --------------------------------------------------------------------------- #


def _clahe(gray: np.ndarray, tiles: int = 8, clip: float = 1.9) -> np.ndarray:
    """
    Contrast Limited Adaptive Histogram Equalization, numpy-only.

    Builds a clipped CDF per tile, then bilinearly interpolates the per-tile
    mappings across the image. Keeps facial micro-contrast (eyes, nose, lip
    line) that a global curve would flatten.
    """
    h, w = gray.shape
    ty, tx = tiles, tiles
    th, tw = int(np.ceil(h / ty)), int(np.ceil(w / tx))

    pad = np.pad(gray, ((0, ty * th - h), (0, tx * tw - w)), mode="edge")
    maps = np.empty((ty, tx, 256), dtype=np.float32)

    limit = max(1.0, clip * (th * tw) / 256.0)
    for iy in range(ty):
        for ix in range(tx):
            tile = pad[iy * th : (iy + 1) * th, ix * tw : (ix + 1) * tw]
            hist = np.bincount(tile.ravel(), minlength=256).astype(np.float32)
            excess = np.maximum(hist - limit, 0).sum()
            hist = np.minimum(hist, limit) + excess / 256.0
            cdf = np.cumsum(hist)
            cdf /= max(cdf[-1], 1e-6)
            maps[iy, ix] = cdf * 255.0

    # Bilinear interpolation of tile mappings.
    yy = np.clip((np.arange(h) + 0.5) / th - 0.5, 0, ty - 1)
    xx = np.clip((np.arange(w) + 0.5) / tw - 0.5, 0, tx - 1)
    y0 = np.floor(yy).astype(int)
    x0 = np.floor(xx).astype(int)
    y1 = np.minimum(y0 + 1, ty - 1)
    x1 = np.minimum(x0 + 1, tx - 1)
    fy = (yy - y0)[:, None].astype(np.float32)
    fx = (xx - x0)[None, :].astype(np.float32)

    g = gray
    a = maps[y0[:, None], x0[None, :], g]
    b = maps[y0[:, None], x1[None, :], g]
    c = maps[y1[:, None], x0[None, :], g]
    d = maps[y1[:, None], x1[None, :], g]

    out = (
        a * (1 - fy) * (1 - fx)
        + b * (1 - fy) * fx
        + c * fy * (1 - fx)
        + d * fy * fx
    )
    return np.clip(out, 0, 255).astype(np.uint8)


def _normalize(gray: np.ndarray, lo_pct: float = 1.5, hi_pct: float = 99.0) -> np.ndarray:
    """Percentile black/white point stretch — kills flat, muddy midtones."""
    lo, hi = np.percentile(gray, [lo_pct, hi_pct])
    if hi - lo < 1e-3:
        return gray
    out = (gray.astype(np.float32) - lo) * (255.0 / (hi - lo))
    return np.clip(out, 0, 255).astype(np.uint8)


def _shape_tone(gray: np.ndarray, gamma: float = 1.05, lift: float = 0.02) -> np.ndarray:
    """Slight gamma + shadow lift so dark hair still yields sampleable particles."""
    x = gray.astype(np.float32) / 255.0
    x = np.power(x, gamma)
    x = lift + (1.0 - lift) * x
    return np.clip(x * 255.0, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- #
# Cropping
# --------------------------------------------------------------------------- #


def _crop_to_subject(
    gray: np.ndarray,
    alpha: np.ndarray,
    margin: float = 0.04,
    mode: str = "head",
    head_span: float = 1.95,
    aspect: float = 1.18,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Crop to the matted subject.

    mode="full"  keep the whole silhouette.
    mode="head"  frame a portrait box around the head: width = head width x
                 `head_span`, height = width x `aspect`. A full bust renders
                 the face far too small at README scale.
    """
    ys, xs = np.where(alpha > 0.35)
    if ys.size == 0:
        return gray, alpha

    sy0, sy1 = int(ys.min()), int(ys.max())
    sx0, sx1 = int(xs.min()), int(xs.max())
    height = sy1 - sy0

    if mode == "head":
        # Measure the head from the top band of the silhouette, then build a
        # portrait-aspect box around it. Using the full top-44% bounding box
        # instead would swallow the shoulders and shrink the face.
        probe = alpha[sy0 : sy0 + max(1, int(height * 0.22))] > 0.35
        pxs = np.where(probe.any(axis=0))[0]
        head_w = float(pxs.max() - pxs.min()) if pxs.size else (sx1 - sx0)
        head_cx = float((pxs.max() + pxs.min()) / 2.0) if pxs.size else (sx0 + sx1) / 2.0

        box_w = head_w * head_span
        box_h = box_w * aspect
        y0 = max(0, sy0 - int(head_w * 0.12))          # a little headroom
        y1 = min(gray.shape[0] - 1, y0 + int(box_h))
        sx0 = int(head_cx - box_w / 2.0)
        sx1 = int(head_cx + box_w / 2.0)
    else:
        y0, y1 = sy0, sy1

    my = int((y1 - y0) * margin)
    mx = int((sx1 - sx0) * margin)
    y0 = max(0, y0 - my)
    y1 = min(gray.shape[0] - 1, y1 + my)
    x0 = max(0, sx0 - mx)
    x1 = min(gray.shape[1] - 1, sx1 + mx)
    if x1 <= x0 or y1 <= y0:
        return gray, alpha
    return gray[y0 : y1 + 1, x0 : x1 + 1], alpha[y0 : y1 + 1, x0 : x1 + 1]


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def prepare(
    src: Path,
    dst: Path,
    size: int = 720,
    remove_bg: bool = True,
    crop: str = "head",
    white_thresh: int = 210,
    sat_thresh: int = 38,
    debug: bool = False,
) -> Path:
    if not src.exists():
        sys.exit(
            f"ERROR: source photo not found at {src}\n"
            f"       Drop your photograph there (jpg/png) and re-run."
        )

    print(f"[prep] loading {src}")
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    print(f"  [in] {img.size[0]}x{img.size[1]}")

    # ---- alpha matte -------------------------------------------------------
    if remove_bg:
        alpha = _matte_with_rembg(img)
        if alpha is None:
            alpha = _matte_white_studio(img, white_thresh, sat_thresh)
    else:
        alpha = np.ones((img.size[1], img.size[0]), dtype=np.float32)

    # ---- grayscale + crop --------------------------------------------------
    gray = np.asarray(img.convert("L"), dtype=np.uint8)
    gray, alpha = _crop_to_subject(gray, alpha, mode=crop)

    # ---- resize (long edge = size) ----------------------------------------
    h, w = gray.shape
    scale = size / float(max(h, w))
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    gray = np.asarray(
        Image.fromarray(gray).resize((nw, nh), Image.LANCZOS), dtype=np.uint8
    )
    alpha = np.asarray(
        Image.fromarray((alpha * 255).astype(np.uint8)).resize((nw, nh), Image.LANCZOS),
        dtype=np.float32,
    ) / 255.0
    print(f"  [out] {nw}x{nh}")

    # ---- tone --------------------------------------------------------------
    gray = _clahe(gray)
    gray = _normalize(gray)
    gray = _shape_tone(gray)
    print("  [tone] CLAHE + percentile stretch + gamma shaping")

    # ---- write -------------------------------------------------------------
    rgba = np.dstack(
        [gray, gray, gray, (np.clip(alpha, 0, 1) * 255).astype(np.uint8)]
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(dst)
    print(f"[prep] wrote {dst}")

    if debug:
        dbg = dst.with_name(dst.stem + "-debug-alpha.png")
        Image.fromarray((alpha * 255).astype(np.uint8)).save(dbg)
        print(f"[prep] wrote {dbg}")

    return dst


def main() -> None:
    p = argparse.ArgumentParser(description="Prepare a portrait for particle sampling.")
    p.add_argument("--input", default="assets/source/photo.jpg")
    p.add_argument("--output", default="assets/source/portrait-prepared.png")
    p.add_argument("--size", type=int, default=720)
    p.add_argument("--no-bg-removal", action="store_true")
    p.add_argument("--crop", choices=["head", "full"], default="head",
                   help="head = head-and-shoulders (default), full = whole silhouette")
    p.add_argument("--white-thresh", type=int, default=210)
    p.add_argument("--sat-thresh", type=int, default=38)
    p.add_argument("--debug", action="store_true")
    a = p.parse_args()

    prepare(
        Path(a.input),
        Path(a.output),
        size=a.size,
        remove_bg=not a.no_bg_removal,
        crop=a.crop,
        white_thresh=a.white_thresh,
        sat_thresh=a.sat_thresh,
        debug=a.debug,
    )


if __name__ == "__main__":
    main()
