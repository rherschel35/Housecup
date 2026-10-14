"""Landscape wizard card.

Only Discord ids in mirror_art.WIDE_CARD_USER_IDS reach this layout.
Everyone else stays on the portrait card in mirror_art.render.

Left half is full-height hero art (a custom portrait when one is mapped
for that id, otherwise the usual sprite portrait), cover-cropped into a
rounded frame. Right half, top to bottom: name and house crest, signature
title, wand, three stat boxes, worn gear as four tiles, quote, title pills.
Same arguments and the same player data as the portrait card.
"""

from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw

from cogs import mirror_art as art

# Picture units. Drawn at art.S, then scaled back to this size.
WIDE_W, WIDE_H = 1840, 860
BORDER = 20          # striped rim, same foil as the portrait card
PAD = 14             # forest margin inside the rim
COL_GAP = 16         # between the hero and the text column
HERO_RADIUS = 18
OUTER_RADIUS = 30
TITLE_PILLS = 4
# Focal point for cover-crop (0–1). Horizontal crop only when the
# frame is taller than the source; 0.5 keeps the subject centered.
PORTRAIT_FOCUS = (0.5, 0.46)

_SLOT_ORDER = ("necklace", "bracelet", "ring", "talisman")
_SLOT_EMOJI = {
    "necklace": "📿",
    "bracelet": "⛓️",
    "ring": "💍",
    "talisman": "🧿",
}


def wide_portrait_path(user_id: int):
    """Resolved portrait file for this id, or None when there isn't one."""
    name = art.WIDE_CARD_PORTRAITS.get(int(user_id))
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    root = art.WIDE_PORTRAIT_DIR.resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        return None
    return path


def hero_box():
    """Left pane (x, y, w, h) in final picture pixels."""
    hero_h = WIDE_H - 2 * (BORDER + PAD)
    hero_w = int(round(hero_h * 4 / 3))
    return (BORDER + PAD, BORDER + PAD, hero_w, hero_h)


def _cover(im: Image.Image, tw: int, th: int, focus=PORTRAIT_FOCUS) -> Image.Image:
    """Scale so the image covers tw×th, then crop toward `focus`."""
    im = im.convert("RGBA")
    sw, sh = im.size
    if sw < 1 or sh < 1 or tw < 1 or th < 1:
        return Image.new("RGBA", (max(1, tw), max(1, th)), (0, 0, 0, 0))
    scale = max(tw / sw, th / sh)
    nw = max(tw, int(round(sw * scale)))
    nh = max(th, int(round(sh * scale)))
    im = im.resize((nw, nh), Image.LANCZOS)
    ax, ay = focus
    left = int(round((nw - tw) * ax))
    top = int(round((nh - th) * ay))
    left = min(max(0, left), nw - tw)
    top = min(max(0, top), nh - th)
    return im.crop((left, top, left + tw, top + th))


def _wrap(draw: ImageDraw.ImageDraw, text: str, face, max_px: float) -> list[str]:
    """Word-wrap. A single token wider than the line is split by character."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    cur = ""

    def _chunks(token: str) -> list[str]:
        if draw.textlength(token, font=face) <= max_px:
            return [token]
        out: list[str] = []
        buf = ""
        for ch in token:
            trial = buf + ch
            if buf and draw.textlength(trial, font=face) > max_px:
                out.append(buf)
                buf = ch
            else:
                buf = trial
        if buf:
            out.append(buf)
        return out or [token]

    for word in words:
        pieces = _chunks(word)
        for i, piece in enumerate(pieces):
            trial = piece if not cur else f"{cur} {piece}"
            if cur and draw.textlength(trial, font=face) > max_px:
                lines.append(cur)
                cur = piece
            else:
                cur = trial
            if i < len(pieces) - 1 and cur:
                # A broken token continues; don't glue the next piece if it won't fit.
                if draw.textlength(cur, font=face) > max_px:
                    lines.append(cur)
                    cur = ""
    if cur:
        lines.append(cur)
    return lines


def _fit_wrapped(draw, text: str, max_w: float, max_lines: int,
                 start: int = 15, minimum: int = 11):
    """Shrink until `text` wraps into max_lines. Ellipsis only as a last resort.

    Returns (font, lines, picture-unit size).
    """
    text = " ".join((text or "").split())
    if not text:
        face = art.font("Alegreya-Italic.ttf", minimum)
        return face, [], minimum
    max_px = max_w * art.S
    for size in range(start, minimum - 1, -1):
        face = art.font("Alegreya-Italic.ttf", size)
        lines = _wrap(draw, text, face, max_px)
        if len(lines) <= max_lines and all(draw.textlength(ln, font=face) <= max_px + 0.5 for ln in lines):
            return face, lines, size
    face = art.font("Alegreya-Italic.ttf", minimum)
    lines = _wrap(draw, text, face, max_px) or [text]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1].rstrip(" .,;:—-")
        lines[-1] = (last + "…") if last else "…"
    return face, lines, minimum


def _fit_line(draw, text: str, font_name: str, size: int, weight, max_w: float, minimum: int = 12):
    """Single line. Shrink, then ellipsis if it still will not fit."""
    face = art.font(font_name, size, weight)
    max_px = max_w * art.S
    while size > minimum and draw.textlength(text, font=face) > max_px:
        size -= 1
        face = art.font(font_name, size, weight)
    if draw.textlength(text, font=face) <= max_px:
        return face, text
    ell = text
    while ell and draw.textlength(ell + "…", font=face) > max_px:
        ell = ell[:-1].rstrip()
    return face, (ell + "…") if ell else "…"


def _line_px(face) -> int:
    ascent, descent = face.getmetrics()
    return max(1, ascent + descent)


def _tracked(draw, text: str, face, cx: float, cy: float, fill, tracking: float):
    """Center a short label with a little extra space between letters."""
    widths = [draw.textlength(ch, font=face) for ch in text]
    if not widths:
        return
    gap = tracking * art.S
    total = sum(widths) + gap * (len(widths) - 1)
    x = cx - total / 2
    for ch, w in zip(text, widths):
        draw.text((x, cy), ch, font=face, fill=fill, anchor="lm")
        x += w + gap


def _load_hero(user_id: int, look: dict, tw: int, th: int):
    """Cover-cropped hero, and whether the sprite pack drew nothing."""
    path = wide_portrait_path(user_id)
    if path is not None:
        try:
            im = Image.open(path)
            return _cover(im, tw, th), False
        except OSError:
            pass
    portrait = art.compose_portrait(art.clean_look(look, user_id))
    try:
        empty = portrait.getchannel("A").getbbox() is None
    except Exception:
        empty = False
    if empty:
        return Image.new("RGBA", (tw, th), (0, 0, 0, 0)), True
    return _cover(portrait, tw, th), False


def _flow(y0: float, y1: float, parts: list, gap_cap: float = 36) -> dict:
    """Place boxes in [y0, y1]. Flexible gaps share leftover space, up to gap_cap.

    Each part is ("box", key, height) or ("gap", key, height, flex_weight).
    Space past the cap stays below the stack so sections don't drift apart.
    """
    total = sum(p[2] for p in parts)
    flex = [i for i, p in enumerate(parts) if p[0] == "gap" and p[3] > 0]
    extra = (y1 - y0) - total
    if extra > 0 and flex:
        room = [max(0.0, gap_cap - parts[i][2]) for i in flex]
        cap_room = sum(room) or 1
        used = 0.0
        for i, can in zip(flex, room):
            kind, key, h, fl = parts[i]
            add = min(can, extra * (can / cap_room))
            parts[i] = (kind, key, h + add, fl)
    elif extra < 0:
        # Pull the overflow out of the gear tiles, then the stat row.
        for key in ("tiles", "stats"):
            for i, p in enumerate(parts):
                if p[0] == "box" and p[1] == key and extra < 0:
                    shrink = min(p[2] - 96, -extra)
                    if shrink > 0:
                        parts[i] = (p[0], p[1], p[2] - shrink)
                        extra += shrink
    y = y0
    pos = {}
    for p in parts:
        if p[0] == "box":
            pos[p[1]] = (y, p[2])
        y += p[2]
    return pos


def _quote_lines(draw, motto: str, max_w: float):
    motto = " ".join((motto or "").split())
    if not motto:
        return None, []
    quoted = f"“{motto}”"
    max_px = max_w * art.S
    for size in range(24, 16, -1):
        face = art.font("Alegreya-Italic.ttf", size)
        if draw.textlength(quoted, font=face) <= max_px:
            return face, [quoted]
    face = art.font("Alegreya-Italic.ttf", 16)
    lines = _wrap(draw, motto, face, max_px) or [motto]
    lines = lines[:3]
    lines[0] = "“" + lines[0]
    lines[-1] = lines[-1] + "”"
    return face, lines


def _pack_pills(draw, labels: list[str], max_w: float):
    """One row when the badges fit; otherwise wrap. Returns (face, rows, pill_h).

    rows is a list of lists of (label, width_px).
    """
    if not labels:
        return None, [], 0
    max_px = max_w * art.S
    gap = 8 * art.S
    pad_x = 12 * art.S
    pad_y = 6 * art.S

    def widths(face, labs):
        return [draw.textlength(lb, font=face) + 2 * pad_x for lb in labs]

    for size in (16, 15, 14, 13):
        face = art.font("Alegreya-Italic.ttf", size)
        ws = widths(face, labels)
        if all(w <= max_px for w in ws) and sum(ws) + gap * (len(labels) - 1) <= max_px:
            pill_h = _line_px(face) + 2 * pad_y
            return face, [[(lb, w) for lb, w in zip(labels, ws)]], pill_h / art.S

    face = art.font("Alegreya-Italic.ttf", 12)
    rows: list[list[tuple[str, float]]] = []
    cur: list[tuple[str, float]] = []
    cur_w = 0.0
    for lb in labels:
        # Shrink a single badge that is wider than the column.
        shown = lb
        w = draw.textlength(shown, font=face) + 2 * pad_x
        if w > max_px:
            while shown and draw.textlength(shown + "…", font=face) + 2 * pad_x > max_px:
                shown = shown[:-1].rstrip()
            shown = (shown + "…") if shown else "…"
            w = draw.textlength(shown, font=face) + 2 * pad_x
        extra = w if not cur else gap + w
        if cur and cur_w + extra > max_px:
            rows.append(cur)
            cur = [(shown, w)]
            cur_w = w
        else:
            cur.append((shown, w))
            cur_w += extra
    if cur:
        rows.append(cur)
    pill_h = _line_px(face) + 2 * pad_y
    return face, rows, pill_h / art.S


def render_wide(*, look: dict, user_id: int, name: str, title: str | None = None,
                titles: list | None = None,
                house_key: str | None = None, house_color: int | str = 0x6C5CE7,
                house_emoji: str | None = None,
                gear: dict | None = None, gear_icons: list | None = None,
                wand: dict | None = None, beast_emoji: str | None = None,
                stats: list | None = None, motto: str | None = None, stars: int = 1,
                aura: bool = False, gold_trim: bool = False, wand_sparks: bool = False,
                spire_backdrop: bool = False) -> bytes:
    """Landscape trading card as PNG bytes. Same fields as mirror_art.render."""
    # The wide layout has no star row, beast badge, aura, or spire watermark.
    # Gold trim and wand sparks still change the foil and the wand line.
    del gear, beast_emoji, stars, aura, spire_backdrop

    hcol = art.hexint(house_color) if isinstance(house_color, int) else house_color
    hc = art.rgb(hcol)
    # Deep forest, tinted by the house so a later opt-in still reads as their house.
    panel = art.mix((8, 24, 16), hc, 0.24)
    cream = art.light(hc, 0.92)
    cream_soft = art.light(hc, 0.70)
    cream_dim = art.light(hc, 0.55)
    ink = art.dark(hc, 0.86)
    header_fill = art.mix((246, 236, 208), art.light(hc, 0.55), 0.10)
    box_fill = art.mix(panel, (236, 226, 196), 0.12)
    box_empty = art.mix(panel, (4, 8, 6), 0.40)
    box_edge = art.mix((228, 216, 176), hc, 0.30)
    S = art.S

    hx, hy, hw, hh = hero_box()
    rx = hx + hw + COL_GAP
    rw = WIDE_W - (BORDER + PAD) - rx
    ry, rh = hy, hh

    card = Image.new("RGBA", (WIDE_W * S, WIDE_H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)

    # Same diagonal foil as the portrait card, masked to the rounded rim.
    foil = Image.new("RGBA", card.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(foil)
    base = (222, 178, 74) if gold_trim else art.light(hc, 0.25)
    for i in range(-WIDE_H, WIDE_W + WIDE_H, 7):
        t = (i % 160) / 160
        wave = 0.45 + 0.45 * math.sin(t * 2 * math.pi)
        c = art.mix(base, (255, 244, 205), wave)
        fd.line([(i * S, 0), ((i + WIDE_H) * S, WIDE_H * S)], fill=art.rgba(c), width=8 * S)
    rim = Image.new("L", card.size, 0)
    ImageDraw.Draw(rim).rounded_rectangle(
        [0, 0, WIDE_W * S, WIDE_H * S], radius=OUTER_RADIUS * S, fill=255)
    foil.putalpha(rim)
    card.alpha_composite(foil)

    d.rounded_rectangle(
        [BORDER * S, BORDER * S, (WIDE_W - BORDER) * S, (WIDE_H - BORDER) * S],
        radius=22 * S, fill=art.rgba(panel))

    # ----- hero -----
    hero, hero_empty = _load_hero(user_id, look or {}, hw * S, hh * S)
    if not hero_empty:
        mask = Image.new("L", hero.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, hero.size[0], hero.size[1]], radius=HERO_RADIUS * S, fill=255)
        hero.putalpha(mask)
        layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
        layer.paste(hero, (hx * S, hy * S), hero)
        card.alpha_composite(layer)
    else:
        d.rounded_rectangle(
            [hx * S, hy * S, (hx + hw) * S, (hy + hh) * S],
            radius=HERO_RADIUS * S,
            fill=art.rgba(art.mix(panel, hc, 0.35)))
        d.multiline_text(
            ((hx + hw / 2) * S, (hy + hh / 2) * S),
            "Portrait unavailable\n(wizard assets missing)",
            font=art.font("Cinzel.ttf", 22, 700),
            fill=art.rgba((255, 244, 220)),
            anchor="mm", align="center", spacing=8 * S,
        )
    d.rounded_rectangle(
        [hx * S, hy * S, (hx + hw) * S, (hy + hh) * S],
        radius=HERO_RADIUS * S,
        outline=art.rgba(art.dark(hc, 0.25), 210), width=2 * S)

    # ----- measure the right column before placing it -----
    header_h = 90
    crest_size = 58
    crest = art.load_crest(house_key, crest_size)
    crest_pad = (crest_size + 36) if (crest or house_emoji) else 8
    name_text = " ".join((name or "Wizard").split())
    name_face, name_text = _fit_line(
        d, name_text.upper(), "Cinzel.ttf", 34, 700,
        max(40, rw - 28 - crest_pad), minimum=18,
    )

    title_text = " ".join((title or "").split())
    title_face = art.font("Alegreya-Italic.ttf", 22)
    title_h = 0
    if title_text:
        title_face, title_text = _fit_line(
            d, title_text, "Alegreya-Italic.ttf", 26, None, rw - 12, minimum=16)
        title_h = 36

    wand_text = ""
    if wand:
        wood = str(wand.get("wood") or "Unknown")
        core = str(wand.get("core") or "unknown core")
        wand_text = f"WAND — {wood.upper()}, {core.upper()}"
        if wand_sparks:
            wand_text += "  ✦"
    wand_h = 28 if wand_text else 0
    if wand_text:
        _, wand_text = _fit_line(d, wand_text, "Cinzel.ttf", 16, 600, rw - 12, minimum=12)

    stat_rows = list(stats or [])[:3]
    stat_h = 128 if stat_rows else 0

    icons = list(gear_icons or [])
    by_slot = {g.get("slot"): g for g in icons}
    tile_gap = 10
    tile_w = (rw - tile_gap * (len(_SLOT_ORDER) - 1)) / len(_SLOT_ORDER)
    text_w = max(20, tile_w - 14)
    # One font size for the whole row: the smallest any name needs, so they match.
    shared_size = 16
    prelim = []
    for slot in _SLOT_ORDER:
        g = by_slot.get(slot) or {}
        label = (g.get("name") or "Empty").strip() or "Empty"
        _face, _lines, size = _fit_wrapped(d, label, text_w, max_lines=3, start=16, minimum=12)
        prelim.append((slot, g, label))
        shared_size = min(shared_size, size)
    shared_face = art.font("Alegreya-Italic.ttf", shared_size)
    fitted_names = []
    name_lines_n = 1
    for slot, g, label in prelim:
        _face, lines, _size = _fit_wrapped(
            d, label, text_w, max_lines=3, start=shared_size, minimum=shared_size)
        fitted_names.append((slot, g, lines))
        name_lines_n = max(name_lines_n, len(lines))
    worn_label_h = 22
    tile_h = max(168, 64 + name_lines_n * (shared_size + 4) + 18)

    quote_face, quote_lines = _quote_lines(d, motto or "", rw - 16)
    quote_h = 0
    if quote_lines and quote_face is not None:
        quote_h = len(quote_lines) * (_line_px(quote_face) / art.S + 6)

    ranked = [t for t in (titles or []) if t]
    shown = ranked[:TITLE_PILLS]
    extra = len(ranked) - len(shown)
    pill_labels = list(shown)
    if extra > 0:
        pill_labels.append(f"+{extra} more")
    pill_face, pill_rows, pill_h = _pack_pills(d, pill_labels, rw)
    titles_label_h = 20 if pill_labels else 0
    pills_h = 0
    if pill_rows:
        pills_h = len(pill_rows) * pill_h + (len(pill_rows) - 1) * 6

    parts = [("box", "header", header_h)]
    if title_h:
        parts.append(("gap", "after_header", 16, 1.0))
        parts.append(("box", "title", title_h))
    if wand_h:
        parts.append(("gap", "after_title", 4, 0))
        parts.append(("box", "wand", wand_h))
    if stat_h:
        parts.append(("gap", "before_stats", 16, 1.15))
        parts.append(("box", "stats", stat_h))
    parts.append(("gap", "before_worn", 14, 1.0))
    parts.append(("box", "worn_label", worn_label_h))
    parts.append(("gap", "before_tiles", 6, 0))
    parts.append(("box", "tiles", tile_h))
    if quote_h:
        parts.append(("gap", "before_quote", 14, 1.2))
        parts.append(("box", "quote", quote_h))
    if pills_h:
        parts.append(("gap", "before_titles", 12, 1.05))
        parts.append(("box", "titles_label", titles_label_h))
        parts.append(("gap", "before_pills", 6, 0))
        parts.append(("box", "pills", pills_h))
    pos = _flow(ry, ry + rh, parts)

    # ----- header -----
    header_y, header_h = pos["header"]
    d.rounded_rectangle(
        [rx * S, header_y * S, (rx + rw) * S, (header_y + header_h) * S],
        radius=14 * S, fill=art.rgba(header_fill),
        outline=art.rgba(art.mix(ink, header_fill, 0.45)), width=2 * S)
    d.text(
        ((rx + 20) * S, (header_y + header_h / 2) * S),
        name_text, font=name_face, fill=art.rgba(ink), anchor="lm",
    )
    if crest:
        art.paste_center(card, crest, rx + rw - 22 - crest_size / 2, header_y + header_h / 2)
    elif house_emoji:
        em = art.emoji_image(house_emoji, 32)
        if em:
            art.paste_center(card, em, rx + rw - 36, header_y + header_h / 2)

    # ----- signature + wand -----
    if title_h and "title" in pos:
        ty, _ = pos["title"]
        d.text(((rx + rw / 2) * S, (ty + title_h / 2) * S), title_text,
               font=title_face, fill=art.rgba(cream), anchor="mm")
    if wand_h and "wand" in pos:
        wy, _ = pos["wand"]
        d.text(((rx + rw / 2) * S, (wy + wand_h / 2) * S), wand_text,
               font=art.font("Cinzel.ttf", 16, 600), fill=art.rgba(cream_soft), anchor="mm")

    # ----- stats -----
    if stat_h and "stats" in pos:
        sy, sh = pos["stats"]
        n = len(stat_rows)
        gap = 10
        bw = (rw - gap * (n - 1)) / n
        for i, (label, value) in enumerate(stat_rows):
            bx = rx + i * (bw + gap)
            d.rounded_rectangle(
                [bx * S, sy * S, (bx + bw) * S, (sy + sh) * S],
                radius=12 * S, fill=art.rgba(box_fill),
                outline=art.rgba(box_edge), width=2 * S)
            val_face, val_text = _fit_line(
                d, str(value), "Cinzel.ttf", 32, 700, bw - 16, minimum=18)
            d.text(((bx + bw / 2) * S, (sy + sh * 0.40) * S), val_text,
                   font=val_face, fill=art.rgba(cream), anchor="mm")
            lab_face, lab_text = _fit_line(
                d, str(label), "Alegreya-Italic.ttf", 16, None, bw - 16, minimum=12)
            d.text(((bx + bw / 2) * S, (sy + sh * 0.74) * S), lab_text,
                   font=lab_face, fill=art.rgba(cream_soft), anchor="mm")

    # ----- worn -----
    if "worn_label" in pos:
        ly, lh = pos["worn_label"]
        _tracked(d, "WORN", art.font("Cinzel.ttf", 15, 700),
                 (rx + rw / 2) * S, (ly + lh / 2) * S, art.rgba(cream_dim), 2.2)
    if "tiles" in pos:
        ty, th = pos["tiles"]
        for i, (slot, g, lines) in enumerate(fitted_names):
            bx = rx + i * (tile_w + tile_gap)
            filled = bool(g.get("name"))
            d.rounded_rectangle(
                [bx * S, ty * S, (bx + tile_w) * S, (ty + th) * S],
                radius=12 * S,
                fill=art.rgba(box_fill if filled else box_empty),
                outline=art.rgba(box_edge if filled else art.mix(box_edge, panel, 0.45)),
                width=2 * S)
            em = art.emoji_image((g.get("emoji") if filled else None) or _SLOT_EMOJI[slot], 34)
            line_h = shared_size + 4
            block = len(lines) * line_h
            group = 44 + 8 + block
            top = ty + max(8, (th - group) / 2)
            icon_cy = top + 20
            if em:
                art.paste_center(card, em, bx + tile_w / 2, icon_cy)
            text_top = top + 46
            fill = art.rgba(cream if filled else cream_dim)
            for li, line in enumerate(lines):
                d.text(((bx + tile_w / 2) * S, (text_top + li * line_h) * S),
                       line, font=shared_face, fill=fill, anchor="mt")

    # ----- quote -----
    if quote_h and "quote" in pos and quote_face is not None:
        qy, qh = pos["quote"]
        line_h = qh / max(1, len(quote_lines))
        for i, line in enumerate(quote_lines):
            d.text(((rx + rw / 2) * S, (qy + i * line_h + line_h / 2) * S),
                   line, font=quote_face, fill=art.rgba(cream), anchor="mm")

    # ----- titles -----
    if pills_h and "titles_label" in pos:
        ty, th = pos["titles_label"]
        _tracked(d, "TITLES", art.font("Cinzel.ttf", 15, 700),
                 (rx + rw / 2) * S, (ty + th / 2) * S, art.rgba(cream_dim), 2.4)
    if pills_h and "pills" in pos and pill_face is not None:
        py, _ph = pos["pills"]
        row_gap = 6
        for r_i, row in enumerate(pill_rows):
            row_w = sum(w for _, w in row) + (8 * S) * (len(row) - 1)
            x = (rx + rw / 2) * S - row_w / 2
            y = (py + r_i * (pill_h + row_gap)) * S
            hpx = pill_h * S
            for label, w in row:
                d.rounded_rectangle(
                    [x, y, x + w, y + hpx],
                    radius=hpx / 2,
                    fill=art.rgba(art.mix(panel, (236, 226, 196), 0.05)),
                    outline=art.rgba(box_edge), width=2 * S)
                d.text((x + w / 2, y + hpx / 2), label, font=pill_face,
                       fill=art.rgba(cream), anchor="mm")
                x += w + 8 * S

    out = card.resize((WIDE_W, WIDE_H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()
