#!/usr/bin/env python3
"""Export Premium Girl Sprites.psd Combined layers into wizard_assets/female_full/."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image
from psd_tools import PSDImage

PACK_FORMAT = "girl_premium"


def _slug(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "layer"


def _find(parent, name_match: str):
    """Find first child whose name contains name_match (case-insensitive)."""
    needle = name_match.lower()
    for layer in parent:
        if needle in layer.name.lower():
            return layer
    return None


def _find_exact(parent, name: str):
    target = name.strip().lower()
    for layer in parent:
        if layer.name.strip().lower() == target:
            return layer
    return None


def _combined(psd: PSDImage):
    for layer in psd:
        if layer.kind == "group" and "combined" in layer.name.lower():
            return layer
    raise RuntimeError("No 'Combined layers' group in PSD")


def _force_visible(layer) -> list:
    """Temporarily show a layer (and descendants). Returns undo records."""
    undo = []

    def walk(node):
        if hasattr(node, "visible"):
            undo.append((node, node.visible))
            node.visible = True
        if getattr(node, "kind", None) == "group":
            for child in node:
                walk(child)

    walk(layer)
    return undo


def _restore(undo: list) -> None:
    for node, vis in undo:
        node.visible = vis


def _render_layer(layer, size: tuple[int, int]) -> Image.Image:
    """Composite a layer/group onto a transparent full-canvas image.

    Invisible layers still export — we force-visible, composite, then restore.
    """
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    undo = _force_visible(layer)
    try:
        try:
            im = layer.composite()
        except Exception:
            im = None
        if im is None and layer.kind == "pixel":
            try:
                im = layer.topil()
            except Exception:
                im = None
    finally:
        _restore(undo)
    if im is None:
        return canvas
    im = im.convert("RGBA")
    x, y = int(layer.left), int(layer.top)
    if x < 0 or y < 0:
        cx, cy = max(0, -x), max(0, -y)
        im = im.crop((cx, cy, im.width, im.height))
        x, y = max(0, x), max(0, y)
    if x >= size[0] or y >= size[1] or im.width == 0 or im.height == 0:
        return canvas
    # Clip if it extends past canvas
    if x + im.width > size[0] or y + im.height > size[1]:
        im = im.crop((0, 0, min(im.width, size[0] - x), min(im.height, size[1] - y)))
    canvas.alpha_composite(im, (x, y))
    return canvas


def _save(im: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Drop fully empty exports
    if im.getbbox() is None:
        print(f"  skip empty {path.name}")
        return
    im.save(path, optimize=True)
    print(f"  wrote {path.relative_to(path.parents[2] if len(path.parts) > 2 else path.parent)} ({path.stat().st_size:,}b)")


def _export_named_pixels(group, out_dir: Path, size, mapping: dict[str, str] | None = None):
    """Export each pixel child; optional mapping display_name_substr -> filename stem."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for layer in group:
        if layer.kind != "pixel":
            continue
        name = layer.name.strip()
        if mapping is not None:
            stem = None
            for key, dest in mapping.items():
                if key.lower() in name.lower():
                    stem = dest
                    break
            if stem is None:
                continue
        else:
            stem = _slug(name)
        _save(_render_layer(layer, size), out_dir / f"{stem}.png")


def export_psd(psd_path: Path, dest: Path) -> None:
    print(f"Opening {psd_path}")
    psd = PSDImage.open(psd_path)
    size = (psd.width, psd.height)
    print(f"Canvas {size[0]}×{size[1]}")
    if dest.exists():
        import shutil
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    comb = _combined(psd)

    # ---- body ----
    body_g = _find(comb, "Body")
    if not body_g:
        raise RuntimeError("Body group missing")
    body_dir = dest / "body"
    for layer in body_g:
        if layer.kind != "pixel":
            continue
        n = layer.name.strip().lower()
        if n == "body":
            _save(_render_layer(layer, size), body_dir / "body.png")
        elif "softer" in n:
            _save(_render_layer(layer, size), body_dir / "body_soft.png")
        elif "blush" in n:
            _save(_render_layer(layer, size), body_dir / "blush.png")
        elif "nose" in n:
            _save(_render_layer(layer, size), body_dir / "nose_highlight.png")

    # ---- hair back ----
    hb = _find(comb, "Back Hair")
    hb_dir = dest / "hair_back"
    hb_map = {
        "long let down": "long_let_down",
        "ponytail short": "ponytail_short",
        "short twintails": "short_twintails",
        "long pony": "long_pony",
        "hair tied back": "tied_back",
        "tied up back of head (better)": "tied_up",
        "tied up back of head": "tied_up",
        "twintails": "twintails",
        "dango covered": "dango_covered",
        "dango": "dango",
    }
    # 'short' alone — match carefully
    for layer in hb:
        if layer.kind == "pixel":
            n = layer.name.strip().lower()
            if n == "short":
                _save(_render_layer(layer, size), hb_dir / "short.png")
            else:
                for key, dest_name in hb_map.items():
                    if key in n:
                        _save(_render_layer(layer, size), hb_dir / f"{dest_name}.png")
                        break
        elif layer.kind == "group" and "braid" in layer.name.lower():
            _save(_render_layer(layer, size), hb_dir / "braids.png")

    # ---- hair front ----
    hf = _find(comb, "Front Hair")
    hf_dir = dest / "hair_front"
    # each subgroup composites as one style
    front_map = {
        "shoulder long": "shoulder",
        "hair 1": "hair_1",
        "hair 2": "hair_2",
        "straight cut front": None,  # ambiguous — handle by child names
        "hair alt": "alt",
        "hime": "hime",
        "short front": "short",
        "cat ears": "cat_ears_group",
    }
    seen_straight = 0
    for layer in hf:
        if layer.kind != "group":
            continue
        n = layer.name.strip().lower()
        if "shoulder" in n:
            _save(_render_layer(layer, size), hf_dir / "shoulder.png")
        elif re.search(r"hair\s*1", n):
            _save(_render_layer(layer, size), hf_dir / "hair_1.png")
        elif re.search(r"hair\s*2", n):
            _save(_render_layer(layer, size), hf_dir / "hair_2.png")
        elif "alt" in n:
            _save(_render_layer(layer, size), hf_dir / "alt.png")
        elif "hime" in n:
            _save(_render_layer(layer, size), hf_dir / "hime.png")
        elif "short front" in n:
            _save(_render_layer(layer, size), hf_dir / "short.png")
        elif "straight" in n:
            # first straight → bangs, second → straight
            stem = "bangs" if seen_straight == 0 else "straight"
            seen_straight += 1
            _save(_render_layer(layer, size), hf_dir / f"{stem}.png")
        elif "cat" in n:
            _save(_render_layer(layer, size), dest / "accessories" / "cat_ears.png")

    # eye covered hair (separate top-level under Combined)
    eye_cov = _find(comb, "EYE COVERED")
    if eye_cov:
        # just the hair pixel, not glasses copies
        for layer in eye_cov:
            if layer.kind == "pixel" and "hair" in layer.name.lower():
                _save(_render_layer(layer, size), hf_dir / "eye_covered.png")
            elif layer.kind == "pixel" and "shadow" in layer.name.lower():
                # bake shadow into eye_covered by compositing both
                pass
        # composite whole group minus glasses subgroup for cleaner hair
        _save(_render_layer(eye_cov, size), hf_dir / "eye_covered.png")

    # ---- clothes ----
    clothes = _find(comb, "Clothes")
    c_dir = dest / "clothes"
    s_dir = dest / "shoes"
    for layer in clothes:
        n = layer.name.strip().lower()
        if layer.kind == "group" and "shoe" in n:
            for shoe in layer:
                if shoe.kind != "pixel":
                    continue
                sn = shoe.name.strip().lower()
                stem = None
                if "loafers" in sn:
                    stem = "loafers"
                elif "sport" in sn:
                    stem = "sport"
                elif "sandal" in sn:
                    stem = "sandals"
                elif "legging" in sn:
                    stem = "leggings"
                elif "long sock" in sn:
                    stem = "long_socks"
                elif "tennis" in sn:
                    stem = "tennis_socks"
                if stem:
                    _save(_render_layer(shoe, size), s_dir / f"{stem}.png")
        elif layer.kind == "group" and "one piece" in n:
            _save(_render_layer(layer, size), c_dir / "one_piece.png")
        elif layer.kind == "pixel" and "jean" in n:
            _save(_render_layer(layer, size), c_dir / "jeans.png")
        elif layer.kind == "group" and n.startswith("short"):
            _save(_render_layer(layer, size), c_dir / "shorts.png")
        elif layer.kind == "group" and "hoodie short" in n:
            _save(_render_layer(layer, size), c_dir / "hoodie_short.png")
        elif layer.kind == "group" and "hoodie long" in n:
            _save(_render_layer(layer, size), c_dir / "hoodie_long.png")
        elif layer.kind == "group" and "school uniform 1" in n:
            blue = _find(layer, "Blue")
            green = _find(layer, "Green")
            shadow = _find(layer, "Shadow")
            for color_name, stem in (("Blue", "uniform1_blue"), ("Green", "uniform1_green")):
                color_g = _find(layer, color_name)
                if not color_g:
                    continue
                canvas = Image.new("RGBA", size, (0, 0, 0, 0))
                if shadow:
                    canvas.alpha_composite(_render_layer(shadow, size))
                canvas.alpha_composite(_render_layer(color_g, size))
                _save(canvas, c_dir / f"{stem}.png")
        elif layer.kind == "group" and "school uniform 2" in n:
            shadow = None
            for child in layer:
                if child.kind == "group" and "shadow" in child.name.lower():
                    shadow = child
            for color_name, stem in (("Black", "uniform2_black"), ("Brown", "uniform2_brown")):
                color_g = _find(layer, color_name)
                if not color_g:
                    continue
                canvas = Image.new("RGBA", size, (0, 0, 0, 0))
                if shadow:
                    canvas.alpha_composite(_render_layer(shadow, size))
                canvas.alpha_composite(_render_layer(color_g, size))
                _save(canvas, c_dir / f"{stem}.png")
        elif layer.kind == "group" and "pe" in n:
            _save(_render_layer(layer, size), c_dir / "pe.png")

    # ---- face ----
    face = _find(comb, "Face")
    mouths = _find(face, "faces")
    mouth_map = {
        "smile": "smile",
        "flat": "flat",
        "grin": "grin",
        "open mouth big": "open_big",
        "open mouth  2": "open_2",
        "open mouth 2": "open_2",
        "open mouth": "open",
        "sad": "sad",
    }
    # longer keys first
    for layer in mouths:
        if layer.kind != "pixel":
            continue
        n = layer.name.strip().lower()
        stem = None
        for key, dest_name in sorted(mouth_map.items(), key=lambda kv: -len(kv[0])):
            if key in n:
                stem = dest_name
                break
        if stem:
            _save(_render_layer(layer, size), dest / "mouth" / f"{stem}.png")

    eyes = _find(face, "eyes")
    for layer in eyes:
        if layer.kind != "pixel":
            continue
        n = layer.name.strip().lower()
        if "white" in n:
            _save(_render_layer(layer, size), dest / "eyes" / "whites.png")
        elif "without reflection" in n:
            _save(_render_layer(layer, size), dest / "eyes" / "no_reflection.png")
        elif "standard" in n:
            _save(_render_layer(layer, size), dest / "eyes" / "standard.png")

    lashes = _find(face, "lashes")
    for layer in lashes:
        if layer.kind != "pixel":
            continue
        n = layer.name.strip().lower()
        if "normal" in n:
            _save(_render_layer(layer, size), dest / "lashes" / "normal.png")
        elif "sharp lashes 2" in n or n.strip() == "sharp lashes 2":
            _save(_render_layer(layer, size), dest / "lashes" / "sharp_2.png")
        elif "sharper" in n:
            _save(_render_layer(layer, size), dest / "lashes" / "sharper.png")

    brows_g = _find(face, "eyebrows")
    brow_sets = {
        "thin": "thin",
        "thick": "thick",
        "circle": "circle",
    }
    brow_expr = {
        "high": "high",
        "surprised": "surprised",
        "sad": "sad",
        "flat": "flat",
        "antagonistic": "antagonistic",
        "very angry": "angry",
        "angry": "angry",
    }
    for group in brows_g:
        if group.kind != "group":
            continue
        gn = group.name.strip().lower()
        prefix = None
        if "thin" in gn:
            prefix = "thin"
        elif "thick" in gn:
            prefix = "thick"
        elif "circle" in gn:
            prefix = "circle"
        if not prefix:
            continue
        for layer in group:
            if layer.kind != "pixel":
                continue
            ln = layer.name.strip().lower()
            expr = None
            for key, dest_name in sorted(brow_expr.items(), key=lambda kv: -len(kv[0])):
                if key in ln:
                    expr = dest_name
                    break
            if expr:
                _save(_render_layer(layer, size), dest / "brows" / f"{prefix}_{expr}.png")

    # ---- accessories ----
    acc2 = _find(comb, "Accessories 2")
    acc_dir = dest / "accessories"
    if acc2:
        for layer in acc2:
            if layer.kind != "pixel":
                continue
            n = layer.name.strip().lower()
            if "choker" in n:
                _save(_render_layer(layer, size), acc_dir / "choker.png")
            elif "gloves" in n:
                _save(_render_layer(layer, size), acc_dir / "gloves.png")
            elif "heart" in n:
                _save(_render_layer(layer, size), acc_dir / "heart.png")
            elif "beauty mark 2" in n:
                _save(_render_layer(layer, size), acc_dir / "beauty_mark_2.png")
            elif "beauty mark" in n:
                _save(_render_layer(layer, size), acc_dir / "beauty_mark.png")

    acc_f = _find(comb, "Accessories front")
    if acc_f:
        for layer in acc_f:
            if layer.kind != "pixel":
                continue
            n = layer.name.strip().lower()
            if "glasses 1" in n:
                _save(_render_layer(layer, size), acc_dir / "glasses_1.png")
            elif "glasses 2" in n:
                _save(_render_layer(layer, size), acc_dir / "glasses_2.png")
            elif "star" in n:
                _save(_render_layer(layer, size), acc_dir / "star.png")

    (dest / ".pack_format").write_text(PACK_FORMAT + "\n", encoding="utf-8")
    print(f"Done → {dest}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("psd", type=Path, help="Path to Premium Girl Sprites.psd")
    ap.add_argument("dest", type=Path, help="Output folder (female_full)")
    args = ap.parse_args(argv)
    if not args.psd.is_file():
        print("PSD not found:", args.psd, file=sys.stderr)
        return 1
    export_psd(args.psd, args.dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
