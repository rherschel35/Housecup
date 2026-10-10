"""Prompt helpers for Fantastic Beasts appear / summon art."""

from __future__ import annotations

PLACE_SCENE = {
    "garden": "mossy herb-garden terraces, warm green-gold daylight, soft petals in the air",
    "library": "cozy magical library stacks, warm lamp glow, floating dust motes and books",
    "dungeons": "candlelit stone dungeon corridor, cool amber light, soft moss on stone",
    "forbidden_woods": "misty enchanted forest floor, dappled green light, ferns and roots",
    "observatory": "starry observatory dome terrace, soft blue-violet night light, faint constellations",
}

STYLE = (
    "Soft storybook illustration for a cozy wizarding-school Discord game, "
    "square composition, cute fantasy creature, gentle lighting, clean readable "
    "silhouette, highly readable at small size, no text, no watermark, no border, no UI"
)


def appear_prompt(beast: dict) -> str:
    scene = PLACE_SCENE.get(beast["place"], "magical school grounds")
    night = " Nighttime scene." if beast.get("night") else ""
    return (
        f"{STYLE}. Portrait of the creature appearing in the wild. "
        f"{beast['name']}: {beast['desc']} "
        f"Scene: {beast['sighting']} Setting: {scene}.{night}"
    )


def summon_prompt(beast: dict) -> str:
    scene = PLACE_SCENE.get(beast["place"], "magical school grounds")
    action = beast.get("summon") or beast["sighting"]
    return (
        f"{STYLE}. The creature is mid-adorable action while summoned. "
        f"{beast['name']}: {beast['desc']} "
        f"Action: {action} Setting: {scene}."
    )
