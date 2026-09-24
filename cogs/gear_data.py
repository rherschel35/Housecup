"""
The 40 pieces of gear. 26 are crafted from satchel materials (/craft);
14 are earned, never crafted, and are handed out automatically when
someone reaches the achievement. Only earned pieces carry a perk, and no
perk touches duels or house points.

Each piece: name, slot, rarity, desc (flavour), and either
    recipe  - {item_id: count} taken from the satchel
    earn    - which achievement awards it (see adornments.EARN_CHECKS)
plus perk (a key the other cogs ask about, or None) and visual (how the
Mirror draws it - see mirror_art).
"""

SLOTS = {
    "necklace": ("Necklace", "📿"),
    "bracelet": ("Bracelet", "⛓️"),
    "ring":     ("Ring", "💍"),
    "talisman": ("Talisman", "🧿"),
}

RARITY_ORDER = ["common", "uncommon", "rare", "legendary"]
RARITY_LABEL = {"common": "Common", "uncommon": "Uncommon", "rare": "Rare", "legendary": "Legendary"}

PLACE_OF = {  # crafted pieces are themed to the place their materials come from
    "garden": "🌿 Garden", "library": "📚 Library", "dungeons": "🕯️ Dungeons",
    "forbidden_woods": "🌲 Forbidden Woods", "observatory": "🔭 Observatory",
}

GEAR = {
    # ================================================================ crafted
    # ---------------------------------------------------------------- garden
    "moonbloom_locket": {
        "name": "Moonbloom Locket", "slot": "necklace", "rarity": "uncommon", "place": "garden",
        "desc": "A silver locket with a moonbloom petal pressed inside. It glows when you're not looking.",
        "recipe": {"moonbloom_petal": 1, "moonstone_chip": 1, "sprite_thread": 1},
        "visual": {"chain": "silver", "pendant": "locket", "color": "#F2B8D0"},
    },
    "snail_pearl_drop": {
        "name": "Snail-Pearl Drop", "slot": "necklace", "rarity": "uncommon", "place": "garden",
        "desc": "One perfect pearl on a thread of sprite silk. The snail wants it back.",
        "recipe": {"snail_pearl": 1, "sprite_thread": 1},
        "visual": {"chain": "silver", "pendant": "drop", "color": "#F1EADC"},
    },
    "dewmint_vine": {
        "name": "Dewmint Vine Bracelet", "slot": "bracelet", "rarity": "common", "place": "garden",
        "desc": "Living vine, braided round the wrist. Always a little damp.",
        "recipe": {"dewmint": 1, "silverleaf": 1, "wandering_seed": 1},
        "visual": {"band": "vine", "style": "vine", "color": "#9ED66B"},
    },
    "sunbell_ring": {
        "name": "Sunbell Ring", "slot": "ring", "rarity": "common", "place": "garden",
        "desc": "A gold band set with a crystal that hums one note at noon.",
        "recipe": {"sunbell": 1, "cracked_crystal": 1},
        "visual": {"band": "gold", "color": "#F2D16B"},
    },
    "owl_feather_charm": {
        "name": "Owl-Feather Charm", "slot": "talisman", "rarity": "common", "place": "garden",
        "desc": "A soft owl feather stitched to a lost button. Very quiet. Very watchful.",
        "recipe": {"owl_feather": 1, "moth_dust": 1, "lost_button": 1},
        "visual": {"shape": "feather", "color": "#C9B08A", "accent": "gold"},
    },
    "fairy_ring_pouch": {
        "name": "Fairy-Ring Pouch", "slot": "talisman", "rarity": "common", "place": "garden",
        "desc": "A bark pouch holding one fairy-ring cap and a glowshroom. It glows through the seams.",
        "recipe": {"fairy_ring_cap": 1, "glowshroom": 1, "tree_bark": 1},
        "visual": {"shape": "pouch", "color": "#8A6A48", "accent": "#9EF0C0"},
    },
    # --------------------------------------------------------------- library
    "pressed_violet_locket": {
        "name": "Pressed-Violet Locket", "slot": "necklace", "rarity": "common", "place": "library",
        "desc": "A violet pressed between the pages of a book nobody returned.",
        "recipe": {"pressed_violet": 1, "overdue_slip": 1},
        "visual": {"chain": "gold", "pendant": "locket", "color": "#8E6BC7"},
    },
    "inkmoth_cuff": {
        "name": "Inkmoth Cuff", "slot": "bracelet", "rarity": "uncommon", "place": "library",
        "desc": "A dark cuff with an inkmoth wing set in glass. Leaves tiny smudges on everything.",
        "recipe": {"inkmoth_wing": 1, "dust_of_forgotten_words": 1},
        "visual": {"band": "#2B2D52", "style": "cuff", "color": "#7F8BD9"},
    },
    "scribes_thread": {
        "name": "Scribe's Thread", "slot": "bracelet", "rarity": "common", "place": "library",
        "desc": "Red binding thread tied three times for luck, and once more for deadlines.",
        "recipe": {"overdue_slip": 1, "marginalia_scrap": 1},
        "visual": {"band": "#A8322E", "style": "band"},
    },
    "inkwell_signet": {
        "name": "Inkwell Signet", "slot": "ring", "rarity": "uncommon", "place": "library",
        "desc": "A silver signet with a drop of ink that never dries. Press it to any page and it signs your name.",
        "recipe": {"self_inking_nib": 1, "marginalia_scrap": 1},
        "visual": {"band": "silver", "color": "#1E2140"},
    },
    "bookmark_tassel": {
        "name": "Bookmark Tassel", "slot": "talisman", "rarity": "common", "place": "library",
        "desc": "A silk tassel that always hangs at the page you stopped on.",
        "recipe": {"marginalia_scrap": 1, "overdue_slip": 1, "dust_of_forgotten_words": 1},
        "visual": {"shape": "tassel", "color": "#7A1F2E", "accent": "gold"},
    },
    # -------------------------------------------------------------- dungeons
    "dead_coin_pendant": {
        "name": "Dead-Coin Pendant", "slot": "necklace", "rarity": "uncommon", "place": "dungeons",
        "desc": "A coin from a kingdom that doesn't exist any more, on a chain that used to hold a prisoner.",
        "recipe": {"dead_currency_coin": 1, "manacle_link": 1},
        "visual": {"chain": "iron", "pendant": "coin", "color": "#B8A14A"},
    },
    "manacle_bangle": {
        "name": "Manacle Bangle", "slot": "bracelet", "rarity": "common", "place": "dungeons",
        "desc": "One link of an old manacle, hammered into a bangle. Much comfier than its first job.",
        "recipe": {"manacle_link": 1, "cellar_moss": 1},
        "visual": {"band": "iron", "style": "cuff", "color": "#5A7A4A"},
    },
    "rusted_key_ring": {
        "name": "Rusted Key Ring", "slot": "ring", "rarity": "common", "place": "dungeons",
        "desc": "The bow of a rusted key, bent into a ring. It still fits one lock somewhere below.",
        "recipe": {"rusted_key": 1, "manacle_link": 1},
        "visual": {"band": "#8A5A2A"},
    },
    "cellar_moss_band": {
        "name": "Cellar-Moss Band", "slot": "ring", "rarity": "common", "place": "dungeons",
        "desc": "An iron band with a curl of moss that stays green in the dark.",
        "recipe": {"cellar_moss": 1, "rusted_key": 1},
        "visual": {"band": "iron", "color": "#5E9A4A"},
    },
    "shadow_chalk_sigil": {
        "name": "Shadow-Chalk Sigil", "slot": "talisman", "rarity": "uncommon", "place": "dungeons",
        "desc": "A disc marked in chalk that only shows up in shadow. Nobody remembers what it means.",
        "recipe": {"chalk_of_shadows": 1, "rusted_key": 1},
        "visual": {"shape": "sigil", "color": "#2A2530", "accent": "#CFCFE8"},
    },
    # ------------------------------------------------------- forbidden woods
    "fang_cord": {
        "name": "Fang Cord", "slot": "necklace", "rarity": "uncommon", "place": "forbidden_woods",
        "desc": "A shed fang on a leather cord. Whatever lost it is still out there.",
        "recipe": {"shed_fang": 1, "raven_feather": 1},
        "visual": {"chain": "cord", "pendant": "fang", "color": "#E8DDC2"},
    },
    "blackened_acorn_beads": {
        "name": "Blackened Acorn Beads", "slot": "bracelet", "rarity": "common", "place": "forbidden_woods",
        "desc": "Acorns burnt black and strung with chips of standing stone. They click when you walk.",
        "recipe": {"blackened_acorn": 1, "standing_stone_chip": 1},
        "visual": {"band": "#2A2530", "style": "beads", "color": "#3A2B2A"},
    },
    "standing_stone_ring": {
        "name": "Standing-Stone Ring", "slot": "ring", "rarity": "common", "place": "forbidden_woods",
        "desc": "A grey stone ring, cold even in summer. The woods recognise it.",
        "recipe": {"standing_stone_chip": 1, "raven_feather": 1},
        "visual": {"band": "#8C8C92", "color": "#6A6A72"},
    },
    "root_charm_knot": {
        "name": "Root-Charm Knot", "slot": "talisman", "rarity": "uncommon", "place": "forbidden_woods",
        "desc": "A root tied in a knot that can't be untied. It keeps you from getting lost. Mostly.",
        "recipe": {"root_charm": 1, "blackened_acorn": 1},
        "visual": {"shape": "knot", "color": "#6E4B2E"},
    },
    "raven_quill_token": {
        "name": "Raven-Quill Token", "slot": "talisman", "rarity": "common", "place": "forbidden_woods",
        "desc": "Two raven feathers bound to a stone chip. Ravens nod at you now.",
        "recipe": {"raven_feather": 2, "standing_stone_chip": 1},
        "visual": {"shape": "feather", "color": "#23202B", "accent": "silver"},
    },
    # ----------------------------------------------------------- observatory
    "meteorite_pendant": {
        "name": "Meteorite Pendant", "slot": "necklace", "rarity": "uncommon", "place": "observatory",
        "desc": "A sliver of fallen star set in brass. Warm to the touch, always.",
        "recipe": {"meteorite_sliver": 1, "orrery_gear": 1},
        "visual": {"chain": "brass", "pendant": "star", "color": "#3B4270"},
    },
    "star_chart_locket": {
        "name": "Star-Chart Locket", "slot": "necklace", "rarity": "common", "place": "observatory",
        "desc": "A locket lined with a scrap of star chart. The stars inside move with the real ones.",
        "recipe": {"star_chart_fragment": 2, "orrery_gear": 1},
        "visual": {"chain": "brass", "pendant": "locket", "color": "#2C3A6B"},
    },
    "orrery_gear_bracelet": {
        "name": "Orrery-Gear Bracelet", "slot": "bracelet", "rarity": "common", "place": "observatory",
        "desc": "Tiny brass gears that turn, very slowly, with the planets.",
        "recipe": {"orrery_gear": 1, "broken_compass": 1},
        "visual": {"band": "brass", "style": "chain", "color": "#C49C4C"},
    },
    "moonglass_ring": {
        "name": "Moonglass Ring", "slot": "ring", "rarity": "uncommon", "place": "observatory",
        "desc": "A clear stone cut from a telescope lens. Look through it and the moon looks back.",
        "recipe": {"moonglass_lens": 1, "star_chart_fragment": 1},
        "visual": {"band": "silver", "color": "#BFE3FF"},
    },
    "compass_charm": {
        "name": "Compass Charm", "slot": "talisman", "rarity": "common", "place": "observatory",
        "desc": "A broken compass that points at whatever you're looking for. It's rarely right.",
        "recipe": {"broken_compass": 1, "star_chart_fragment": 1},
        "visual": {"shape": "compass", "color": "#B03030", "accent": "brass"},
    },

    # ================================================================ earned
    # --------------------------------------------------------------- necklaces
    "legends_tooth": {
        "name": "Legend's Tooth Necklace", "slot": "necklace", "rarity": "legendary",
        "desc": "A tooth given freely by a legendary beast. Nobody takes one; it's offered.",
        "earn": "legendary_beast", "earn_text": "Befriend a legendary beast.",
        "perk": "legend_aura", "perk_text": "A golden aura in your Mirror, and a legendary flourish on /summon.",
        "visual": {"chain": "gold", "pendant": "tooth", "color": "#F2E8CC", "glow": True},
    },
    "nightwatch_pendant": {
        "name": "Nightwatch Pendant", "slot": "necklace", "rarity": "rare",
        "desc": "A crescent of moonstone that stirs when something wild wakes in the dark.",
        "earn": "night_beasts", "earn_text": "Befriend 3 different night-only beasts.",
        "perk": "nightwatch", "perk_text": "A quiet heads-up (a DM) when a night beast appears. Turn it off with /nightwatch.",
        "visual": {"chain": "silver", "pendant": "crescent", "color": "#CFE3FF", "glow": True},
    },
    "magpies_eye": {
        "name": "Magpie's Eye Necklace", "slot": "necklace", "rarity": "rare",
        "desc": "Every Nibbler in Velmora chipped in something shiny. It notices things.",
        "earn": "all_nibblers", "earn_text": "Befriend all five Nibblers.",
        "perk": "double_find", "perk_text": "Sometimes you find two of something instead of one.",
        "visual": {"chain": "gold", "pendant": "eye", "color": "#3FA0E0"},
    },
    # --------------------------------------------------------------- bracelets
    "house_cup_bracelet": {
        "name": "House Cup Bracelet", "slot": "bracelet", "rarity": "legendary",
        "desc": "Gold links in your house colours, for those who helped lift the Cup.",
        "earn": "house_cup", "earn_text": "Help your house win the House Cup.",
        "perk": "cheer", "perk_text": "Gold trim on your Mirror banner, and /cheer for a house celebration.",
        "visual": {"band": "gold", "style": "chain", "color": "house", "charm": "star"},
    },
    "kindred_bracelet": {
        "name": "Kindred Bracelet", "slot": "bracelet", "rarity": "rare",
        "desc": "Braided from your own thread and a strand of your familiar's fur, feather or scale.",
        "earn": "familiar_devoted", "earn_text": "Raise your familiar to Devoted.",
        "perk": "kindred", "perk_text": "Your familiar's friendship grows faster.",
        "visual": {"band": "#8E5A3A", "style": "beads", "color": "#D8742E"},
    },
    "lingering_charm": {
        "name": "Lingering Charm", "slot": "bracelet", "rarity": "rare",
        "desc": "A little silver hourglass that makes wild things hesitate a moment longer.",
        "earn": "beasts_20", "earn_text": "Befriend 20 different beasts.",
        "perk": "linger", "perk_text": "After a beast leaves, you still have 2 minutes to /approach it.",
        "visual": {"band": "silver", "style": "chain", "color": "#CDD3DC", "charm": "drop"},
    },
    "champions_band": {
        "name": "Champion's Band", "slot": "bracelet", "rarity": "legendary",
        "desc": "A gold laurel for a Duelist of the Week. It doesn't help you duel. It just looks like it does.",
        "earn": "duelist_of_week", "earn_text": "Be crowned Duelist of the Week.",
        "perk": None, "perk_text": "For show only.",
        "visual": {"band": "gold", "style": "laurel"},
    },
    # ------------------------------------------------------------------ rings
    "seekers_ring": {
        "name": "Seeker's Ring", "slot": "ring", "rarity": "rare",
        "desc": "A green stone that warms when something is hidden nearby.",
        "earn": "secrets_10", "earn_text": "Find 10 secrets anywhere in Velmora.",
        "perk": "seeker", "perk_text": "A better chance of finding something when you /explore.",
        "visual": {"band": "gold", "color": "#4FC26A"},
    },
    "trackers_band": {
        "name": "Tracker's Band", "slot": "ring", "rarity": "rare",
        "desc": "A band scratched with the tracks of ten beasts. It hums when the next one draws near.",
        "earn": "beasts_10", "earn_text": "Befriend 10 different beasts.",
        "perk": "tracker", "perk_text": "Your /bestiary shows roughly when the next beast will appear.",
        "visual": {"band": "bronze", "color": "#C9A24A"},
    },
    "duelists_signet": {
        "name": "Duelist's Signet", "slot": "ring", "rarity": "legendary",
        "desc": "Crossed wands on a red stone. Your wand throws brighter sparks in the Mirror. That's all it does.",
        "earn": "master_duelist", "earn_text": "Reach Master of the Circle in duels.",
        "perk": None, "perk_text": "For show only: brighter wand sparks in your Mirror.",
        "visual": {"band": "gold", "color": "#C0392B"},
    },
    "familiars_bell": {
        "name": "Familiar's Bell Ring", "slot": "ring", "rarity": "rare",
        "desc": "A ring with a tiny gold bell. Your familiar comes running at the sound.",
        "earn": "familiar_inseparable", "earn_text": "Raise your familiar to Inseparable.",
        "perk": "bell", "perk_text": "Your familiar does extra things when you feed, pet or play with it.",
        "visual": {"band": "gold", "charm": "bell"},
    },
    # -------------------------------------------------------------- talismans
    "keepers_talisman": {
        "name": "Keeper's Talisman", "slot": "talisman", "rarity": "legendary",
        "desc": "An old iron key that fits no lock. It knows where the secrets are, if not what they are.",
        "earn": "place_secrets", "earn_text": "Find every secret in one place.",
        "perk": "secret_count", "perk_text": "/secrets shows how many you haven't found yet in each place.",
        "visual": {"shape": "key", "color": "#8A5A2A"},
    },
    "beastcallers_whistle": {
        "name": "Beastcaller's Whistle", "slot": "talisman", "rarity": "rare",
        "desc": "A bone whistle no human can hear. Every beast in Velmora can.",
        "earn": "beasts_35", "earn_text": "Befriend 35 different beasts.",
        "perk": "whistle", "perk_text": "Once a week, /whistle makes the next beast appear right now (anyone can race you for it).",
        "visual": {"shape": "whistle", "color": "#E8DDC2", "accent": "#E8DDC2"},
    },
    "beastmasters_totem": {
        "name": "Beastmaster's Totem", "slot": "talisman", "rarity": "legendary",
        "desc": "Carved with all seventy. Only one person at a time ever seems to hold it.",
        "earn": "beasts_all", "earn_text": "Befriend all 70 beasts.",
        "perk": "totem", "perk_text": "A befriended beast stands beside you in the Mirror, and /summon can call two at once.",
        "visual": {"shape": "totem", "color": "#5A3A22", "accent": "gold"},
    },
}


def by_slot(slot: str) -> list[str]:
    order = {r: i for i, r in enumerate(RARITY_ORDER)}
    return sorted((k for k, g in GEAR.items() if g["slot"] == slot),
                  key=lambda k: (order[GEAR[k]["rarity"]], GEAR[k]["name"]))


def crafted() -> list[str]:
    return [k for k, g in GEAR.items() if "recipe" in g]


def earned() -> list[str]:
    return [k for k, g in GEAR.items() if "earn" in g]
