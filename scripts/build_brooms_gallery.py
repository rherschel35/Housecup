#!/usr/bin/env python3
"""Regenerate compendium_site/brooms.html from cogs/brooms.BROOMS."""
from __future__ import annotations

import html
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "compendium_site" / "brooms.html"


def _load_brooms():
    spec = importlib.util.spec_from_file_location("brooms_cog", ROOT / "cogs" / "brooms.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _bar_html(n: int) -> str:
    n = max(0, min(10, int(n)))
    return (
        f'<span class="stat-bar" aria-label="{n} out of 10">'
        f'<span class="stat-fill" style="width:{n * 10}%"></span></span>'
        f'<span class="stat-n">{n}/10</span>'
    )


def _card(model: str, blurb: str, brooms_mod) -> str:
    # Catalog showcase stats — same shape as Discord; personal /broom rolls from wand words.
    digest = brooms_mod._digest(f"catalog|{model}")
    stats = brooms_mod._stat_block(digest)
    finish = f"The {model} settles into your hand like it had been waiting."
    reading = (
        f"The {model} is {blurb}. "
        "It will not make you faster. It will make you look like yourself."
    )
    serious = "".join(
        f'<div class="stat"><span class="stat-label">{html.escape(brooms_mod.STAT_LABELS[k])}</span>'
        f"{_bar_html(stats[k])}</div>"
        for k in brooms_mod.SERIOUS_STATS
    )
    silly = "".join(
        f'<div class="stat"><span class="stat-label">{html.escape(brooms_mod.STAT_LABELS[k])}</span>'
        f"{_bar_html(stats[k])}</div>"
        for k in brooms_mod.SILLY_STATS
    )
    m = html.escape(model)
    return (
        f'<article class="card broom-card" id="{m}">'
        f'<img src="brooms/{m}.jpg" alt="{m}" width="320" height="320" loading="lazy">'
        f'<div class="broom-body">'
        f"<h3>{m}</h3>"
        f'<p class="broom-finish">{html.escape(finish)}</p>'
        f'<p class="broom-reading">{html.escape(reading)}</p>'
        f'<div class="broom-stats">'
        f'<p class="stat-group">Flight (for show)</p>{serious}'
        f'<p class="stat-group">Also (deeply scientific)</p>{silly}'
        f"</div></div></article>"
    )


def main() -> int:
    brooms_mod = _load_brooms()
    cards = "".join(_card(m, b, brooms_mod) for m, b in brooms_mod.BROOMS.items())
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Brooms · Velmora</title><meta name="description" content="All 100 unique Velmora broom portraits — readings, silly stats, and looks. Claimed with /broom from your wand words.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600;700&family=Alegreya:ital,wght@0,400;0,500;0,700;1,400&family=Alegreya+Sans+SC:wght@500;700&display=swap"><link rel="stylesheet" href="style.css">
<script>try{{var t=localStorage.getItem('vc-theme');if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
</head><body>
<header class="top"><div class="top-in"><a class="brand" href="index.html">Velmora <span>Compendium</span></a>
<nav class="nav" aria-label="Sections"><a href="index.html">Home</a><a href="houses.html">Houses &amp; Cup</a><a href="ghosts.html">Ghosts</a><a href="explore.html">Explore</a><a href="beasts.html">Beasts</a><a href="duels.html">Duels &amp; Threats</a><a href="descent.html">Descent</a><a href="quidditch.html">Quidditch</a><a href="magic.html">Wands &amp; Familiars</a><a href="brooms.html" aria-current="page">Brooms</a><a href="cards.html">Cards &amp; Gear</a><a href="tales.html">Tales</a><a href="commands.html">Commands</a></nav>
<button class="theme" type="button" onclick="(function(){{var d=document.documentElement,n=(d.dataset.theme==='dark'||(!d.dataset.theme&&matchMedia('(prefers-color-scheme: dark)').matches))?'light':'dark';d.dataset.theme=n;try{{localStorage.setItem('vc-theme',n)}}catch(e){{}}}})()">Light / dark</button>
</div></header>
<main class="wrap">
<div class="head"><p class="eyebrow">Velmora · the magic</p><h1>The Brooms</h1><p class="lede">One hundred fixed portraits. <code>/broom</code> claims yours from the same three words as your wand — one owner each, next-closest if taken. Purely cosmetic.</p>
<p class="sub">Each card mirrors what Discord shows: a short reading of why it fits, plus bragging stats. Catalog stats below are a sample for that broom; yours roll from your wand words when you claim. Jump by name, e.g. <a href="#Moonflare"><code>#Moonflare</code></a>.</p></div>
<section id="gallery">
<div class="grid broom-grid">{cards}</div>
</section>
<footer class="site">Kept by the House Cup. Everything here comes straight from the bots that run Velmora. The secrets are not written down anywhere. You'll have to find those yourself.</footer>
</main><script>(function(){{var h=document.querySelector('.top');function s(){{document.documentElement.style.setProperty('--tb',h.offsetHeight+'px')}}s();addEventListener('resize',s);var n=document.querySelector('.nav'),c=n&&n.querySelector('[aria-current]');if(c&&n.scrollWidth>n.clientWidth)n.scrollLeft=(c.offsetLeft-n.offsetLeft)-n.clientWidth/2+c.clientWidth/2;}})();</script></body></html>
"""
    OUT.write_text(page, encoding="utf-8")
    print(f"Wrote {OUT} ({len(brooms_mod.BROOMS)} brooms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
