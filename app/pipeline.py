"""
pipeline.py – Video rendering pipeline with audio synchronisation.

Key features:
  - Audio-driven slide durations: actual audio length is measured, then slide
    durations are proportionally scaled so total == audio length.
  - Ken-Burns zoom animation on each slide image (zoompan filter).
  - 8 layout types: hero, lower_third, cinematic, card, top_banner,
    split_right, quote, minimal_bottom.
  - 5 design styles: modern, vintage, kawaii, neon, minimal.
  - Text animations: fade-in + slide-up per layout.
  - MAX_IMAGES limit: only first N slides get unique AI images; extras reuse cycling.
  - Multi-line bullet text rendered per-line to avoid ffmpeg wrap issues.
  - Concurrent image generation (configurable via CONCURRENT_IMAGES).
  - Robust ffmpeg filter escaping.
  - Per-slide video and full-project SSE events.
  - Slide metadata (storyboard.json) is editable by the API layer.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .ai_providers import PipelineError, Slide, get_provider
from .config import (
    AUDIO_BITRATE,
    CONCURRENT_IMAGES,
    CONCURRENT_RENDERS,
    FPS,
    FONT_BULLET_SIZE,
    FONT_TITLE_SIZE,
    MAX_IMAGES,
    SLIDE_PADDING,
    VIDEO_BITRATE,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
)
from .storage import ensure_project_dirs, write_status

# ──────────────────────────────────────────────────────────────────────────────
# Design styles
# ──────────────────────────────────────────────────────────────────────────────

DESIGN_STYLES: Dict[str, dict] = {
    "modern": {
        "title_color": "white",
        "bullet_color": "white",
        "accent": "0x38bdf8",
        "title_shadow": "black@0.9",
        "bullet_shadow": "black@0.8",
        "sep_color": "0x38bdf8@0.9",
        "sep_h": 3,
        "bullet_char": "• ",
        "title_scale": 1.0,
        "overlay_top": "black@0.55",
        "overlay_bot": "black@0.38",
    },
    "vintage": {
        "title_color": "0xf5e6c8",
        "bullet_color": "0xe8d5a3",
        "accent": "0xc9a02a",
        "title_shadow": "0x3d1500@0.9",
        "bullet_shadow": "0x3d1500@0.8",
        "sep_color": "0xc9a02a@0.85",
        "sep_h": 2,
        "bullet_char": "◆ ",
        "title_scale": 0.92,
        "overlay_top": "0x1a0800@0.68",
        "overlay_bot": "0x1a0800@0.50",
    },
    "kawaii": {
        "title_color": "0xff6eb4",
        "bullet_color": "0xffffff",
        "accent": "0xffb6c1",
        "title_shadow": "0x800040@0.8",
        "bullet_shadow": "0x400020@0.7",
        "sep_color": "0xffb6c1@0.9",
        "sep_h": 4,
        "bullet_char": "♥ ",
        "title_scale": 1.05,
        "overlay_top": "0x1a0030@0.58",
        "overlay_bot": "0x0a0020@0.42",
    },
    "neon": {
        "title_color": "0x00ffcc",
        "bullet_color": "0xff00cc",
        "accent": "0x00ffcc",
        "title_shadow": "0x00ffcc@0.55",
        "bullet_shadow": "0xff00cc@0.45",
        "sep_color": "0x00ffcc@0.9",
        "sep_h": 2,
        "bullet_char": "▸ ",
        "title_scale": 1.0,
        "overlay_top": "black@0.80",
        "overlay_bot": "black@0.65",
    },
    "minimal": {
        "title_color": "white",
        "bullet_color": "0xbbbbbb",
        "accent": "white",
        "title_shadow": "black@0.4",
        "bullet_shadow": "black@0.35",
        "sep_color": "white@0.5",
        "sep_h": 1,
        "bullet_char": "– ",
        "title_scale": 0.85,
        "overlay_top": "black@0.28",
        "overlay_bot": "black@0.20",
    },
}

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ffmpeg_path() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise PipelineError("ffmpeg not found on PATH. Please install ffmpeg.")
    return p


def _find_bold_font() -> Optional[str]:
    """Locate a bold sans-serif font file on common Linux/macOS paths."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
        "/Library/Fonts/Arial Bold.ttf",        # macOS
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def _run_ffmpeg(args: List[str], cwd: Optional[Path] = None) -> None:
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise PipelineError(proc.stdout[-3000:] if proc.stdout else "ffmpeg error")


def _get_audio_duration(audio_path: Path) -> float:
    """Return audio duration in seconds via ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    proc = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def _escape_drawtext(value: str) -> str:
    """Escape a string for use in an ffmpeg drawtext filter value."""
    return (
        value
        .replace("\\", "\\\\")
        .replace("'", "\u2019")   # right single quotation mark (safer than escaping)
        .replace(":", "\\:")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("%", "\\%")
    )


# ──────────────────────────────────────────────────────────────────────────────
# Animation expression helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fade_in(start: float, dur: float = 0.8) -> str:
    """Alpha fade-in: invisible until `start` s, then fades over `dur` seconds.
    Commas are literal — caller must wrap in single quotes: alpha='{expr}'
    """
    end = round(start + dur, 3)
    return f"if(lt(t,{start}),0,if(lt(t,{end}),(t-{start})/{dur},1))"


def _slide_up_y(base_y: int, start: float = 0.2, dur: float = 0.7, px: int = 20) -> str:
    """Y expression: slides up `px` pixels over `dur` s starting at `start`.
    Commas are literal — caller must wrap in single quotes: y='{expr}'
    """
    end = round(start + dur, 3)
    return (
        f"if(lt(t,{start}),{base_y + px},"
        f"if(lt(t,{end}),{base_y}+{px}*(1-(t-{start})/{dur}),{base_y}))"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Ken-Burns animation
# ──────────────────────────────────────────────────────────────────────────────

def _ken_burns_exprs(slide_idx: int) -> Tuple[str, str, str]:
    """Return (zoom_expr, x_expr, y_expr) for Ken-Burns zoompan filter."""
    pattern = slide_idx % 6
    if pattern == 0:
        return ("min(1+0.0004*on,1.12)", "(iw-iw/zoom)/2", "(ih-ih/zoom)/2")   # zoom in, centre
    elif pattern == 1:
        return ("max(1.12-0.0004*on,1.0)", "(iw-iw/zoom)/2", "(ih-ih/zoom)/2") # zoom out, centre
    elif pattern == 2:
        return ("min(1+0.0003*on,1.10)", "0", "(ih-ih/zoom)/2")                 # pan right
    elif pattern == 3:
        return ("min(1+0.0003*on,1.10)", "(iw-iw/zoom)", "(ih-ih/zoom)/2")      # pan left
    elif pattern == 4:
        return ("min(1+0.0003*on,1.08)", "(iw-iw/zoom)/2", "0")                 # pan down
    else:
        return ("min(1+0.0003*on,1.08)", "(iw-iw/zoom)/2", "(ih-ih/zoom)")      # pan up


# ──────────────────────────────────────────────────────────────────────────────
# Layout filter builders
# ──────────────────────────────────────────────────────────────────────────────

def _build_visual_filters(
    slide: Slide,
    style_cfg: dict,
    layout: str,
    bold_font: Optional[str],
    pad_x: int,
    pad_y: int,
) -> str:
    """Build the FFmpeg filter chain segment (after zoompan) for all visual overlays."""
    W, H = VIDEO_WIDTH, VIDEO_HEIGHT
    fs = style_cfg
    font_spec = f"fontfile='{bold_font}':" if bold_font else ""
    title_size = int(FONT_TITLE_SIZE * fs.get("title_scale", 1.0))
    bul_size = FONT_BULLET_SIZE
    line_h = bul_size + 14

    title_esc = _escape_drawtext(slide.title)
    bullets = slide.bullets[:5]

    parts: List[str] = []

    def box(x, y, w, h, color: str) -> None:
        parts.append(f"drawbox=x={x}:y={y}:width={w}:height={h}:color={color}:t=fill")

    def sep(x, y, w, h: Optional[int] = None) -> None:
        sh = h if h is not None else fs["sep_h"]
        parts.append(
            f"drawbox=x={x}:y={y}:width={w}:height={sh}:color={fs['sep_color']}:t=fill"
        )

    def title(x_str, y_val: int, size: int, alpha: Optional[str] = None, y_expr: Optional[str] = None) -> None:
        y_str = y_expr if y_expr else str(y_val)
        f = (
            f"drawtext={font_spec}fontcolor={fs['title_color']}:fontsize={size}:"
            f"x='{x_str}':y='{y_str}':"
            f"text='{title_esc}':"
            f"shadowcolor={fs['title_shadow']}:shadowx=3:shadowy=3:box=0"
        )
        if alpha:
            f += f":alpha='{alpha}'"
        parts.append(f)

    def bullet(txt: str, x_str, y_val: int, size: int = 0, alpha: Optional[str] = None) -> None:
        escaped = _escape_drawtext(f"{fs['bullet_char']}{txt}")
        bsz = size if size else bul_size
        f = (
            f"drawtext={font_spec}fontcolor={fs['bullet_color']}:fontsize={bsz}:"
            f"x='{x_str}':y='{y_val}':"
            f"text='{escaped}':"
            f"shadowcolor={fs['bullet_shadow']}:shadowx=2:shadowy=2:box=0"
        )
        if alpha:
            f += f":alpha='{alpha}'"
        parts.append(f)

    # ── Layout implementations ─────────────────────────────────────────────────

    if layout == "hero":
        # Classic: title top-left with separator, bullets centre-left
        box(0, 0, W, int(H * 0.45), fs["overlay_top"])
        box(0, int(H * 0.55), W, H - int(H * 0.55), fs["overlay_bot"])
        sep_y = pad_y + title_size + 16
        sep(pad_x, sep_y, W - pad_x * 2)
        title(str(pad_x), pad_y, title_size,
              alpha=_fade_in(0.2, 0.8),
              y_expr=_slide_up_y(pad_y, 0.2, 0.8, 18))
        bul_y = int(H * 0.53)
        for i, b in enumerate(bullets):
            bullet(b, str(pad_x), bul_y + i * line_h,
                   alpha=_fade_in(0.7 + i * 0.25, 0.6))

    elif layout == "lower_third":
        # News broadcast: strong bottom band, compact text at bottom
        grad_y = int(H * 0.52)
        box(0, grad_y, W, H - grad_y, "black@0.82")
        title_y = int(H * 0.61)
        # Short accent bar above title
        sep(pad_x, title_y - 12, 80, fs["sep_h"] + 2)
        title(str(pad_x), title_y, title_size,
              alpha=_fade_in(0.2, 0.7),
              y_expr=_slide_up_y(title_y, 0.2, 0.7, 14))
        bul_y = int(H * 0.74)
        for i, b in enumerate(bullets[:3]):
            bullet(b, str(pad_x), bul_y + i * line_h,
                   alpha=_fade_in(0.55 + i * 0.2, 0.55))

    elif layout == "cinematic":
        # Epic: vignette edges, large centered title, minimal text
        box(0, 0, W, int(H * 0.18), "black@0.78")
        box(0, int(H * 0.82), W, int(H * 0.18), "black@0.78")
        big_size = int(title_size * 1.28)
        title_y = int(H * 0.37)
        title("(W-tw)/2", title_y, big_size,
              alpha=_fade_in(0.4, 1.1),
              y_expr=_slide_up_y(title_y, 0.4, 1.0, 28))
        # First bullet as subtitle (centred, smaller)
        if bullets:
            sub_esc = _escape_drawtext(bullets[0])
            sub_y = title_y + big_size + 28
            parts.append(
                f"drawtext={font_spec}fontcolor={fs['bullet_color']}:fontsize={int(bul_size * 1.05)}:"
                f"x='(W-tw)/2':y='{sub_y}':"
                f"text='{sub_esc}':"
                f"shadowcolor={fs['bullet_shadow']}:shadowx=2:shadowy=2:box=0"
                f":alpha='{_fade_in(1.2, 0.9)}'"
            )

    elif layout == "card":
        # Frosted card: dark box in centre, text inside
        box(0, 0, W, H, "black@0.22")
        card_x = pad_x - 24
        card_y = int(H * 0.19)
        card_w = W - (pad_x - 24) * 2
        bul_count = min(len(bullets), 5)
        card_h = max(title_size + 30 + bul_count * line_h + 40, int(H * 0.48))
        box(card_x, card_y, card_w, card_h, "black@0.72")
        inner_y = card_y + 28
        title(str(pad_x), inner_y, title_size,
              alpha=_fade_in(0.3, 0.8),
              y_expr=_slide_up_y(inner_y, 0.3, 0.8, 12))
        sep(pad_x, inner_y + title_size + 10, card_w - 48)
        bul_base = inner_y + title_size + 28
        for i, b in enumerate(bullets[:5]):
            bullet(b, str(pad_x), bul_base + i * line_h,
                   alpha=_fade_in(0.65 + i * 0.2, 0.55))

    elif layout == "top_banner":
        # Dark top band, title and bullets in upper third
        box(0, 0, W, int(H * 0.40), "black@0.80")
        title(str(pad_x), pad_y, title_size,
              alpha=_fade_in(0.2, 0.7),
              y_expr=_slide_up_y(pad_y, 0.2, 0.7, 14))
        sep(pad_x, pad_y + title_size + 8, W - pad_x * 2)
        bul_y = pad_y + title_size + 28
        for i, b in enumerate(bullets[:4]):
            bullet(b, str(pad_x), bul_y + i * line_h,
                   alpha=_fade_in(0.55 + i * 0.2, 0.55))

    elif layout == "split_right":
        # Dark right half, right-aligned text
        box(int(W * 0.50), 0, int(W * 0.50), H, "black@0.78")
        title_y = int(H * 0.20)
        title(f"W-tw-{pad_x}", title_y, title_size,
              alpha=_fade_in(0.3, 0.8))
        sep_x = int(W * 0.52)
        sep(sep_x, title_y + title_size + 10, W - sep_x - pad_x)
        bul_y = title_y + title_size + 30
        for i, b in enumerate(bullets[:4]):
            escaped = _escape_drawtext(f"{fs['bullet_char']}{b}")
            f = (
                f"drawtext={font_spec}fontcolor={fs['bullet_color']}:fontsize={bul_size}:"
                f"x='W-tw-{pad_x}':y='{bul_y + i * line_h}':"
                f"text='{escaped}':"
                f"shadowcolor={fs['bullet_shadow']}:shadowx=2:shadowy=2:box=0"
                f":alpha='{_fade_in(0.7 + i * 0.22, 0.55)}'"
            )
            parts.append(f)

    elif layout == "quote":
        # Full-screen overlay, single huge centred title + thin decorative lines
        box(0, 0, W, H, "black@0.42")
        big_size = int(title_size * 1.38)
        title_y = int(H * 0.36)
        # Decorative horizontal rules
        sep(pad_x, int(H * 0.33), W - pad_x * 2, 1)
        title("(W-tw)/2", title_y, big_size,
              alpha=_fade_in(0.5, 1.2),
              y_expr=_slide_up_y(title_y, 0.5, 1.0, 32))
        sep(pad_x, title_y + big_size + 30, W - pad_x * 2, 1)
        # First bullet as attribution
        if bullets:
            attr_esc = _escape_drawtext(bullets[0])
            attr_y = title_y + big_size + 46
            parts.append(
                f"drawtext={font_spec}fontcolor={fs['bullet_color']}:fontsize={int(bul_size * 0.82)}:"
                f"x='(W-tw)/2':y='{attr_y}':"
                f"text='{attr_esc}':"
                f"shadowcolor={fs['bullet_shadow']}:shadowx=1:shadowy=1:box=0"
                f":alpha='{_fade_in(1.5, 0.7)}'"
            )

    elif layout == "minimal_bottom":
        # Breathe: thin bottom gradient, small title, image dominates
        bot_y = int(H * 0.77)
        box(0, bot_y, W, H - bot_y, "black@0.68")
        sep(pad_x, bot_y + 8, 55)  # short accent tick
        sm_title = int(title_size * 0.78)
        title_y = int(H * 0.81)
        title(str(pad_x), title_y, sm_title,
              alpha=_fade_in(0.3, 0.8),
              y_expr=_slide_up_y(title_y, 0.3, 0.8, 10))
        # 1–2 bullets max, compact
        sm_bul = int(bul_size * 0.72)
        for i, b in enumerate(bullets[:2]):
            bullet(b, str(pad_x), title_y + sm_title + 8 + i * (sm_bul + 8),
                   size=sm_bul,
                   alpha=_fade_in(0.65 + i * 0.3, 0.55))

    elif layout == "split_left":
        # Mirror of split_right: dark left half, left-aligned text
        box(0, 0, int(W * 0.50), H, "black@0.78")
        title_y = int(H * 0.20)
        title(str(pad_x), title_y, title_size, alpha=_fade_in(0.3, 0.8))
        sep(pad_x, title_y + title_size + 10, int(W * 0.44) - pad_x)
        bul_y = title_y + title_size + 30
        for i, b in enumerate(bullets[:4]):
            bullet(b, str(pad_x), bul_y + i * line_h, alpha=_fade_in(0.7 + i * 0.22, 0.55))

    elif layout == "vignette_all":
        # Dark vignette on all 4 edges, centered text
        vig_h, vig_w = int(H * 0.22), int(W * 0.15)
        box(0, 0, W, vig_h, "black@0.72")
        box(0, H - vig_h, W, vig_h, "black@0.72")
        box(0, 0, vig_w, H, "black@0.60")
        box(W - vig_w, 0, vig_w, H, "black@0.60")
        title_y = int(H * 0.40)
        title("(W-tw)/2", title_y, title_size,
              alpha=_fade_in(0.4, 1.0), y_expr=_slide_up_y(title_y, 0.4, 0.9, 22))
        bul_y = title_y + title_size + 22
        for i, b in enumerate(bullets[:3]):
            bullet(b, str(pad_x), bul_y + i * line_h, alpha=_fade_in(0.9 + i * 0.25, 0.6))

    elif layout == "oversized":
        # Giant 1.6× title, subtle global overlay
        box(0, 0, W, H, "black@0.25")
        big = int(title_size * 1.60)
        title_y = int(H * 0.32)
        title("(W-tw)/2", title_y, big,
              alpha=_fade_in(0.3, 1.1), y_expr=_slide_up_y(title_y, 0.3, 1.0, 35))
        sep(pad_x, title_y + big + 18, W - pad_x * 2, 1)
        if bullets:
            bullet(bullets[0], "(W-tw)/2", title_y + big + 34,
                   size=int(bul_size * 0.90), alpha=_fade_in(1.1, 0.8))

    elif layout == "ribbon":
        # Horizontal dark band across mid-screen, text inside
        rib_y, rib_h = int(H * 0.38), int(H * 0.26)
        box(0, rib_y, W, rib_h, "black@0.85")
        title_y = rib_y + 18
        title("(W-tw)/2", title_y, title_size,
              alpha=_fade_in(0.3, 0.8), y_expr=_slide_up_y(title_y, 0.3, 0.8, 14))
        sep(pad_x, title_y + title_size + 8, W - pad_x * 2, 1)
        bul_y = title_y + title_size + 24
        for i, b in enumerate(bullets[:2]):
            bullet(b, "(W-tw)/2", bul_y + i * line_h,
                   size=int(bul_size * 0.90), alpha=_fade_in(0.7 + i * 0.25, 0.55))

    elif layout == "news_full":
        # Wide deep bottom band (full newscast style)
        grad_y = int(H * 0.48)
        box(0, grad_y, W, H - grad_y, "black@0.90")
        sep(0, grad_y, W, fs["sep_h"] + 2)
        title_y = grad_y + 22
        title(str(pad_x), title_y, title_size,
              alpha=_fade_in(0.2, 0.7), y_expr=_slide_up_y(title_y, 0.2, 0.7, 14))
        bul_y = title_y + title_size + 20
        for i, b in enumerate(bullets[:4]):
            bullet(b, str(pad_x), bul_y + i * line_h, alpha=_fade_in(0.55 + i * 0.18, 0.50))

    elif layout == "left_panel":
        # Narrow dark left panel (38%), text inside
        panel_w = int(W * 0.38)
        box(0, 0, panel_w, H, "black@0.82")
        sm = int(title_size * 0.88)
        title_y = int(H * 0.25)
        title(str(pad_x), title_y, sm,
              alpha=_fade_in(0.3, 0.8), y_expr=_slide_up_y(title_y, 0.3, 0.8, 14))
        sep(pad_x, title_y + sm + 10, panel_w - pad_x * 2)
        bul_y = title_y + sm + 30
        for i, b in enumerate(bullets[:5]):
            bullet(b, str(pad_x), bul_y + i * line_h,
                   size=int(bul_size * 0.85), alpha=_fade_in(0.65 + i * 0.20, 0.55))

    elif layout == "floating_right":
        # Floating dark box at right side, text inside
        box(0, 0, W, H, "black@0.18")
        fx = int(W * 0.54)
        fy = int(H * 0.22)
        fw = int(W * 0.42)
        bul_count = min(len(bullets), 5)
        fh = max(title_size + 30 + bul_count * line_h + 36, int(H * 0.50))
        box(fx, fy, fw, fh, "black@0.78")
        ix, iy = fx + 24, fy + 22
        sm = int(title_size * 0.88)
        title(str(ix), iy, sm, alpha=_fade_in(0.3, 0.8), y_expr=_slide_up_y(iy, 0.3, 0.8, 12))
        sep(ix, iy + sm + 8, fw - 48)
        bul_y = iy + sm + 26
        for i, b in enumerate(bullets[:5]):
            bullet(b, str(ix), bul_y + i * line_h,
                   size=int(bul_size * 0.82), alpha=_fade_in(0.65 + i * 0.20, 0.50))

    elif layout == "letterbox":
        # Cinema letterbox: solid black bars top + bottom
        bar_h = int(H * 0.12)
        box(0, 0, W, bar_h, "black@1.0")
        box(0, H - bar_h, W, bar_h, "black@1.0")
        box(0, H - bar_h * 3, W, bar_h * 2, "black@0.58")
        title_y = H - bar_h + max(4, int(bar_h * 0.18))
        title("(W-tw)/2", title_y, int(title_size * 0.72), alpha=_fade_in(0.4, 0.8))
        if bullets:
            sub_esc = _escape_drawtext(bullets[0])
            parts.append(
                f"drawtext={font_spec}fontcolor={fs['bullet_color']}:fontsize={int(bul_size * 0.65)}:"
                f"x='(W-tw)/2':y='{int(bar_h * 0.28)}':"
                f"text='{sub_esc}':"
                f"shadowcolor={fs['bullet_shadow']}:shadowx=1:shadowy=1:box=0"
                f":alpha='{_fade_in(0.6, 0.7)}'"
            )

    elif layout == "chapter":
        # Elegant chapter marker: thin rules + large centered title
        box(0, 0, W, H, "black@0.32")
        big = int(title_size * 1.18)
        title_y = int(H * 0.40)
        sep(int(W * 0.20), title_y - 18, int(W * 0.60), 1)
        title("(W-tw)/2", title_y, big,
              alpha=_fade_in(0.4, 1.0), y_expr=_slide_up_y(title_y, 0.4, 1.0, 20))
        sep(int(W * 0.20), title_y + big + 20, int(W * 0.60), 1)
        bul_y = title_y + big + 38
        for i, b in enumerate(bullets[:2]):
            bullet(b, "(W-tw)/2", bul_y + i * line_h,
                   size=int(bul_size * 0.88), alpha=_fade_in(1.1 + i * 0.30, 0.70))

    elif layout == "stat":
        # Statistics focus: small title + HUGE first bullet (for numbers/percentages)
        box(0, 0, W, int(H * 0.30), "black@0.72")
        box(0, int(H * 0.78), W, int(H * 0.22), "black@0.55")
        title(str(pad_x), pad_y, int(title_size * 0.58), alpha=_fade_in(0.2, 0.6))
        if bullets:
            huge = int(title_size * 1.90)
            stat_esc = _escape_drawtext(bullets[0])
            parts.append(
                f"drawtext={font_spec}fontcolor={fs['title_color']}:fontsize={huge}:"
                f"x='(W-tw)/2':y='{int(H * 0.28)}':"
                f"text='{stat_esc}':"
                f"shadowcolor={fs['title_shadow']}:shadowx=5:shadowy=5:box=0"
                f":alpha='{_fade_in(0.5, 1.1)}'"
            )
            sm_bul = int(bul_size * 0.80)
            for i, b in enumerate(bullets[1:4]):
                bullet(b, str(pad_x), int(H * 0.80) + i * (sm_bul + 10),
                       size=sm_bul, alpha=_fade_in(1.3 + i * 0.20, 0.50))

    elif layout == "broadcast_tag":
        # News chyron: info tag at very bottom
        tag_h = int(H * 0.15)
        tag_y = H - tag_h
        box(0, tag_y, W, tag_h, "black@0.90")
        sep(0, tag_y, W, fs["sep_h"])
        title(str(pad_x), tag_y + int(tag_h * 0.18), int(title_size * 0.65),
              alpha=_fade_in(0.2, 0.7))
        if bullets:
            txt = _escape_drawtext(f"{fs['bullet_char']}{bullets[0]}")
            parts.append(
                f"drawtext={font_spec}fontcolor={fs['bullet_color']}:fontsize={int(bul_size * 0.62)}:"
                f"x='{pad_x}':y='{tag_y + int(tag_h * 0.60)}':"
                f"text='{txt}':"
                f"shadowcolor={fs['bullet_shadow']}:shadowx=1:shadowy=1:box=0"
                f":alpha='{_fade_in(0.5, 0.6)}'"
            )

    elif layout == "corner_tl":
        # Compact info block in top-left corner
        sm = int(title_size * 0.80)
        box(0, 0, int(W * 0.42), int(H * 0.42), "black@0.78")
        title_y = pad_y
        title(str(pad_x), title_y, sm,
              alpha=_fade_in(0.3, 0.8), y_expr=_slide_up_y(title_y, 0.3, 0.8, 12))
        sep(pad_x, title_y + sm + 8, int(W * 0.34))
        bul_y = title_y + sm + 26
        for i, b in enumerate(bullets[:3]):
            bullet(b, str(pad_x), bul_y + i * line_h,
                   size=int(bul_size * 0.78), alpha=_fade_in(0.6 + i * 0.20, 0.50))

    elif layout == "corner_br":
        # Info block at bottom-right corner
        cw, ch = int(W * 0.44), int(H * 0.44)
        box(W - cw, H - ch, cw, ch, "black@0.80")
        cp = pad_x
        sm = int(title_size * 0.80)
        title_y = H - ch + 24
        title(f"W-tw-{cp}", title_y, sm, alpha=_fade_in(0.3, 0.8))
        sep(W - cw + cp, title_y + sm + 8, cw - cp * 2)
        bul_y = title_y + sm + 26
        for i, b in enumerate(bullets[:3]):
            esc = _escape_drawtext(f"{fs['bullet_char']}{b}")
            parts.append(
                f"drawtext={font_spec}fontcolor={fs['bullet_color']}:fontsize={int(bul_size * 0.78)}:"
                f"x='W-tw-{cp}':y='{bul_y + i * line_h}':"
                f"text='{esc}':"
                f"shadowcolor={fs['bullet_shadow']}:shadowx=2:shadowy=2:box=0"
                f":alpha='{_fade_in(0.6 + i * 0.20, 0.50)}'"
            )

    elif layout == "title_center":
        # Title horizontally centered, left-aligned bullets below
        box(0, 0, W, H, "black@0.28")
        box(0, 0, W, int(H * 0.44), "black@0.32")
        big = int(title_size * 1.10)
        title_y = int(H * 0.18)
        title("(W-tw)/2", title_y, big,
              alpha=_fade_in(0.3, 0.9), y_expr=_slide_up_y(title_y, 0.3, 0.9, 20))
        sep(pad_x, title_y + big + 14, W - pad_x * 2)
        bul_y = int(H * 0.52)
        for i, b in enumerate(bullets[:4]):
            bullet(b, str(pad_x), bul_y + i * line_h, alpha=_fade_in(0.7 + i * 0.22, 0.55))

    elif layout == "dark_right":
        # Heavy dark right 55%, text in that section
        box(int(W * 0.45), 0, int(W * 0.55), H, "black@0.82")
        rp = int(W * 0.47)
        sm = int(title_size * 0.92)
        title_y = int(H * 0.22)
        title(str(rp), title_y, sm,
              alpha=_fade_in(0.3, 0.8), y_expr=_slide_up_y(title_y, 0.3, 0.8, 16))
        sep(rp, title_y + sm + 10, W - rp - pad_x)
        bul_y = title_y + sm + 30
        for i, b in enumerate(bullets[:4]):
            bullet(b, str(rp), bul_y + i * line_h,
                   size=int(bul_size * 0.88), alpha=_fade_in(0.68 + i * 0.22, 0.55))

    elif layout == "magazine_bot":
        # Large bold title at very bottom, full width
        bot_h = int(H * 0.28)
        box(0, H - bot_h, W, bot_h, "black@0.80")
        sep(0, H - bot_h, W, fs["sep_h"])
        big = int(title_size * 1.15)
        title_y = H - bot_h + 18
        title("(W-tw)/2", title_y, big,
              alpha=_fade_in(0.3, 0.8), y_expr=_slide_up_y(title_y, 0.3, 0.8, 18))
        bul_y = title_y + big + 14
        for i, b in enumerate(bullets[:2]):
            bullet(b, "(W-tw)/2", bul_y + i * (int(bul_size * 0.82) + 8),
                   size=int(bul_size * 0.82), alpha=_fade_in(0.7 + i * 0.25, 0.55))

    elif layout == "reveal":
        # Strong reveal gradient from bottom, text in gradient zone
        box(0, int(H * 0.45), W, int(H * 0.20), "black@0.55")
        box(0, int(H * 0.65), W, int(H * 0.35), "black@0.88")
        title_y = int(H * 0.67)
        title(str(pad_x), title_y, title_size,
              alpha=_fade_in(0.3, 0.8), y_expr=_slide_up_y(title_y, 0.3, 0.8, 18))
        sep(pad_x, title_y + title_size + 10, W - pad_x * 2)
        bul_y = title_y + title_size + 28
        for i, b in enumerate(bullets[:4]):
            bullet(b, str(pad_x), bul_y + i * line_h, alpha=_fade_in(0.65 + i * 0.20, 0.55))

    elif layout == "two_blocks":
        # Title top-left block + bullets bottom-right block (separated)
        box(0, 0, int(W * 0.55), int(H * 0.36), "black@0.78")
        box(int(W * 0.45), int(H * 0.60), int(W * 0.55), int(H * 0.40), "black@0.80")
        title(str(pad_x), pad_y, title_size,
              alpha=_fade_in(0.2, 0.8), y_expr=_slide_up_y(pad_y, 0.2, 0.8, 16))
        sep(pad_x, pad_y + title_size + 10, int(W * 0.48))
        rp = int(W * 0.48)
        bul_y = int(H * 0.63)
        for i, b in enumerate(bullets[:4]):
            bullet(b, str(rp), bul_y + i * line_h, alpha=_fade_in(0.7 + i * 0.22, 0.55))

    elif layout == "narrow_card":
        # Narrow centered card (portrait orientation)
        box(0, 0, W, H, "black@0.28")
        cw = int(W * 0.46)
        cx = (W - cw) // 2
        ch = int(H * 0.70)
        cy = (H - ch) // 2
        box(cx, cy, cw, ch, "black@0.78")
        ix, iy = cx + 28, cy + 30
        sm = int(title_size * 0.88)
        title(str(ix), iy, sm, alpha=_fade_in(0.3, 0.8), y_expr=_slide_up_y(iy, 0.3, 0.8, 12))
        sep(ix, iy + sm + 10, cw - 56)
        bul_y = iy + sm + 28
        for i, b in enumerate(bullets[:5]):
            bullet(b, str(ix), bul_y + i * line_h,
                   size=int(bul_size * 0.84), alpha=_fade_in(0.65 + i * 0.20, 0.50))

    elif layout == "fullscreen_text":
        # Heavy dark overlay, all text centered
        box(0, 0, W, H, "black@0.72")
        big = int(title_size * 1.12)
        title_y = int(H * 0.28)
        sep(pad_x, int(H * 0.24), W - pad_x * 2, 1)
        title("(W-tw)/2", title_y, big,
              alpha=_fade_in(0.3, 0.9), y_expr=_slide_up_y(title_y, 0.3, 0.9, 22))
        sep(pad_x, title_y + big + 18, W - pad_x * 2, 1)
        bul_y = title_y + big + 40
        for i, b in enumerate(bullets[:5]):
            bullet(b, "(W-tw)/2", bul_y + i * (line_h + 4),
                   alpha=_fade_in(0.85 + i * 0.25, 0.60))

    else:
        # Unknown layout → fall back to hero
        return _build_visual_filters(slide, style_cfg, "hero", bold_font, pad_x, pad_y)

    return ",".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Slide video rendering
# ──────────────────────────────────────────────────────────────────────────────

def _render_slide_video(
    slide: Slide,
    image_path: Path,
    out_path: Path,
    slide_idx: int,
) -> None:
    """Render a single slide to MP4 with Ken-Burns animation, layout, style, and text animations."""
    ffmpeg = _ffmpeg_path()
    duration = max(4.0, slide.duration)
    total_frames = int(math.ceil(duration * FPS))

    zoom_expr, x_expr, y_expr = _ken_burns_exprs(slide_idx)

    style_name = getattr(slide, "style", "modern")
    style_cfg = DESIGN_STYLES.get(style_name, DESIGN_STYLES["modern"])
    layout = getattr(slide, "layout", "hero")

    pad_y = int(VIDEO_HEIGHT * SLIDE_PADDING)
    pad_x = int(VIDEO_WIDTH * 0.06)
    bold_font = _find_bold_font()

    visual = _build_visual_filters(slide, style_cfg, layout, bold_font, pad_x, pad_y)

    zoom_w = int(VIDEO_WIDTH * 1.5)
    zoom_h = int(VIDEO_HEIGHT * 1.5)
    filters = (
        f"scale={zoom_w}:{zoom_h}:flags=lanczos,"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={total_frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={FPS},"
        + visual
    )

    _run_ffmpeg([
        ffmpeg, "-y",
        "-threads", "0",
        "-loop", "1",
        "-i", str(image_path),
        "-t", str(duration),
        "-vf", filters,
        "-r", str(FPS),
        "-c:v", "libx264",
        "-crf", "18",
        "-maxrate", VIDEO_BITRATE,
        "-bufsize", "10M",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ])


def _render_fallback_slide(slide: Slide, out_path: Path) -> None:
    """Create a solid-colour slide (no image) as emergency fallback."""
    ffmpeg = _ffmpeg_path()
    duration = max(4.0, slide.duration)
    pad_x = int(VIDEO_WIDTH * 0.06)
    pad_y = int(VIDEO_HEIGHT * 0.12)
    title_esc = _escape_drawtext(slide.title)

    title_filter = (
        f"drawtext=fontcolor=white:fontsize={FONT_TITLE_SIZE}:"
        f"x={pad_x}:y={pad_y}:text='{title_esc}':"
        f"shadowcolor=black:shadowx=2:shadowy=2"
    )
    bullet_filters: List[str] = []
    line_h = FONT_BULLET_SIZE + 14
    y0 = int(VIDEO_HEIGHT * 0.52)
    for bi, b in enumerate(slide.bullets[:5]):
        txt = _escape_drawtext(f"• {b}")
        bullet_filters.append(
            f"drawtext=fontcolor=white:fontsize={FONT_BULLET_SIZE}:"
            f"x={pad_x}:y={y0 + bi * line_h}:text='{txt}':"
            f"shadowcolor=black:shadowx=2:shadowy=2"
        )

    vf = f"color=c=0x0f172a:s={VIDEO_WIDTH}x{VIDEO_HEIGHT},{title_filter}"
    for bf in bullet_filters:
        vf += f",{bf}"

    _run_ffmpeg([
        ffmpeg, "-y",
        "-f", "lavfi",
        "-i", vf,
        "-t", str(duration),
        "-r", str(FPS),
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ])


# ──────────────────────────────────────────────────────────────────────────────
# Concat + master
# ──────────────────────────────────────────────────────────────────────────────

def _concat_videos(video_paths: List[Path], audio_path: Path, output_path: Path) -> None:
    ffmpeg = _ffmpeg_path()
    list_file = output_path.parent / "concat.txt"
    lines = "\n".join(f"file '{p.as_posix()}'" for p in video_paths)
    list_file.write_text(lines, encoding="utf-8")

    temp_video = output_path.parent / "_temp_noaudio.mp4"

    _run_ffmpeg([
        ffmpeg, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c:v", "copy",
        "-movflags", "+faststart",
        str(temp_video),
    ])

    _run_ffmpeg([
        ffmpeg, "-y",
        "-i", str(temp_video),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ])

    temp_video.unlink(missing_ok=True)
    list_file.unlink(missing_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# Main pipeline entry point
# ──────────────────────────────────────────────────────────────────────────────

def run_project(project_id: str, reset: bool = False) -> None:
    provider = get_provider()
    paths = ensure_project_dirs(project_id)

    if reset:
        # Preserve settings.json across resets
        settings_backup = None
        sp = paths["output"] / "settings.json"
        if sp.exists():
            settings_backup = sp.read_text(encoding="utf-8")
        shutil.rmtree(paths["output"], ignore_errors=True)
        shutil.rmtree(paths["assets"], ignore_errors=True)
        paths = ensure_project_dirs(project_id)
        if settings_backup:
            (paths["output"] / "settings.json").write_text(settings_backup, encoding="utf-8")

    # Load per-project style settings saved by the API
    settings_path = paths["output"] / "settings.json"
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    art_style = settings.get("art_style", "photo")
    video_style = settings.get("video_style", "modern")

    # ── Find audio ─────────────────────────────────────────────────────────────
    audio_files = [f for f in paths["audio"].glob("*") if f.is_file()]
    if not audio_files:
        raise PipelineError("No audio file found. Please upload an audio file first.")
    audio_path = audio_files[0]
    audio_duration = _get_audio_duration(audio_path)

    # ── Stage 1: ASR ───────────────────────────────────────────────────────────
    transcript_path = paths["output"] / "transcript.srt"
    if transcript_path.exists():
        write_status(project_id, {"state": "asr", "progress": 10,
                                  "message": "Using cached transcript"})
        transcript = transcript_path.read_text(encoding="utf-8")
    else:
        write_status(project_id, {"state": "asr", "progress": 10,
                                  "message": "Transcribing audio with timestamps…"})
        transcript = provider.transcribe(audio_path)
        transcript_path.write_text(transcript, encoding="utf-8")

    # ── Stage 2: Script ────────────────────────────────────────────────────────
    script_path = paths["output"] / "script.json"
    if script_path.exists():
        write_status(project_id, {"state": "script", "progress": 30,
                                  "message": "Using cached scene plan"})
        script = script_path.read_text(encoding="utf-8")
    else:
        write_status(project_id, {"state": "script", "progress": 30,
                                  "message": "Planning visual scenes with LLM…"})
        script = provider.generate_script(transcript, project_id=project_id)
        script_path.write_text(script, encoding="utf-8")

    # ── Stage 3: Storyboard ────────────────────────────────────────────────────
    storyboard_path = paths["output"] / "storyboard.json"
    if storyboard_path.exists():
        write_status(project_id, {"state": "storyboard", "progress": 50,
                                  "message": "Using cached storyboard"})
        slides = _load_storyboard(storyboard_path)
    else:
        write_status(project_id, {"state": "storyboard", "progress": 50,
                                  "message": "Building AI storyboard…"})
        slides = provider.generate_storyboard(script, project_id=project_id)
        if not slides:
            raise PipelineError("AI produced an empty storyboard.")
        _save_storyboard(storyboard_path, slides)

    # ── Apply project video_style to all slides ────────────────────────────────
    for slide in slides:
        slide.style = video_style

    # ── Adjust durations to match actual audio length ─────────────────────────
    if audio_duration > 0 and slides:
        slides = _sync_durations_to_audio(slides, audio_duration)
        _save_storyboard(storyboard_path, slides)

    # ── Stage 4: Image generation (concurrent, max MAX_IMAGES unique) ──────────
    gen_count = min(len(slides), MAX_IMAGES)
    write_status(project_id, {"state": "images", "progress": 60,
                              "message": f"Generating {gen_count} AI images ({art_style} style)…"})
    _generate_images_concurrent(provider, slides, paths, project_id, art_style=art_style)
    _save_storyboard(storyboard_path, slides)

    # ── Stage 5: Slide video rendering ────────────────────────────────────────
    write_status(project_id, {"state": "render", "progress": 75,
                              "message": "Rendering animated slides…"})
    slide_videos = _render_all_slides(slides, paths, project_id)

    # ── Stage 6: Final mastering ───────────────────────────────────────────────
    output_video = paths["output"] / "final.mp4"
    if not output_video.exists():
        write_status(project_id, {"state": "render", "progress": 95,
                                  "message": "Mastering final composite…"})
        _concat_videos(slide_videos, audio_path, output_video)

    write_status(project_id, {"state": "done", "progress": 100,
                              "message": "Production complete. Master ready."})


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _save_storyboard(path: Path, slides: List[Slide]) -> None:
    data = [
        {
            "title": s.title,
            "bullets": s.bullets,
            "image_prompt": s.image_prompt,
            "duration": s.duration,
            "start_time": s.start_time,
            "image_ext": s.image_ext,
            "layout": getattr(s, "layout", "hero"),
            "style": getattr(s, "style", "modern"),
        }
        for s in slides
    ]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_storyboard(path: Path) -> List[Slide]:
    data = json.loads(path.read_text(encoding="utf-8"))
    slides: List[Slide] = []
    for item in data:
        slides.append(Slide(
            title=str(item.get("title", "Untitled")),
            bullets=list(item.get("bullets") or []),
            image_prompt=str(item.get("image_prompt", "Abstract background")),
            duration=float(item.get("duration") or 8),
            start_time=float(item.get("start_time") or 0),
            image_ext=str(item.get("image_ext") or "png"),
            layout=str(item.get("layout") or "hero"),
            style=str(item.get("style") or "modern"),
        ))
    return slides


def _sync_durations_to_audio(slides: List[Slide], audio_duration: float) -> List[Slide]:
    """Scale slide durations proportionally so they sum to audio_duration."""
    total = sum(s.duration for s in slides)
    if total <= 0:
        equal = audio_duration / len(slides)
        for s in slides:
            s.duration = equal
        return slides

    scale = audio_duration / total
    cumulative = 0.0
    for s in slides:
        s.duration = round(s.duration * scale, 3)
        s.start_time = round(cumulative, 3)
        cumulative += s.duration
    return slides


def _generate_images_concurrent(
    provider,
    slides: List[Slide],
    paths: Dict[str, Path],
    project_id: str,
    art_style: str = "photo",
) -> None:
    """Generate AI images for the first MAX_IMAGES slides; remaining slides reuse earlier images."""
    gen_count = min(len(slides), MAX_IMAGES)
    semaphore = threading.Semaphore(CONCURRENT_IMAGES)
    errors: List[str] = []
    completed = [0]

    def _progress() -> None:
        write_status(
            project_id,
            {
                "state": "images",
                "progress": 60 + int((completed[0] / gen_count) * 15),
                "message": f"Image {completed[0]}/{gen_count} ready",
            },
        )

    def _gen_one(idx: int, slide: Slide) -> None:  # idx is 1-based
        ffmpeg_bin = shutil.which("ffmpeg") or ""
        image_base = paths["assets"] / f"slide_{idx:02d}"

        # Check if already generated
        for ext in ("png", "jpg", "jpeg"):
            if (paths["assets"] / f"slide_{idx:02d}.{ext}").exists():
                slide.image_ext = ext
                completed[0] += 1
                _progress()
                return

        with semaphore:
            try:
                ext = provider.generate_image(slide.image_prompt, image_base.with_suffix(".png"),
                                              art_style=art_style)
                slide.image_ext = ext
            except Exception as exc:
                errors.append(f"Slide {idx}: {exc}")
                if ffmpeg_bin:
                    fallback = image_base.with_suffix(".png")
                    subprocess.run(
                        [
                            ffmpeg_bin, "-y", "-f", "lavfi",
                            "-i", f"color=c=0x0f172a:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}",
                            "-frames:v", "1", str(fallback),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    slide.image_ext = "png"

        completed[0] += 1
        _progress()

    # Only spawn threads for the first gen_count slides
    threads = [
        threading.Thread(target=_gen_one, args=(i, slide), daemon=True)
        for i, slide in enumerate(slides[:gen_count], start=1)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Propagate image_ext to remaining slides (they will reuse cycling images)
    if gen_count < len(slides):
        for i in range(gen_count, len(slides)):
            slides[i].image_ext = slides[i % gen_count].image_ext

    if errors:
        print(f"[WARN] Image gen errors (fallbacks used): {errors}")


def _render_all_slides(
    slides: List[Slide],
    paths: Dict[str, Path],
    project_id: str,
) -> List[Path]:
    gen_count = min(len(slides), MAX_IMAGES)
    total = len(slides)
    completed = [0]
    errors: List[str] = []
    semaphore = threading.Semaphore(CONCURRENT_RENDERS)
    lock = threading.Lock()

    # Pre-build output path list in order
    slide_videos: List[Optional[Path]] = [None] * total

    def _render_one(idx: int, slide: Slide) -> None:  # idx is 1-based
        video_path = paths["output"] / f"slide_{idx:02d}.mp4"

        if video_path.exists():
            with lock:
                slide_videos[idx - 1] = video_path
                completed[0] += 1
            return

        source_idx = idx if idx <= gen_count else ((idx - 1) % gen_count) + 1
        image_path: Optional[Path] = None
        for ext in (slide.image_ext, "png", "jpg", "jpeg"):
            candidate = paths["assets"] / f"slide_{source_idx:02d}.{ext}"
            if candidate.exists():
                image_path = candidate
                break

        with semaphore:
            try:
                if image_path and image_path.exists():
                    _render_slide_video(slide, image_path, video_path, idx)
                else:
                    _render_fallback_slide(slide, video_path)
            except PipelineError as exc:
                errors.append(f"Slide {idx}: {exc}")
                try:
                    _render_fallback_slide(slide, video_path)
                except Exception as e2:
                    errors.append(f"Slide {idx} fallback failed: {e2}")
                    return

        with lock:
            slide_videos[idx - 1] = video_path
            completed[0] += 1
            write_status(
                project_id,
                {
                    "state": "render",
                    "progress": 75 + int((completed[0] / total) * 20),
                    "message": f"Rendered {completed[0]}/{total} slides…",
                },
            )

    threads = [
        threading.Thread(target=_render_one, args=(i, slide), daemon=True)
        for i, slide in enumerate(slides, start=1)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if errors:
        print(f"[WARN] Render errors: {errors}")

    return [p for p in slide_videos if p is not None]
