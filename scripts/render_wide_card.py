#!/usr/bin/env python3
"""Render a sample landscape wizard card and check it stays opt-in.

The sample uses the mockup's copy (Headmaster Gon Vale, Thornmere) so the
PNG can be compared with the layout reference. It is not live player data.

    python scripts/render_wide_card.py [out.png]

With no path, the sample is written to /tmp/wide-wizard-card.png.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cogs.mirror_art import (  # noqa: E402
    CH,
    CW,
    WIDE_CARD_PORTRAITS,
    WIDE_CARD_USER_IDS,
    WIDE_PORTRAIT_DIR,
    render,
)
from cogs.mirror_wide import (  # noqa: E402
    WIDE_H,
    WIDE_W,
    _fit_wrapped,
    _wrap,
    hero_box,
    render_wide,
    wide_portrait_path,
)

GON = 555141900802457630


def _sample_scene(user_id: int) -> dict:
    titles = [
        "Champion of the Circle",
        "Legend-Tamer",
        "Human Fortress",
        "Certified Hex Menace",
    ]
    titles += [f"Kept Title {i}" for i in range(5, 33)]
    return dict(
        look={},
        user_id=user_id,
        name="Headmaster Gon Vale",
        title="Terror of the Dueling Floor",
        titles=titles,
        house_key="thornmere",
        house_color=0x3E8E5A,
        house_emoji="🐺",
        gear_icons=[
            {"slot": "necklace", "emoji": "📿", "name": "Spireheart Necklace"},
            {"slot": "bracelet", "emoji": "⛓️", "name": "Blackened Acorn Bracelet"},
            {"slot": "ring", "emoji": "💍", "name": "Seeker's Ring"},
            {"slot": "talisman", "emoji": "🧿", "name": "Owl-Feather Charm"},
        ],
        wand={"wood": "Pine", "core": "Starglass Shard"},
        stats=[("Duel wins", 457), ("Beasts", 31), ("Gear", "31/45")],
        motto="Loyalty is earned, Advantage is taken.",
        stars=4,
        gold_trim=False,
        wand_sparks=False,
    )


def _open(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGBA")


def _stddev(im: Image.Image) -> float:
    small = im.resize((24, 24), Image.BOX)
    pix = small.load()
    vals = [pix[x, y] for y in range(small.height) for x in range(small.width)]
    n = len(vals)
    means = [sum(p[c] for p in vals) / n for c in range(3)]
    var = sum(sum((p[c] - means[c]) ** 2 for c in range(3)) for p in vals) / n
    return var ** 0.5


def _check_wrap() -> None:
    scratch = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(scratch)
    face, lines, size = _fit_wrapped(draw, "Blackened Acorn Bracelet", 100, 3)
    assert lines and "…" not in " ".join(lines), lines
    assert len(lines) >= 2, lines
    max_px = 100 * 2  # mirror_art.S
    for line in lines:
        assert draw.textlength(line, font=face) <= max_px + 1, line
    long_face, long_lines, _ = _fit_wrapped(
        draw, "Supercalifragilisticexpialidocious Charm", 90, 3, start=14, minimum=11)
    for line in long_lines:
        assert draw.textlength(line, font=long_face) <= 90 * 2 + 1, (line, size)
    wrapped = _wrap(draw, "Seeker's Ring", face, 400)
    assert wrapped == ["Seeker's Ring"]


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/wide-wizard-card.png")
    assert GON in WIDE_CARD_USER_IDS
    assert isinstance(WIDE_CARD_USER_IDS, frozenset)
    assert WIDE_CARD_PORTRAITS[GON] == "gon-thornmere.jpg"
    portrait = wide_portrait_path(GON)
    assert portrait is not None and portrait.is_file(), portrait
    assert portrait.parent == WIDE_PORTRAIT_DIR.resolve()
    _check_wrap()

    sample = _sample_scene(GON)
    wide_png = render(**sample)
    wide = _open(wide_png)
    assert wide.size == (WIDE_W, WIDE_H), wide.size
    assert WIDE_W > WIDE_H

    # Hero pane is the cover-cropped portrait, not a flat house wash.
    hx, hy, hw, hh = hero_box()
    hero = wide.crop((hx + 8, hy + 8, hx + hw - 8, hy + hh - 8))
    assert _stddev(hero) > 25, _stddev(hero)

    standard = _open(render(**_sample_scene(1)))
    assert standard.size == (CW, CH), standard.size
    assert CH > CW

    # Allowlisted id with no portrait file still gets the wide layout.
    fallback = _open(render_wide(**_sample_scene(42)))
    assert fallback.size == (WIDE_W, WIDE_H)

    # Sparse data should still render.
    sparse = _open(render_wide(
        look={}, user_id=42, name="A", title=None, titles=[],
        house_key=None, house_color=0x6C5CE7, gear_icons=[],
        wand={"wood": "Pine", "core": "Starglass Shard"}, stats=[], motto=None,
        gold_trim=True, wand_sparks=True,
    ))
    assert sparse.size == (WIDE_W, WIDE_H)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(wide_png)
    print(f"wrote {out} ({wide.size[0]}x{wide.size[1]}, {len(wide_png)} bytes)")
    print("OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL {exc}")
        raise
