#!/usr/bin/env python3
"""Expand broom-race scenario pool and rebuild course stages.

Each stage keeps the usual 6-kind option set (clean, 2× tech, bold, stall, trap)
with reading-required labels — no neon tells like (steady)/shortcut/illusion.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COURSES_PATH = ROOT / "data" / "broom_courses.json"

ATMOS = [
    "",
    " The crowd hushes.",
    " Your broom twitches.",
    " Someone cheers too early.",
    " Flags snap along the stands.",
    " A whistle cuts the air.",
]

# prompt, options[6] with kinds. Thresholds tuned so mid stats can meet one clean/tech.
SCENARIOS: list[dict] = [
    {
        "prompt": "Three arches ahead — low, mid, and sky-scraping.",
        "options": [
            {"label": "Take the low speed arch", "kind": "clean", "stat": "speed", "threshold": 4},
            {"label": "Thread the middle needle", "kind": "tech", "stat": "altitude", "threshold": 3},
            {"label": "Vault the sky arch", "kind": "tech", "stat": "altitude", "threshold": 7},
            {"label": "Drop under the arches onto the turf", "kind": "bold"},
            {"label": "Ease off and thread between posts", "kind": "stall"},
            {"label": "Cut right where the crowd leans in", "kind": "trap"},
        ],
    },
    {
        "prompt": "A false finish ribbon snaps in the breeze early.",
        "options": [
            {"label": "Hold the painted centre line", "kind": "clean", "stat": "speed", "threshold": 4},
            {"label": "Climb to read the far post", "kind": "tech", "stat": "altitude", "threshold": 3},
            {"label": "Dive under the lower ribbon", "kind": "tech", "stat": "speed", "threshold": 6},
            {"label": "Raise the broom and coast the last bend", "kind": "bold"},
            {"label": "Pause at the ribbon and reset", "kind": "stall"},
            {"label": "Rail left along the stand wall", "kind": "trap"},
        ],
    },
    {
        "prompt": "The course dives into a timber tunnel.",
        "options": [
            {"label": "Commit to a flat speed line", "kind": "clean", "stat": "speed", "threshold": 6},
            {"label": "Ride the upper rafters", "kind": "tech", "stat": "altitude", "threshold": 4},
            {"label": "Skip the tunnel over the roof", "kind": "tech", "stat": "altitude", "threshold": 8},
            {"label": "Barrel-roll the tunnel mouth", "kind": "bold"},
            {"label": "Touch down and kick along the bank", "kind": "stall"},
            {"label": "Take the scratched inner notch", "kind": "trap"},
        ],
    },
    {
        "prompt": "Standing stones force a sudden S-bend.",
        "options": [
            {"label": "Rail the inside at speed", "kind": "clean", "stat": "speed", "threshold": 6},
            {"label": "Hop the outer stones high", "kind": "tech", "stat": "altitude", "threshold": 6},
            {"label": "Bank vertical between pillars", "kind": "tech", "stat": "altitude", "threshold": 9},
            {"label": "Commit blind through the spray", "kind": "bold"},
            {"label": "Take the long outer curve", "kind": "stall"},
            {"label": "Aim for the bright rune gap", "kind": "trap"},
        ],
    },
    {
        "prompt": "The final stretch offers a risky cliff cut.",
        "options": [
            {"label": "Keep the chalked lane", "kind": "clean", "stat": "speed", "threshold": 3},
            {"label": "Take the high overlook cut", "kind": "tech", "stat": "altitude", "threshold": 5},
            {"label": "Dive the cliff for a slingshot", "kind": "tech", "stat": "speed", "threshold": 9},
            {"label": "Cut wide toward the packed stands", "kind": "bold"},
            {"label": "Settle early into a tidy landing", "kind": "stall"},
            {"label": "Dive for the floating cup marker", "kind": "trap"},
        ],
    },
    {
        "prompt": "A flock of post owls crosses mid-course.",
        "options": [
            {"label": "Punch a gap at speed", "kind": "clean", "stat": "speed", "threshold": 4},
            {"label": "Climb over the flock", "kind": "tech", "stat": "altitude", "threshold": 3},
            {"label": "Slip beneath wingtip-tight", "kind": "tech", "stat": "speed", "threshold": 8},
            {"label": "Break formation with a loud cut", "kind": "bold"},
            {"label": "Loop once to let the pack clear", "kind": "stall"},
            {"label": "Stay on the owl's wing line", "kind": "trap"},
        ],
    },
    {
        "prompt": "A bank of mist swallows the next marker.",
        "options": [
            {"label": "Drop under the mist-line", "kind": "clean", "stat": "speed", "threshold": 3},
            {"label": "Climb above the fog bank", "kind": "tech", "stat": "altitude", "threshold": 4},
            {"label": "Thread the lit mid-gap", "kind": "tech", "stat": "altitude", "threshold": 6},
            {"label": "Drive straight into the whiteout", "kind": "bold"},
            {"label": "Hold altitude until the mist shifts", "kind": "stall"},
            {"label": "Swing left toward the lantern run", "kind": "trap"},
        ],
    },
    {
        "prompt": "A crosswind slams the pitch from starboard.",
        "options": [
            {"label": "Lean into it and accelerate", "kind": "clean", "stat": "speed", "threshold": 5},
            {"label": "Rise above the shear", "kind": "tech", "stat": "altitude", "threshold": 5},
            {"label": "Feather the bristles and slipstream", "kind": "tech", "stat": "speed", "threshold": 7},
            {"label": "Stay upright through the shear", "kind": "bold"},
            {"label": "Bleed speed into a trailing line", "kind": "stall"},
            {"label": "Tuck the line behind the board", "kind": "trap"},
        ],
    },
    {
        "prompt": "Rain turns the pitch into a mirror.",
        "options": [
            {"label": "Skim low and read the ripples", "kind": "clean", "stat": "speed", "threshold": 5},
            {"label": "Climb out of the glare", "kind": "tech", "stat": "altitude", "threshold": 4},
            {"label": "Match your reflection's line", "kind": "tech", "stat": "altitude", "threshold": 7},
            {"label": "Cut the waterline for a splash", "kind": "bold"},
            {"label": "Wait on the ridge for a dry gust", "kind": "stall"},
            {"label": "Trust the inverted marker row", "kind": "trap"},
        ],
    },
    {
        "prompt": "Hoops stack in a rising spiral.",
        "options": [
            {"label": "Spiral tight and fast", "kind": "clean", "stat": "speed", "threshold": 5},
            {"label": "Widen and climb through", "kind": "tech", "stat": "altitude", "threshold": 5},
            {"label": "Skip two hoops on a high cut", "kind": "tech", "stat": "altitude", "threshold": 8},
            {"label": "Reverse the spiral one turn", "kind": "bold"},
            {"label": "Draft behind the next racer", "kind": "stall"},
            {"label": "Mirror the opposite hoop stack", "kind": "trap"},
        ],
    },
    # ---- new scenarios ----
    {
        "prompt": "Banner poles lean over the lane after a gust.",
        "options": [
            {"label": "Duck the poles and keep the lane", "kind": "clean", "stat": "speed", "threshold": 4},
            {"label": "Climb clear of the swinging cloth", "kind": "tech", "stat": "altitude", "threshold": 4},
            {"label": "Thread between two leaning poles", "kind": "tech", "stat": "altitude", "threshold": 7},
            {"label": "Slash through the nearest banner", "kind": "bold"},
            {"label": "Swing wide around the whole row", "kind": "stall"},
            {"label": "Aim for the gold-fringed gap", "kind": "trap"},
        ],
    },
    {
        "prompt": "A cart of quaffles rolls loose across the chalk.",
        "options": [
            {"label": "Hop the seam and hold speed", "kind": "clean", "stat": "speed", "threshold": 5},
            {"label": "Climb until the cart rolls past", "kind": "tech", "stat": "altitude", "threshold": 3},
            {"label": "Weave the bouncing quaffle gaps", "kind": "tech", "stat": "speed", "threshold": 8},
            {"label": "Kick a quaffle aside mid-pass", "kind": "bold"},
            {"label": "Circle once behind the cart", "kind": "stall"},
            {"label": "Chase the red quaffle's bounce line", "kind": "trap"},
        ],
    },
    {
        "prompt": "Sunflare off the lake blinds the next turn.",
        "options": [
            {"label": "Squint the shoreline and punch through", "kind": "clean", "stat": "speed", "threshold": 5},
            {"label": "Climb until the glare softens", "kind": "tech", "stat": "altitude", "threshold": 4},
            {"label": "Ride the shadowed bank under the trees", "kind": "tech", "stat": "altitude", "threshold": 6},
            {"label": "Close on the bright water hard", "kind": "bold"},
            {"label": "Ease along the ridge until you see again", "kind": "stall"},
            {"label": "Steer toward the glittering strip", "kind": "trap"},
        ],
    },
    {
        "prompt": "Scaffolding for the night match crowds the air.",
        "options": [
            {"label": "Take the open service lane", "kind": "clean", "stat": "speed", "threshold": 4},
            {"label": "Climb above the top plank", "kind": "tech", "stat": "altitude", "threshold": 5},
            {"label": "Thread the scaffold squares", "kind": "tech", "stat": "altitude", "threshold": 8},
            {"label": "Clip a rope and keep flying", "kind": "bold"},
            {"label": "Drift outside the whole frame", "kind": "stall"},
            {"label": "Cut through the painted workers' gap", "kind": "trap"},
        ],
    },
    {
        "prompt": "A sudden silence — the commentator's voice cuts out.",
        "options": [
            {"label": "Trust your marks and hold the line", "kind": "clean", "stat": "speed", "threshold": 4},
            {"label": "Climb to spot the next flag yourself", "kind": "tech", "stat": "altitude", "threshold": 4},
            {"label": "Dive to the painted ground arrows", "kind": "tech", "stat": "speed", "threshold": 7},
            {"label": "Sprint on instinct alone", "kind": "bold"},
            {"label": "Slow until the loudspeakers return", "kind": "stall"},
            {"label": "Veer where the last cheer pointed", "kind": "trap"},
        ],
    },
    {
        "prompt": "Practice rings from an earlier heat still hang low.",
        "options": [
            {"label": "Thread the lowest clear ring", "kind": "clean", "stat": "speed", "threshold": 5},
            {"label": "Climb over the leftover stack", "kind": "tech", "stat": "altitude", "threshold": 4},
            {"label": "Skip two rings on a high diagonal", "kind": "tech", "stat": "altitude", "threshold": 8},
            {"label": "Ram the nearest ring aside", "kind": "bold"},
            {"label": "Go around the whole leftover set", "kind": "stall"},
            {"label": "Take the ring with the bright ribbon", "kind": "trap"},
        ],
    },
    {
        "prompt": "Ember ash drifts across from the feast fires.",
        "options": [
            {"label": "Push through the thin ash seam", "kind": "clean", "stat": "speed", "threshold": 4},
            {"label": "Climb above the warm drift", "kind": "tech", "stat": "altitude", "threshold": 3},
            {"label": "Slip under the densest plume", "kind": "tech", "stat": "speed", "threshold": 7},
            {"label": "Burst the plume for a clear wake", "kind": "bold"},
            {"label": "Hold back until the ash thins", "kind": "stall"},
            {"label": "Follow the orange spark trail", "kind": "trap"},
        ],
    },
    {
        "prompt": "A rival broom's wake knocks your nose off-line.",
        "options": [
            {"label": "Correct and reclaim the racing line", "kind": "clean", "stat": "speed", "threshold": 5},
            {"label": "Climb out of their wash", "kind": "tech", "stat": "altitude", "threshold": 4},
            {"label": "Dive under their tail and rethread", "kind": "tech", "stat": "speed", "threshold": 8},
            {"label": "Shoulder through their wake proud", "kind": "bold"},
            {"label": "Yield a length and reset behind", "kind": "stall"},
            {"label": "Match their exact wake curve", "kind": "trap"},
        ],
    },
    {
        "prompt": "Candle-float charms light a crooked corridor.",
        "options": [
            {"label": "Hold the dark gap between flames", "kind": "clean", "stat": "speed", "threshold": 5},
            {"label": "Climb where the corridor opens", "kind": "tech", "stat": "altitude", "threshold": 4},
            {"label": "Thread the candle grid on edge", "kind": "tech", "stat": "altitude", "threshold": 8},
            {"label": "Scatter the nearest charms aside", "kind": "bold"},
            {"label": "Wait for a clearer corridor beat", "kind": "stall"},
            {"label": "Chase the brightest candle row", "kind": "trap"},
        ],
    },
    {
        "prompt": "The pitch bells toll mid-stage — timing slips.",
        "options": [
            {"label": "Count your own strokes and press on", "kind": "clean", "stat": "speed", "threshold": 4},
            {"label": "Climb to see the next bell tower mark", "kind": "tech", "stat": "altitude", "threshold": 5},
            {"label": "Cut low along the tower shadow", "kind": "tech", "stat": "speed", "threshold": 7},
            {"label": "Race the peal without looking", "kind": "bold"},
            {"label": "Ease until the bells finish", "kind": "stall"},
            {"label": "Turn with the echo off the east wall", "kind": "trap"},
        ],
    },
    {
        "prompt": "Ice patches glitter on the lower rails.",
        "options": [
            {"label": "Keep the dry centre rail", "kind": "clean", "stat": "speed", "threshold": 5},
            {"label": "Climb off the iced rails entirely", "kind": "tech", "stat": "altitude", "threshold": 4},
            {"label": "Skate a controlled edge on the frost", "kind": "tech", "stat": "speed", "threshold": 8},
            {"label": "Grind the ice for sparks", "kind": "bold"},
            {"label": "Lift and drift until the rails clear", "kind": "stall"},
            {"label": "Aim for the brightest ice sheet", "kind": "trap"},
        ],
    },
    {
        "prompt": "A lost house scarf tangles across two posts.",
        "options": [
            {"label": "Duck under the scarf and keep pace", "kind": "clean", "stat": "speed", "threshold": 3},
            {"label": "Climb over both posts clean", "kind": "tech", "stat": "altitude", "threshold": 4},
            {"label": "Split the posts on a tight bank", "kind": "tech", "stat": "altitude", "threshold": 7},
            {"label": "Tear through the scarf at speed", "kind": "bold"},
            {"label": "Go around the posts entirely", "kind": "stall"},
            {"label": "Grab the scarf line like a guide", "kind": "trap"},
        ],
    },
    {
        "prompt": "Night-lamps flicker — half the course goes dim.",
        "options": [
            {"label": "Fly the still-lit centre marks", "kind": "clean", "stat": "speed", "threshold": 4},
            {"label": "Climb toward the remaining lamps", "kind": "tech", "stat": "altitude", "threshold": 5},
            {"label": "Dive to the pale chalk you can still see", "kind": "tech", "stat": "speed", "threshold": 6},
            {"label": "Charge the dark stretch flat-out", "kind": "bold"},
            {"label": "Hold until more lamps catch again", "kind": "stall"},
            {"label": "Steer for the single lamp that flares", "kind": "trap"},
        ],
    },
    {
        "prompt": "A pride of first-years wanders onto the edge of the lane.",
        "options": [
            {"label": "Bend the lane and keep your speed", "kind": "clean", "stat": "speed", "threshold": 5},
            {"label": "Climb well clear of their heads", "kind": "tech", "stat": "altitude", "threshold": 3},
            {"label": "Thread the gap they just opened", "kind": "tech", "stat": "speed", "threshold": 8},
            {"label": "Shout them aside and bull through", "kind": "bold"},
            {"label": "Loop out until the marshals move them", "kind": "stall"},
            {"label": "Follow the prefect pointing left", "kind": "trap"},
        ],
    },
    {
        "prompt": "Thunderheads stack over the far hoops.",
        "options": [
            {"label": "Beat the weather on the low line", "kind": "clean", "stat": "speed", "threshold": 5},
            {"label": "Climb the clear shoulder of cloud", "kind": "tech", "stat": "altitude", "threshold": 6},
            {"label": "Slash under the darkest shelf", "kind": "tech", "stat": "speed", "threshold": 8},
            {"label": "Punch straight at the thunderhead", "kind": "bold"},
            {"label": "Hold short of the storm wall", "kind": "stall"},
            {"label": "Ride the silver lightning fork", "kind": "trap"},
        ],
    },
]


def _note_for(stage: dict) -> str:
    clean = next(o["label"] for o in stage["options"] if o["kind"] == "clean")
    trap = next(o["label"] for o in stage["options"] if o["kind"] == "trap")
    templates = [
        f"Racers who studied note: Don't {trap[0].lower() + trap[1:]}; {clean[0].lower() + clean[1:]} — saves seconds.",
        f"If the tempting line is “{trap}”, reset with {clean[0].lower() + clean[1:]}.",
        f"Marker paint flakes on the true path near {clean[0].lower() + clean[1:]}.",
        f"On this course, altitude beats panic: prefer {clean[0].lower() + clean[1:]} when the wind shifts.",
    ]
    # Prefer a note that doesn't start with awkward "Don't take the..." when trap doesn't start with verb nicely
    return random.choice(templates)


def main() -> None:
    random.seed(42)
    data = json.loads(COURSES_PATH.read_text(encoding="utf-8"))
    courses = data["courses"]
    assert len(SCENARIOS) >= 20

    for course in courses:
        picks = random.sample(SCENARIOS, 6)
        stages = []
        for sc in picks:
            atmos = random.choice(ATMOS)
            prompt = sc["prompt"] + atmos
            opts = []
            for o in sc["options"]:
                copy = dict(o)
                # normalize optional fields
                if copy["kind"] in ("bold", "stall", "trap"):
                    copy.pop("stat", None)
                    copy.pop("threshold", None)
                opts.append(copy)
            stages.append({"prompt": prompt.strip(), "options": opts})
        course["stages"] = stages
        course["study_note"] = _note_for(stages[0])

    COURSES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    stems = {s["prompt"].split(".")[0] for c in courses for s in c["stages"]}
    print(f"courses={len(courses)} scenarios_pool={len(SCENARIOS)} unique_prompt_stems~={len(stems)}")


if __name__ == "__main__":
    main()
