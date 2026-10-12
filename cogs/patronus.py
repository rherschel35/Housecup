"""
Patronuses — silver guardians, one unique form each.

    /patronus              - cast yours from wand words, or see it again
    /patronus @member      - see someone else's
    /patronusreset         - once: three new words, a new unique patronus

Staff:
    /staff identity patronusreset @member - new words for them; wand stays

First cast reuses the wandmaker's three words. Forms are unique across the
school (like brooms) — if the shape your words want is taken, the next-closest
open form is yours. Headmaster-sealed customs stay put and are not resettable.
"""

import hashlib
import json
import logging
import os
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.patronus")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
PATRONUS_PATH = STATE_DIR / "patronus.json"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "patronus_art_assets"

MODEL = os.getenv("PATRONUS_MODEL", "claude-haiku-4-5-20251001")
SILVER = 0xC4CCD6

# Headmaster-assigned patronuses (animal need not be on ANIMALS).
# Optional "art" filename under patronus_art_assets/. Overrides stored casts.
CUSTOM_PATRONUSES: dict[str, dict] = {
    "929771936408547328": {  # Rowie / Class Pres. Rowwie
        "animal": "Siren",
        "form": (
            "Silver scales shimmer beneath dark, restless waves as the siren "
            "rises from the sea, her voice carrying with the sharp clarity of "
            "a bell across open water. She moves with deliberate confidence, "
            "never hesitating, never asking permission from the tide."
        ),
        "reading": (
            "You possess a nature that refuses to be softened for anyone else. "
            "You say what you mean, pursue what you want, and meet competition "
            "with a smile that suggests you already intend to win. Your strength "
            "is your directness. You do not circle the truth when you can simply "
            "reach for it.\n\n"
            "**Your guard is this:** you are the current beneath the surface. "
            "You may be calm, but you are never passive. You protect what matters "
            "by confronting what threatens it, by refusing to back down when "
            "challenged, and by making yourself impossible to overlook.\n\n"
            "Like the sea, you do not need to raise your voice to prove your "
            "power. You simply keep moving forward until everything in your path "
            "learns to move with you."
        ),
        "art": "Siren_Rowie.png",
        "defeat_art": "Siren_Rowie_Defeat.png",
    },
    "206828442065305600": {  # Pod / Vish's Head Boy Pod
        "animal": "Griffin",
        "form": (
            "A powerful griffin emerges from the light, its wings spreading "
            "wide as its lion-like paws settle firmly against the ground. There "
            "is something regal in its presence, but not distant. Its gaze is "
            "sharp, curious, and almost mischievous, as though it has already "
            "figured out the room before anyone else has."
        ),
        "reading": (
            "You carry an unusual combination of confidence and curiosity. You "
            "want to experience everything, meet people, build connections, and "
            "leave something behind that matters. You can be loud, ridiculous, "
            "flirtatious, and completely unserious one moment, then surprisingly "
            "thoughtful and fiercely determined the next.\n\n"
            "**Your guard is this:** you don't give up on people easily. You may "
            "become frustrated, hurt, or disappointed, but the people you truly "
            "claim as your own tend to stay yours. You have a stubborn instinct "
            "to protect, encourage, and fight for the things you believe deserve "
            "a chance.\n\n"
            "The griffin represents the part of you that refuses to be reduced "
            "to one thing. **You can be both tenderness and force, humor and "
            "seriousness, ambition and loyalty.** You adapt without losing "
            "yourself.\n\n"
            "And beneath everything is a desire to live a life that actually "
            "feels *alive*. To make memories. To love loudly. To build something "
            "you're proud of. To be surrounded by people who matter and to know "
            "that, when the moment comes, you had the courage to choose your "
            "own path.\n\n"
            "**The griffin does not simply guard what it loves. It rises above "
            "it, sees where it is going, and then refuses to turn back.**"
        ),
        "art": "Griffin_Pod.jpg",
        "defeat_art": "Griffin_Pod_Defeat.jpg",
    },
    "534422209968734209": {  # Mara
        "animal": "Margarita",
        "form": (
            "Golden-green light gathers in a shimmering swirl, forming the "
            "unmistakable shape of a margarita glass. Its silvery magic glows "
            "around the rim like frost, while a tiny lime wedge rests against "
            "the edge, bright and mischievous. The Patronus sparkles with the "
            "kind of energy that makes it impossible to tell whether it just "
            "arrived to protect you or convince you to stay out another three "
            "hours."
        ),
        "reading": (
            "You carry a warmth that draws people in without trying. You're "
            "playful, silly, and effortlessly flirty, with a talent for turning "
            "ordinary moments into something worth remembering. You love people, "
            "conversation, laughter, and the simple joy of being surrounded by "
            "good company.\n\n"
            "Your greatest strength is your generosity. You're the person who "
            "notices when someone needs a hand, pulls them into the fun, and "
            "somehow manages to make them feel like they belong. You give freely, "
            "whether it's your time, your attention, your humor, or the last "
            "drink at the table.\n\n"
            "**Your guard is this:** you refuse to let life become too serious "
            "for too long. You protect joy. You remind people to laugh when "
            "they've forgotten how, to loosen their grip on the things weighing "
            "them down, and to enjoy the moment while it's still happening.\n\n"
            "Like a margarita, you're bright, refreshing, a little dangerous "
            "when underestimated, and considerably more fun when shared."
        ),
        "art": "Margarita_Mara.png",
        "defeat_art": "Margarita_Mara_Defeat.png",
    },
    "793895185896439838": {  # Saro
        "animal": "Chimera",
        "form": (
            "Silver mist gathers and coils through the air, shimmering brighter "
            "as it takes shape. First come the eyes of a lion, fierce and "
            "unwavering, followed by the long, powerful head of a dragon. Great "
            "dragon wings unfold from its back, scattering ribbons of luminous "
            "silver through the darkness. It does not emerge gently. It arrives "
            "like something ancient remembering exactly what it was made to protect."
        ),
        "reading": (
            "You carry both the quiet independence of the lion and the untamed "
            "power of the dragon. You know your own strength, and you have no "
            "need to constantly prove it. You can stand alone when you have to, "
            "but your strength becomes something far greater when you choose to "
            "stand beside someone you love.\n\n"
            "Your chimera knows that loyalty is not given freely. It is earned. "
            "You are selective about who gets close enough to see the softer "
            "parts of you, but once someone has earned their place, you protect "
            "them with a ferocity that can surprise anyone who mistook your "
            "independence for indifference.\n\n"
            "The lion in your Patronus represents your courage, pride, and "
            "unwavering sense of self. The dragon represents the part of you "
            "that refuses to be diminished, controlled, or made smaller for "
            "someone else's comfort. Its silver form reflects something deeper: "
            "you have learned that your strength does not have to be loud to be "
            "powerful.\n\n"
            "When danger comes, the chimera doesn't simply defend you.\n\n"
            "It rises.\n\n"
            "Its wings spread wide, its lion's roar becomes a thunderous "
            "dragon's cry, and the silver mist around it burns brighter until "
            "there is nowhere left for darkness to hide.\n\n"
            "It is not a creature that asks permission to exist.\n\n"
            "It knows exactly what it is."
        ),
        "art": "Chimera_Saro.jpg",
        "defeat_art": "Chimera_Saro_Defeat.jpg",
    },
    "581173716206223372": {  # Lumi / Lumière✨ — AsterWilde house president
        "animal": "Kraken",
        "label": "A Kraken of Silvery Mist",
        "form": (
            "Silvery mist coils into the shape of a colossal kraken, its immense "
            "body emerging from the haze like an ancient terror rising from the "
            "abyss. Eight enormous tentacles unfurl into the air, each one "
            "twisting with deliberate, unsettling grace, their luminous silver "
            "tips leaving trails of shimmering mist in their wake. Vast, ghostly "
            "eyes burn through the fog as its shadow spreads across the ground, "
            "vast enough to swallow everything beneath it. The mist churns around "
            "its form like a storm-tossed sea, revealing glimpses of its monstrous "
            "silhouette before concealing it once more."
        ),
        "reading": (
            "You protect by becoming the force no darkness can contain. Where "
            "others see an impossible obstacle, you find a thousand ways to "
            "overcome it. You are patient when patience is needed, merciless when "
            "the moment demands it, and impossible to trap in the confines of "
            "another's expectations. Your strength lies in your ability to reach "
            "beyond the obvious, to strike from unexpected depths, and to turn "
            "even the most chaotic circumstances to your advantage. You do not "
            "simply stand between danger and those you protect; you become the "
            "very thing danger learns to fear. Like the kraken that rules the "
            "deepest waters, you carry an ancient, unyielding power beneath a "
            "composed surface. And when the people you cherish are threatened, "
            "the mist parts, the abyss awakens, and the world remembers that "
            "some forces were never meant to be challenged."
        ),
        "art": "Kraken_Lumi.jpg",
        "defeat_art": "Kraken_Lumi_Defeat.jpg",
    },
}

# Real animals only, each with what it guards. The reader must choose from
# these, the same way the wandmaker chooses from real woods and cores.
ANIMALS = {
    "Hare":           "quick and watchful; guards the people who can't run",
    "Fox":            "clever and self-reliant; protects by outthinking trouble",
    "Otter":          "playful and devoted; keeps its family close",
    "Stag":           "steady and proud; stands between danger and the herd",
    "Doe":            "gentle and alert; its strength is quiet vigilance",
    "Wolf":           "loyal to its pack above everything",
    "Elephant":       "remembers everyone; never abandons the ones who fall behind",
    "Owl":            "patient and wise; sees what others miss in the dark",
    "Raven":          "curious and sharp; carries secrets safely",
    "Swan":           "graceful, fiercely protective of what it loves",
    "Heron":          "still and patient; waits for exactly the right moment",
    "Hawk":           "focused and fast; never loses sight of its own",
    "Falcon":         "fearless in a dive; strikes first to keep others safe",
    "Eagle":          "sees the whole of things from far above",
    "Horse":          "strong and free; carries others through hard ground",
    "Badger":         "stubborn and fierce; defends its home to the last",
    "Hedgehog":       "small, soft inside, impossible to hurt",
    "Bear":           "enormous warmth and enormous strength in the same body",
    "Lynx":           "solitary and perceptive; knows what's coming before it arrives",
    "Snow leopard":   "rare and quiet; at home where it's hardest to live",
    "Tiger":          "courage that doesn't need an audience",
    "Lion":           "leads from the front and takes the blows first",
    "Whale":          "vast and calm; its song carries across whole oceans",
    "Dolphin":        "joyful and social; never leaves one of its own behind",
    "Seal":           "at ease in two worlds at once",
    "Tortoise":       "slow, certain, and older than any trouble",
    "Cat":            "independent; chooses its people and keeps them",
    "Hound":          "faithful beyond reason; follows its person anywhere",
    "Mouse":          "brave in small ways, every single day",
    "Squirrel":       "always preparing, so no one goes without",
    "Bat":            "finds its way where there is no light at all",
    "Crane":          "devoted for life; a symbol of long faithfulness",
    "Magpie":         "clever and bright; collects what others overlook",
    "Robin":          "cheerful in the coldest season",
    "Hummingbird":    "tiny, tireless, and full of more life than seems possible",
    "Octopus":        "endlessly inventive; solves what can't be solved",
    "Manta ray":      "moves through the deep with calm, gliding certainty",
    "Ram":            "headstrong; meets every obstacle straight on",
    "Moose":          "gentle until something it loves is threatened",
    "Salmon":         "swims home against every current",

    "Pine marten":       "nimble through the trees; slips danger past its family",
    "Red panda":         "gentle climber; soft courage in high places",
    "Arctic fox":        "thrives in the cold; white against every storm",
    "Kestrel":           "hovers still, then strikes to keep the ground safe",
    "Barn owl":          "silent wings; watches over sleepers",
    "Kingfisher":        "dives true; finds what others miss in the water",
    "Albatross":         "crosses oceans; never loses the way home",
    "Puffin":            "cheerful in harsh wind; keeps its burrow warm",
    "Nightingale":       "sings through the dark so no one feels alone",
    "Lark":              "rises at dawn; carries hope upward",
    "Sparrow":           "small and everywhere; notices every kindness",
    "Wren":              "tiny voice, fierce heart; fills the hedges with life",
    "Blackbird":         "sings from the fence-post; claims the evening",
    "Thrush":            "breaks open hard shells; finds sweetness inside",
    "Dove":              "gentle peace that still refuses to flee",
    "Pigeon":            "finds home from impossible distances",
    "Peacock":           "proud beauty; guards dignity like treasure",
    "Pheasant":          "startles into flight; warns the whole field",
    "Partridge":         "stays low and loyal to its ground",
    "Quail":             "small flock-heart; never wanders far from its own",
    "Goose":             "loud when needed; leads the sky in formation",
    "Duck":              "calm on the surface; paddles hard underneath",
    "Ibis":              "wades the shallows; patient with murky truths",
    "Egret":             "white stillness; purity of focus",
    "Stork":             "long journeys; delivers what was promised",
    "Pelican":           "gathers plenty; shares without keeping score",
    "Cormorant":         "dives deep; brings back what was hidden",
    "Gannet":            "commits fully to the plunge",
    "Petrel":            "dances the storm-wave; at home in rough weather",
    "Tern":              "light over water; precise and tireless",
    "Gull":              "survivor of coasts; clever with scraps and chances",
    "Osprey":            "fisher of clear strikes; takes only what it needs",
    "Buzzard":           "circles wide; sees the whole territory",
    "Kite":              "rides the thermals; patience with height",
    "Harrier":           "skims the marsh; low and thorough",
    "Merlin":            "small falcon-fire; quickest mercy",
    "Goshawk":           "forest thunder; protects the deep wood",
    "Sparrowhawk":       "sudden and sure among the branches",
    "Vulture":           "cleans what others leave; necessary and unafraid",
    "Condor":            "ancient wings; watches centuries pass",
    "Crow":              "remembers faces; keeps clever counsel",
    "Jackdaw":           "curious thief of shiny truths",
    "Rook":              "gathers in councils; strength in numbers",
    "Jay":               "flashes color; warns the wood of strangers",
    "Starling":          "murmurs as one; never truly alone",
    "Goldfinch":         "bright thistle-singer; finds beauty in briars",
    "Bullfinch":         "quiet orchard-keeper; soft and steadfast",
    "Crossbill":         "opens what seems sealed; specialized hope",
    "Waxwing":           "shares fruit down the line; generosity in flocks",
    "Nuthatch":          "walks trunks upside-down; sees another angle",
    "Woodpecker":        "keeps knocking until the truth opens",
    "Swift":             "never lands if it can help it; pure motion",
    "Swallow":           "returns every spring; promise kept",
    "Nightjar":          "dusk-camouflaged; guards the in-between hours",
    "Kea":               "mountain mischief with a loyal streak",
    "Kakapo":            "rare night-parrot; stubborn survival",
    "Kiwi":              "flightless but undeterred; nose to the earth",
    "Emu":               "long-stride guardian of open land",
    "Cassowary":         "formidable and private; do not test its quiet",
    "Flamingo":          "stands one-legged in harsh water; makes it graceful",
    "Spoonbill":         "sweeps the shallows for what others skip",
    "Avocet":            "upturned patience; elegant persistence",
    "Lapwing":           "cries alarm for the whole field",
    "Plover":            "feigns hurt to lead danger away from the nest",
    "Sandpiper":         "quick shore-steps; always moving, always watching",
    "Curlew":            "long call over wetlands; memory of wild places",
    "Snipe":             "zigzag escape; hard to pin down",
    "Moorhen":           "busy water-edge; keeps the pond lively",
    "Coot":              "bald-faced determination on open water",
    "Grebe":             "dives clean; resurfaces somewhere wiser",
    "Loon":              "haunting call; claims the northern lakes",
    "Penguin":           "endures ice together; no one walks alone",
    "Sea otter":         "holds paws so it won't drift from love",
    "Manatee":           "gentle giant of warm rivers",
    "Dugong":            "sea-meadow grazer; peace in blue light",
    "Narwhal":           "unicorn of the deep; rare and true",
    "Beluga":            "white song in arctic water",
    "Orca":              "family hunter; fierce devotion to the pod",
    "Porpoise":          "shy cousin of joy; quieter seas",
    "Blue whale":        "largest heart in the world; still gentle",
    "Humpback":          "breaches for joy; songs across oceans",
    "Grey whale":        "migrates farther than doubt",
    "Walrus":            "tusked and social; hauls out for company",
    "Polar bear":        "endures the white edge of the world",
    "Panda":             "patient bamboo strength; rare and treasured",
    "Koala":             "clings to what feeds it; slow wisdom",
    "Wombat":            "digs deep safety; solid as its burrow",
    "Wallaby":           "small bounce with big heart",
    "Kangaroo":          "carries its young through open danger",
    "Quokka":            "smiles at hard islands; stubborn cheer",
    "Tasmanian devil":   "loud loyalty; fierce for its size",
    "Numbat":            "striped termite-hunter; focused purpose",
    "Bilby":             "soft ears in red dust; fragile hope",
    "Possum":            "plays dead to live; clever surrender",
    "Sugar glider":      "glides between trees; never leaps alone",
    "Flying fox":        "fruit-winged night traveler",
    "Flying squirrel":   "glides rather than falls",
    "Chipmunk":          "cheek-full preparation; tiny providence",
    "Marmot":            "whistles warning across the alpine",
    "Prairie dog":       "town-builder; alarm for the whole colony",
    "Beaver":            "reshapes rivers so others can thrive",
    "Capybara":          "calm among all creatures; peace that spreads",
    "Vole":              "quiet undergrass life; foundation of the field",
    "Shrew":             "fierce metabolism; tiny and relentless",
    "Mole":              "blind certainty underground",
    "Weasel":            "slender courage through tight spaces",
    "Stoat":             "turns white for winter; adapts without apology",
    "Ermine":            "winter-white weasel; elegance in snow",
    "Mink":              "sleek water-hunter; private strength",
    "Ferret":            "playful tunnel-explorer; curious to the end",
    "Wolverine":         "refuses to yield; strength beyond its size",
    "Marten":            "forest acrobat; bright-eyed independence",
    "Sable":             "luxurious quiet; valued and elusive",
    "Coyote":            "sings at edges; thrives where others won't",
    "Jackal":            "desert cleverness; opportunistic care",
    "Dingo":             "wild dog of red earth; free and wary",
    "Caracal":           "tufted ears; desert leap and precision",
    "Serval":            "long-legged pounce; hears the grass breathe",
    "Ocelot":            "spotted night-walker; soft-footed certainty",
    "Jaguar":            "river-roar strength; bites through doubt",
    "Leopard":           "rosette patience; hauls its wins into trees",
    "Cheetah":           "fastest mercy; sprint then rest",
    "Cougar":            "mountain solitude; powerful quiet",
    "Bobcat":            "tufted stub-tail; adaptable hunter",
    "Snowshoe hare":     "turns white with winter; times itself to the cold",
    "Jackrabbit":        "long-ear sprint across open danger",
    "Pika":              "haymaker of the rocks; prepares for long winters",
    "Chinchilla":        "softest armor; leaps between ledges",
    "Tapir":             "ancient nose; paths through the jungle",
    "Boar":              "charges when cornered; tusks for the truth",
    "Warthog":           "kneels to eat, rises to fight",
    "Ibex":              "cliff-dancer; balance as faith",
    "Chamois":           "alpine sure-foot; leaps the gap",
    "Gazelle":           "grace under chase; leaps beyond fear",
    "Impala":            "red-leap abundance; watches while it grazes",
    "Springbok":         "pronks for joy even in hard country",
    "Oryx":              "desert spears; survives on almost nothing",
    "Kudu":              "spiral horns; shy strength in the bush",
    "Eland":             "largest antelope; gentle bulk",
    "Wildebeest":        "migrates with the rains; trusts the journey",
    "Bison":             "shoulder into the storm; herd thunder",
    "Buffalo":           "wall of the grassland; mud and loyalty",
    "Yak":               "high-altitude warmth; carries the cold country",
    "Zebra":             "stripe-dazzle; no two the same",
    "Okapi":             "forest giraffe-secret; quiet rarity",
    "Giraffe":           "sees farther; gentle from a height",
    "Camel":             "stores what the desert demands",
    "Llama":             "spits at nonsense; carries its share",
    "Alpaca":            "soft flock; gentle mountain company",
    "Reindeer":          "carries winter light; finds lichen under snow"
}

# Patronuses that share a shape with a house emblem.
EMBLEM_MATCH = {"Elephant": "vashara", "Bear": "vashara", "Grizzly": "vashara", "Stag": "veyren", "Doe": "veyren", "Wolf": "thornmere"}

READING_PROMPT = """You are reading a student's patronus at Velmora, a school of magic. A patronus is a guardian made of silver light, and it takes the shape of what a person would protect - the thing that keeps them steady when everything else goes dark.

The student once gave three words. The wand read them for temperament. You are reading words for something different: what this person holds dear, and how they guard it.

Do NOT match their words literally. If they wrote an animal's name, do not simply hand that animal back. Read the heart behind the words.

Choose exactly one animal from this shortlist of still-available forms (closest first):
{ranked}

Blurbs:
{blurbs}

Then write:
- "form": one sentence describing how the patronus appears or moves when it first takes shape. Silver, specific, and a little strange.
- "reading": two or three sentences, speaking to the student directly, on why this is the shape their protection takes. Warm, a little uncanny. Refer to what the words revealed, not to the words themselves.

The student's words: "{words}"

Reply with ONLY a JSON object:
{{"animal": "...", "form": "...", "reading": "..."}}"""


def image_name(animal: str) -> str:
    """Filename for a pool patronus portrait."""
    return animal.replace(" ", "_") + ".png"


def _words_key(words: str) -> str:
    toks = sorted(w.lower() for w in re.findall(r"[\w']+", words or ""))
    return " ".join(toks)


def _digest(words: str) -> bytes:
    return hashlib.sha256(("patronus:" + _words_key(words)).encode()).digest()


def preference_rank(words: str) -> list[str]:
    """All pool animals ordered from closest to furthest match for these words."""
    digest = _digest(words)
    key = _words_key(words)

    def score(animal: str) -> tuple:
        h = hashlib.sha256(f"{key}|{animal}".encode()).digest()
        dist = sum(abs(h[i] - digest[i]) for i in range(12))
        return (dist, animal)

    return sorted(ANIMALS, key=score)


def fallback_patronus(words: str, animal: str) -> dict:
    """Cast without the API, locked to a specific still-available animal."""
    return {
        "animal": animal,
        "form": f"The {animal.lower()} gathers itself out of the silver light and stays close.",
        "reading": f"The {animal.lower()} is {ANIMALS[animal]}. That is what your words were guarding.",
    }


def _clean(raw: dict, allowed: set[str]) -> dict | None:
    """Accept the model's answer only if the animal is on the open shortlist."""
    try:
        animal = str(raw["animal"]).strip()
        form = str(raw["form"]).strip()
        reading = str(raw["reading"]).strip()
    except (KeyError, TypeError):
        return None
    match = next((a for a in allowed if a.lower() == animal.lower()), None)
    if not match or not form or not reading:
        return None
    return {"animal": match, "form": form[:300], "reading": reading[:600]}


class Patronus(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()

    def _load(self) -> dict:
        """Normalize legacy flat uid->record files into owners/resets_used."""
        try:
            with open(PATRONUS_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return {"owners": {}, "resets_used": {}}
        except (OSError, json.JSONDecodeError):
            log.exception("Patronus records unreadable - starting empty.")
            return {"owners": {}, "resets_used": {}}

        if isinstance(raw, dict) and "owners" in raw:
            owners = raw.get("owners") or {}
            resets = raw.get("resets_used") or {}
            if not isinstance(owners, dict):
                owners = {}
            if not isinstance(resets, dict):
                resets = {}
            return {"owners": owners, "resets_used": resets}

        # Legacy: {uid: {animal, form, reading}}
        owners = {}
        if isinstance(raw, dict):
            for uid, rec in raw.items():
                if uid.startswith("_"):
                    continue
                if isinstance(rec, dict) and rec.get("animal"):
                    owners[str(uid)] = rec
        return {"owners": owners, "resets_used": {}}

    @property
    def owners(self) -> dict:
        return self.state.setdefault("owners", {})

    @property
    def resets_used(self) -> dict:
        return self.state.setdefault("resets_used", {})

    def save(self) -> None:
        try:
            PATRONUS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = PATRONUS_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, PATRONUS_PATH)
        except OSError:
            log.exception("Could not save patronus records.")

    def custom_of(self, user_id: int) -> dict | None:
        return CUSTOM_PATRONUSES.get(str(user_id))

    def claimed_animals(self) -> dict[str, str]:
        """animal -> user_id for every currently owned pool patronus."""
        out: dict[str, str] = {}
        for uid, rec in self.owners.items():
            if not isinstance(rec, dict):
                continue
            animal = rec.get("animal")
            if animal in ANIMALS and animal not in out:
                out[animal] = uid
        return out

    def available_animals(self) -> list[str]:
        taken = set(self.claimed_animals())
        return [a for a in ANIMALS if a not in taken]

    def assign_animal(self, words: str, user_id: int | None = None) -> str | None:
        """Closest preferred animal that nobody else owns yet."""
        taken = set(self.claimed_animals())
        # Keep this user's current claim available to them (re-cast / race).
        if user_id is not None:
            mine = self.owners.get(str(user_id))
            if isinstance(mine, dict) and mine.get("animal") in taken:
                if self.claimed_animals().get(mine["animal"]) == str(user_id):
                    taken.discard(mine["animal"])
        for animal in preference_rank(words):
            if animal not in taken:
                return animal
        return None

    def patronus_of(self, user_id: int) -> dict | None:
        custom = self.custom_of(user_id)
        if custom:
            out = {
                "animal": custom["animal"],
                "form": custom["form"],
                "reading": custom["reading"],
            }
            if custom.get("label"):
                out["label"] = custom["label"]
            return out
        rec = self.owners.get(str(user_id))
        if not isinstance(rec, dict):
            return None
        return {
            "animal": rec["animal"],
            "form": rec.get("form", ""),
            "reading": rec.get("reading", ""),
        }

    def reset_used_by(self, user_id: int) -> bool:
        return bool(self.resets_used.get(str(user_id)))

    def mark_reset_used(self, user_id: int) -> None:
        self.resets_used[str(user_id)] = True
        self.save()

    @staticmethod
    def headline_for(patronus: dict) -> str:
        """Bold lead line — customs may override with a full epithet."""
        label = (patronus.get("label") or "").strip()
        if label:
            return label
        return f"A silver {patronus['animal'].lower()}"

    def art_path_for(self, user_id: int, *, field: str = "art") -> Path | None:
        custom = self.custom_of(user_id)
        if custom:
            name = custom.get(field) or (custom.get("art") if field == "defeat_art" else None)
            if not name:
                return None
            path = ASSETS_DIR / name
            return path if path.is_file() else None
        # Pool patronuses: one portrait per animal form.
        if field != "art":
            return None
        rec = self.patronus_of(user_id)
        if not rec:
            return None
        path = ASSETS_DIR / image_name(rec["animal"])
        return path if path.is_file() else None

    def art_file_for(self, user_id: int, *, field: str = "art") -> discord.File | None:
        path = self.art_path_for(user_id, field=field)
        if path is None:
            return None
        return discord.File(path, filename="patronus.png")

    def art_file_for_animal(self, animal: str) -> discord.File | None:
        path = ASSETS_DIR / image_name(animal)
        if not path.is_file():
            return None
        return discord.File(path, filename="patronus.png")

    def release(self, user_id: int) -> bool:
        # Custom assignments are code-level — wandreset can't erase them.
        if self.custom_of(user_id):
            return False
        gone = self.owners.pop(str(user_id), None) is not None
        if gone:
            self.save()
        return gone

    async def cast(self, words: str, user_id: int | None = None) -> dict | None:
        animal = self.assign_animal(words, user_id=user_id)
        if animal is None:
            return None

        open_animals = set(self.available_animals())
        if user_id is not None:
            mine = self.owners.get(str(user_id))
            if isinstance(mine, dict) and mine.get("animal") in ANIMALS:
                if self.claimed_animals().get(mine["animal"]) == str(user_id):
                    open_animals.add(mine["animal"])

        ranked = [a for a in preference_rank(words) if a in open_animals][:12]
        if animal not in ranked:
            ranked = [animal] + ranked

        wands = self.bot.get_cog("Wands")
        client = getattr(wands, "client", None)
        if client is not None:
            prompt = READING_PROMPT.format(
                ranked=", ".join(ranked),
                blurbs="\n".join(f"- {k}: {ANIMALS[k]}" for k in ranked),
                words=words.replace('"', "'")[:200],
            )
            try:
                response = await client.messages.create(
                    model=MODEL, max_tokens=400,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = "".join(getattr(b, "text", "") for b in response.content)
                match = re.search(r"\{.*\}", text, re.S)
                if match:
                    result = _clean(json.loads(match.group(0)), set(ranked))
                    if result:
                        claimed = self.claimed_animals()
                        owner = claimed.get(result["animal"])
                        if owner is None or owner == str(user_id):
                            return result
                        alt = self.assign_animal(words, user_id=user_id)
                        if alt:
                            return fallback_patronus(words, alt)
                log.warning("Patronus reading came back unusable; using the fallback.")
            except Exception:
                log.exception("Patronus reading failed; using the fallback.")

        pick = self.assign_animal(words, user_id=user_id)
        if pick is None:
            return None
        return fallback_patronus(words, pick)

    def embed_for(
        self,
        member,
        patronus: dict,
        fresh: bool = False,
        *,
        with_art: bool = False,
        reset: bool = False,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=("Silver light spills from the wand…" if fresh
                   else f"{member.display_name}'s patronus"),
            description=(f"**{self.headline_for(patronus)}**\n"
                         f"*{patronus['form']}*\n\n{patronus['reading']}"),
            color=SILVER,
        )
        house = EMBLEM_MATCH.get(patronus["animal"])
        if house:
            from cogs.store import HOUSES
            embed.add_field(name="​",
                            value=f"It shares its shape with {HOUSES[house]['emoji']} "
                                  f"House {HOUSES[house]['name']}'s emblem.", inline=False)
        if with_art:
            embed.set_image(url="attachment://patronus.png")
        left = len(self.available_animals())
        if fresh:
            note = "recast from new words" if reset else "cast from the words that chose their wand"
            embed.set_footer(text=f"{member.display_name}'s patronus • {note} • "
                                  f"{left} forms still unclaimed")
        elif self.custom_of(member.id):
            embed.set_footer(text=f"{member.display_name}'s patronus • headmaster-sealed")
        else:
            embed.set_footer(text=f"Unique form • {left} still unclaimed")
        return embed

    async def _send_patronus(
        self,
        interaction: discord.Interaction,
        member,
        patronus: dict,
        *,
        fresh: bool = False,
        followup: bool = False,
        reset: bool = False,
    ) -> None:
        art = self.art_file_for(member.id)
        if art is None and patronus.get("animal"):
            art = self.art_file_for_animal(patronus["animal"])
        embed = self.embed_for(
            member, patronus, fresh=fresh, with_art=art is not None, reset=reset
        )
        kwargs = {"embed": embed}
        if art is not None:
            kwargs["file"] = art
        if followup:
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)

    @app_commands.command(name="patronus",
                          description="Cast your unique patronus from the words that chose your wand.")
    @app_commands.describe(member="Whose patronus to see (leave blank for your own)")
    async def patronus(self, interaction: discord.Interaction, member: discord.Member = None):
        hexes = self.bot.get_cog("Hexes")
        if hexes and await hexes.deny_if_limp_wand(interaction):
            return

        target = member or interaction.user

        existing = self.patronus_of(target.id)
        if existing:
            await self._send_patronus(interaction, target, existing)
            return

        if member and member.id != interaction.user.id:
            await interaction.response.send_message(
                f"{member.display_name} hasn't cast a patronus yet.", ephemeral=True
            )
            return

        wands = self.bot.get_cog("Wands")
        wand = wands.wand_of(target.id) if wands else None
        if not wand or not wand.get("words"):
            await interaction.response.send_message(
                "Your patronus answers to the same words that chose your wand — "
                "and no wand has chosen you yet. Start with `/wand`.",
                ephemeral=True,
            )
            return

        if not self.available_animals():
            await interaction.response.send_message(
                "Every patronus form in Velmora has already been claimed.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        result = await self.cast(wand["words"], user_id=target.id)
        if result is None:
            await interaction.followup.send(
                "Every patronus form in Velmora has already been claimed.",
                ephemeral=True,
            )
            return
        # Race check
        claimed = self.claimed_animals()
        owner = claimed.get(result["animal"])
        if owner and owner != str(target.id):
            alt = self.assign_animal(wand["words"], user_id=target.id)
            if alt is None:
                await interaction.followup.send(
                    "Every patronus form in Velmora has already been claimed.",
                    ephemeral=True,
                )
                return
            result = fallback_patronus(wand["words"], alt)

        self.owners[str(target.id)] = {
            **result,
            "words": wand["words"],
        }
        self.save()
        await self._send_patronus(
            interaction, target, result, fresh=True, followup=True
        )

    @app_commands.command(
        name="patronusreset",
        description="Once only: give three new words and claim a new unique patronus.",
    )
    async def patronusreset(self, interaction: discord.Interaction):
        hexes = self.bot.get_cog("Hexes")
        if hexes and await hexes.deny_if_limp_wand(interaction):
            return

        uid = interaction.user.id
        if self.custom_of(uid):
            await interaction.response.send_message(
                "Your patronus is headmaster-sealed — it can't be reset.",
                ephemeral=True,
            )
            return

        if not self.patronus_of(uid):
            await interaction.response.send_message(
                "You don't have a patronus to reset yet. Cast one with `/patronus` first.",
                ephemeral=True,
            )
            return

        if self.reset_used_by(uid):
            await interaction.response.send_message(
                "You've already used your one patronus reset.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(PatronusResetModal(self))

    async def staff_patronusreset(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        """Staff: new patronus words for someone else; wand untouched."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message(
                "That one's for staff.", ephemeral=True
            )
            return
        if self.custom_of(member.id):
            await interaction.response.send_message(
                f"{member.display_name}'s patronus is headmaster-sealed — it can't be reset.",
                ephemeral=True,
            )
            return
        if not self.patronus_of(member.id):
            await interaction.response.send_message(
                f"{member.display_name} doesn't have a patronus to reset yet.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            StaffPatronusResetModal(self, member)
        )


class PatronusResetModal(discord.ui.Modal, title="A new patronus"):
    words = discord.ui.TextInput(
        label="Tell me any three words",
        placeholder="New words. Any order. Whatever comes to you now.",
        max_length=120,
    )

    def __init__(self, cog: Patronus):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        hexes = self.cog.bot.get_cog("Hexes")
        if hexes and await hexes.deny_if_limp_wand(interaction):
            return

        uid = interaction.user.id
        if self.cog.custom_of(uid):
            await interaction.response.send_message(
                "Your patronus is headmaster-sealed — it can't be reset.",
                ephemeral=True,
            )
            return
        if self.cog.reset_used_by(uid):
            await interaction.response.send_message(
                "You've already used your one patronus reset.",
                ephemeral=True,
            )
            return
        if not self.cog.patronus_of(uid):
            await interaction.response.send_message(
                "You don't have a patronus to reset yet.",
                ephemeral=True,
            )
            return

        words = str(self.words.value or "").strip()
        toks = re.findall(r"[\w']+", words)
        if len(toks) < 3:
            await interaction.response.send_message(
                "I need at least three words to read a new patronus.",
                ephemeral=True,
            )
            return

        # Free the old form before assigning so it can be reclaimed by others
        # (and so this caster isn't stuck if their old animal was the only match).
        old = self.cog.owners.pop(str(uid), None)
        # Don't save yet — commit with the new claim + reset flag together.

        await interaction.response.defer(thinking=True)
        result = await self.cog.cast(words, user_id=uid)
        if result is None:
            # Restore previous claim if the pool is exhausted.
            if old is not None:
                self.cog.owners[str(uid)] = old
                self.cog.save()
            await interaction.followup.send(
                "Every other patronus form is already claimed — yours stays as it was.",
                ephemeral=True,
            )
            return

        claimed = self.cog.claimed_animals()
        owner = claimed.get(result["animal"])
        if owner and owner != str(uid):
            alt = self.cog.assign_animal(words, user_id=uid)
            if alt is None:
                if old is not None:
                    self.cog.owners[str(uid)] = old
                    self.cog.save()
                await interaction.followup.send(
                    "Every other patronus form is already claimed — yours stays as it was.",
                    ephemeral=True,
                )
                return
            result = fallback_patronus(words, alt)

        self.cog.owners[str(uid)] = {
            **result,
            "words": words,
            "reset": True,
        }
        self.cog.resets_used[str(uid)] = True
        self.cog.save()
        await self.cog._send_patronus(
            interaction, interaction.user, result, fresh=True, followup=True, reset=True
        )


class StaffPatronusResetModal(discord.ui.Modal, title="Staff: new patronus"):
    words = discord.ui.TextInput(
        label="Three words for their new patronus",
        placeholder="Any order. Wand stays — only the patronus changes.",
        max_length=120,
    )

    def __init__(self, cog: Patronus, member: discord.Member):
        super().__init__()
        self.cog = cog
        self.member = member

    async def on_submit(self, interaction: discord.Interaction):
        store = self.cog.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message(
                "That one's for staff.", ephemeral=True
            )
            return

        uid = self.member.id
        if self.cog.custom_of(uid):
            await interaction.response.send_message(
                f"{self.member.display_name}'s patronus is headmaster-sealed — "
                "it can't be reset.",
                ephemeral=True,
            )
            return
        if not self.cog.patronus_of(uid):
            await interaction.response.send_message(
                f"{self.member.display_name} doesn't have a patronus to reset yet.",
                ephemeral=True,
            )
            return

        words = str(self.words.value or "").strip()
        toks = re.findall(r"[\w']+", words)
        if len(toks) < 3:
            await interaction.response.send_message(
                "I need at least three words to read a new patronus.",
                ephemeral=True,
            )
            return

        old = self.cog.owners.pop(str(uid), None)

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.cast(words, user_id=uid)
        if result is None:
            if old is not None:
                self.cog.owners[str(uid)] = old
                self.cog.save()
            await interaction.followup.send(
                f"Every other patronus form is already claimed — "
                f"{self.member.display_name}'s stays as it was.",
                ephemeral=True,
            )
            return

        claimed = self.cog.claimed_animals()
        owner = claimed.get(result["animal"])
        if owner and owner != str(uid):
            alt = self.cog.assign_animal(words, user_id=uid)
            if alt is None:
                if old is not None:
                    self.cog.owners[str(uid)] = old
                    self.cog.save()
                await interaction.followup.send(
                    f"Every other patronus form is already claimed — "
                    f"{self.member.display_name}'s stays as it was.",
                    ephemeral=True,
                )
                return
            result = fallback_patronus(words, alt)

        # Wand words stay on the Wands cog. Don't spend their one player reset.
        self.cog.owners[str(uid)] = {
            **result,
            "words": words,
            "reset": True,
            "staff_reset": True,
        }
        self.cog.save()
        await interaction.followup.send(
            f"Reset {self.member.display_name}'s patronus (wand unchanged).",
            ephemeral=True,
        )
        await self.cog._send_patronus(
            interaction,
            self.member,
            result,
            fresh=True,
            followup=True,
            reset=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Patronus(bot))
