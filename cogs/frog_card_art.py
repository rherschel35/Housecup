"""Velmora Chocolate Frog card compositor — custom art + foil frame."""

from __future__ import annotations

import colorsys
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

ART_DIR = Path(__file__).resolve().parent.parent / "frog_art_assets"

RARITY_FRAME = {
    "common": (168, 132, 92),
    "uncommon": (72, 168, 128),
    "rare": (88, 132, 210),
    "legendary": (220, 178, 64),
    "mythic": (232, 212, 139),
}
RARITY_LABEL = {
    "common": "COMMON",
    "uncommon": "UNCOMMON",
    "rare": "RARE",
    "legendary": "LEGENDARY",
    "mythic": "MYTHIC",
}

# card_id -> filename under frog_art_assets/
ART_FILES = {
    "lumen_aegis": "spell_lumen_aegis.jpg",
    "velmora_seal": "crest_velmora.jpg",
    "chimera_saro": "beast_chimera_saro.jpg",
    "kraken_lumi": "beast_kraken_lumi.jpg",
    "griffin": "beast_griffin.jpg",
    "sorting_net": "spell_sorting_net.jpg",
    "expecto": "spell_expecto.jpg",
    "crest_oakmont": "crest_oakmont.jpg",
    "crest_asterwilde": "crest_asterwilde.jpg",
    "crest_veyren": "crest_veyren.jpg",
    "crest_caldrin": "crest_caldrin.jpg",
    "crest_thornmere": "crest_thornmere.jpg",
    "dementor": "beast_dementor.png",
    "shadow_wolf": "beast_shadow_wolf.png",
    "incendio": "spell_incendio.jpg",
    "protego": "spell_protego.jpg",
    "expelliarmus": "spell_expelliarmus.jpg",
    "stupefy": "spell_stupefy.jpg",
    "bombarda": "spell_bombarda.jpg",
    "petrificus": "spell_petrificus.jpg",
    "obliviate": "spell_obliviate.jpg",
    "stormcaller": "beast_stormcaller.jpg",
    "ember_hound": "beast_ember_hound.png",
    "volt_serpent": "beast_volt_serpent.png",
    "niffler": "beast_niffler.jpg",
    "lanternback_stag": "beast_lanternback_stag.png",
    "moonglass_owl": "beast_moonglass_owl.png",
    "arresto": "spell_arresto.jpg",
    "glacius": "spell_glacius.jpg",
    "lumos": "spell_lumos.jpg",
    "accio": "spell_accio.jpg",
    "reparo": "spell_reparo.jpg",
    "wingardium": "spell_wingardium.jpg",
    "alohomora": "spell_alohomora.jpg",
    "colloportus": "spell_colloportus.jpg",
    "finite": "spell_finite.jpg",
    "riddikulus": "spell_riddikulus.jpg",
    "impedimenta": "spell_impedimenta.jpg",
    "silencio": "spell_silencio.jpg",
    "episkey": "spell_episkey.jpg",
    "descendo": "spell_descendo.jpg",
    "ventus": "spell_ventus.jpg",
    "hopsalot": "beast_hopsalot.jpg",
    "wild_threat": "beast_wild_threat.jpg",
    "field_cow": "beast_field_cow.png",
    "cistern_toad": "beast_cistern_toad.png",
    "button_magpie": "beast_button_magpie.png",
    "blackcap_squirrel": "beast_blackcap_squirrel.png",
    "duskpup": "beast_duskpup.png",
    "orbitcat": "beast_orbitcat.png",
}


def _font(size: int, *, bold: bool = True) -> ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates += [
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    candidates += [
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for name in candidates:
        if Path(name).exists():
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                pass
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _vignette(size: tuple[int, int], strength: float = 0.55) -> Image.Image:
    w, h = size
    # Radial darkening from edges — build a soft white center mask.
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    max_inset = min(w, h) // 2 - 2
    steps = 24
    for i in range(steps):
        t = i / max(steps - 1, 1)
        inset = int(max_inset * t)
        shade = int(255 * (1.0 - strength * t * t))
        d.ellipse((inset, inset, w - inset - 1, h - inset - 1), fill=shade)
    mask = mask.filter(ImageFilter.GaussianBlur(22))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    black = Image.new("RGBA", (w, h), (0, 0, 0, 160))
    # Where mask is bright (center), keep art; dark edges get black.
    return Image.composite(overlay, black, mask)


def _foil_noise(size: tuple[int, int], color: tuple[int, int, int], seed: int) -> Image.Image:
    """Subtle sparkle layer for rare+ cards."""
    import random

    rng = random.Random(seed)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = size
    n = 28 if seed % 3 else 40
    for _ in range(n):
        x, y = rng.randint(20, w - 20), rng.randint(20, h - 20)
        s = rng.randint(1, 3)
        a = rng.randint(40, 120)
        d.ellipse((x, y, x + s, y + s), fill=(*color, a))
        if rng.random() < 0.35:
            d.line((x - 3, y + s // 2, x + s + 3, y + s // 2), fill=(*color, a), width=1)
            d.line((x + s // 2, y - 3, x + s // 2, y + s + 3), fill=(*color, a), width=1)
    return layer


def render_card_png(card_id: str, meta: dict, *, index: int, total: int) -> bytes:
    """Compose a show-off Chocolate Frog card for Discord."""
    rarity = meta.get("rarity", "common")
    frame = RARITY_FRAME.get(rarity, (180, 150, 100))
    w, h = 768, 1080

    # Chocolate base
    base = Image.new("RGB", (w, h), (42, 28, 20))
    draw = ImageDraw.Draw(base)
    for y in range(h):
        t = y / h
        c = _lerp((56, 36, 24), (28, 18, 14), t)
        draw.line([(0, y), (w, y)], fill=c)

    # Outer cream rim + foil frame
    draw.rounded_rectangle((10, 10, w - 11, h - 11), radius=34, fill=(245, 236, 214))
    draw.rounded_rectangle((26, 26, w - 27, h - 27), radius=28, fill=frame)
    # Inner dark plate
    draw.rounded_rectangle((46, 46, w - 47, h - 47), radius=22, fill=(32, 22, 16))

    # Corner gems
    for cx, cy in ((70, 70), (w - 70, 70), (70, h - 70), (w - 70, h - 70)):
        draw.regular_polygon((cx, cy, 14), n_sides=4, fill=_lerp(frame, (255, 255, 240), 0.35))
        draw.regular_polygon((cx, cy, 7), n_sides=4, fill=(32, 22, 16))

    title_font = _font(40)
    label_font = _font(20)
    body_font = _font(23, bold=False)
    tiny_font = _font(16, bold=False)
    rarity_font = _font(22)

    # Header
    draw.rounded_rectangle((62, 58, w - 63, 118), radius=12, fill=(48, 32, 22))
    kind = (meta.get("kind") or "spell").upper()
    header = f"VELMORA  ·  {kind} CARD"
    tw = draw.textlength(header, font=label_font)
    draw.text(((w - tw) / 2, 78), header, font=label_font, fill=(236, 198, 118))

    # Art window
    art_box = (62, 136, w - 63, 700)
    aw, ah = art_box[2] - art_box[0], art_box[3] - art_box[1]
    draw.rounded_rectangle(art_box, radius=16, fill=(20, 14, 10))

    art_name = ART_FILES.get(card_id)
    art_path = ART_DIR / art_name if art_name else None
    if art_path and art_path.exists():
        portrait = Image.open(art_path).convert("RGB")
        # Slight contrast polish
        portrait = ImageEnhance.Contrast(portrait).enhance(1.08)
        portrait = ImageEnhance.Color(portrait).enhance(1.05)
        fitted = ImageOps.fit(portrait, (aw - 8, ah - 8), method=Image.Resampling.LANCZOS)
        # Round-ish paste via mask
        mask = Image.new("L", fitted.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, fitted.size[0] - 1, fitted.size[1] - 1), radius=12, fill=255
        )
        base.paste(fitted, (art_box[0] + 4, art_box[1] + 4), mask)
        # Vignette over art
        art_region = base.crop(
            (art_box[0] + 4, art_box[1] + 4, art_box[0] + 4 + fitted.size[0], art_box[1] + 4 + fitted.size[1])
        ).convert("RGBA")
        art_region = Image.alpha_composite(art_region, _vignette(fitted.size, 0.45))
        if rarity in ("rare", "legendary", "mythic"):
            art_region = Image.alpha_composite(
                art_region, _foil_noise(fitted.size, frame, seed=hash(card_id) & 0xFFFF)
            )
        base.paste(art_region.convert("RGB"), (art_box[0] + 4, art_box[1] + 4))
    else:
        draw.rectangle(
            (art_box[0] + 4, art_box[1] + 4, art_box[2] - 4, art_box[3] - 4),
            fill=(60, 40, 28),
        )

    # Thin foil border around art
    draw.rounded_rectangle(art_box, radius=16, outline=frame, width=3)

    # Name plate
    plate_top = 716
    draw.rounded_rectangle((62, plate_top, w - 63, plate_top + 78), radius=14, fill=(54, 36, 24))
    name = meta.get("name", card_id)
    # Shrink font if name is long
    nf = title_font
    while draw.textlength(name, font=nf) > w - 160 and nf.size > 22:
        nf = _font(nf.size - 2)
    nw = draw.textlength(name, font=nf)
    draw.text(((w - nw) / 2, plate_top + 18), name, font=nf, fill=(252, 238, 206))

    # Rarity ribbon
    rlab = RARITY_LABEL.get(rarity, rarity.upper())
    rw = draw.textlength(rlab, font=rarity_font)
    ribbon_w = int(rw + 48)
    rx0 = (w - ribbon_w) // 2
    ry0 = plate_top + 88
    # Slightly brighter ribbon fill
    hls = colorsys.rgb_to_hls(*(c / 255 for c in frame))
    bright = tuple(int(x * 255) for x in colorsys.hls_to_rgb(hls[0], min(0.55, hls[1] + 0.08), hls[2]))
    draw.rounded_rectangle((rx0, ry0, rx0 + ribbon_w, ry0 + 36), radius=10, fill=bright)
    draw.text((rx0 + 24, ry0 + 6), rlab, font=rarity_font, fill=(28, 18, 12))

    # Blurb on parchment
    parch_top = ry0 + 52
    draw.rounded_rectangle((62, parch_top, w - 63, h - 88), radius=14, fill=(58, 42, 30))
    blurb = meta.get("blurb", "")
    by = parch_top + 22
    for line in _wrap(draw, blurb, body_font, w - 150)[:5]:
        lw = draw.textlength(line, font=body_font)
        draw.text(((w - lw) / 2, by), line, font=body_font, fill=(230, 210, 180))
        by += 28

    # Footer
    foot = f"#{index:02d}/{total}  ·  Velmora Collectible"
    fw = draw.textlength(foot, font=tiny_font)
    draw.text(((w - fw) / 2, h - 72), foot, font=tiny_font, fill=(180, 150, 110))

    buf = io.BytesIO()
    base.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
