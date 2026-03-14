"""
ai_providers.py – AI backend layer (OpenAI / compatible endpoints).

Improvements:
  - Retry logic (exponential back-off) on all AI calls
  - Proper SRT timestamp parsing for audio-image sync
  - Robust JSON extraction (handles markdown code-fences from LLMs)
  - JPEG/PNG auto-detection and correct extension handling
  - Slide dataclass extended with start_time for sync
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .config import MAX_RETRIES, PROVIDER


class PipelineError(RuntimeError):
    pass


# ──────────────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────────────

# Layout cycle for visual variety – assigned automatically per slide index (28 total)
SLIDE_LAYOUTS = [
    # Original 8
    "hero", "lower_third", "cinematic", "card",
    "top_banner", "split_right", "quote", "minimal_bottom",
    # 20 new layouts
    "split_left", "vignette_all", "oversized", "ribbon",
    "news_full", "left_panel", "floating_right", "letterbox",
    "chapter", "stat", "broadcast_tag", "corner_tl",
    "corner_br", "title_center", "dark_right", "magazine_bot",
    "reveal", "two_blocks", "narrow_card", "fullscreen_text",
]

# Art styles for image generation – visual modifier appended to each image prompt
ART_STYLES: dict = {
    "photo":        {"name": "Hyper-Real Photo",        "modifier": "hyper-realistic DSLR photography, lifelike textures, natural lighting, shallow depth of field"},
    "cinematic":    {"name": "Epic Cinematic",           "modifier": "epic widescreen cinema frame, dramatic rim lighting, rich colour grading, shallow focus, massive scale"},
    "cyberpunk":    {"name": "Surreal Cyberpunk",        "modifier": "surrealist cyberpunk, vibrant neon blues and magentas, rain-slick streets, holographic atmosphere, cinematic haze"},
    "cartoon":      {"name": "Vibrant Cartoon",          "modifier": "classic 2D cartoon illustration, bold outlines, saturated flat colours, expressive simplified shapes"},
    "pixar":        {"name": "Pixar 3D Render",          "modifier": "polished Pixar-style 3D render, physically-based materials, cinematic lighting rigs, subsurface scattering"},
    "flat":         {"name": "Flat Illustration",        "modifier": "clean flat vector illustration, solid shapes, limited pastel palette, long soft shadows, minimal gradients"},
    "isometric":    {"name": "Isometric Vector",         "modifier": "crisp isometric vector art, 120-degree angles, uniform stroke weight, bright tech-friendly palette"},
    "watercolor":   {"name": "Watercolour Pastel",       "modifier": "soft watercolour painting, delicate brush-stroke textures, loose bleeding edges, gentle pastel palette"},
    "mono":         {"name": "Minimalist Monochrome",    "modifier": "minimalist high-contrast monochrome, strong geometric composition, generous negative space, editorial aesthetic"},
    "synthwave":    {"name": "Retro Synthwave",          "modifier": "1980s synthwave aesthetic, magenta sunset, chrome grid, neon glowing wireframes, VHS nostalgic mood"},
    "noir":         {"name": "Neo-Noir Grit",            "modifier": "moody neo-noir, chiaroscuro lighting, sharp silhouettes, smoke haze, deep blacks, wet reflective streets"},
    "baroque":      {"name": "Baroque Painting",         "modifier": "dramatic baroque oil painting, ornate golden highlights, heavy rich fabrics, theatrical chiaroscuro, impasto texture"},
    "anime":        {"name": "High-Detail Anime",        "modifier": "polished anime art, crisp line art, expressive stylised shading, dynamic poses, rich colour gradients"},
    "manga":        {"name": "B&W Manga",                "modifier": "classic monochrome manga, screentone shading, clean linework, motion lines, stylised Japanese comic"},
    "vintage_pulp": {"name": "Vintage Pulp Cover",       "modifier": "retro pulp magazine cover, bold saturated colours, dramatic poses, distressed print texture"},
    "steampunk":    {"name": "Industrial Steampunk",     "modifier": "richly detailed steampunk, brass machinery, intricate gears, Victorian fashion, warm metallic tones, steam haze"},
    "fantasy_epic": {"name": "High-Fantasy Epic",        "modifier": "grand high-fantasy scene, sweeping landscape, magical light sources, heroic silhouettes, painterly atmosphere"},
    "gothic":       {"name": "Gothic Dark Art",          "modifier": "brooding gothic composition, sharp arches, candlelit shadows, melancholic figures, deep desaturated tones"},
    "lowpoly":      {"name": "Low-Poly Render",          "modifier": "3D low-poly scene, clean faceted geometry, solid flat colours, hard stylised shading, geometric composition"},
    "lego":         {"name": "LEGO Brick Build",         "modifier": "LEGO brick construction, stud-and-tile geometry, minifigure proportions, plastic materials, toy-friendly colours"},
    "ukiyoe":       {"name": "Ukiyo-e Woodblock",        "modifier": "traditional Japanese ukiyo-e woodblock print, flat colours, bold outlines, flowing wave patterns, ink texture"},
    "concept_art":  {"name": "AAA Concept Art",          "modifier": "high-end concept art, dramatic mood lighting, matte-painting precision, layered depth, narrative brushwork"},
    "impressionist":{"name": "Impressionist Painting",   "modifier": "impressionist oil painting, loose visible brushstrokes, soft edges, vibrant broken colours, light over detail"},
    "brutalist":    {"name": "Brutalist Architecture",   "modifier": "stark brutalist scene, raw concrete textures, monolithic geometric forms, sharp dramatic shadows"},
    "futuristic_ui":{"name": "Futuristic HUD",           "modifier": "holographic heads-up display, glowing vector glyphs, clean translucent panels, precise sci-fi iconography"},
    "claymation":   {"name": "Claymation Style",         "modifier": "claymation stop-motion, soft clay-like materials, handmade imperfections, warm practical lighting"},
    "stained_glass":{"name": "Stained Glass",            "modifier": "intricate stained-glass artwork, bold lead-frame outlines, jewel-toned translucent colours, glowing backlight"},
    "charcoal":     {"name": "Charcoal Sketch",          "modifier": "textured charcoal drawing, smudged shadows, rough paper grain, expressive organic linework, bold contrast"},
    "dieselpunk":   {"name": "Dieselpunk Retro-Tech",    "modifier": "dieselpunk aesthetic, heavy riveted machinery, WWI-era industrial fashion, muted metals, engine smoke haze"},
    "kawaii":       {"name": "Cute Kawaii",              "modifier": "ultra-cute kawaii illustration, round simplified shapes, soft pastel gradients, charming characters, gentle mood"},
}


# Unified design themes – one theme per art style, each paired with the most
# harmonious slide design style and a palette_hint to anchor image colours.
# Detailed style properties (colours, fonts, bullets) are now part of each theme.
DESIGN_THEMES: dict = {
    # ── Photography & Realism ──────────────────────────────────────────────
    "photo": {
        "name": "Hyper-Real Photo",
        "description": "Lifelike DSLR photography with natural light and texture",
        "art_style": "photo",
        "design_style": "modern",
        "palette_hint": "natural daylight tones, neutral greys and warm whites, photorealistic palette",
        "preview_colors": ["#1a1a1a", "#38bdf8", "#d4d4d4"],
        "title_color": "white", "accent_color": "#38bdf8", "bullet_color": "#e2e8f0",
        "font_style": "bold_sans", "bullet_char": "-",
    },
    "cinematic_pro": {
        "name": "Cinematic Pro",
        "description": "Dramatic widescreen cinema with deep shadow grading",
        "art_style": "cinematic",
        "design_style": "modern",
        "palette_hint": "dramatic deep blue-silver cinematic colour grade, warm amber highlights, rich shadow contrast",
        "preview_colors": ["#0d1929", "#38bdf8", "#c8922a"],
        "title_color": "white", "accent_color": "#c8922a", "bullet_color": "#d1d5db",
        "font_style": "bold_sans", "bullet_char": "-",
    },
    # ── Sci-Fi & Futurism ─────────────────────────────────────────────────
    "neon_pulse": {
        "name": "Neon Pulse",
        "description": "Cyberpunk city nights with electrifying neon glow",
        "art_style": "cyberpunk",
        "design_style": "neon",
        "palette_hint": "electric neon cyan and magenta on dark rain-wet streets, holographic atmosphere",
        "preview_colors": ["#050510", "#00ffcc", "#ff00cc"],
        "title_color": "#00ffcc", "accent_color": "#00ffcc", "bullet_color": "#ff00cc",
        "font_style": "mono", "bullet_char": "-",
    },
    "futuristic_ui": {
        "name": "Futuristic HUD",
        "description": "Glowing holographic interface with sci-fi precision",
        "art_style": "futuristic_ui",
        "design_style": "neon",
        "palette_hint": "holographic teal and electric blue glyphs, translucent dark panels, sci-fi glow",
        "preview_colors": ["#030d1a", "#00d4ff", "#0066ff"],
        "title_color": "#00d4ff", "accent_color": "#00d4ff", "bullet_color": "#9ca3af",
        "font_style": "mono", "bullet_char": "-",
    },
    "retro_synthwave": {
        "name": "Retro Synthwave",
        "description": "80s nostalgia with chrome grids and neon sunsets",
        "art_style": "synthwave",
        "design_style": "neon",
        "palette_hint": "magenta-pink sunset gradient, chrome reflective grid, electric violet and hot pink",
        "preview_colors": ["#08001a", "#ff2d78", "#b24bff"],
        "title_color": "#ff2d78", "accent_color": "#b24bff", "bullet_color": "#fbcfe8",
        "font_style": "mono", "bullet_char": "*",
    },
    # ── Illustration & Animation ──────────────────────────────────────────
    "cartoon_pop": {
        "name": "Cartoon Pop",
        "description": "Bold outlines and saturated flat colours with energy",
        "art_style": "cartoon",
        "design_style": "kawaii",
        "palette_hint": "vivid primary colours, bold black outlines, flat saturated palette, playful mood",
        "preview_colors": ["#1a0a2e", "#ff6eb4", "#ffe066"],
        "title_color": "#ff6eb4", "accent_color": "#ffe066", "bullet_color": "white",
        "font_style": "rounded", "bullet_char": "-",
    },
    "pixar_3d": {
        "name": "Pixar 3D",
        "description": "Polished 3D render with cinematic studio lighting",
        "art_style": "pixar",
        "design_style": "kawaii",
        "palette_hint": "warm polished 3D render tones, rich saturated highlights, friendly colour palette",
        "preview_colors": ["#0a1a30", "#ff6eb4", "#f5a623"],
        "title_color": "white", "accent_color": "#f5a623", "bullet_color": "#ffb6c1",
        "font_style": "rounded", "bullet_char": "-",
    },
    "corporate_pro": {
        "name": "Corporate Pro",
        "description": "Clean flat design with confident blue accents",
        "art_style": "flat",
        "design_style": "modern",
        "palette_hint": "clean sky-blue and teal flat palette, crisp white space, professional cool tones",
        "preview_colors": ["#061428", "#0ea5e9", "#38bdf8"],
        "title_color": "white", "accent_color": "#0ea5e9", "bullet_color": "#cbd5e1",
        "font_style": "bold_sans", "bullet_char": "-",
    },
    "isometric_tech": {
        "name": "Isometric Tech",
        "description": "Crisp vector geometry with a tech-forward palette",
        "art_style": "isometric",
        "design_style": "modern",
        "palette_hint": "bright tech blues and greens, crisp geometric isometric palette, clean white highlights",
        "preview_colors": ["#080f20", "#38bdf8", "#34d399"],
        "title_color": "white", "accent_color": "#38bdf8", "bullet_color": "#34d399",
        "font_style": "bold_sans", "bullet_char": "-",
    },
    "kawaii_dream": {
        "name": "Kawaii Dream",
        "description": "Ultra-cute pastel world with soft rounded aesthetics",
        "art_style": "kawaii",
        "design_style": "kawaii",
        "palette_hint": "soft pastel pink lavender and cream, warm gentle light, adorable cute mood",
        "preview_colors": ["#1a0830", "#ff6eb4", "#ffcce8"],
        "title_color": "#ff6eb4", "accent_color": "#ffcce8", "bullet_color": "white",
        "font_style": "rounded", "bullet_char": "-",
    },
    "claymation": {
        "name": "Claymation",
        "description": "Handmade clay stop-motion with warm practical light",
        "art_style": "claymation",
        "design_style": "kawaii",
        "palette_hint": "warm clay earth tones, soft pastel highlights, handmade tactile palette",
        "preview_colors": ["#1a0f0a", "#ff6eb4", "#f5c07a"],
        "title_color": "#f5c07a", "accent_color": "#ff6eb4", "bullet_color": "#fef3c7",
        "font_style": "rounded", "bullet_char": "-",
    },
    "lego_build": {
        "name": "LEGO Build",
        "description": "Plastic brick geometry with playful toy-box colours",
        "art_style": "lego",
        "design_style": "kawaii",
        "palette_hint": "bold primary LEGO red yellow blue, glossy plastic surface, toy-bright palette",
        "preview_colors": ["#0a1a2e", "#ff2d2d", "#ffe600"],
        "title_color": "#ffe600", "accent_color": "#ff2d2d", "bullet_color": "white",
        "font_style": "rounded", "bullet_char": "-",
    },
    "lowpoly_geo": {
        "name": "Low-Poly Geo",
        "description": "Faceted 3D geometry with crisp hard-shaded surfaces",
        "art_style": "lowpoly",
        "design_style": "modern",
        "palette_hint": "geometric teal-blue and violet faceted palette, sharp contrast flat shading",
        "preview_colors": ["#0a1520", "#34d399", "#818cf8"],
        "title_color": "white", "accent_color": "#34d399", "bullet_color": "#818cf8",
        "font_style": "bold_sans", "bullet_char": "-",
    },
    # ── Traditional & Fine Art ────────────────────────────────────────────
    "watercolor_soft": {
        "name": "Watercolor Soft",
        "description": "Delicate pastel brushwork with soft bleeding washes",
        "art_style": "watercolor",
        "design_style": "vintage",
        "palette_hint": "soft watercolor washes of warm cream, rose, and sage, gentle bleeding pigment edges",
        "preview_colors": ["#150d0a", "#c9a02a", "#e8c8b0"],
        "title_color": "#c9a02a", "accent_color": "#c9a02a", "bullet_color": "#475569",
        "font_style": "serif", "bullet_char": "-",
    },
    "baroque_gold": {
        "name": "Baroque Gold",
        "description": "Rich oil painting with ornate gold and theatrical shadow",
        "art_style": "baroque",
        "design_style": "vintage",
        "palette_hint": "deep Baroque shadow, rich ochre and burnt sienna, ornate gold highlight, candlelit warmth",
        "preview_colors": ["#1a0a00", "#c9a02a", "#8b4513"],
        "title_color": "#fef3c7", "accent_color": "#c9a02a", "bullet_color": "#d6d3d1",
        "font_style": "serif", "bullet_char": "*",
    },
    "impressionist": {
        "name": "Impressionist",
        "description": "Loose painterly brushwork with vibrant broken colour",
        "art_style": "impressionist",
        "design_style": "vintage",
        "palette_hint": "soft dappled light, impressionist warm gold and lavender, loose vibrant broken-colour palette",
        "preview_colors": ["#15100a", "#c9a02a", "#a78bca"],
        "title_color": "white", "accent_color": "#c9a02a", "bullet_color": "#e2e8f0",
        "font_style": "serif", "bullet_char": "-",
    },
    "ukiyoe": {
        "name": "Ukiyo-e Print",
        "description": "Japanese woodblock with flat colour and flowing outlines",
        "art_style": "ukiyoe",
        "design_style": "vintage",
        "palette_hint": "traditional ukiyo-e indigo blue, vermillion red, and rice-paper cream, flat woodblock palette",
        "preview_colors": ["#0d1520", "#c9a02a", "#c0392b"],
        "title_color": "#f5e6c8", "accent_color": "#c0392b", "bullet_color": "#c9a02a",
        "font_style": "serif", "bullet_char": "-",
    },
    "stained_glass": {
        "name": "Stained Glass",
        "description": "Jewel-toned cathedral light through bold lead frames",
        "art_style": "stained_glass",
        "design_style": "vintage",
        "palette_hint": "jewel-toned ruby, sapphire, and emerald stained glass, glowing backlit palette",
        "preview_colors": ["#0a0a1a", "#c9a02a", "#0d6b3a"],
        "title_color": "white", "accent_color": "#0d6b3a", "bullet_color": "#ecfdf5",
        "font_style": "serif", "bullet_char": "*",
    },
    "charcoal_sketch": {
        "name": "Charcoal Sketch",
        "description": "Expressive hand-drawn linework on rough paper grain",
        "art_style": "charcoal",
        "design_style": "minimal",
        "palette_hint": "dark charcoal black on cream paper grain, smudged mid-grey, monochrome textured palette",
        "preview_colors": ["#0a0a0a", "#666666", "#d4c8b0"],
        "title_color": "#1f2937", "accent_color": "#4b5563", "bullet_color": "#64748b",
        "font_style": "light", "bullet_char": "-",
    },
    # ── Monochrome & Minimal ──────────────────────────────────────────────
    "minimal_clean": {
        "name": "Minimal Clean",
        "description": "Pure editorial monochrome with generous negative space",
        "art_style": "mono",
        "design_style": "minimal",
        "palette_hint": "pure black and white high contrast, clean grey neutral tones, stark editorial minimal",
        "preview_colors": ["#0a0a0a", "#666666", "#f0f0f0"],
        "title_color": "white", "accent_color": "white", "bullet_color": "#94a3b8",
        "font_style": "light", "bullet_char": "-",
    },
    "dark_noir": {
        "name": "Dark Noir",
        "description": "Moody chiaroscuro shadows with rain-slick mystery",
        "art_style": "noir",
        "design_style": "neon",
        "palette_hint": "deep black with single cool moonlight beam, wet cobblestone reflections, dramatic monochrome",
        "preview_colors": ["#050505", "#1a1a2e", "#b0b0b0"],
        "title_color": "white", "accent_color": "#b0b0b0", "bullet_color": "#9ca3af",
        "font_style": "mono", "bullet_char": "-",
    },
    "manga_ink": {
        "name": "Manga Ink",
        "description": "Classic B&W manga with screentone and motion lines",
        "art_style": "manga",
        "design_style": "minimal",
        "palette_hint": "stark black ink on white, screentone grey halftone, clean high-contrast manga palette",
        "preview_colors": ["#0a0a0a", "#555555", "#e8e8e8"],
        "title_color": "#111827", "accent_color": "#1f2937", "bullet_color": "#4b5563",
        "font_style": "light", "bullet_char": "-",
    },
    "brutalist": {
        "name": "Brutalist",
        "description": "Raw concrete monoliths with stark geometric shadow",
        "art_style": "brutalist",
        "design_style": "minimal",
        "palette_hint": "raw concrete grey, harsh geometric shadow, industrial monochrome palette",
        "preview_colors": ["#111111", "#555555", "#cccccc"],
        "title_color": "white", "accent_color": "#cccccc", "bullet_color": "#94a3b8",
        "font_style": "light", "bullet_char": "*",
    },
    # ── Genre & Retro ─────────────────────────────────────────────────────
    "golden_vintage": {
        "name": "Golden Vintage",
        "description": "Retro pulp magazine with bold ink and aged paper",
        "art_style": "vintage_pulp",
        "design_style": "vintage",
        "palette_hint": "warm sepia amber and cream, aged golden patina, bold saturated retro inks",
        "preview_colors": ["#1a0a00", "#c9a02a", "#f5e6c8"],
        "title_color": "#f5e6c8", "accent_color": "#c9a02a", "bullet_color": "#e2bc52",
        "font_style": "serif", "bullet_char": "*",
    },
    "industrial_steam": {
        "name": "Industrial Steam",
        "description": "Brass gears and Victorian machinery in warm metallic haze",
        "art_style": "steampunk",
        "design_style": "vintage",
        "palette_hint": "warm brass copper and aged leather tones, Victorian sepia, steam-haze atmosphere",
        "preview_colors": ["#1a0e00", "#c9a02a", "#7a4a1a"],
        "title_color": "#fef3c7", "accent_color": "#c9a02a", "bullet_color": "#d6d3d1",
        "font_style": "serif", "bullet_char": "-",
    },
    "dieselpunk": {
        "name": "Dieselpunk",
        "description": "WWI-era riveted industry with muted metal and smoke",
        "art_style": "dieselpunk",
        "design_style": "vintage",
        "palette_hint": "muted olive-green and rust-brown steel, WWI-era industrial tones, smoke-grey palette",
        "preview_colors": ["#0f0e0a", "#c9a02a", "#5a5040"],
        "title_color": "#d1d5db", "accent_color": "#c9a02a", "bullet_color": "#9ca3af",
        "font_style": "serif", "bullet_char": "-",
    },
    # ── Dark & Atmospheric ────────────────────────────────────────────────
    "fantasy_epic": {
        "name": "Fantasy Epic",
        "description": "Grand magical vistas with heroic golden light",
        "art_style": "fantasy_epic",
        "design_style": "modern",
        "palette_hint": "deep azure sky, golden magical sunlight rays, rich teal and sapphire atmosphere",
        "preview_colors": ["#0a1a2e", "#4a8fd4", "#d4a820"],
        "title_color": "white", "accent_color": "#d4a820", "bullet_color": "#bfdbfe",
        "font_style": "bold_sans", "bullet_char": "*",
    },
    "gothic_dark": {
        "name": "Gothic Dark",
        "description": "Brooding cathedral shadows with candlelit melancholy",
        "art_style": "gothic",
        "design_style": "neon",
        "palette_hint": "deep desaturated purple and slate, cold candlelit amber, gothic shadow palette",
        "preview_colors": ["#080510", "#00ffcc", "#4a3060"],
        "title_color": "#a78bca", "accent_color": "#00ffcc", "bullet_color": "#d1d5db",
        "font_style": "mono", "bullet_char": "-",
    },
    # ── Japanese Animation ────────────────────────────────────────────────
    "anime_vivid": {
        "name": "Anime Vivid",
        "description": "Polished anime art with crisp linework and rich gradients",
        "art_style": "anime",
        "design_style": "kawaii",
        "palette_hint": "vibrant anime sky-blue and cherry-blossom pink, rich colour gradient, dynamic highlights",
        "preview_colors": ["#0a0820", "#ff6eb4", "#38bdf8"],
        "title_color": "white", "accent_color": "#ff6eb4", "bullet_color": "#38bdf8",
        "font_style": "rounded", "bullet_char": "-",
    },
    # ── Professional Illustration ─────────────────────────────────────────
    "concept_art": {
        "name": "AAA Concept Art",
        "description": "Matte-painting precision with dramatic narrative mood",
        "art_style": "concept_art",
        "design_style": "modern",
        "palette_hint": "dramatic cinematic mood palette, deep teal and gold matte-painting tones, layered atmospheric depth",
        "preview_colors": ["#0a1520", "#38bdf8", "#c8922a"],
        "title_color": "white", "accent_color": "#c8922a", "bullet_color": "#cbd5e1",
        "font_style": "bold_sans", "bullet_char": "*",
    },
}


@dataclass
class Slide:
    title: str
    bullets: List[str]
    image_prompt: str
    duration: float          # seconds, float for precision
    start_time: float = 0.0  # audio offset (for sync)
    image_ext: str = "png"   # "png" or "jpg" – set after image gen
    layout: str = "hero"     # one of SLIDE_LAYOUTS
    style: str = "modern"    # design style: modern|vintage|kawaii|neon|minimal


# ──────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_valid_image(path: Path) -> bool:
    """Check magic bytes: PNG or JPEG."""
    try:
        if path.stat().st_size < 8:
            return False
        header = path.read_bytes()[:8]
        return header[:8] == b"\x89PNG\r\n\x1a\n" or header[:3] == b"\xff\xd8\xff"
    except Exception:
        return False


def _srt_time_to_sec(t: str) -> float:
    """Convert SRT timestamp '00:01:23,456' → seconds."""
    t = t.replace(",", ".")
    parts = t.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(parts[0])


def _extract_json(raw: str) -> str:
    """Strip markdown code fences and fix common LLM JSON errors like unescaped backslashes."""
    raw = raw.strip()
    # Remove ```json ... ``` or ``` ... ```
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
    if m:
        raw = m.group(1).strip()
    
    # Simple heuristic to fix unescaped backslashes in JSON strings.
    # We replace \ with \\ unless it's followed by a valid JSON escape character.
    # Note: this is a best-effort fix for models that fail to escape correctly.
    fixed = re.sub(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})', r'\\\\', raw)
    return fixed


# Patterns that strip text/typography references (Flux-Klein cannot render text)
# and vague conceptual language (Flux-Klein requires physically grounded descriptions).
_TEXT_IN_PROMPT_PATTERNS = [
    # 'neon sign reading "X"', 'sign saying "X"', 'text reading X', etc.
    (re.compile(
        r'\b(?:neon\s+)?(?:text|label|sign|caption|bold text|neon text|chrome text)'
        r'\s+(?:reading|saying|spelling|labeled|that reads?|written)\s+'
        r'(?:["\'])[^\'"]{2,60}(?:["\'])',
        re.IGNORECASE), ''),
    # Any quoted string ≥ 2 words (likely a text label)
    (re.compile(r'["\']([A-Za-z0-9][A-Za-z0-9 &\'\-]{2,50})["\']'), ''),
    # Typography references
    (re.compile(r'\b(?:chrome|bold|neon|retro|80s)\s+typography\b[^,\.]*', re.IGNORECASE), 'geometric surfaces'),
    (re.compile(r'\btypography\b[^,\.]*', re.IGNORECASE), 'geometric forms'),
    (re.compile(r'\bchrome\s+text\b[^,\.]*', re.IGNORECASE), 'chrome geometric surfaces'),
    # Vague conceptual / symbolic language that Flux-Klein cannot ground visually
    (re.compile(r'\b(?:symbolizes?|represents?|evokes?|embodies?|concept of|idea of|metaphor for|stands? for)\b[^,\.]*', re.IGNORECASE), ''),
    # Surreal / impossible physics
    (re.compile(r'\b(?:dreamlike|surreal|ethereal|otherworldly|mystical|magical|fantastical|impossible)\b[^,\.]*', re.IGNORECASE), ''),
]

def _truncate_to_words(text: str, max_words: int = 100) -> str:
    """Hard-cap prompt at max_words words (Flux-Klein limit)."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])

def _sanitize_image_prompt(prompt: str) -> str:
    """Remove text/typography references and vague language for Flux-Klein.
    Flux-Klein is a 4-bit model that requires concrete, physically grounded
    descriptions and cannot render text or abstract concepts reliably.
    Hard-caps output at 100 words.
    """
    for pattern, replacement in _TEXT_IN_PROMPT_PATTERNS:
        prompt = pattern.sub(replacement, prompt)
    # Clean up double spaces / trailing commas left after removal
    prompt = re.sub(r',\s*,', ',', prompt)
    prompt = re.sub(r'\s{2,}', ' ', prompt).strip(' ,')
    # Enforce Flux-Klein 100-word limit
    return _truncate_to_words(prompt, 100)


def _retry(fn, label: str, retries: int = MAX_RETRIES):
    """Call fn(), retrying up to `retries` times with exponential back-off."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except PipelineError:
            raise
        except Exception as exc:
            last_exc = exc
            wait = 2 ** attempt
            print(f"[RETRY] {label} attempt {attempt}/{retries} failed: {exc}. Waiting {wait}s…")
            time.sleep(wait)
    raise PipelineError(f"{label} failed after {retries} retries: {last_exc}")


# ──────────────────────────────────────────────────────────────────────────────
# Base class
# ──────────────────────────────────────────────────────────────────────────────

class AIProvider:
    def transcribe(self, audio_path: Path) -> str:
        raise NotImplementedError

    def generate_script(self, transcript: str, project_id: str | None = None) -> str:
        raise NotImplementedError

    def generate_storyboard(self, script: str, project_id: str | None = None) -> List[Slide]:
        raise NotImplementedError

    def generate_image(self, prompt: str, output_path: Path, art_style: str = "photo", palette_hint: str = "") -> str:
        """Generate image and save. Returns actual extension ('png' or 'jpg')."""
        raise NotImplementedError


# ──────────────────────────────────────────────────────────────────────────────
# OpenAI provider
# ──────────────────────────────────────────────────────────────────────────────

class OpenAIProvider(AIProvider):

    # Visual style palette for Flux-Klein – concrete, photographic, physically grounded.
    # Flux is a 4-bit model that requires precise real-world descriptions; abstract/
    # imaginative concepts produce poor results. Each style specifies lighting, camera
    # angle, materials and palette so GPT can anchor the image_prompt accordingly.
    STYLES = [
        "Cinematic 35mm photography, wide-angle establishing shot, dramatic golden-hour side lighting, long shadows, shallow depth of field, warm amber color grade, photorealistic",
        "Commercial studio photography, three-point lighting, polished chrome and glass surfaces, neutral gradient background, ultra-sharp product detail, controlled specular highlights",
        "Aerial drone overhead shot, midday sun, geometric patterns of urban infrastructure, crisp concrete and steel, muted earth-tone palette, photorealistic",
        "Dark dramatic interior, single hard spotlight from above, deep shadow chiaroscuro, polished concrete floor, rough exposed brick, cool desaturated palette, cinematic",
        "Blue-hour exterior, ambient street and neon lighting, wet reflective asphalt, sharp architectural glass facades, cool 6000K color temperature, photorealistic night scene",
        "Macro close-up photography, ring-flash illumination, extreme material texture detail, shallow depth of field, isolated subject on soft neutral background, ultra-sharp",
        "Soft diffused overcast window light, warm neutral interior workspace, documentary realism, matte surfaces, honest color rendition, sharp foreground detail",
        "Sunrise landscape, low-angle raking light casting long shadows across terrain, saturated warm amber and deep blue sky, wide panoramic composition, photorealistic nature",
    ]

    def __init__(self, api_key: str, base_url: str) -> None:
        from openai import OpenAI
        from .config import OPENAI_MODEL, WHISPER_MODEL, DALLE_MODEL, DALLE_QUALITY
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=180.0)
        self.model = OPENAI_MODEL
        self.whisper_model = WHISPER_MODEL
        self.dalle_model = DALLE_MODEL
        self.dalle_quality = DALLE_QUALITY

    # ── ASR ─────────────────────────────────────────────────────────────────

    def transcribe(self, audio_path: Path) -> str:
        def _call():
            with audio_path.open("rb") as f:
                result = self.client.audio.transcriptions.create(
                    model=self.whisper_model,
                    file=f,
                    response_format="srt",
                )
            return str(result)

        return _retry(_call, "ASR transcription")

    # ── Script (scene planning) ─────────────────────────────────────────────

    @staticmethod
    def _parse_srt_blocks(srt: str) -> List[dict]:
        """Parse SRT into list of dicts: {text, start, end}."""
        blocks = re.split(r"\n\s*\n", srt.strip())
        result = []
        for block in blocks:
            lines = block.strip().splitlines()
            if len(lines) < 3:
                continue
            # line 0 = index, line 1 = timestamps, line 2+ = text
            m = re.match(
                r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})",
                lines[1],
            )
            if not m:
                continue
            start = _srt_time_to_sec(m.group(1))
            end = _srt_time_to_sec(m.group(2))
            text = " ".join(lines[2:])
            result.append({"text": text, "start": start, "end": end})
        return result

    def generate_script(self, transcript: str, project_id: str | None = None) -> str:
        from .storage import write_status

        blocks = self._parse_srt_blocks(transcript)
        if not blocks:
            # Fallback: treat entire transcript as one block
            total_dur = 120.0
            blocks = [{"text": transcript, "start": 0.0, "end": total_dur}]

        # Group into ~30-second chunks preserving start/end times
        CHUNK_SEC = 30.0
        chunks: List[List[dict]] = []
        current: List[dict] = []
        chunk_start = blocks[0]["start"] if blocks else 0.0

        for b in blocks:
            current.append(b)
            if (b["end"] - chunk_start) >= CHUNK_SEC:
                chunks.append(current)
                current = []
                chunk_start = b["end"]
        if current:
            chunks.append(current)

        all_scenes: List[dict] = []

        for i, chunk in enumerate(chunks):
            if project_id:
                write_status(
                    project_id,
                    {
                        "state": "script",
                        "progress": 10 + int((i / len(chunks)) * 20),
                        "message": f"Planning scenes: section {i + 1}/{len(chunks)}…",
                    },
                )

            chunk_start_t = chunk[0]["start"]
            chunk_end_t = chunk[-1]["end"]
            duration = max(5.0, chunk_end_t - chunk_start_t)
            text = " ".join(b["text"] for b in chunk)
            style = self.STYLES[i % len(self.STYLES)]

            prompt = (
                f"You are a video storyboard director.\n"
                f"Segment timing: {chunk_start_t:.1f}s → {chunk_end_t:.1f}s (duration ~{duration:.1f}s).\n"
                f"Visual style reference for image_prompt: {style}\n\n"
                "CRITICAL LANGUAGE RULE: Detect the language of the transcript below and write ALL title and bullets fields "
                "in THAT SAME LANGUAGE. Do NOT translate. Do NOT switch to English. "
                "The image_prompt field must always be in English (for image generation).\n\n"
                "Return a JSON object {\"scenes\": [...]}. Each scene has these STRICTLY SEPARATE fields:\n\n"
                "  - title: THE TOPIC/THEME of this section for the audience (e.g. 'Revenue Growth', 'Key Takeaways'). "
                "Max 3 words. NEVER describe what an image looks like. NEVER use words like 'shows', 'depicts', 'features'.\n\n"
                "  - bullets: 2–4 CONTENT FACTS the audience should learn or remember. "
                "Write as informative talking points (e.g. 'Growing Fast', 'Three Core Principles'). "
                "Max 3 words each. NEVER describe visuals. NEVER say 'shows', 'depicts', 'displays', 'features', 'illustrates'.\n\n"
                "  - image_prompt: [VISUAL ONLY — never shown as on-screen text] A CONCRETE, PHYSICALLY GROUNDED scene for a background image. "
                "Structure: [main subject] + [setting] + [lighting] + [camera angle] + [materials] + [colour palette]. "
                "Use cinematography terms: '35mm lens', 'f/2.8 bokeh', 'golden-hour side light', 'overhead drone', 'rim lighting'. "
                "Specify materials: 'brushed aluminium', 'polished concrete', 'rough oak wood'. "
                "NO text/letters/signs in the image. NO vague abstract words. MAXIMUM 100 words. ALWAYS in English.\n\n"
                f"  - duration: float seconds (all scenes must sum to ~{duration:.1f}s)\n"
                f"  - start_time: float seconds from audio start (first scene: {chunk_start_t:.2f})\n\n"
                "Transcript:\n" + text
            )

            def _call(p=prompt):
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "You are a visual director. Output ONLY valid JSON. Write title and bullets in the same language as the transcript. image_prompt must always be in English."},
                        {"role": "user", "content": p},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.65,
                )
                raw = resp.choices[0].message.content or "{}"
                # Use strict=False to allow control characters (like newlines) in strings
                data = json.loads(_extract_json(raw), strict=False)
                return data.get("scenes") or []

            scenes = _retry(_call, f"Script section {i + 1}")
            all_scenes.extend(scenes if isinstance(scenes, list) else [])

        return json.dumps(all_scenes)

    # ── Storyboard ────────────────────────────────────────────────────────────

    def generate_storyboard(self, script: str, project_id: str | None = None) -> List[Slide]:
        """Convert script JSON into Slide objects."""
        from .storage import write_status
        from .config import VIDEO_STYLE

        if project_id:
            write_status(project_id, {"state": "storyboard", "progress": 50,
                                      "message": "Finalising storyboard…"})
        try:
            # Use strict=False to be more tolerant of control characters
            raw = json.loads(script, strict=False)
        except json.JSONDecodeError as exc:
            raise PipelineError(f"Script JSON parse error: {exc}")

        if isinstance(raw, dict):
            raw = raw.get("scenes", [])
        if not isinstance(raw, list):
            raise PipelineError("Script has unexpected format (not a list).")

        slides: List[Slide] = []
        for i, item in enumerate(raw):
            title = str(item.get("title", "Untitled"))[:80].strip()
            # Sanitize title for ffmpeg drawtext (remove problematic chars)
            title = re.sub(r"[\":<>|?*]", "", title)
            bullets_raw = item.get("bullets") or []
            bullets = []
            for b in (bullets_raw if isinstance(bullets_raw, list) else []):
                clean = re.sub(r"[\":<>|?*]", "", str(b))[:100].strip()
                if clean:
                    bullets.append(clean)

            image_prompt = str(item.get("image_prompt", "Modern office interior, diffused window light, clean desk, muted neutral tones"))[:600]
            image_prompt = _sanitize_image_prompt(image_prompt)
            duration = max(4.0, float(item.get("duration") or 8))
            start_time = max(0.0, float(item.get("start_time") or 0))

            # Assign layout: cycle through SLIDE_LAYOUTS; slides with no bullets → cinematic/quote
            if not bullets:
                layout = "cinematic" if i % 2 == 0 else "quote"
            else:
                layout = SLIDE_LAYOUTS[i % len(SLIDE_LAYOUTS)]

            slides.append(Slide(
                title=title,
                bullets=bullets,
                image_prompt=image_prompt,
                duration=duration,
                start_time=start_time,
                layout=layout,
                style=VIDEO_STYLE,
            ))

        return slides

    # ── Image generation ──────────────────────────────────────────────────────

    def generate_image(self, prompt: str, output_path: Path, art_style: str = "photo", palette_hint: str = "") -> str:
        """Generate and save image using b64_json response format."""
        import base64

        # Sanitize: no text/vague language, enforce 100-word cap
        clean_prompt = _sanitize_image_prompt(prompt)
        style_cfg = ART_STYLES.get(art_style, ART_STYLES["photo"])
        style_modifier = style_cfg["modifier"]
        
        if palette_hint:
            clean_prompt = f"{clean_prompt}. Color palette: {palette_hint}. Style: {style_modifier}."
        else:
            clean_prompt = f"{clean_prompt}. Style: {style_modifier}."
        
        clean_prompt = "No text, no words, no letters, no signs, no labels. " + clean_prompt
        clean_prompt = _truncate_to_words(clean_prompt, 120)

        def _call():
            resp = self.client.images.generate(
                model=self.dalle_model,
                prompt=clean_prompt[:950],
                size="1792x1024",
                quality=self.dalle_quality, # type: ignore
                response_format="b64_json",
                n=1,
            )
            b64_data = resp.data[0].b64_json
            if not b64_data:
                raise PipelineError("Image API returned empty b64_json data")
            return base64.b64decode(b64_data)

        raw = _retry(_call, f"Image generation '{prompt[:40]}…'")

        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            ext = "png"
        elif raw[:3] == b"\xff\xd8\xff":
            ext = "jpg"
        else:
            raise PipelineError(f"Image API returned unexpected format")

        actual_path = output_path.with_suffix(f".{ext}")
        actual_path.write_bytes(raw)
        if actual_path != output_path:
            output_path.write_bytes(raw)
        return ext


# ──────────────────────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────────────────────

def get_provider() -> AIProvider:
    from .config import OPENAI_API_KEY, OPENAI_BASE_URL
    return OpenAIProvider(OPENAI_API_KEY or "no-key", OPENAI_BASE_URL)
