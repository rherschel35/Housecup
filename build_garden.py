"""
Builds data/world/garden.json (every piece of Garden writing, with its tags)
and data/world/items.json (every item in Velmora - shared by all places).

Run:  python tools/build_garden.py

Edit the lists below (or the JSON directly) to change what the Garden can
say. Rules the engine enforces from these tags:

  zones  - where a piece can appear. A creature/object/discovery only shows
           up at a location sharing at least one zone. "any" fits anywhere.
  time   - "any", "day" or "night".
  mood   - atmospheres set the mood (calm, bright, eerie, ominous); creatures
           and outcomes list the moods they fit.
  kind   - creatures have a kind (flier, crawler, swimmer, hopper, walker,
           unknown); actions list which kinds can do them.
  events - only appears while one of these events is active.
  not_events - never appears while one of these events is active.
  tone   - outcomes are chosen by how the Garden feels about the visitor
           (beloved, friendly, neutral, wary, hostile).
"""

import json
from pathlib import Path

WORLD = Path(__file__).resolve().parent.parent / "data" / "world"
OUT = WORLD / "garden.json"
ITEMS_OUT = WORLD / "items.json"


# words that make a discovery "a plant" - those offer Tend / Uproot / Leave
PLANT_WORDS = ("flower", "herbs", "blooming", "seed has sprouted", "garden has been planted",
               "roses have", "mushrooms", "every leaf", "moss has grown")

# ------------------------------------------------------------------ houses
# Creatures a house meets more often (x) or never (0).
HOUSE_WEIGHTS = {
    "wolf_pup": {"thornmere": 5},
    "fox": {"thornmere": 2},
    "deer": {"veyren": 5},
    "hedgehog": {"veyren": 2},
    "elephant_shrew": {"vashara": 5},
    "snail": {"vashara": 2},
    "moon_hare": {"moonveil": 4},
    "vine_thing": {"moonveil": 3},
    "owl": {"caldrin": 3},
    "tiny_dragon": {"caldrin": 3},
}

# Discoveries only one house can come across.
HOUSE_DISCOVERIES = [
    ("a ring of wolf tracks circles the path, then heads for the brambles - and there's a thorn crown left in the middle",
     ["wild", "grove", "roses"], "any", ["thornsalt"], None, {"requires": {"houses": ["thornmere"]}, "flags": ["saw_wolf_ring"]}),
    ("a young stag has left its shed antler velvet on a low branch, as if hung there for someone",
     ["meadow", "grove", "wild"], "any", ["hare_whisker"], None, {"requires": {"houses": ["veyren"]}, "flags": ["saw_stag_gift"]}),
    ("a bed of healing herbs has grown in the shape of an elephant, trunk raised",
     ["herbs", "glass", "meadow"], "any", ["dewmint", "silverleaf"], None, {"requires": {"houses": ["vashara"]}, "flags": ["saw_herb_elephant"]}),
    ("the constellations are reflected in a puddle that shouldn't be there - and one of them is moving",
     ["water", "stone", "meadow"], "night", ["moonstone_chip", "starseed"], None, {"requires": {"houses": ["caldrin"]}, "flags": ["saw_moving_star"]}),
    ("a full moon is reflected in the water, even though tonight's moon is a sliver",
     ["water", "meadow"], "night", ["moonbloom_petal", "moonstone_chip"], None, {"requires": {"houses": ["moonveil"]}, "flags": ["saw_false_moon"]}),
    # reputation-gated
    ("the Garden has laid out a path of white petals just for you",
     ["any"], "any", ["ghost_orchid", "moonbloom_petal"], None, {"requires": {"rep_min": 15}}),
    ("thorns have grown across the path you were on a moment ago",
     ["any"], "any", ["thornsalt"], None, {"requires": {"rep_max": -8}}),
    # needs an item
    ("the map scrap in your satchel is warm - and the path in front of you matches it exactly",
     ["wild", "grove", "stone"], "any", ["starseed", "hollow_morel"], None, {"requires": {"items": ["map_scrap"]}, "flags": ["followed_map"]}),
    # academy event
    ("banners in every House's colours have been tied through the hedges - the Garden knows the tournament is on",
     ["any"], "any", ["sprite_thread"], None, {"requires": {"academy": ["tournament"]}}),
]

# Things students can /use while in the Garden.
USES = [
    {"id": "map_path", "item": "map_scrap", "consume": True, "reward": "starseed", "rep": 1, "points": 1,
     "flags": ["walked_map_path"],
     "response": "🗺️ You unfold the scrap and follow it - left at the statue, through the ferns, three steps past the dry well - to a patch of soil nobody has touched in a century. Something is waiting in it."},
    {"id": "key_garden", "item": "strange_key", "consume": False,
     "response": "🗝️ You try the key in the old gate, the glasshouse door, the gardener's shed. It fits none of them. Whatever it opens, it isn't here."},
    {"id": "pool_vial", "item": "pool_water", "consume": True, "reward": "prophecy_leaf", "rep": 1,
     "response": "💧 You pour the pool water back where it came from. For a moment the surface shows words - and then a leaf drifts up, with those same words in its veins."},
    {"id": "seed_plant", "item": "starseed", "consume": True, "rep": 2, "flags": ["planted_starseed"],
     "response": "🌟 You plant the starseed. Nothing happens. Then, very slowly, a single silver shoot turns - not toward the sun, but toward where the stars will be tonight."},
    {"id": "acorn_tree", "item": "sleeping_acorn", "consume": True, "reward": "tree_bark", "rep": 1,
     "response": "🌰 You tuck the snoring acorn among the Ancient Tree's roots. The snoring stops. The Tree creaks, once, like a sigh of relief, and drops something at your feet."},
]

# ------------------------------------------------------------------ items
# rarity: common, uncommon, rare, very_rare, legendary
ITEMS = [
    # herbs
    ("moonbloom_petal", "Moonbloom Petal", "🌸", "uncommon", "Glows faintly when nobody is looking at it."),
    ("silverleaf", "Silverleaf", "🌿", "common", "Leaves that ring like tiny bells when shaken."),
    ("dewmint", "Dewmint", "🌿", "common", "Always damp, even in a drought."),
    ("whisperwort", "Whisperwort", "🌿", "uncommon", "Hold it to your ear and it repeats the last thing said nearby."),
    ("starthistle", "Starthistle", "✨", "rare", "Its spines point north at night."),
    ("hushroot", "Hushroot", "🌱", "uncommon", "Muffles every sound around it for a heartbeat."),
    ("emberleaf", "Emberleaf", "🍂", "common", "Warm to the touch, like it remembers summer."),
    ("thornsalt", "Thornsalt", "🧂", "uncommon", "Crystals scraped from the old rose thorns."),
    # mushrooms
    ("fairy_ring_cap", "Fairy-Ring Cap", "🍄", "common", "Grows in circles. Never step inside the circle."),
    ("glowshroom", "Glowshroom", "🍄", "uncommon", "A soft blue lantern that sprouted from the dark."),
    ("inkcap", "Weeping Inkcap", "🍄", "uncommon", "Drips ink that writes on its own if you leave it."),
    ("hollow_morel", "Hollow Morel", "🍄", "rare", "Blow through it and it plays one note, always the same one."),
    # crystals
    ("cracked_crystal", "Cracked Crystal", "💎", "common", "Something inside it moved once. Probably."),
    ("moonstone_chip", "Moonstone Chip", "💎", "uncommon", "Cold at noon, warm at midnight."),
    ("dew_diamond", "Dew Diamond", "💎", "rare", "A single drop of morning dew that refused to fall."),
    ("tree_amber", "Heart-Amber", "🟠", "very_rare", "Sap from the Ancient Tree. There's something tiny caught inside."),
    # flowers
    ("black_rose", "Black Rose", "🥀", "rare", "Only blooms when every other flower goes dark."),
    ("sunbell", "Sunbell", "🌼", "common", "Rings once at sunrise. Loudly."),
    ("veil_lily", "Veil Lily", "🌷", "uncommon", "Its petals are see-through if you squint."),
    ("ghost_orchid", "Ghost Orchid", "🤍", "very_rare", "Appears in your hand. You don't remember picking it."),
    # seeds
    ("wandering_seed", "Wandering Seed", "🌱", "common", "Never where you left it."),
    ("starseed", "Starseed", "🌟", "rare", "Planted, it would grow toward the stars instead of the sun."),
    ("sleeping_acorn", "Sleeping Acorn", "🌰", "uncommon", "Snores, very softly."),
    # feathers & creature materials
    ("moth_dust", "Moth Dust", "🦋", "common", "Silver powder from a moon moth's wings."),
    ("sprite_thread", "Sprite Thread", "🧵", "uncommon", "Finer than hair, stronger than rope."),
    ("dragon_scale", "Tiny Dragon Scale", "🐉", "rare", "Warm, and slightly smug."),
    ("frog_gold", "Golden Frog Scale", "🐸", "very_rare", "Worth a fortune to the right frog."),
    ("owl_feather", "Barn-Owl Feather", "🪶", "common", "Makes no sound at all when it falls."),
    ("heron_plume", "Grey Heron Plume", "🪶", "uncommon", "Points toward still water."),
    ("phoenix_down", "Phoenix Down", "🔥", "legendary", "Nobody has seen a phoenix in the Garden. Nobody."),
    ("hare_whisker", "Moon-Hare Whisker", "🐇", "uncommon", "Twitches when rain is coming."),
    ("snail_pearl", "Snail Pearl", "⚪", "rare", "The snail was very reluctant to give this up."),
    ("unknown_husk", "Unidentified Husk", "👀", "rare", "Whatever wore this is bigger now."),
    # oddities
    ("strange_key", "Strange Key", "🗝️", "very_rare", "It fits nothing. Yet."),
    ("pressed_letter", "Pressed Letter", "📜", "uncommon", "A love letter pressed flat in an old book. The names are smudged."),
    ("pool_water", "Vial of Pool Water", "💧", "uncommon", "Your reflection in it is a second late."),
    ("tree_bark", "Heartwood Bark", "🪵", "uncommon", "Still has a pulse, if you listen."),
    ("lost_button", "Somebody's Button", "🔘", "common", "Brass. Engraved with a V."),
    ("map_scrap", "Map Scrap", "🗺️", "rare", "Shows a path in the Garden nobody has walked."),
    ("prophecy_leaf", "Prophecy Leaf", "🍃", "rare", "There are words in the veins. They change."),
]

# ------------------------------------------------------------------ locations
# (id, "at" phrase, zones, time, drops)
LOCATIONS = [
    ("ancient_tree", "beneath the Ancient Tree", ["tree", "grove"], "any", ["tree_bark", "sleeping_acorn", "tree_amber"]),
    ("tree_roots", "among the knotted roots of the Ancient Tree", ["tree", "stone"], "any", ["tree_bark", "fairy_ring_cap"]),
    ("reflection_pool", "at the edge of the Reflection Pool", ["water"], "any", ["pool_water", "dew_diamond"]),
    ("pool_steps", "on the mossy steps down to the Reflection Pool", ["water", "stone"], "any", ["pool_water", "snail_pearl"]),
    ("moonbloom_meadow", "in the Moonbloom Meadow", ["meadow", "flowers"], "any", ["moonbloom_petal", "sunbell"]),
    ("meadow_night", "in the Moonbloom Meadow, where every bloom is open", ["meadow", "flowers"], "night", ["moonbloom_petal", "moonstone_chip"]),
    ("herb_terraces", "along the Herb Terraces", ["herbs"], "any", ["silverleaf", "dewmint", "emberleaf"]),
    ("lower_terrace", "on the lowest of the Herb Terraces", ["herbs", "stone"], "any", ["hushroot", "whisperwort"]),
    ("rose_walk", "halfway down the Rose Walk", ["roses", "flowers"], "any", ["thornsalt", "black_rose"]),
    ("rose_arch", "under the rose arch at the Garden's gate", ["roses", "stone"], "any", ["thornsalt", "lost_button"]),
    ("glasshouse", "inside the old glasshouse", ["glass", "herbs"], "any", ["veil_lily", "glowshroom"]),
    ("glasshouse_night", "inside the glasshouse, lit only by the plants", ["glass", "herbs"], "night", ["glowshroom", "starthistle"]),
    ("reading_alcove", "in the Reading Alcove", ["alcove", "stone"], "any", ["pressed_letter", "prophecy_leaf"]),
    ("alcove_bench", "on the crooked bench outside the Reading Alcove", ["alcove", "stone"], "any", ["lost_button", "map_scrap"]),
    ("mushroom_hollow", "down in the Mushroom Hollow", ["wild", "grove"], "any", ["fairy_ring_cap", "inkcap", "hollow_morel"]),
    ("fern_tunnel", "inside the fern tunnel", ["wild"], "any", ["wandering_seed", "sprite_thread"]),
    ("willow_bank", "under the weeping willow by the stream", ["water", "grove"], "any", ["heron_plume", "dewmint"]),
    ("stepping_stones", "on the stepping stones across the stream", ["water", "stone"], "any", ["snail_pearl", "pool_water"]),
    ("sundial", "beside the broken sundial", ["stone", "meadow"], "day", ["sunbell", "cracked_crystal"]),
    ("moon_dial", "beside the broken sundial, which is somehow telling moon-time", ["stone", "meadow"], "night", ["moonstone_chip", "starseed"]),
    ("crystal_bed", "at the crystal beds behind the glasshouse", ["stone", "glass"], "any", ["cracked_crystal", "moonstone_chip", "dew_diamond"]),
    ("owl_oak", "under the owl oak", ["tree", "wild"], "any", ["owl_feather", "sleeping_acorn"]),
    ("bramble_edge", "at the edge of the brambles, where the paths run out", ["wild", "roses"], "any", ["thornsalt", "unknown_husk"]),
    ("statue_garden", "among the statues nobody remembers commissioning", ["stone"], "any", ["cracked_crystal", "lost_button"]),
    ("lantern_path", "along the lantern path", ["meadow", "stone"], "night", ["moth_dust", "moonbloom_petal"]),
    ("sprite_hedge", "by the sprite hedge", ["wild", "flowers"], "any", ["sprite_thread", "veil_lily"]),
    ("sunken_garden", "in the sunken garden", ["stone", "flowers", "water"], "any", ["veil_lily", "pool_water"]),
    ("orchard", "in the little orchard", ["tree", "meadow"], "day", ["sleeping_acorn", "wandering_seed"]),
    ("well", "at the dry well that isn't always dry", ["stone", "water"], "any", ["pool_water", "unknown_husk"]),
    ("gardener_shed", "outside the old gardener's shed", ["herbs", "stone"], "any", ["wandering_seed", "lost_button", "map_scrap"]),
    # event-only
    ("rose_door", "at the door behind the roses", ["roses", "stone"], "any", ["strange_key", "map_scrap"], ["door"]),
    ("new_path", "on a path you're certain wasn't there yesterday", ["wild", "grove"], "any", ["map_scrap", "starseed", "ghost_orchid"], ["path"]),
]

# ------------------------------------------------------------------ atmospheres
# (text, mood, time, zones or None, events, not_events)
ATMOS = [
    ("The air smells of rain that hasn't fallen yet.", "calm", "any", None),
    ("Everything is very still, as if the Garden is holding its breath.", "eerie", "any", None),
    ("Bees drone lazily from bloom to bloom.", "bright", "day", ["flowers", "meadow", "herbs"]),
    ("Sunlight falls in long gold stripes through the leaves.", "bright", "day", None),
    ("A warm breeze carries the smell of crushed mint.", "calm", "day", ["herbs"]),
    ("Somewhere nearby, water is laughing over stones.", "calm", "any", ["water"]),
    ("The moon hangs low enough to touch.", "calm", "night", None),
    ("Fireflies drift up out of the grass like sparks that forgot to go out.", "bright", "night", ["meadow", "flowers", "wild"]),
    ("Your footsteps make no sound at all.", "eerie", "any", None),
    ("The shadows here are a little longer than the light should allow.", "eerie", "any", None),
    ("A cold wind moves through the leaves, but nothing else stirs.", "ominous", "any", None),
    ("For a moment, every leaf turns to face you.", "eerie", "any", ["tree", "wild", "herbs", "grove"]),
    ("Mist curls low over the ground, knee-deep and slow.", "eerie", "night", None),
    ("The stars are unusually bright tonight, and there seem to be more of them.", "calm", "night", None),
    ("Dew beads on every petal, catching the light.", "bright", "day", ["flowers", "meadow"]),
    ("An owl calls once, then thinks better of it.", "eerie", "night", None),
    ("The glass panes hum faintly, like a finger around a wine glass.", "eerie", "any", ["glass"]),
    ("It smells of old paper and wet stone.", "calm", "any", ["alcove", "stone"]),
    ("The Garden feels busy today, full of small invisible errands.", "bright", "day", None),
    ("Something rustles just beyond the edge of the path.", "eerie", "any", ["wild", "grove", "roses"]),
    ("The roses are unusually fragrant, almost dizzying.", "bright", "any", ["roses"]),
    ("A single leaf falls, very slowly, and never quite lands.", "eerie", "any", ["tree", "grove"]),
    ("The ground is warm underfoot, like something beneath it is sleeping.", "calm", "any", None),
    ("Distant bells ring out, though Velmora has no bells that sound like that.", "ominous", "any", None),
    ("The water is perfectly still, and the reflection in it is not quite yours.", "ominous", "any", ["water"]),
    ("A thin, silver light clings to the edges of everything.", "calm", "night", None),
    ("The wind carries voices, too faint to make out.", "eerie", "any", None),
    ("Butterflies settle on the path ahead, as if waiting for you.", "bright", "day", ["flowers", "meadow", "wild"]),
    ("The temperature drops sharply, just for a breath.", "ominous", "any", None),
    ("The whole Garden smells, impossibly, of fresh bread.", "bright", "any", None),
    ("The lanterns flicker in a pattern that's almost a word.", "eerie", "night", ["stone", "meadow"]),
    ("Petals drift down from a tree that isn't blooming.", "calm", "any", ["tree", "grove", "flowers"]),
    ("Clouds gather overhead, but only overhead of you.", "ominous", "day", None),
    ("The ivy on the walls has grown since this morning. You can tell.", "eerie", "any", ["stone"]),
    ("It's warm and green and perfectly peaceful.", "calm", "day", None),
    ("Crickets sing in perfect time with each other.", "calm", "night", ["meadow", "wild", "herbs"]),
    # event-only
    ("Every flower in sight has turned black.", "ominous", "any", None, ["black_flowers"]),
    ("The petals are black as ink, and the air tastes of ash.", "ominous", "any", None, ["black_flowers"]),
    ("The Ancient Tree's glow reaches even here, gold and green.", "bright", "any", None, ["tree_glow"]),
    ("The air is thick with moon moths, silver wings everywhere you look.", "bright", "any", None, ["moths"]),
]

# ------------------------------------------------------------------ creatures
# (id, a, the, kind, zones, time, moods, drops, rarity, events)
CREATURES = [
    ("moon_moth", "a silver moon moth", "the moth", "flier", ["any"], "night", ["calm", "bright", "eerie"], ["moth_dust"], "common"),
    ("moth_swarm", "a cloud of moon moths", "the cloud of moths", "flier", ["any"], "any", ["bright", "calm"], ["moth_dust"], "common", ["moths"]),
    ("sprite", "a garden sprite no bigger than your thumb", "the sprite", "flier", ["flowers", "wild", "herbs", "meadow"], "any", ["bright", "calm"], ["sprite_thread"], "uncommon"),
    ("sprites", "a trio of quarrelling sprites", "the smallest sprite", "flier", ["flowers", "wild", "meadow"], "day", ["bright"], ["sprite_thread"], "uncommon"),
    ("tiny_dragon", "a tiny green dragon", "the dragon", "walker", ["stone", "glass", "herbs"], "any", ["bright", "calm", "eerie"], ["dragon_scale"], "rare"),
    ("dragon_sleeping", "a tiny dragon curled up asleep", "the dragon", "walker", ["stone", "tree", "glass"], "any", ["calm"], ["dragon_scale"], "rare"),
    ("frog", "an enchanted frog wearing a very small crown", "the frog", "hopper", ["water"], "any", ["calm", "bright", "eerie"], ["pool_water"], "uncommon"),
    ("golden_frog", "a golden frog with a merchant's eyes", "the golden frog", "hopper", ["water", "stone", "meadow"], "any", ["bright", "eerie", "calm"], ["frog_gold"], "very_rare", ["frog"]),
    ("toad", "a fat, unimpressed toad", "the toad", "hopper", ["water", "herbs", "stone"], "any", ["calm", "eerie"], ["pool_water"], "common"),
    ("moon_hare", "a white moon-hare", "the hare", "hopper", ["meadow", "grove", "wild"], "night", ["calm", "eerie"], ["hare_whisker"], "uncommon"),
    ("hedgehog", "a hedgehog with moss growing on its spines", "the hedgehog", "walker", ["herbs", "wild", "grove"], "any", ["calm", "bright"], ["wandering_seed"], "common"),
    ("fox", "a silver fox that is definitely watching you", "the fox", "walker", ["wild", "grove", "meadow"], "any", ["eerie", "calm"], ["hare_whisker"], "uncommon"),
    ("owl", "a barn owl", "the owl", "flier", ["tree", "wild", "grove"], "night", ["eerie", "calm"], ["owl_feather"], "common"),
    ("heron", "a grey heron standing on one leg", "the heron", "walker", ["water"], "day", ["calm"], ["heron_plume"], "uncommon"),
    ("snail", "a snail with a shell like stained glass", "the snail", "crawler", ["stone", "herbs", "water", "glass"], "any", ["calm", "bright"], ["snail_pearl"], "uncommon"),
    ("beetle", "a jewel-green beetle", "the beetle", "crawler", ["any"], "day", ["bright", "calm"], ["wandering_seed"], "common"),
    ("bees", "a line of bees walking, not flying", "the lead bee", "crawler", ["flowers", "meadow", "herbs"], "day", ["bright", "eerie"], ["sunbell"], "common"),
    ("spider", "a spider weaving letters into its web", "the spider", "crawler", ["stone", "alcove", "wild", "glass"], "any", ["eerie"], ["sprite_thread"], "uncommon"),
    ("raven", "a raven that knows your name", "the raven", "flier", ["tree", "stone", "alcove"], "any", ["eerie", "ominous"], ["owl_feather"], "uncommon"),
    ("hummingbird", "a hummingbird made of light", "the hummingbird", "flier", ["flowers", "glass", "meadow"], "day", ["bright"], ["veil_lily"], "rare"),
    ("koi", "a pale koi with gold writing on its scales", "the koi", "swimmer", ["water"], "any", ["calm", "eerie"], ["dew_diamond"], "rare"),
    ("newt", "a fire-red newt", "the newt", "crawler", ["water", "stone"], "any", ["calm", "bright"], ["emberleaf"], "common"),
    ("mole", "a mole wearing tiny spectacles", "the mole", "walker", ["herbs", "meadow", "stone"], "any", ["calm", "bright"], ["cracked_crystal"], "uncommon"),
    ("squirrel", "a squirrel hoarding something that glows", "the squirrel", "walker", ["tree", "grove"], "day", ["bright"], ["sleeping_acorn"], "common"),
    ("lantern_bug", "a lantern-bug the size of a plum", "the lantern-bug", "flier", ["any"], "night", ["calm", "eerie", "bright"], ["glowshroom"], "uncommon"),
    ("shadow", "a shape in the shadows that no one has ever identified", "the shape", "unknown", ["wild", "grove", "stone"], "any", ["eerie", "ominous"], ["unknown_husk"], "rare"),
    ("vine_thing", "something small and leafy that pretends to be a plant when you look", "the leafy thing", "unknown", ["herbs", "wild", "glass"], "any", ["eerie", "bright"], ["wandering_seed"], "uncommon"),
    ("pool_eye", "an eye in the water that is not a reflection", "the eye", "unknown", ["water"], "any", ["ominous", "eerie"], ["pool_water"], "rare"),
    ("crows", "a parliament of silent crows", "the eldest crow", "flier", ["tree", "stone", "wild"], "any", ["ominous", "eerie"], ["owl_feather"], "uncommon"),
    ("deer", "a young stag with frost on its antlers", "the stag", "walker", ["meadow", "grove", "wild"], "any", ["calm", "eerie"], ["hare_whisker"], "rare"),
    ("wolf_pup", "a wolf pup who seems very lost", "the pup", "walker", ["wild", "grove"], "any", ["calm", "eerie"], ["hare_whisker"], "rare"),
    ("elephant_shrew", "an elephant shrew carrying a leaf like a banner", "the shrew", "walker", ["herbs", "meadow", "stone"], "day", ["bright", "calm"], ["silverleaf"], "uncommon"),
]

# ------------------------------------------------------------------ actions
# (text, kinds)   verb phrases that take an object
ACTIONS = [
    ("circling", ["flier"]),
    ("hovering over", ["flier"]),
    ("landing, very carefully, on", ["flier"]),
    ("sniffing at", ["walker", "hopper"]),
    ("guarding", ["walker", "hopper", "flier", "unknown", "crawler"]),
    ("nudging", ["walker", "hopper"]),
    ("sitting perfectly still beside", ["walker", "hopper", "flier"]),
    ("trying to drag away", ["walker", "crawler", "hopper"]),
    ("crawling slowly across", ["crawler"]),
    ("hiding behind", ["walker", "hopper", "crawler", "unknown"]),
    ("staring at", ["walker", "hopper", "flier", "unknown", "swimmer"]),
    ("tapping", ["flier", "walker"]),
    ("curled up against", ["walker", "crawler"]),
    ("swimming slow circles beneath", ["swimmer"]),
    ("watching you from behind", ["walker", "hopper", "unknown", "flier"]),
    ("humming to", ["flier", "unknown"]),
    ("polishing", ["walker", "crawler"]),
    ("arguing with", ["flier", "walker", "hopper"]),
]

# ------------------------------------------------------------------ objects
# (id, a, the, zones, drops, rarity, events)
OBJECTS = [
    ("crystal", "a cracked crystal", "the crystal", ["stone", "glass", "any"], ["cracked_crystal"], "common"),
    ("moonstone", "a moonstone half-buried in moss", "the moonstone", ["stone", "meadow", "grove"], ["moonstone_chip"], "uncommon"),
    ("teacup", "an overturned porcelain teacup", "the teacup", ["any"], ["lost_button"], "common"),
    ("book", "a book with no title on its spine", "the book", ["alcove", "stone", "tree"], ["pressed_letter", "prophecy_leaf"], "uncommon"),
    ("button", "a brass button engraved with a V", "the button", ["any"], ["lost_button"], "common"),
    ("lantern", "a lantern that is still warm", "the lantern", ["stone", "meadow", "alcove"], ["moth_dust"], "common"),
    ("feather", "a single white feather", "the feather", ["any"], ["owl_feather"], "common"),
    ("seed_pod", "a seed pod that ticks like a clock", "the seed pod", ["herbs", "meadow", "wild", "glass"], ["wandering_seed", "starseed"], "uncommon"),
    ("mirror", "a hand mirror, face-down", "the mirror", ["stone", "water", "alcove"], ["pool_water"], "uncommon"),
    ("acorn", "an acorn that is snoring", "the acorn", ["tree", "grove"], ["sleeping_acorn"], "common"),
    ("ring", "a ring of mushrooms", "the ring of mushrooms", ["wild", "grove", "meadow"], ["fairy_ring_cap"], "common"),
    ("letter", "a folded letter sealed with green wax", "the letter", ["alcove", "stone", "roses"], ["pressed_letter"], "uncommon"),
    ("trowel", "a rusted trowel", "the trowel", ["herbs", "glass", "stone"], ["wandering_seed"], "common"),
    ("rose_black", "a single black rose", "the black rose", ["roses", "flowers"], ["black_rose"], "rare"),
    ("glowing_root", "a root glowing through the soil", "the root", ["tree", "grove", "herbs"], ["tree_bark"], "uncommon"),
    ("egg", "a speckled egg, much too warm", "the egg", ["tree", "wild", "stone"], ["dragon_scale"], "rare"),
    ("sundial_shadow", "a shadow with nothing to cast it", "the shadow", ["stone", "meadow"], ["unknown_husk"], "rare"),
    ("chalk_mark", "a chalk circle nobody admits to drawing", "the circle", ["stone", "alcove"], ["prophecy_leaf"], "uncommon"),
    ("jar", "a jar of fireflies with the lid off", "the jar", ["meadow", "glass", "stone"], ["moth_dust"], "common"),
    ("map", "a scrap of map pinned under a stone", "the map", ["stone", "wild", "alcove"], ["map_scrap"], "rare"),
    ("dew_drop", "a drop of dew that won't fall", "the dewdrop", ["flowers", "meadow", "herbs"], ["dew_diamond"], "rare"),
    ("thorn_crown", "a crown woven from thorns", "the crown", ["roses"], ["thornsalt"], "uncommon"),
    ("watering_can", "a watering can that's always full", "the watering can", ["herbs", "glass", "flowers"], ["dewmint"], "common"),
    ("candle", "a candle burning with a green flame", "the candle", ["alcove", "stone", "glass"], ["emberleaf"], "uncommon"),
    ("bell", "a tiny silver bell hanging from nothing", "the bell", ["any"], ["sunbell"], "uncommon"),
    ("orchid", "a ghost-white orchid", "the orchid", ["glass", "flowers", "wild"], ["ghost_orchid"], "very_rare"),
    ("amber", "a bead of golden sap", "the sap", ["tree"], ["tree_amber"], "very_rare"),
    ("key", "a strange key", "the key", ["stone", "alcove", "roses"], ["strange_key"], "very_rare", ["key"]),
    ("frog_coin", "a pile of tiny golden coins", "the coins", ["water", "stone"], ["frog_gold"], "rare", ["frog"]),
    ("statue_hand", "a stone hand, palm up, as if asking for something", "the stone hand", ["stone"], ["cracked_crystal"], "uncommon"),
]

# ------------------------------------------------------------------ discoveries
# standalone scenes, no creature. (text, zones, time, drops, mood or None)
DISCOVERIES = [
    ("a ring of mushrooms has grown here overnight", ["wild", "grove", "meadow"], "any", ["fairy_ring_cap"]),
    ("someone has planted a single flower in the exact middle of the path", ["any"], "any", ["wandering_seed"]),
    ("the moss has grown into the shape of a word you almost recognise", ["stone", "tree", "grove"], "any", ["prophecy_leaf"]),
    ("a trail of silver footprints leads away and simply stops", ["any"], "night", ["moth_dust"]),
    ("the herbs have rearranged themselves into alphabetical order", ["herbs", "glass"], "any", ["silverleaf", "dewmint"]),
    ("there's a door-shaped outline in the hedge", ["wild", "roses"], "any", ["map_scrap"]),
    ("a patch of flowers here is blooming out of season", ["flowers", "meadow"], "any", ["moonbloom_petal", "veil_lily"]),
    ("the water has frozen into a perfect circle, in the middle of summer", ["water"], "any", ["dew_diamond"]),
    ("there's a bench here that wasn't here yesterday", ["stone", "meadow", "alcove"], "any", ["lost_button"]),
    ("the vines have written something in the dust", ["stone", "alcove", "glass"], "any", ["prophecy_leaf"]),
    ("a dozen crystals have pushed up through the soil like teeth", ["stone", "glass"], "any", ["cracked_crystal", "moonstone_chip"]),
    ("the petals on the ground spell out the start of a name", ["flowers", "roses", "meadow"], "any", ["moonbloom_petal"]),
    ("someone left a cup of tea here. It's still hot", ["any"], "any", ["dewmint"]),
    ("the shadows are pointing the wrong way", ["any"], "day", ["unknown_husk"]),
    ("every leaf on one branch is silver", ["tree", "grove"], "any", ["silverleaf", "tree_bark"]),
    ("a book lies open, its pages turning slowly by themselves", ["alcove", "stone"], "any", ["pressed_letter", "prophecy_leaf"]),
    ("a small garden has been planted inside a teacup", ["glass", "herbs"], "any", ["wandering_seed"]),
    ("the glass panes are fogged from the inside, and someone has drawn a smiling face", ["glass"], "any", ["veil_lily"]),
    ("there are tiny footprints all over the soil, far too many to count", ["herbs", "wild", "meadow"], "any", ["sprite_thread"]),
    ("the stepping stones have rearranged into a spiral", ["water", "stone"], "any", ["snail_pearl"]),
    ("a lantern is burning here with nobody to hold it", ["stone", "meadow"], "night", ["moth_dust"]),
    ("a seed has sprouted in your footprint from a moment ago", ["any"], "any", ["wandering_seed", "starseed"]),
    ("the roses have all turned their heads the same way", ["roses"], "any", ["thornsalt"]),
    ("there's warm light coming from under the soil", ["herbs", "meadow", "tree"], "night", ["glowshroom"]),
    ("a statue's head has turned since the last time anyone looked", ["stone"], "any", ["cracked_crystal"]),
    ("the fog has settled into the shape of a person sitting", ["any"], "night", ["hushroot"]),
    ("somebody has carved initials into the bark, very recently", ["tree", "grove"], "any", ["tree_bark"]),
    ("a ribbon is tied around a branch, in House colours", ["tree", "grove", "wild"], "any", ["sprite_thread"]),
    ("the well has water in it today, and it is humming", ["water", "stone"], "any", ["pool_water"]),
    ("a single star has fallen into the grass and is still glowing", ["meadow"], "night", ["starthistle", "starseed"]),
    ("the Reflection Pool shows the sky from a different night", ["water"], "any", ["pool_water"]),
    ("every mushroom here has turned to face the Ancient Tree", ["wild", "grove"], "any", ["glowshroom", "hollow_morel"]),
    ("the sundial's shadow is moving backwards", ["stone"], "day", ["cracked_crystal"]),
    ("there's a neat row of pebbles, largest to smallest, leading into the ferns", ["wild", "stone"], "any", ["map_scrap"]),
    ("the thorns on the roses have fallen out and lie in a perfect circle", ["roses"], "any", ["thornsalt"]),
    ("the air shimmers here like heat off stone", ["any"], "day", ["emberleaf"]),
    ("someone has hung wind chimes made of bones. Small bones", ["wild", "grove"], "any", ["unknown_husk"], "ominous"),
    ("the earth here has been dug up, and then carefully put back", ["herbs", "wild", "grove"], "any", ["unknown_husk"], "ominous"),
    ("a doorway of woven willow stands in the middle of nowhere", ["water", "grove"], "any", ["map_scrap"]),
    ("the Ancient Tree has dropped a single leaf with writing on it", ["tree"], "any", ["prophecy_leaf"]),
]

# ------------------------------------------------------------------ outcomes
# (text, tones, moods or None, events)
# tones: beloved, friendly, neutral, wary, hostile
OUTCOMES = [
    # beloved
    ("The vines draw quietly aside to let you pass.", ["beloved", "friendly"]),
    ("A flower opens beneath your hand as you reach out.", ["beloved"]),
    ("The Garden hums, very softly, as though it's pleased you came.", ["beloved"]),
    ("Petals settle on your shoulders like a welcome.", ["beloved", "friendly"]),
    ("The path ahead seems to lean toward you.", ["beloved"]),
    ("You get the distinct feeling you are being looked after.", ["beloved"]),
    ("Something beneath the soil remembers you. Fondly.", ["beloved"]),
    ("The roses bow, just slightly, as you go by.", ["beloved"]),
    # friendly
    ("A warm breeze follows you for a while.", ["friendly", "beloved"]),
    ("The grass springs back behind you faster than it should.", ["friendly"]),
    ("A bird lands on a branch beside you and stays.", ["friendly", "neutral"]),
    ("The light shifts to show you something you'd have missed.", ["friendly", "beloved"]),
    ("A thorn that was about to catch your sleeve pulls itself back.", ["friendly"]),
    ("The Garden seems to be in a generous mood.", ["friendly", "beloved"]),
    # neutral
    ("As you approach, the flowers close around you.", ["neutral", "wary"]),
    ("The Garden carries on with its business, as if you're not quite there.", ["neutral"]),
    ("A leaf lands on your head. It might have been an accident.", ["neutral"]),
    ("Nothing else happens, which somehow feels deliberate.", ["neutral", "wary"]),
    ("You have the feeling you've been noticed.", ["neutral"]),
    ("A sprinkler of dew goes off, perfectly aimed at your shoes.", ["neutral", "wary"]),
    ("The moment passes. The Garden moves on. So do you.", ["neutral"]),
    ("Somewhere, something makes a note of your visit.", ["neutral", "wary"]),
    # wary
    ("The roses turn their faces away as you pass.", ["wary"]),
    ("A bramble snags your robe and doesn't let go straight away.", ["wary", "hostile"]),
    ("The path is a little longer on the way back than it was on the way in.", ["wary"]),
    ("Every bloom within reach snaps shut.", ["wary", "hostile"]),
    ("The Garden goes very quiet while you're here.", ["wary"]),
    ("The birds stop singing until you leave.", ["wary", "hostile"]),
    # hostile
    ("Something beneath the soil remembers you.", ["hostile"]),
    ("The vines tighten across the path. You find another way.", ["hostile"]),
    ("A cold wind pushes you, gently but firmly, toward the gate.", ["hostile"]),
    ("Every rose snaps shut at once. The sound is like a door slamming.", ["hostile"]),
    ("The ground is hard and unfriendly, and your footprints fill with water.", ["hostile"]),
    ("You hear, very clearly, the Garden sigh.", ["hostile"]),
    # event
    ("The black petals rustle as you pass, like whispering.", ["neutral", "wary", "hostile", "friendly", "beloved"], None, ["black_flowers"]),
    ("A moth lands on your hand and stays there, glowing.", ["friendly", "beloved", "neutral"], None, ["moths"]),
    ("The Tree's light brushes your face, warm as summer.", ["friendly", "beloved", "neutral"], None, ["tree_glow"]),
]

# Outcomes shown to members of the house the Garden is "behaving strangely"
# toward during that event.
STRANGE_HOUSE = [
    "The Garden rearranges the path behind you, just to see what you'll do.",
    "Every flower you pass turns to follow you. All of them. Slowly.",
    "The hedges hum your House's name.",
    "You're sure the statues were facing the other way a moment ago.",
    "Something plucks a single thread from your sleeve and keeps it.",
    "The Garden seems very interested in you today. Unsettlingly so.",
]

# ------------------------------------------------------------------ templates
# Fields: {loc} {Loc} (at-phrase), {creature} {Creature} {creature_the}
# {action} {object} {object_the} {discovery} {Discovery} {atmos} {outcome}
# kinds: "creature" (needs creature+action+object), "discovery", "quiet"
TEMPLATES = [
    ("creature", "{Loc}, you notice {creature} {action} {object}.\n\n{atmos} {outcome}"),
    ("creature", "{atmos}\n\n{Loc}, {creature} is {action} {object}. {outcome}"),
    ("creature", "You linger {loc}. {atmos}\n\nYou spot {creature} {action} {object}. Everything goes very still when you arrive. {outcome}"),
    ("creature", "{Loc}: {creature}, {action} {object}.\n\n{outcome}"),
    ("creature", "{atmos}\n\n{Loc}, something catches your eye: {creature}, {action} {object}. {outcome}"),
    ("discovery", "{Loc}, {discovery}.\n\n{atmos} {outcome}"),
    ("discovery", "{atmos}\n\n{Loc}, you realise {discovery}. {outcome}"),
    ("discovery", "You take a different turning and end up {loc}. {Discovery}.\n\n{outcome}"),
    ("quiet", "You spend a while {loc}. {atmos}\n\n{outcome}"),
    ("quiet", "You stand {loc} for a while. {atmos} {outcome}"),
]

# ------------------------------------------------------------------ forage lines
FORAGE_LINES = [
    "You search {loc} and turn up {item}.",
    "Digging carefully {loc}, you find {item}.",
    "{Loc}, half-hidden under the leaves, is {item}.",
    "You part the undergrowth {loc}. There - {item}.",
    "Something glints {loc}. It's {item}.",
    "You kneel {loc} and come up with {item}.",
]
FORAGE_EMPTY = [
    "You search {loc} for a long while and find nothing but dirt under your nails.",
    "{Loc}, the Garden has hidden everything worth finding. Today, anyway.",
    "You come away from {loc} empty-handed. Something rustles, pleased with itself.",
]

# ------------------------------------------------------------------ choices after a creature encounter
# key: (label, emoji)
CHOICES = {
    "feed": ("Feed it", "🍃"),
    "follow": ("Follow it", "👣"),
    "steal": ("Steal from it", "🫳"),
    "leave": ("Leave it be", "🌿"),
}
PLANT_CHOICES = {
    "tend": ("Tend it", "🌱"),
    "uproot": ("Uproot it", "🪓"),
    "leave": ("Leave it be", "🌿"),
}
CHOICE_LINES = {
    "feed_good": ["{The} accepts your offering and nuzzles your hand before vanishing.", "{The} eats from your palm, then leaves something behind in thanks."],
    "feed_meh": ["{The} sniffs your offering, considers it, and walks off with it without a word."],
    "follow_good": ["You follow {the} deeper in than you've ever been, until it stops and shows you something.", "{The} leads you to a place you've never seen - and then it's gone."],
    "follow_lost": ["You follow {the} for what feels like an hour. When you look up, you're back at the gate."],
    "steal_good": ["You snatch it and run. {The} doesn't chase you. That's somehow worse."],
    "steal_caught": ["{The} is quicker than you. Much quicker. You leave with nothing but a scratch - and the Garden saw."],
    "leave": ["You let {the} be. As you go, you feel the Garden watching - approvingly, you think.", "You step softly around {the} and carry on. Nothing is taken. Nothing is owed."],
    "tend": ["You clear the weeds, loosen the soil, and give it a little water. The Garden notices.",
             "You kneel and tend it carefully. Somewhere behind you, a flower that was closed opens.",
             "You straighten a bent stem and pat the earth down. It seems to stand a little taller."],
    "uproot": ["You tear it up by the roots. The Garden goes silent around you. It will remember this.",
               "It comes up easily - too easily. The soil where it grew turns grey.",
               "You rip it out and pocket what's useful. Every leaf nearby turns away from you."],
}

OFFER_LINES = {
    "full": "The Garden accepts your offering without comment. It has had enough gifts from you for one day.",
    "warm": ["The soil takes your offering gently. A flower you haven't seen before opens nearby.",
             "The Garden accepts. Somewhere, a bird starts singing.",
             "Your offering sinks into the earth, and the ground feels warmer where it went."],
    "cold": ["The earth swallows your offering. The Garden says nothing. It noticed, though.",
             "The roots draw your offering down into the dark. A little of the chill goes out of the air.",
             "Your offering is gone the moment you look away. That's a start."],
}

# ------------------------------------------------------------------ reputation reveals
REVEALS = {
    "beloved": "🌿 *The vines draw back from the path as you approach. A flower opens where your hand rests. The Garden has decided you should know: **it likes you.***",
    "friendly": "🌸 *For the first time, a bloom turns toward you when you pass. The Garden has started to notice you - kindly.*",
    "wary": "🥀 *The roses close as you pass. It's the first time you've noticed. It won't be the last.*",
    "hostile": "🌑 *Something beneath the soil remembers you. The Garden has decided you should know that, too.*",
}

# ------------------------------------------------------------------ random events
# (key, emoji, title, announcement, end line, (min_minutes, max_minutes), weight)
EVENTS = [
    ("door", "🚪", "A Door Behind the Roses",
     "A door has appeared behind the roses. Old wood, iron hinges, no handle. Nobody remembers it being there.",
     "The door behind the roses is gone. Just roses now. As if it never was.", (180, 1440), 10),
    ("black_flowers", "🥀", "The Flowers Turn Black",
     "Every flower in the Garden has turned black. Nobody knows why. The Garden isn't saying.",
     "Colour creeps back into the petals, one flower at a time. Whatever it was has passed.", (60, 720), 8),
    ("frog", "🐸", "The Golden Frog",
     "A golden frog has set up shop by the water, and it's offering trades. Bring it something. It might give you something back.",
     "The golden frog packs up its tiny stall and hops away. Business, apparently, is concluded.", (120, 480), 8),
    ("whisper", "💧", "A Name From the Pool",
     "The Reflection Pool is whispering a name. **{name}**. Over and over, very softly.",
     "The Pool goes quiet. Whatever it wanted to say, it has stopped saying it.", (60, 360), 7),
    ("tree_glow", "🌳", "The Ancient Tree Glows",
     "The Ancient Tree has begun to glow, gold and green, from the roots up. The whole Garden feels warmer.",
     "The Tree's glow fades back into ordinary bark. It hums, once, and falls silent.", (60, 480), 8),
    ("moths", "🦋", "The Moon Moths",
     "Hundreds of moon moths have poured out of nowhere. The Garden is silver with them.",
     "The moon moths lift all at once and are gone, leaving silver dust on everything.", (30, 240), 9),
    ("key", "🗝️", "A Key Beneath the Bench",
     "Someone has spotted a strange key beneath a bench in the Garden. It's still there. For now.",
     "The key beneath the bench is gone. Somebody took it - or something did.", (60, 1440), 5),
    ("path", "🌿", "An Unexplored Path",
     "A new path has opened in the Garden, winding somewhere nobody has been. The hedges on either side are very, very quiet.",
     "The unexplored path has closed over. Ivy where the gap was. Did anyone see where it went?", (120, 1440), 6),
    ("strange_house", "🏡", "The Garden Takes an Interest",
     "The Garden is behaving very strangely toward **House {house}**. Flowers follow them. Paths shift. Nobody can say why.",
     "The Garden seems to have lost interest in House {house}. For now.", (180, 1440), 7),
]

# ------------------------------------------------------------------ secrets
# Typed (not slash-commanded) in the Garden channel. Matching is loose: the
# message must CONTAIN one of the phrases, case and punctuation ignored.
# once: "user" (each person once) / "server" (first finder only) / "event" (once per person per event)
SECRETS = [
    # --- house secrets: only that house's students get an answer
    {"id": "thornmere_howl", "phrases": ["i howl at the brambles", "howl with the wolves", "i howl"],
     "requires": {"houses": ["thornmere"], "time": "night"},
     "response": "🐺 You howl. For a long moment, nothing. Then, from deep in the brambles, something howls back - and a crown of thorns rolls out onto the path.",
     "reward": "thornsalt", "rep": 2, "points": 1, "once": "user"},
    {"id": "veyren_bow", "phrases": ["i bow to the stag", "bow to the white stag", "i bow to the deer"],
     "requires": {"houses": ["veyren"]},
     "response": "🦌 You bow. When you look up, a stag is standing at the edge of the meadow. It bows back - and is gone.",
     "reward": "hare_whisker", "rep": 2, "points": 1, "once": "user"},
    {"id": "vashara_heal", "phrases": ["i heal the broken flower", "i mend the broken stem", "heal the wilted flower"],
     "requires": {"houses": ["vashara"]},
     "response": "🐘 You cup the broken stem in your hands. It straightens. It blooms. Every flower nearby turns to look at you.",
     "reward": "veil_lily", "rep": 3, "points": 1, "once": "user"},
    {"id": "caldrin_stars", "phrases": ["i read the stars in the pool", "chart the stars in the reflection pool", "i map the reflected stars"],
     "requires": {"houses": ["caldrin"], "time": "night"},
     "response": "🔭 You trace the reflected stars with a fingertip. They're not tonight's stars - they're a sky from centuries ago, and one of them is labelled in handwriting.",
     "reward": "starthistle", "rep": 2, "points": 1, "once": "user"},
    {"id": "moonveil_hypothesis", "phrases": ["i test a hypothesis on the moonblooms", "for research purposes", "i run an experiment in the garden"],
     "requires": {"houses": ["moonveil"]},
     "response": "🌕 You change one variable - just one - and every moonbloom in the meadow changes colour to match. The Garden, it seems, likes a good experiment.",
     "reward": "moonbloom_petal", "rep": 2, "points": 1, "once": "user"},
    # --- reputation-gated
    {"id": "beloved_gift", "phrases": ["i ask the garden for a gift", "may i have a gift", "what would you give me"],
     "requires": {"rep_min": 15},
     "response": "🌿 The Garden has been waiting for you to ask. A single white orchid grows up out of the path, opens, and lets itself be picked.",
     "reward": "ghost_orchid", "rep": 1, "points": 2, "once": "user"},
    {"id": "apology", "phrases": ["i am sorry", "i apologise to the garden", "i apologize to the garden", "forgive me garden"],
     "requires": {"rep_max": -3},
     "response": "🥀 You say it out loud. The roses don't open. But the thorns across the path pull back, just a little.",
     "rep": 3, "once": "user"},
    # --- a chain: the pool learns your name, then answers a question
    {"id": "pool_ask", "phrases": ["i ask the pool a question", "pool what do you see", "what do you see pool"],
     "requires": {"flags": ["garden:pool_name"]},
     "response": "💧 The Pool knows your name now, so it answers. It shows you a corridor in the Library you've never walked down, and a cabinet with a very small keyhole.",
     "reward": "prophecy_leaf", "rep": 1, "points": 1, "once": "user", "flags": ["heard_of_cabinet"]},
    {"id": "tree_listen", "phrases": ["i press my ear to the ancient tree", "listen to the ancient tree", "ear to the tree"],
     "response": "🌳 You press your ear to the bark. Deep inside, slow as centuries, something is **counting**. It gets to a number, stops, and starts again. You think it's counting students.",
     "reward": "tree_bark", "rep": 2, "points": 1, "once": "user"},
    {"id": "pool_name", "phrases": ["i say my name to the pool", "tell the pool my name", "whisper my name into the pool"],
     "response": "💧 You whisper your name into the Reflection Pool. It whispers back a different one. You don't know it. Yet.",
     "reward": "pool_water", "rep": 1, "points": 1, "once": "user"},
    {"id": "roses_greet", "phrases": ["good morning roses", "good evening roses", "hello roses"],
     "response": "🌹 The roses, one by one, turn to face you. It's a little unnerving. It's also, you think, polite.",
     "rep": 2, "once": "user"},
    {"id": "hollow_ring", "phrases": ["i step into the mushroom ring", "step inside the fairy ring", "step into the ring"],
     "response": "🍄 You step inside the ring. The world goes quiet and very green. When you step out, your pockets are heavier and your shoes are on the wrong feet.",
     "reward": "hollow_morel", "rep": -1, "points": 1, "once": "user"},
    {"id": "moonbloom_night", "phrases": ["i sing to the moonblooms", "sing to the moonbloom", "hum to the moonblooms"],
     "response": "🌸 You sing, softly. Every moonbloom in the meadow opens at once, and one of them lets go a petal straight into your hand.",
     "reward": "moonbloom_petal", "rep": 2, "points": 1, "once": "user", "time": "night"},
    {"id": "sundial_wind", "phrases": ["i turn the sundial", "turn back the sundial", "wind the sundial"],
     "response": "☀️ The sundial resists, then gives. For one second, every shadow in the Garden jumps an hour. Somewhere, a bell rings that shouldn't.",
     "reward": "cracked_crystal", "rep": -2, "once": "user", "time": "day"},
    {"id": "gardener_shed", "phrases": ["i knock on the gardener's shed", "knock on the shed", "knock three times on the shed"],
     "response": "🏚️ You knock. From inside, someone knocks back - three times. The door stays shut. Under it, someone slides out a folded scrap of map.",
     "reward": "map_scrap", "points": 2, "once": "user"},
    {"id": "statue_offer", "phrases": ["i place a crystal in the stone hand", "give the statue a crystal", "put a crystal in the stone hand"],
     "requires": {"items": ["cracked_crystal"]}, "consumes": ["cracked_crystal"],
     "response": "🗿 You place it in the statue's palm. The fingers close, very slowly, and when they open again there's something else in them.",
     "reward": "moonstone_chip", "rep": 2, "once": "user"},
    {"id": "first_to_owl", "phrases": ["i bow to the owl", "bow to the barn owl", "ask the owl a question"],
     "response": "🦉 The owl looks at you for a long time. Then it says - clearly, in words - *\"You're the first to ask.\"* It will not say anything else. Ever.",
     "reward": "owl_feather", "rep": 3, "points": 3, "once": "server", "time": "night"},
    {"id": "willow_door", "phrases": ["i walk through the willow door", "step through the willow doorway", "walk through the willow"],
     "response": "🌿 You step through the woven willow doorway and out the other side - into the same Garden, but quieter, and a little bit older. When you look back, you're somehow in front of the doorway again.",
     "reward": "starseed", "rep": 1, "points": 1, "once": "user"},
    # event-bound
    {"id": "door_knock", "phrases": ["i knock on the door behind the roses", "knock on the door", "open the door behind the roses"],
     "response": "🚪 You knock. The door doesn't open - but beneath it, something slides out toward your feet.",
     "reward": "map_scrap", "rep": 1, "points": 1, "once": "event", "event": "door"},
    {"id": "frog_trade", "phrases": ["i trade with the golden frog", "trade with the frog", "offer the frog a trade"],
     "response": "FROG_TRADE", "once": "none", "event": "frog"},
    {"id": "key_take", "phrases": ["i take the key", "pick up the key", "take the strange key"],
     "response": "🗝️ You kneel and take the key from beneath the bench. It's heavier than it looks - and very, very cold.",
     "reward": "strange_key", "points": 2, "rep": 1, "once": "server_event", "event": "key"},
    {"id": "path_walk", "phrases": ["i walk the unexplored path", "follow the new path", "take the unexplored path"],
     "response": "🌿 You follow the new path through the hedge. It winds and winds, and at its end there's something waiting, as if left for you.",
     "reward_pool": ["ghost_orchid", "starseed", "map_scrap", "hollow_morel"], "rep": 1, "points": 1, "once": "event", "event": "path"},
    {"id": "pool_answer", "phrases": ["i answer the pool", "answer the reflection pool", "i'm here"],
     "response": "POOL_ANSWER", "once": "event", "event": "whisper"},
    {"id": "tree_touch_glow", "phrases": ["i touch the glowing tree", "touch the ancient tree", "put my hand on the tree"],
     "response": "🌳 The glow runs up your arm like warm water. For a heartbeat, you can hear every root in the Garden.",
     "reward": "tree_amber", "rep": 1, "points": 1, "once": "event", "event": "tree_glow"},
]


def pack():
    def loc(t):
        d = {"id": t[0], "at": t[1], "zones": t[2], "time": t[3], "drops": t[4]}
        if len(t) > 5:
            d["events"] = t[5]
        return d

    def atm(i, t):
        d = {"id": f"atm{i}", "text": t[0], "mood": t[1], "time": t[2], "zones": t[3] or ["any"]}
        if len(t) > 4:
            d["events"] = t[4]
        else:
            d["not_events"] = ["black_flowers"] if t[1] != "ominous" else []
        return d

    def cre(t):
        d = {"id": t[0], "a": t[1], "the": t[2], "kind": t[3], "zones": t[4], "time": t[5],
             "moods": t[6], "drops": t[7], "rarity": t[8]}
        if len(t) > 9 and t[9]:
            d["events"] = t[9]
        if t[0] in HOUSE_WEIGHTS:
            d["house_weight"] = HOUSE_WEIGHTS[t[0]]
        return d

    def obj(t):
        d = {"id": t[0], "a": t[1], "the": t[2], "zones": t[3], "drops": t[4], "rarity": t[5]}
        if len(t) > 6:
            d["events"] = t[6]
        return d

    def dis(i, t):
        d = {"id": f"dis{i}", "text": t[0], "zones": t[1], "time": t[2], "drops": t[3]}
        if len(t) > 4 and t[4]:
            d["moods"] = [t[4]]
        if len(t) > 5 and t[5]:
            d.update(t[5])
        if any(w in t[0] for w in PLANT_WORDS):
            d["plant"] = True
            d.setdefault("the", "the plant")
        return d

    def out(i, t):
        d = {"id": f"out{i}", "text": t[0], "tones": t[1]}
        if len(t) > 2 and t[2]:
            d["moods"] = t[2]
        if len(t) > 3:
            d["events"] = t[3]
        return d

    return {
        "name": "the Garden",
        "title": "🌿 The Garden",
        "blurb": "The Garden is alive, and it remembers. Flowers move when no wind blows, paths lead where they shouldn't, and the Reflection Pool whispers things nobody told it.",
        "limits": {"explore": 5, "forage": 3},
        "item_ids": [i[0] for i in ITEMS],
        "areas": [loc(t) for t in LOCATIONS],
        "atmospheres": [atm(i, t) for i, t in enumerate(ATMOS)],
        "creatures": [cre(t) for t in CREATURES],
        "actions": [{"id": f"act{i}", "text": t[0], "kinds": t[1]} for i, t in enumerate(ACTIONS)],
        "objects": [obj(t) for t in OBJECTS],
        "discoveries": [dis(i, t) for i, t in enumerate(DISCOVERIES + HOUSE_DISCOVERIES)],
        "outcomes": [out(i, t) for i, t in enumerate(OUTCOMES)],
        "strange_house": STRANGE_HOUSE,
        "templates": [{"id": f"tpl{i}", "kind": k, "text": t} for i, (k, t) in enumerate(TEMPLATES)],
        "forage_lines": FORAGE_LINES,
        "forage_empty": FORAGE_EMPTY,
        "choices": {k: {"label": v[0], "emoji": v[1]} for k, v in CHOICES.items()},
        "plant_choices": {k: {"label": v[0], "emoji": v[1]} for k, v in PLANT_CHOICES.items()},
        "choice_lines": CHOICE_LINES,
        "offer_lines": OFFER_LINES,
        "uses": USES,
        "rare_boost_events": ["tree_glow", "path"],
        "event_drop_boost": {"moths": ["moth_dust"], "black_flowers": ["black_rose"], "tree_glow": ["tree_amber", "tree_bark"]},
        "reveals": REVEALS,
        "events": [{"key": e[0], "emoji": e[1], "title": e[2], "start": e[3], "end": e[4],
                    "minutes": list(e[5]), "weight": e[6]} for e in EVENTS],
        "secrets": SECRETS,
    }


if __name__ == "__main__":
    data = pack()
    WORLD.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    items = {i[0]: {"name": i[1], "emoji": i[2], "rarity": i[3], "desc": i[4]} for i in ITEMS}
    ITEMS_OUT.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    counts = {k: len(v) for k, v in data.items() if isinstance(v, (list, dict))}
    print(counts)
