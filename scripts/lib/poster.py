"""Lay out the bird collage and render it to a poster image.

No family grouping, no grid: every cutout is trimmed to the bird's own
silhouette (no leftover transparent padding), given a shared base size with
light per-bird jitter (so it reads as scattered rather than identical
tiles), and all species are packed together directly with a single
variable-height shelf algorithm (tallest first, best-fit into whichever
open row wastes the least space) for maximum density.

An auto-fit pass still tries a few canvas widths/scales so the result lands
in a printable, wall-sized range.
"""
import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "fonts"
DPI = 150

CREAM = (244, 239, 227)
INK = (43, 36, 32)
MUTED = (107, 94, 78)
RULE = (184, 164, 126)

MARGIN_X = 100
MARGIN_TOP = 160
MARGIN_BOTTOM = 320  # room for the badge + credits line
HEADER_BLOCK_H = 280

BASE_BIRD_H = 300
JITTER_LOW = 0.74
JITTER_HIGH = 1.34

BASE_GAP = 6
BASE_LABEL_GAP = 5
IMAGE_MAX_DIM = 520

WIDTHS_IN = [24, 30, 36, 40, 44]
TARGET_MAX_HEIGHT_IN = 48
COMFORTABLE_MIN_SCALE = 0.7
ABSOLUTE_MIN_SCALE = 0.5
SCALE_STEP = 0.05


def _font(name, size, variation=None):
    size = max(int(size), 8)
    f = ImageFont.truetype(str(FONT_DIR / name), size)
    if variation:
        try:
            f.set_variation_by_name(variation)
        except Exception:
            pass
    return f


class Fonts:
    def __init__(self, scale):
        self.title = _font("CormorantGaramond.ttf", 150, b"SemiBold")
        self.subtitle = _font("EBGaramond-Italic.ttf", 52)
        self.label = _font("EBGaramond-Italic.ttf", 26 * max(scale, 0.8))
        self.footer = _font("EBGaramond.ttf", 28)
        self.badge_title = _font("CormorantGaramond.ttf", 46, b"SemiBold")
        self.badge_meta = _font("EBGaramond-Italic.ttf", 34)
        self.badge_date = _font("EBGaramond.ttf", 28)


class Sizes:
    def __init__(self, canvas_w, scale):
        self.canvas_w = canvas_w
        self.scale = scale
        self.gap = BASE_GAP * scale
        self.label_gap = BASE_LABEL_GAP * scale
        self.bird_h = BASE_BIRD_H * scale
        self.content_w = canvas_w - 2 * MARGIN_X


def tracked_width(font, text, tracking=0):
    if not text:
        return 0
    return sum(font.getlength(ch) for ch in text) + tracking * (len(text) - 1)


def draw_tracked_text(draw, xy, text, font, fill, tracking=0):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking
    return x - xy[0]


def _fit_label(name, max_w, font):
    """Every label uses the same font size for a consistent typographic
    rhythm across the poster. A name that doesn't fit its bird's width
    wraps onto two lines at the best space; only if that still doesn't
    fit does it truncate with an ellipsis. Returns (lines, font, line_h)."""
    line_h = font.size * 1.22
    if font.getlength(name) <= max_w:
        return [name], font, line_h

    words = name.split(" ")
    if len(words) > 1:
        best = None
        for i in range(1, len(words)):
            line1, line2 = " ".join(words[:i]), " ".join(words[i:])
            m = max(font.getlength(line1), font.getlength(line2))
            if best is None or m < best[0]:
                best = (m, line1, line2)
        if best and best[0] <= max_w:
            return [best[1], best[2]], font, line_h

    trunc = name
    while font.getlength(trunc + "…") > max_w and len(trunc) > 4:
        trunc = trunc[:-2]
    return [trunc + "…"], font, line_h


def _load_trimmed(path):
    img = Image.open(path).convert("RGBA")
    bbox = img.split()[-1].getbbox()
    if bbox:
        img = img.crop(bbox)
    if max(img.size) > IMAGE_MAX_DIM:
        img = img.copy()
        img.thumbnail((IMAGE_MAX_DIM, IMAGE_MAX_DIM), Image.LANCZOS)
    return img


def _build_image_cache(resolved, log):
    cache = {}
    for code, path in resolved.items():
        if path is None:
            continue
        try:
            cache[code] = _load_trimmed(path)
        except Exception as e:
            log(f"  ! could not load image for {code}: {e}")
    return cache


def _jitter(code, low=JITTER_LOW, high=JITTER_HIGH):
    h = hash(code) % 1000
    return low + (high - low) * (h / 1000)


def _pack_variable(items, label_extra, max_width, gap, label_gap):
    """items: [(key, w, h), ...]. label_extra: {key: px reserved for its label}.
    Bottom-aligned shelf packing, tallest first, best-fit into whichever
    open row wastes the least space, sized so each row leaves room for the
    tallest label-stack in it (not just the tallest image).
    Returns {key: (x, shelf_y, w, h, shelf_h)}, used_width, total_height."""
    order_idx = sorted(range(len(items)), key=lambda i: -items[i][2])
    shelves = []  # each: {height, x, members: [key,...]}
    membership = {}
    for i in order_idx:
        key, w, h = items[i]
        best = None
        for shelf in shelves:
            remaining = max_width - shelf["x"]
            need = w + (gap if shelf["x"] > 0 else 0)
            if remaining >= need and (best is None or shelf["height"] < best["height"]):
                best = shelf
        if best is None:
            best = {"height": h, "x": 0.0, "members": []}
            shelves.append(best)
        x = best["x"] + (gap if best["x"] > 0 else 0)
        membership[key] = (best, x, w, h)
        best["x"] = x + w
        best["members"].append(key)

    placements = {}
    y_cursor = 0
    for shelf in shelves:
        shelf_extra = max((label_extra[k] for k in shelf["members"]), default=0)
        for key in shelf["members"]:
            _, x, w, h = membership[key]
            placements[key] = (x, y_cursor, w, h, shelf["height"])
        y_cursor += shelf["height"] + label_gap + shelf_extra + gap
    total_h = (y_cursor - gap) if shelves else 0
    used_w = max((x + w for x, _, w, _, _ in placements.values()), default=0)
    return placements, used_w, total_h


def _layout(species_rows, image_cache, sizes, fonts, size_multipliers):
    items = []
    label_extra = {}
    labels = {}
    for sp in species_rows:
        code = sp["species_code"]
        img = image_cache[code]
        coolness_scale = size_multipliers.get(code, 1.0) if size_multipliers else 1.0
        h = sizes.bird_h * coolness_scale * _jitter(code)
        w = h * (img.width / img.height)
        items.append((code, w, h))
        lines, font, line_h = _fit_label(sp["common_name"], w + 8, fonts.label)
        labels[code] = (lines, font, line_h)
        label_extra[code] = len(lines) * line_h

    placements, _, content_h = _pack_variable(items, label_extra, sizes.content_w, sizes.gap, sizes.label_gap)
    start_y = MARGIN_TOP + HEADER_BLOCK_H
    total_h = start_y + content_h + MARGIN_BOTTOM
    return placements, labels, start_y, total_h


def _autofit(species_rows, image_cache, size_multipliers):
    def try_config(width_in, scale):
        canvas_w = width_in * DPI
        sizes = Sizes(canvas_w, scale)
        fonts = Fonts(scale)
        placements, labels, start_y, total_h = _layout(
            species_rows, image_cache, sizes, fonts, size_multipliers
        )
        return sizes, fonts, placements, labels, start_y, total_h

    def best_for_width(width_in, min_scale):
        scale = 1.0
        last = None
        while scale >= min_scale - 1e-9:
            result = try_config(width_in, scale)
            last = result
            if result[-1] <= TARGET_MAX_HEIGHT_IN * DPI:
                return result, True
            scale = round(scale - SCALE_STEP, 2)
        return last, False

    for width_in in WIDTHS_IN:
        result, fit = best_for_width(width_in, COMFORTABLE_MIN_SCALE)
        if fit:
            return result
    result, _ = best_for_width(WIDTHS_IN[-1], ABSOLUTE_MIN_SCALE)
    return result


def _draw_badge(draw, x, bottom_y, title, n_species, asof_label, fonts):
    pad = 26
    line_gap = 10
    title_text = title.upper()
    meta_text = f"{n_species} species"
    date_text = f"as of {asof_label}".upper()

    title_w = tracked_width(fonts.badge_title, title_text, tracking=3)
    meta_w = draw.textlength(meta_text, font=fonts.badge_meta)
    date_w = tracked_width(fonts.badge_date, date_text, tracking=2)
    box_w = max(title_w, meta_w, date_w) + pad * 2

    title_h, meta_h, date_h = 52, 40, 34
    box_h = pad * 2 + title_h + line_gap + meta_h + line_gap + date_h
    top = bottom_y - box_h

    draw.rectangle([x, top, x + box_w, top + box_h], outline=RULE, width=2)
    ty = top + pad
    draw_tracked_text(draw, (x + pad, ty), title_text, fonts.badge_title, INK, tracking=3)
    ty += title_h + line_gap
    draw.text((x + pad, ty), meta_text, font=fonts.badge_meta, fill=MUTED)
    ty += meta_h + line_gap
    draw_tracked_text(draw, (x + pad, ty), date_text, fonts.badge_date, MUTED, tracking=2)
    return box_w, box_h


def build_poster(life_df, resolved_cutouts, title, subtitle, out_path, asof=None, log=print,
                  size_multipliers=None):
    """size_multipliers: optional {species_code: scale_factor} - a neutral
    (missing or 1.0) entry renders exactly as today's baseline sizing;
    computing the actual coolness score from a baseline+override is the
    caller's job (see app/coolness.py for the web app), not this module's -
    poster.py only knows how to apply a size, not why."""
    asof = asof or datetime.date.today()
    if hasattr(asof, "strftime"):
        asof_label = f"{asof.strftime('%B')} {asof.day}, {asof.year}"
    else:
        asof_label = str(asof)

    image_cache = _build_image_cache(resolved_cutouts, log)
    species_rows = [
        row for _, row in life_df.iterrows()
        if row["species_code"] in image_cache
    ]

    sizes, fonts, placements, labels, start_y, canvas_h = _autofit(
        species_rows, image_cache, size_multipliers or {}
    )
    canvas_h = int(canvas_h)

    img = Image.new("RGB", (sizes.canvas_w, canvas_h), CREAM)
    draw = ImageDraw.Draw(img)
    cx = sizes.canvas_w // 2

    y = MARGIN_TOP
    title_w = tracked_width(fonts.title, title.upper(), tracking=10)
    draw_tracked_text(draw, (cx - title_w / 2, y), title.upper(), fonts.title, INK, tracking=10)
    y += 165
    sub_w = draw.textlength(subtitle, font=fonts.subtitle)
    draw.text((cx - sub_w / 2, y), subtitle, font=fonts.subtitle, fill=MUTED)
    y += 80
    draw.line([(cx - 260, y), (cx + 260, y)], fill=RULE, width=3)

    n_species = 0
    for code, (x, shelf_y, w, h, shelf_h) in placements.items():
        n_species += 1
        draw_x = MARGIN_X + x
        draw_y = start_y + shelf_y + (shelf_h - h)
        try:
            bird = image_cache[code].resize((max(1, round(w)), max(1, round(h))), Image.LANCZOS)
            img.paste(bird, (round(draw_x), round(draw_y)), bird)
        except Exception as e:
            log(f"  ! could not place image for {code}: {e}")

        lines, label_font, line_h = labels[code]
        baseline_y = start_y + shelf_y + shelf_h + sizes.label_gap
        for li, line in enumerate(lines):
            line_w = label_font.getlength(line)
            draw.text((draw_x + (w - line_w) / 2, baseline_y + li * line_h),
                      line, font=label_font, fill=INK)

    badge_bottom = canvas_h - 70
    badge_w, badge_h = _draw_badge(draw, MARGIN_X, badge_bottom, title, n_species, asof_label, fonts)

    footer_text = (
        "Bird photographs courtesy of iNaturalist contributors, used under Creative Commons "
        "licenses — full credits in photo_credits.txt"
    )
    ft_w = draw.textlength(footer_text, font=fonts.footer)
    draw.text((sizes.canvas_w - MARGIN_X - ft_w, badge_bottom - 34), footer_text,
              font=fonts.footer, fill=MUTED)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, dpi=(DPI, DPI))
    w_in, h_in = sizes.canvas_w / DPI, canvas_h / DPI
    log(f"Saved poster: {out_path}  ({sizes.canvas_w}x{canvas_h}px, {w_in:.1f}x{h_in:.1f}in @{DPI}dpi)")
    if h_in > TARGET_MAX_HEIGHT_IN:
        log(f"  Note: at {n_species} species this poster came out taller than a typical "
            f"{TARGET_MAX_HEIGHT_IN}in print. Consider a large-format/banner print, or ask to "
            "split it into two panels.")
    return out_path
