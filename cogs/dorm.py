"""
Your Own Room — personal dorm you decorate.

    /room [member]     - view a dorm
    /room find         - discover one decor item (once / day)
    /room place        - put an owned item into a slot
    /room clear        - clear a slot
    /room stash        - list decor you own

Daily finds fill your stash (~25 options per slot — hard to match).
Frog cards can sit on the shelf (ids from /frog album). Purely cosmetic.
"""

from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from cogs.velmora_channels import channel_mentions, with_study_hall_and_duels_potions
from cogs.world_engine import today

log = logging.getLogger("velmora.dorm")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "dorm_state.json"

ROOM_CHANNEL_IDS = with_study_hall_and_duels_potions()
COZY = 0x6B4F3A

SLOTS = ("bed", "wall", "desk", "shelf", "window")
SLOT_LABEL = {
    "bed": "Bed",
    "wall": "Wall",
    "desk": "Desk",
    "shelf": "Shelf",
    "window": "Window",
}

# Decor catalog — ~25 options per slot (found via /room find)
DECOR = {
    # --- bed (25) ---
    "patchwork_quilt": {
        "name": "Patchwork Quilt",
        "slot": "bed",
        "blurb": "Every square is a different excuse for staying up late.",
        "emoji": "🛏️",
    },
    "star_mobile": {
        "name": "Star Mobile",
        "slot": "bed",
        "blurb": "Turns slowly. Judges your sleep schedule.",
        "emoji": "✨",
    },
    "cozy_rug": {
        "name": "Cozy Rug",
        "slot": "bed",
        "blurb": "Technically for the floor. Spiritually for the bed.",
        "emoji": "🧶",
    },
    "cloud_duvet": {
        "name": "Cloud Duvet",
        "slot": "bed",
        "blurb": "Sleeps like fog. Weighs like a rumor.",
        "emoji": "☁️",
    },
    "oakmont_coverlet": {
        "name": "Oakmont Coverlet",
        "slot": "bed",
        "blurb": "Purple stitching. Patient warmth.",
        "emoji": "🐻",
    },
    "aster_chaos_blanket": {
        "name": "AsterWilde Chaos Blanket",
        "slot": "bed",
        "blurb": "No two folds agree. Neither do you.",
        "emoji": "🐿️",
    },
    "veyren_wool_throw": {
        "name": "Veyren Wool Throw",
        "slot": "bed",
        "blurb": "Quiet as a deer. Soft as a promise.",
        "emoji": "🦌",
    },
    "caldrin_measured_sheets": {
        "name": "Caldrin Measured Sheets",
        "slot": "bed",
        "blurb": "Hospital corners. Emotional support optional.",
        "emoji": "📐",
    },
    "thornmere_wolf_pelt": {
        "name": "Thornmere Wolf Pelt",
        "slot": "bed",
        "blurb": "Loyalty earned. Naps taken.",
        "emoji": "🐺",
    },
    "hammock_charm": {
        "name": "Indoor Hammock Charm",
        "slot": "bed",
        "blurb": "Bed, but socially ambitious.",
        "emoji": "🪢",
    },
    "midnight_canopy": {
        "name": "Midnight Canopy",
        "slot": "bed",
        "blurb": "Stars painted underneath. Some move.",
        "emoji": "🌌",
    },
    "featherlight_pillows": {
        "name": "Featherlight Pillows",
        "slot": "bed",
        "blurb": "Too many. Still not enough.",
        "emoji": "🪶",
    },
    "hot_water_bottle": {
        "name": "Enchanted Hot Bottle",
        "slot": "bed",
        "blurb": "Stays warm. Holds grudges about cold feet.",
        "emoji": "🧴",
    },
    "snore_muffler": {
        "name": "Snore Muffler Curtain",
        "slot": "bed",
        "blurb": "Your roommate paid for this. Quietly.",
        "emoji": "🔇",
    },
    "dreamcatcher_net": {
        "name": "Dreamcatcher Net",
        "slot": "bed",
        "blurb": "Keeps nightmares. Releases midterms.",
        "emoji": "🕸️",
    },
    "quilt_of_detention": {
        "name": "Detention Quilt",
        "slot": "bed",
        "blurb": "Stitched during hours you will not discuss.",
        "emoji": "🧵",
    },
    "broom_bunk_rail": {
        "name": "Broom Bunk Rail",
        "slot": "bed",
        "blurb": "For when the bed is too ordinary.",
        "emoji": "🧹",
    },
    "nest_bedding": {
        "name": "Animagus Nest Bedding",
        "slot": "bed",
        "blurb": "Smells faintly of your other form.",
        "emoji": "🪺",
    },
    "silk_sleep_mask": {
        "name": "Silk Sleep Mask",
        "slot": "bed",
        "blurb": "Blocks light. Not gossip.",
        "emoji": "😷",
    },
    "weighted_calm_blanket": {
        "name": "Weighted Calm Blanket",
        "slot": "bed",
        "blurb": "Presses anxiety into a manageable square.",
        "emoji": "🪨",
    },
    "ember_bed_warmer": {
        "name": "Ember Bed Warmer",
        "slot": "bed",
        "blurb": "Safe heat. Unsafe temptation to never rise.",
        "emoji": "🔥",
    },
    "frost_sheet_set": {
        "name": "Frost Sheet Set",
        "slot": "bed",
        "blurb": "Cool side of the pillow, forever.",
        "emoji": "❄️",
    },
    "storybook_headboard": {
        "name": "Storybook Headboard",
        "slot": "bed",
        "blurb": "Carved scenes change when you finish a chapter.",
        "emoji": "📖",
    },
    "travel_trunk_bed": {
        "name": "Travel-Trunk Bed",
        "slot": "bed",
        "blurb": "Folds up. Refuses to pack itself.",
        "emoji": "🧳",
    },
    "moonphase_comforter": {
        "name": "Moonphase Comforter",
        "slot": "bed",
        "blurb": "Pattern shifts with the Chicago night sky.",
        "emoji": "🌙",
    },
    # --- wall (25) ---
    "house_banner": {
        "name": "House Banner",
        "slot": "wall",
        "blurb": "Crooked on purpose. Or by talent.",
        "emoji": "🏳️",
    },
    "moving_sketch": {
        "name": "Moving Sketch",
        "slot": "wall",
        "blurb": "The subject keeps changing hats.",
        "emoji": "🖼️",
    },
    "wanted_poster_joke": {
        "name": "Joke Wanted Poster",
        "slot": "wall",
        "blurb": "Reward: one biscuit. Last seen: everywhere.",
        "emoji": "📜",
    },
    "velmora_map": {
        "name": "Annotated Velmora Map",
        "slot": "wall",
        "blurb": "Shortcuts in red. Detours in denial.",
        "emoji": "🗺️",
    },
    "family_portrait_blank": {
        "name": "Blank Family Portrait",
        "slot": "wall",
        "blurb": "Faces appear when you miss someone.",
        "emoji": "🖼️",
    },
    "clock_that_lies": {
        "name": "Clock That Lies",
        "slot": "wall",
        "blurb": "Always five minutes more. Always.",
        "emoji": "🕰️",
    },
    "mirror_shard_frame": {
        "name": "Mirror-Shard Frame",
        "slot": "wall",
        "blurb": "Reflects only your best angle. Suspicious.",
        "emoji": "🪞",
    },
    "quidditch_poster": {
        "name": "Quidditch Finals Poster",
        "slot": "wall",
        "blurb": "Creased. Loved. Historically inaccurate.",
        "emoji": "🏆",
    },
    "spell_diagram": {
        "name": "Framed Spell Diagram",
        "slot": "wall",
        "blurb": "Wrong on purpose to catch cheaters.",
        "emoji": "📐",
    },
    "pressed_flower_panel": {
        "name": "Pressed Flower Panel",
        "slot": "wall",
        "blurb": "Blooms again on birthdays.",
        "emoji": "🌸",
    },
    "shadow_box_keys": {
        "name": "Shadow Box of Keys",
        "slot": "wall",
        "blurb": "None open anything you own.",
        "emoji": "🗝️",
    },
    "tapestry_forest": {
        "name": "Forest Tapestry",
        "slot": "wall",
        "blurb": "Wind moves the threads. Leaves fall out.",
        "emoji": "🌲",
    },
    "wall_astrolabe": {
        "name": "Wall Astrolabe",
        "slot": "wall",
        "blurb": "Tracks stars. Judges ambition.",
        "emoji": "⭐",
    },
    "chalkboard_doodle": {
        "name": "Everlasting Chalkboard",
        "slot": "wall",
        "blurb": "Doodles erase themselves at dawn.",
        "emoji": "✏️",
    },
    "house_cup_ribbon": {
        "name": "Faded Cup Ribbon",
        "slot": "wall",
        "blurb": "From a season nobody will shut up about.",
        "emoji": "🎖️",
    },
    "windowless_window": {
        "name": "Painted False Window",
        "slot": "wall",
        "blurb": "Shows wherever you wish you were.",
        "emoji": "🪟",
    },
    "broom_mount": {
        "name": "Wall-Mounted Broom",
        "slot": "wall",
        "blurb": "Decorative. Mostly. Don't sit on it.",
        "emoji": "🧹",
    },
    "patronus_silhouette": {
        "name": "Patronus Silhouette",
        "slot": "wall",
        "blurb": "Silver outline of courage on quiet nights.",
        "emoji": "🦌",
    },
    "duel_score_slate": {
        "name": "Duel Score Slate",
        "slot": "wall",
        "blurb": "Keeps your wins. Forgets your losses. Kind.",
        "emoji": "⚔️",
    },
    "riddle_plaque": {
        "name": "Riddle Plaque",
        "slot": "wall",
        "blurb": "Answer changes weekly. Pride does not.",
        "emoji": "❓",
    },
    "moon_chart": {
        "name": "Hand-Drawn Moon Chart",
        "slot": "wall",
        "blurb": "Smudged where you fell asleep studying.",
        "emoji": "🌕",
    },
    "oak_shelf_rail": {
        "name": "Floating Shelf Rail",
        "slot": "wall",
        "blurb": "Holds three things. Demands five.",
        "emoji": "📚",
    },
    "curtain_of_silence": {
        "name": "Curtain of Silence",
        "slot": "wall",
        "blurb": "Muffles hallway drama by 40%.",
        "emoji": "🎭",
    },
    "crest_embroidery": {
        "name": "Crest Embroidery Hoop",
        "slot": "wall",
        "blurb": "Your house animal, slightly judgmental.",
        "emoji": "🪡",
    },
    "night_sky_mural": {
        "name": "Night-Sky Mural Charm",
        "slot": "wall",
        "blurb": "Constellations rearrange into gossip.",
        "emoji": "🌠",
    },
    # --- desk (25) ---
    "ink_stained_desk": {
        "name": "Ink-Stained Desk Set",
        "slot": "desk",
        "blurb": "The quill has opinions about your essays.",
        "emoji": "🖋️",
    },
    "tea_tray": {
        "name": "Perpetual Tea Tray",
        "slot": "desk",
        "blurb": "Still warm. Always. Suspicious.",
        "emoji": "🍵",
    },
    "crystal_paperweight": {
        "name": "Crystal Paperweight",
        "slot": "desk",
        "blurb": "Contains a tiny storm. Office-safe.",
        "emoji": "💎",
    },
    "candle_cluster": {
        "name": "Candle Cluster",
        "slot": "desk",
        "blurb": "Never drip on homework. Will drip on pride.",
        "emoji": "🕯️",
    },
    "hourglass_essay": {
        "name": "Essay Hourglass",
        "slot": "desk",
        "blurb": "Runs faster near deadlines.",
        "emoji": "⌛",
    },
    "ink_pot_rainbow": {
        "name": "Rainbow Ink Pot",
        "slot": "desk",
        "blurb": "Color changes with your mood. Rude.",
        "emoji": "🖋️",
    },
    "stack_of_tomes": {
        "name": "Precarious Tome Stack",
        "slot": "desk",
        "blurb": "One sneeze from disaster.",
        "emoji": "📚",
    },
    "owl_perch_mini": {
        "name": "Desk Owl Perch",
        "slot": "desk",
        "blurb": "For correspondence. And judgment.",
        "emoji": "🦉",
    },
    "abacus_of_excuses": {
        "name": "Abacus of Excuses",
        "slot": "desk",
        "blurb": "Calculates how late you actually are.",
        "emoji": "🧮",
    },
    "music_box_study": {
        "name": "Study Music Box",
        "slot": "desk",
        "blurb": "Plays one song. Somehow always right.",
        "emoji": "🎼",
    },
    "potion_sample_rack": {
        "name": "Sample Vial Rack",
        "slot": "desk",
        "blurb": "Labeled. Mislabelled. Both true.",
        "emoji": "⚗️",
    },
    "letter_opener_wand": {
        "name": "Letter-Opener Wand",
        "slot": "desk",
        "blurb": "Not for dueling. You will try anyway.",
        "emoji": "✉️",
    },
    "desk_globe_velmora": {
        "name": "Velmora Desk Globe",
        "slot": "desk",
        "blurb": "Spins to the lake when you're homesick.",
        "emoji": "🌍",
    },
    "blotting_dragon": {
        "name": "Blotting Dragon",
        "slot": "desk",
        "blurb": "Eats spills. Burps glitter.",
        "emoji": "🐉",
    },
    "focus_charm_lamp": {
        "name": "Focus Charm Lamp",
        "slot": "desk",
        "blurb": "Brightens only what you're avoiding.",
        "emoji": "💡",
    },
    "sealed_diary": {
        "name": "Sealed Desk Diary",
        "slot": "desk",
        "blurb": "Opens for honesty. Closes for drama.",
        "emoji": "📓",
    },
    "wax_seal_kit": {
        "name": "Wax Seal Kit",
        "slot": "desk",
        "blurb": "Crest stamps included. Taste debated.",
        "emoji": "🔏",
    },
    "biscuit_tin": {
        "name": "Endless Biscuit Tin",
        "slot": "desk",
        "blurb": "One left. Always one left.",
        "emoji": "🍪",
    },
    "quill_sharpener": {
        "name": "Auto Quill Sharpener",
        "slot": "desk",
        "blurb": "Hums. Occasionally screams.",
        "emoji": "✏️",
    },
    "mini_cauldron": {
        "name": "Desk Mini-Cauldron",
        "slot": "desk",
        "blurb": "For tea. Officially.",
        "emoji": "🫖",
    },
    "scratch_slate": {
        "name": "Scratch Arithmetic Slate",
        "slot": "desk",
        "blurb": "Sums your house points for fun. Painful fun.",
        "emoji": "🔢",
    },
    "ink_phoenix_feather": {
        "name": "Ink Phoenix Feather",
        "slot": "desk",
        "blurb": "Writes apologies first. Then the essay.",
        "emoji": "🪶",
    },
    "paperweight_frog": {
        "name": "Chocolate Frog Paperweight",
        "slot": "desk",
        "blurb": "Does not hop. Mentally hops.",
        "emoji": "🐸",
    },
    "timetable_crystal": {
        "name": "Timetable Crystal",
        "slot": "desk",
        "blurb": "Glows when you're in the wrong class.",
        "emoji": "🔮",
    },
    "drawer_of_confiscation": {
        "name": "Confiscation Drawer",
        "slot": "desk",
        "blurb": "Holds three secrets and a whistle.",
        "emoji": "🗄️",
    },
    # --- shelf (25) ---
    "shelf_of_oddities": {
        "name": "Shelf of Oddities",
        "slot": "shelf",
        "blurb": "None of these objects agree on a theme.",
        "emoji": "📦",
    },
    "plant_that_bites": {
        "name": "Plant That Bites",
        "slot": "shelf",
        "blurb": "Friendly. Mostly. Water from a distance.",
        "emoji": "🪴",
    },
    "jar_of_fireflies": {
        "name": "Jar of Fireflies",
        "slot": "shelf",
        "blurb": "They blink in Morse. Mostly insults.",
        "emoji": "🫙",
    },
    "snow_globe_castle": {
        "name": "Castle Snow Globe",
        "slot": "shelf",
        "blurb": "Shake for weather. Overuse for detention.",
        "emoji": "❄️",
    },
    "tiny_broom_display": {
        "name": "Tiny Broom Display",
        "slot": "shelf",
        "blurb": "1:12 scale. Full attitude.",
        "emoji": "🧹",
    },
    "shell_from_lake": {
        "name": "Lake Shell Collection",
        "slot": "shelf",
        "blurb": "Each hums a different rumor.",
        "emoji": "🐚",
    },
    "crystal_geode": {
        "name": "Split Crystal Geode",
        "slot": "shelf",
        "blurb": "Purple inside. Ambition outside.",
        "emoji": "💎",
    },
    "stack_of_letters": {
        "name": "Tied Letter Stack",
        "slot": "shelf",
        "blurb": "Addresses smudged. Feelings intact.",
        "emoji": "💌",
    },
    "pocket_watch_stopped": {
        "name": "Stopped Pocket Watch",
        "slot": "shelf",
        "blurb": "Frozen at the best minute of last term.",
        "emoji": "⌚",
    },
    "miniature_snitch": {
        "name": "Miniature Snitch",
        "slot": "shelf",
        "blurb": "Wings twitch when you procrastinate.",
        "emoji": "🏆",
    },
    "bottle_ship_spell": {
        "name": "Ship-in-a-Bottle Spell",
        "slot": "shelf",
        "blurb": "Sails when you study. Sinks when you don't.",
        "emoji": "🚢",
    },
    "herbology_clippings": {
        "name": "Herbology Clippings",
        "slot": "shelf",
        "blurb": "Growing. Plotting. Photosynthesizing spite.",
        "emoji": "🌿",
    },
    "marble_bust_frog": {
        "name": "Marble Frog Bust",
        "slot": "shelf",
        "blurb": "Classical. Amphibious. Unimpressed.",
        "emoji": "🗿",
    },
    "candle_that_listens": {
        "name": "Candle That Listens",
        "slot": "shelf",
        "blurb": "Flame leans toward secrets.",
        "emoji": "🕯️",
    },
    "dice_of_destiny": {
        "name": "Dice of Destiny",
        "slot": "shelf",
        "blurb": "Always land on 'group project'.",
        "emoji": "🎲",
    },
    "tea_tin_tower": {
        "name": "Tea Tin Tower",
        "slot": "shelf",
        "blurb": "Seven flavors. One is revenge.",
        "emoji": "🍵",
    },
    "hourglass_sand_pink": {
        "name": "Rose Sand Hourglass",
        "slot": "shelf",
        "blurb": "Pretty. Useless. Essential.",
        "emoji": "⏳",
    },
    "beast_figurine": {
        "name": "Beast Figurine Set",
        "slot": "shelf",
        "blurb": "Rearranges overnight into a tableau.",
        "emoji": "🦁",
    },
    "compass_that_points_home": {
        "name": "Homeward Compass",
        "slot": "shelf",
        "blurb": "Points to common room. Or biscuits.",
        "emoji": "🧭",
    },
    "orb_of_static": {
        "name": "Orb of Pleasant Static",
        "slot": "shelf",
        "blurb": "White noise for anxious brains.",
        "emoji": "🔮",
    },
    "folded_paper_crane": {
        "name": "Enchanted Paper Crane",
        "slot": "shelf",
        "blurb": "Delivers notes across the room only.",
        "emoji": "🕊️",
    },
    "trophy_participation": {
        "name": "Participation Trophy",
        "slot": "shelf",
        "blurb": "Won for showing up. Displayed with irony.",
        "emoji": "🏅",
    },
    "bookend_wolves": {
        "name": "Wolf Bookends",
        "slot": "shelf",
        "blurb": "Growl if you shelve out of order.",
        "emoji": "🐺",
    },
    "mushroom_lamp": {
        "name": "Mushroom Shelf Lamp",
        "slot": "shelf",
        "blurb": "Soft glow. Soft spores. Soft threats.",
        "emoji": "🍄",
    },
    "empty_frame_waiting": {
        "name": "Empty Frame (Waiting)",
        "slot": "shelf",
        "blurb": "For the frog card you haven't pulled yet.",
        "emoji": "🖼️",
    },
    # --- window (25) ---
    "window_charm_rain": {
        "name": "Rain Charm Pane",
        "slot": "window",
        "blurb": "Soft rain against the glass even in drought.",
        "emoji": "🌧️",
    },
    "window_charm_aurora": {
        "name": "Aurora Pane",
        "slot": "window",
        "blurb": "Northern lights on demand. Show-off setting included.",
        "emoji": "🌌",
    },
    "moon_catcher": {
        "name": "Moon Catcher",
        "slot": "window",
        "blurb": "Catches moonlight. Releases gossip.",
        "emoji": "🌙",
    },
    "sunbeam_filter": {
        "name": "Sunbeam Filter",
        "slot": "window",
        "blurb": "Golden hour, all day. Skin optional.",
        "emoji": "☀️",
    },
    "fog_on_purpose": {
        "name": "Purposeful Fog",
        "slot": "window",
        "blurb": "Mystery mode for dramatic exits.",
        "emoji": "🌫️",
    },
    "starfield_glass": {
        "name": "Starfield Glass",
        "slot": "window",
        "blurb": "Constellations of your choosing.",
        "emoji": "✨",
    },
    "lake_view_charm": {
        "name": "Lake View Charm",
        "slot": "window",
        "blurb": "Shows the shore. Hides the homework.",
        "emoji": "🌊",
    },
    "forest_edge_pane": {
        "name": "Forest Edge Pane",
        "slot": "window",
        "blurb": "Trees lean in when you're lonely.",
        "emoji": "🌲",
    },
    "quidditch_pitch_view": {
        "name": "Pitch View Charm",
        "slot": "window",
        "blurb": "Live score smudges on the glass.",
        "emoji": "🏟️",
    },
    "snowfall_loop": {
        "name": "Looping Snowfall",
        "slot": "window",
        "blurb": "Never drifts inside. Almost.",
        "emoji": "❄️",
    },
    "stained_house_glass": {
        "name": "House Stained Glass",
        "slot": "window",
        "blurb": "Your crest in light. Slightly biased.",
        "emoji": "🪟",
    },
    "curtain_beads": {
        "name": "Beaded Curtain",
        "slot": "window",
        "blurb": "Announces every entrance with clatter.",
        "emoji": "📿",
    },
    "breeze_catcher": {
        "name": "Breeze Catcher",
        "slot": "window",
        "blurb": "Smells like spring. Feels like recess.",
        "emoji": "🍃",
    },
    "thunderstorm_pane": {
        "name": "Thunderstorm Pane",
        "slot": "window",
        "blurb": "Rumbles softly during essays. Supportive.",
        "emoji": "⛈️",
    },
    "candlefly_dusk": {
        "name": "Candlefly Dusk",
        "slot": "window",
        "blurb": "Fireflies outside on cue.",
        "emoji": "🪲",
    },
    "mirror_window": {
        "name": "Mirror Window",
        "slot": "window",
        "blurb": "Looks out. Also looks back.",
        "emoji": "🪞",
    },
    "travel_postcard_view": {
        "name": "Postcard View",
        "slot": "window",
        "blurb": "Cycles through places you've never been.",
        "emoji": "🏞️",
    },
    "greenhouse_mist": {
        "name": "Greenhouse Mist",
        "slot": "window",
        "blurb": "Warm humidity. Occasional vine knock.",
        "emoji": "🪴",
    },
    "midnight_blue_tint": {
        "name": "Midnight Blue Tint",
        "slot": "window",
        "blurb": "Night mode for day people.",
        "emoji": "💙",
    },
    "sunrise_alarm_pane": {
        "name": "Sunrise Alarm Pane",
        "slot": "window",
        "blurb": "Gets brighter until you rise. Evil. Effective.",
        "emoji": "🌅",
    },
    "owlery_glimpse": {
        "name": "Owlery Glimpse",
        "slot": "window",
        "blurb": "Feathers against the glass. Letters inbound.",
        "emoji": "🦉",
    },
    "rainbow_prism": {
        "name": "Rainbow Prism Latch",
        "slot": "window",
        "blurb": "Scatters light into gossip colors.",
        "emoji": "🌈",
    },
    "frost_filigree": {
        "name": "Frost Filigree",
        "slot": "window",
        "blurb": "Ice lace that spells your initials.",
        "emoji": "❄️",
    },
    "curtain_velvet_green": {
        "name": "Velvet Green Drapes",
        "slot": "window",
        "blurb": "Thornmere-adjacent drama.",
        "emoji": "🟢",
    },
    "open_sky_charm": {
        "name": "Open Sky Charm",
        "slot": "window",
        "blurb": "No glass. Just breeze and bravery.",
        "emoji": "🌤️",
    },
}



def _channel_hint() -> str:
    return f"Dorm rooms are in {channel_mentions(ROOM_CHANNEL_IDS)}."


class Dorm(commands.Cog):
    room = app_commands.Group(
        name="room",
        description="Your dorm — find decor, place it, show it off.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self.rng = random.Random()

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {"players": {}}
        except (OSError, json.JSONDecodeError):
            log.exception("Dorm state unreadable — starting empty.")
            return {"players": {}}
        data.setdefault("players", {})
        return data

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save dorm state.")

    async def _require_channel(self, interaction: discord.Interaction) -> bool:
        if interaction.channel_id in ROOM_CHANNEL_IDS:
            return True
        await interaction.response.send_message(_channel_hint(), ephemeral=True)
        return False

    def record(self, user_id: int) -> dict:
        rec = self.state.setdefault("players", {}).setdefault(str(user_id), {})
        rec.setdefault("stash", {})  # decor_id -> count
        rec.setdefault("slots", {})  # slot -> {"kind":"decor"|"frog", "id":...}
        rec.setdefault("found_on", "")
        return rec

    def _frog_meta(self, card_id: str) -> dict | None:
        frogs = self.bot.get_cog("Frogs")
        if not frogs:
            return None
        from cogs.frogs import CARDS
        return CARDS.get(card_id)

    def embed_room(self, member: discord.abc.User, rec: dict) -> discord.Embed:
        lines = []
        for slot in SLOTS:
            placed = (rec.get("slots") or {}).get(slot)
            label = SLOT_LABEL[slot]
            if not placed:
                lines.append(f"**{label}:** _empty_")
                continue
            kind = placed.get("kind")
            pid = placed.get("id")
            if kind == "decor" and pid in DECOR:
                d = DECOR[pid]
                lines.append(f"**{label}:** {d['emoji']} {d['name']} — *{d['blurb']}*")
            elif kind == "frog":
                meta = self._frog_meta(pid)
                if meta:
                    lines.append(
                        f"**{label}:** {meta['emoji']} Frog card — **{meta['name']}**"
                    )
                else:
                    lines.append(f"**{label}:** 🃏 Frog card (`{pid}`)")
            else:
                lines.append(f"**{label}:** _something odd_")
        stash_n = sum(int(v) for v in (rec.get("stash") or {}).values())
        embed = discord.Embed(
            title=f"🏠 {member.display_name}'s Room",
            description="\n".join(lines),
            color=COZY,
        )
        embed.set_footer(
            text=f"Stash: {stash_n} decor · /room find · /room place · shelf holds frog cards too"
        )
        return embed

    @room.command(name="view", description="Look at a dorm room.")
    @app_commands.describe(member="Whose room (default: yours)")
    async def room_view(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ):
        if not await self._require_channel(interaction):
            return
        target = member or interaction.user
        rec = self.record(target.id)
        await interaction.response.send_message(
            embed=self.embed_room(target, rec),
            ephemeral=True,
        )

    # Also allow bare /room as alias via same group default? Discord needs subcommand.
    # Add a top-level convenience: users asked for /room — add both group view and
    # a top-level command named room_show? Keep group only: /room view

    @room.command(name="find", description="Find one decor item for your stash (once per day).")
    async def room_find(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        rec = self.record(interaction.user.id)
        day = today()
        if rec.get("found_on") == day:
            await interaction.response.send_message(
                "You've already found something for your room today. "
                "Try `/room place` or `/room view`.",
                ephemeral=True,
            )
            return
        stash = rec.setdefault("stash", {})
        owned = {
            k for k, v in stash.items() if int(v) > 0 and k in DECOR
        }
        for placed in (rec.get("slots") or {}).values():
            if placed and placed.get("kind") == "decor" and placed.get("id") in DECOR:
                owned.add(placed["id"])
        # Prefer new pieces so rooms stay unique longer
        fresh = [cid for cid in DECOR if cid not in owned]
        item_id = self.rng.choice(fresh or list(DECOR))
        stash[item_id] = int(stash.get(item_id, 0)) + 1
        rec["found_on"] = day
        self.save()
        d = DECOR[item_id]
        owned_n = len(owned | {item_id})
        embed = discord.Embed(
            title=f"Found: {d['emoji']} {d['name']}",
            description=(
                f"*{d['blurb']}*\n\n"
                f"Best on the **{SLOT_LABEL[d['slot']]}**. "
                f"Place it with `/room place`.\n"
                f"Collection: **{owned_n}/{len(DECOR)}** decor pieces"
            ),
            color=COZY,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @room.command(name="catalog", description="Browse all decor options for a slot (25 each).")
    @app_commands.describe(slot="Which furniture slot to list")
    @app_commands.choices(
        slot=[app_commands.Choice(name=SLOT_LABEL[s], value=s) for s in SLOTS]
    )
    async def room_catalog(
        self, interaction: discord.Interaction, slot: app_commands.Choice[str]
    ):
        if not await self._require_channel(interaction):
            return
        slot_key = slot.value
        rec = self.record(interaction.user.id)
        stash = rec.get("stash") or {}
        placed_id = None
        placed = (rec.get("slots") or {}).get(slot_key)
        if placed and placed.get("kind") == "decor":
            placed_id = placed.get("id")
        lines = []
        for cid, d in sorted(
            ((i, DECOR[i]) for i in DECOR if DECOR[i]["slot"] == slot_key),
            key=lambda kv: kv[1]["name"],
        ):
            mark = ""
            if cid == placed_id:
                mark = " · placed"
            elif int(stash.get(cid, 0)) > 0:
                mark = " · owned"
            lines.append(f"{d['emoji']} **{d['name']}**{mark}")
        embed = discord.Embed(
            title=f"🏠 {SLOT_LABEL[slot_key]} — 25 options",
            description="\n".join(lines),
            color=COZY,
        )
        embed.set_footer(text="Find with /room find · place with /room place")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @room.command(name="stash", description="Decor waiting in your stash.")
    async def room_stash(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        rec = self.record(interaction.user.id)
        stash = {
            k: int(v)
            for k, v in (rec.get("stash") or {}).items()
            if int(v) > 0 and k in DECOR
        }
        if not stash:
            await interaction.response.send_message(
                "Stash is empty. `/room find` once a day.", ephemeral=True
            )
            return
        lines = [
            f"{DECOR[i]['emoji']} **{DECOR[i]['name']}** ×{n} "
            f"({SLOT_LABEL[DECOR[i]['slot']]})"
            for i, n in sorted(stash.items(), key=lambda kv: DECOR[kv[0]]["name"])
        ]
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Room stash",
                description="\n".join(lines),
                color=COZY,
            ),
            ephemeral=True,
        )

    @room.command(name="place", description="Place decor (or a frog card on the shelf).")
    @app_commands.describe(
        item="Decor from your stash, or a frog card id/name for the shelf",
        slot="Where to put it (frog cards → shelf)",
    )
    @app_commands.choices(
        slot=[app_commands.Choice(name=SLOT_LABEL[s], value=s) for s in SLOTS]
    )
    async def room_place(
        self,
        interaction: discord.Interaction,
        item: str,
        slot: app_commands.Choice[str],
    ):
        if not await self._require_channel(interaction):
            return
        rec = self.record(interaction.user.id)
        slot_key = slot.value
        key = item.strip().lower().replace(" ", "_")

        # Decor path
        if key in DECOR or any(
            DECOR[d]["name"].lower() == item.strip().lower() for d in DECOR
        ):
            if key not in DECOR:
                key = next(
                    d
                    for d in DECOR
                    if DECOR[d]["name"].lower() == item.strip().lower()
                )
            d = DECOR[key]
            if d["slot"] != slot_key:
                await interaction.response.send_message(
                    f"**{d['name']}** belongs on the **{SLOT_LABEL[d['slot']]}**, "
                    f"not the {SLOT_LABEL[slot_key]}.",
                    ephemeral=True,
                )
                return
            stash = rec.setdefault("stash", {})
            if int(stash.get(key, 0)) < 1:
                await interaction.response.send_message(
                    "You don't have that in your stash. `/room find` or check `/room stash`.",
                    ephemeral=True,
                )
                return
            # return previous decor to stash if any
            prev = (rec.get("slots") or {}).get(slot_key)
            if prev and prev.get("kind") == "decor" and prev.get("id") in DECOR:
                pid = prev["id"]
                stash[pid] = int(stash.get(pid, 0)) + 1
            stash[key] = int(stash[key]) - 1
            if stash[key] <= 0:
                stash.pop(key, None)
            rec.setdefault("slots", {})[slot_key] = {"kind": "decor", "id": key}
            self.save()
            await interaction.response.send_message(
                embed=self.embed_room(interaction.user, rec),
                ephemeral=True,
            )
            return

        # Frog card path — shelf only
        from cogs.frogs import CARDS

        frog_id = key if key in CARDS else None
        if not frog_id:
            for cid, meta in CARDS.items():
                if meta["name"].lower() == item.strip().lower():
                    frog_id = cid
                    break
        if not frog_id:
            await interaction.response.send_message(
                "Unknown item. Use a decor name from `/room stash`, "
                "or a frog card from `/frog album` for the shelf.",
                ephemeral=True,
            )
            return
        if slot_key != "shelf":
            await interaction.response.send_message(
                "Frog cards only display on the **Shelf**.", ephemeral=True
            )
            return
        frogs = self.bot.get_cog("Frogs")
        if not frogs or frogs.counts(interaction.user.id).get(frog_id, 0) < 1:
            await interaction.response.send_message(
                "You don't have that frog card.", ephemeral=True
            )
            return
        # previous shelf decor returns to stash; previous frog just swaps (cards stay in album)
        prev = (rec.get("slots") or {}).get("shelf")
        if prev and prev.get("kind") == "decor" and prev.get("id") in DECOR:
            pid = prev["id"]
            stash = rec.setdefault("stash", {})
            stash[pid] = int(stash.get(pid, 0)) + 1
        rec.setdefault("slots", {})["shelf"] = {"kind": "frog", "id": frog_id}
        self.save()
        await interaction.response.send_message(
            embed=self.embed_room(interaction.user, rec),
            ephemeral=True,
        )

    @room_place.autocomplete("item")
    async def place_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        rec = self.record(interaction.user.id)
        needle = (current or "").lower()
        out: list[app_commands.Choice[str]] = []
        for did, n in (rec.get("stash") or {}).items():
            if int(n) < 1 or did not in DECOR:
                continue
            name = DECOR[did]["name"]
            if needle and needle not in name.lower():
                continue
            out.append(app_commands.Choice(name=f"{name} (decor)", value=did))
        frogs = self.bot.get_cog("Frogs")
        if frogs:
            from cogs.frogs import CARDS
            for cid, n in frogs.counts(interaction.user.id).items():
                if n < 1:
                    continue
                name = CARDS[cid]["name"]
                if needle and needle not in name.lower():
                    continue
                out.append(app_commands.Choice(name=f"{name} (frog → shelf)", value=cid))
        return out[:25]

    @room.command(name="clear", description="Clear a furniture slot (decor returns to stash).")
    @app_commands.choices(
        slot=[app_commands.Choice(name=SLOT_LABEL[s], value=s) for s in SLOTS]
    )
    async def room_clear(
        self, interaction: discord.Interaction, slot: app_commands.Choice[str]
    ):
        if not await self._require_channel(interaction):
            return
        rec = self.record(interaction.user.id)
        slot_key = slot.value
        prev = (rec.get("slots") or {}).pop(slot_key, None)
        if not prev:
            await interaction.response.send_message(
                f"The {SLOT_LABEL[slot_key]} is already empty.", ephemeral=True
            )
            return
        if prev.get("kind") == "decor" and prev.get("id") in DECOR:
            stash = rec.setdefault("stash", {})
            pid = prev["id"]
            stash[pid] = int(stash.get(pid, 0)) + 1
        self.save()
        await interaction.response.send_message(
            embed=self.embed_room(interaction.user, rec),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Dorm(bot))
