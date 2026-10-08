"""
Headmaster hexes - a prank spell a Headmaster can cast on a student so that,
without warning, whatever they type comes out cursed — or their wand goes limp.

    /staff hex cast member:<@user> effect:<pick one> duration:<minutes>
    /staff hex lift member:<@user>
    /staff hex list

Most curses mangle chat: the bot deletes the cursed member's message and
reposts it through a per-channel webhook wearing their name and avatar.
Limp Wand is different — it leaves chat alone and blocks /wand, /patronus,
/broom, and /cast for one hour.

Needs the bot to hold Manage Messages (to delete the original) and Manage
Webhooks (to create/reuse the relay webhook) in this server.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("velmora.hexes")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "hexes_state.json"

HEADMASTER_ROLE_NAME = "headmasters"  # matched case-insensitively; rename here if the role's name changes
RELAY_WEBHOOK_NAME = "Velmora Hex Relay"

WORD = re.compile(r"[A-Za-z']+")
WORD_WITH_EDGES = re.compile(r"^([^a-zA-Z]*)([a-zA-Z]+)([^a-zA-Z]*)$")
VOWELS = "aeiouAEIOU"

CHEER_EMOJIS = ["📣", "🎉", "✨", "💜", "🙌", "🏆", "⭐"]
CHEER_OPENERS = ["OMG okay so like,", "Ahh wait,", "Okay but like,", "Bestie,", "Okay okay but hear me out,"]
CHEER_CLOSERS = ["GO VELMORA!!", "VELMORA PRIDE FOREVER", "we are SO velmora", "V-E-L-M-O-R-A!!", "GO GO VELMORA!!"]

# Longer phrases first — applied before single-word swaps.
PIRATES_PHRASE_MAP = [
    (r"\bi don't know\b", "I know not"),
    (r"\bi dont know\b", "I know not"),
    (r"\bi'm going to\b", "I be settin' sail to"),
    (r"\bim going to\b", "I be settin' sail to"),
    (r"\bgoing to\b", "settin' sail to"),
    (r"\bgotta\b", "got to"),
    (r"\bhave to\b", "got to"),
    (r"\bhas to\b", "got to"),
    (r"\bwant to\b", "be wantin' to"),
    (r"\bwanna\b", "be wantin' to"),
    (r"\btrying to\b", "tryin' to"),
    (r"\btry to\b", "try to"),
    (r"\bwhat's up\b", "what be the word"),
    (r"\bwhats up\b", "what be the word"),
    (r"\bhow are you\b", "how fare ye"),
    (r"\bhow r u\b", "how fare ye"),
    (r"\bthank you\b", "thankee kindly"),
    (r"\bthanks\b", "thankee"),
    (r"\bgood morning\b", "fine mornin'"),
    (r"\bgood night\b", "fair winds this night"),
    (r"\bgood evening\b", "fine evenin'"),
    (r"\bsee you later\b", "until we cross paths again"),
    (r"\bsee you\b", "fair winds"),
    (r"\bbye bye\b", "fair winds"),
    (r"\bbye\b", "fair winds"),
    (r"\bi'm sorry\b", "beggin' yer pardon"),
    (r"\bim sorry\b", "beggin' yer pardon"),
    (r"\bi am sorry\b", "beggin' yer pardon"),
    (r"\bsorry\b", "beggin' yer pardon"),
    (r"\bi love you\b", "I be fond o' ye"),
    (r"\bi like you\b", "I be keen on ye"),
    (r"\blet's go\b", "weigh anchor"),
    (r"\blets go\b", "weigh anchor"),
    (r"\bcome on\b", "look lively"),
    (r"\bhurry up\b", "lively now"),
    (r"\bshut up\b", "stow it"),
    (r"\bbe quiet\b", "stow it"),
    (r"\bhelp me\b", "lend me a hand"),
    (r"\bhelp you\b", "lend ye a hand"),
    (r"\bi think\b", "methinks"),
    (r"\bi believe\b", "I reckon"),
    (r"\bi guess\b", "I reckon"),
    (r"\bi mean\b", "what I mean be"),
    (r"\bright now\b", "this very tide"),
    (r"\bright away\b", "at once"),
    (r"\ba lot\b", "a heap"),
    (r"\ba bit\b", "a wee bit"),
    (r"\bokey dokey\b", "aye aye"),
    (r"\ball right\b", "aye"),
    (r"\balright\b", "aye"),
]
PIRATES_TONGUE_MAP = {
    # pronouns / be-verbs
    "i": "I", "i'm": "I be", "im": "I be", "i've": "I have", "ive": "I have",
    "i'll": "I shall", "i'd": "I'd",
    "you": "ye", "you'd": "ye'd", "you'll": "ye'll", "you've": "ye've",
    "your": "yer", "yours": "yer own", "you're": "ye be", "youre": "ye be",
    "yourself": "yerself", "yourselves": "yerselves",
    "my": "me", "mine": "me own", "myself": "meself",
    "me": "me", "we": "we", "we're": "we be", "were": "were",  # "were" stays (past)
    "we've": "we have", "we'll": "we shall", "our": "our", "ours": "our own",
    "they": "they", "they're": "they be", "theyre": "they be",
    "their": "their", "theirs": "their own", "them": "them",
    "he": "he", "she's": "she be", "shes": "she be", "he's": "he be", "hes": "he be",
    "his": "his", "her": "her", "hers": "hers",
    "it": "it", "it's": "it be", "its": "its",
    "is": "be", "are": "be", "am": "be", "was": "were", "been": "been",
    "isn't": "ain't", "aren't": "ain't", "wasn't": "weren't", "weren't": "weren't",
    "don't": "don't", "doesn't": "don't", "didn't": "didn't",
    "can't": "can't", "cannot": "can't", "won't": "won't", "wouldn't": "wouldn't",
    "shouldn't": "shouldn't", "couldn't": "couldn't",
    "have": "have", "has": "has", "had": "had",
    "do": "do", "does": "does", "did": "did",
    "will": "shall", "shall": "shall", "would": "would", "could": "could",
    "should": "should", "may": "may", "might": "might", "must": "must",
    # greetings / answers
    "hello": "ahoy", "hi": "ahoy", "hey": "ahoy", "hiya": "ahoy", "sup": "ahoy",
    "yes": "aye", "yeah": "aye", "yep": "aye", "yup": "aye", "yea": "aye",
    "no": "nay", "nope": "nay", "nah": "nay",
    "okay": "aye aye", "ok": "aye aye", "k": "aye", "kk": "aye aye",
    "please": "if ye please", "pls": "if ye please", "plz": "if ye please",
    # people
    "friend": "matey", "friends": "mateys", "buddy": "matey", "bud": "matey",
    "dude": "scallywag", "man": "lad", "guy": "lad", "guys": "lads",
    "girl": "lass", "girls": "lasses", "boy": "lad", "boys": "lads",
    "bro": "matey", "bruh": "matey", "sis": "lass",
    "person": "soul", "people": "crew", "everyone": "all hands",
    "somebody": "some soul", "someone": "some soul", "anybody": "any soul",
    "anyone": "any soul", "nobody": "nary a soul", "noone": "nary a soul",
    "kid": "cabin boy", "kids": "cabin boys", "child": "cabin boy",
    "children": "cabin boys", "baby": "wee one",
    "boss": "captain", "teacher": "captain", "staff": "officers",
    "idiot": "landlubber", "fool": "fool", "loser": "bilge rat",
    "enemy": "sworn foe", "enemies": "sworn foes",
    # places / things
    "house": "crew", "home": "quarters", "room": "cabin", "school": "academy",
    "bathroom": "head", "toilet": "head", "kitchen": "galley",
    "floor": "deck", "ground": "deck", "wall": "bulkhead", "door": "hatch",
    "window": "porthole", "stairs": "ladder", "bed": "hammock",
    "car": "ship", "truck": "ship", "bus": "ship", "boat": "ship",
    "plane": "sky-ship", "train": "iron ship", "bike": "land skiff",
    "phone": "speaking horn", "computer": "thinking box", "laptop": "thinking box",
    "internet": "the wide seas", "online": "aboard", "offline": "ashore",
    "message": "dispatch", "chat": "galley talk", "server": "crew",
    "channel": "deck", "discord": "the great tavern",
    "money": "doubloons", "cash": "doubloons", "dollars": "doubloons",
    "dollar": "doubloon", "points": "doubloons", "gold": "gold",
    "drink": "grog", "drinks": "grog", "alcohol": "grog", "beer": "grog",
    "wine": "grog", "coffee": "bitter grog", "tea": "leaf grog",
    "water": "fresh water", "soda": "fizzin' grog",
    "food": "grub", "meal": "grub", "dinner": "grub", "lunch": "grub",
    "breakfast": "mornin' grub", "snack": "ship's biscuit",
    "treasure": "booty", "loot": "booty", "prize": "booty",
    "map": "chart", "book": "tome", "story": "yarn", "joke": "yarn",
    "song": "shanty", "music": "shanty", "party": "revel",
    "fight": "scuffle", "battle": "skirmish", "war": "war",
    "game": "contest", "match": "contest", "win": "claim victory",
    "won": "claimed victory", "lose": "be bested", "lost": "were bested",
    "work": "duties", "job": "duties", "homework": "ship's duties",
    "class": "lesson", "test": "trial", "exam": "trial",
    "problem": "trouble", "issue": "trouble", "bug": "barnacle",
    "error": "blunder", "mistake": "blunder",
    # verbs / adjectives / fillers
    "stop": "avast", "wait": "hold fast", "hold": "hold fast",
    "look": "spy", "see": "spy", "watch": "keep watch",
    "go": "sail", "goes": "sails", "went": "sailed", "gone": "sailed off",
    "come": "come aboard", "coming": "comin' aboard",
    "leave": "shove off", "left": "shoved off", "leaving": "shovin' off",
    "run": "make haste", "running": "makin' haste", "ran": "made haste",
    "walk": "trudge", "walking": "trudgin'",
    "talk": "speak", "talking": "speakin'", "speak": "speak",
    "say": "say", "said": "said", "tell": "tell", "told": "told",
    "ask": "ask", "asked": "asked", "answer": "answer",
    "help": "aid", "helping": "aidin'", "helped": "aided",
    "need": "be needin'", "needs": "be needin'", "needed": "were needin'",
    "want": "be wantin'", "wants": "be wantin'", "wanted": "were wantin'",
    "like": "be fond o'", "likes": "be fond o'", "liked": "were fond o'",
    "love": "be smitten with", "loves": "be smitten with", "loved": "were smitten with",
    "hate": "be cursed by", "hates": "be cursed by",
    "know": "know", "knows": "knows", "knew": "knew",
    "think": "reckon", "thinks": "reckons", "thought": "reckoned",
    "feel": "feel", "feels": "feels", "felt": "felt",
    "get": "get", "got": "got", "getting": "gettin'",
    "give": "hand over", "gives": "hands over", "gave": "handed over",
    "take": "seize", "takes": "seizes", "took": "seized",
    "make": "make", "makes": "makes", "made": "made",
    "find": "find", "finds": "finds", "found": "found",
    "kill": "send to Davy Jones", "die": "meet Davy Jones", "dead": "gone to Davy Jones",
    "sleep": "rest yer bones", "sleeping": "restin' yer bones", "slept": "rested yer bones",
    "wake": "rise", "woke": "rose", "awake": "risen",
    "eat": "feast on", "eats": "feasts on", "ate": "feasted on", "eating": "feastin' on",
    "drink": "drink", "drinking": "drinkin'", "drank": "drank",
    "good": "fine", "great": "mighty fine", "awesome": "legendary",
    "amazing": "legendary", "cool": "fine", "nice": "fine", "fine": "fine",
    "bad": "foul", "terrible": "cursed", "awful": "cursed",
    "ugly": "barnacle-faced", "stupid": "daft", "dumb": "daft", "weird": "strange",
    "crazy": "mad as a storm", "funny": "a fine yarn", "sad": "downhearted",
    "happy": "merry", "angry": "cross", "scared": "yellow-bellied",
    "tired": "weary", "bored": "idle", "busy": "hard at work",
    "big": "mighty", "small": "wee", "little": "wee", "huge": "vast",
    "fast": "swift", "slow": "sluggish", "strong": "stout", "weak": "feeble",
    "new": "fresh", "old": "ancient", "young": "green",
    "true": "true", "false": "false", "real": "true", "fake": "counterfeit",
    "very": "mighty", "really": "truly", "so": "so", "too": "too",
    "just": "just", "only": "only", "also": "also", "even": "even",
    "here": "here", "there": "yonder", "where": "whereabouts",
    "when": "when", "why": "why", "how": "how", "what": "what", "who": "who",
    "which": "which", "this": "this", "that": "that", "these": "these", "those": "those",
    "now": "now", "then": "then", "today": "this day", "tomorrow": "the morrow",
    "yesterday": "yestertide", "tonight": "this night", "morning": "mornin'",
    "night": "night", "evening": "evenin'", "afternoon": "afternoon",
    "always": "always", "never": "ne'er", "sometimes": "now and again",
    "maybe": "mayhap", "perhaps": "mayhap", "probably": "like as not",
    "because": "on account o'", "about": "about", "around": "about",
    "with": "with", "without": "without", "from": "from", "into": "into",
    "onto": "onto", "over": "over", "under": "under", "before": "afore",
    "after": "after", "again": "again", "back": "back", "away": "away",
    "up": "aloft", "down": "below", "out": "out", "in": "in", "on": "on", "off": "off",
    "of": "o'", "the": "th'", "a": "a", "an": "an", "and": "an'", "or": "or",
    "but": "but", "if": "if", "as": "as", "than": "than", "for": "fer",
    "to": "t'", "at": "at", "by": "by",
}
PIRATE_OPENERS = [
    "Arr, ", "Yarr, ", "Avast — ", "Ahoy — ", "Shiver me timbers — ",
    "By the powers — ", "Listen well — ", "Hear me now — ",
]
PIRATE_CLOSERS = [
    ", arr!", " arrr!", ", yarrr!", ", matey!", ", ye scallywag!",
    " — savvy?", ", aye!", " Ho!", ", or walk the plank!",
]
COUNTRY_MAP = {
    "you": "y'all", "your": "yer", "you're": "y'all're", "yours": "yer own",
    "gonna": "fixin' to", "going": "fixin'", "friend": "partner", "friends": "partners",
    "hello": "howdy", "hi": "howdy", "hey": "howdy", "man": "fella", "guy": "fella", "dude": "fella",
    "girl": "gal", "boy": "young'un", "yes": "reckon so", "yeah": "reckon so", "yep": "yessir",
    "no": "nah, reckon not", "isn't": "ain't", "aren't": "ain't", "wasn't": "weren't",
    "is not": "ain't", "cool": "mighty fine", "great": "mighty fine", "awesome": "plumb amazing",
    "very": "plumb", "really": "sure as shootin'", "car": "truck", "money": "coin",
    "okay": "reckon so", "ok": "reckon so", "food": "grub", "crazy": "loco",
}
CHEER_MAP = {
    "yes": "YES OMG YES", "hi": "OMG HII", "hello": "OMG HELLO", "hey": "OMG HEYY",
    "good": "like SO good", "great": "literally amazing", "cool": "so iconic", "nice": "so iconic",
    "no": "no way, like NO", "friend": "bestie", "friends": "besties",
    "happy": "like SO happy", "excited": "SO hyped", "fun": "literally SO fun",
    "love": "am OBSESSED with", "like": "am OBSESSED with",
}
CAVEMAN_PRONOUN_MAP = {"i": "me", "my": "me", "we": "us", "our": "us", "myself": "me"}
CAVEMAN_FILLERS = {
    "a", "an", "the", "is", "are", "am", "was", "were", "be", "been", "being",
    "to", "of", "and", "but", "that", "this", "these", "those", "so", "very",
    "really", "just", "then", "well", "actually", "basically", "kind", "sort",
    "like", "um", "uh", "also", "too", "quite", "rather", "perhaps", "maybe",
    "please", "would", "could", "should", "will", "shall", "there", "here",
}


def _split_word(word: str) -> Optional[tuple[str, str, str]]:
    m = WORD_WITH_EDGES.match(word)
    return m.groups() if m else None  # (leading punctuation, core letters, trailing punctuation)


def _match_case(core: str, replacement: str) -> str:
    if core.isupper():
        return replacement.upper()
    if core[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


def _word_swap(text: str, mapping: dict) -> str:
    def repl(m: re.Match) -> str:
        w = m.group(0)
        rep = mapping.get(w.lower())
        if rep is None:
            return w
        return _match_case(w, rep)
    return WORD.sub(repl, text)


def fx_reversed(text: str) -> str:
    return text[::-1]


def fx_runon(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", text)


def fx_piglatin(text: str) -> str:
    def repl(m: re.Match) -> str:
        parts = _split_word(m.group(0))
        if not parts:
            return m.group(0)
        lead, core, trail = parts
        if core[0] in VOWELS:
            new = core + "way"
        else:
            i = 0
            while i < len(core) and core[i] not in VOWELS:
                i += 1
            new = core[i:] + core[:i] + "ay"
        return lead + _match_case(core, new) + trail
    return re.sub(r"[A-Za-z]+", repl, text)


def fx_country(text: str) -> str:
    text = _word_swap(text, COUNTRY_MAP)
    if random.random() < 0.4:
        text = text.rstrip() + random.choice([", y'all.", " — mighty fine, partner.", ", I reckon.",
                                              " ...that's the way it be, pardner."])
    return text


def fx_stutter(text: str) -> str:
    def repl(m: re.Match) -> str:
        parts = _split_word(m.group(0))
        if not parts or random.random() > 0.35:
            return m.group(0)
        lead, core, trail = parts
        if len(core) < 2:
            return m.group(0)
        n = 1 if len(core) <= 3 else random.choice([1, 2])
        return f"{lead}{core[:n]}-{core}{trail}"
    return re.sub(r"[A-Za-z]+", repl, text)


def fx_cheerleader(text: str) -> str:
    text = _word_swap(text, CHEER_MAP)
    if random.random() < 0.5:
        text = f"{random.choice(CHEER_OPENERS)} {text}"
    words = text.split(" ")
    out = []
    for w in words:
        out.append(w)
        if random.random() < 0.15:
            out.append(random.choice(CHEER_EMOJIS))
    text = " ".join(out)
    if random.random() < 0.6:
        text = f"{text.rstrip()} {random.choice(CHEER_CLOSERS)} {random.choice(CHEER_EMOJIS)}"
    return text


def _every_nth_word(text: str, replacement: str, n: int = 3) -> str:
    counter = 0

    def repl(m: re.Match) -> str:
        nonlocal counter
        parts = _split_word(m.group(0))
        core = parts[1] if parts else m.group(0)
        counter += 1
        if counter % n == 0:
            return _match_case(core, replacement)
        return m.group(0)
    return re.sub(r"[A-Za-z']+", repl, text)


def fx_frog(text: str) -> str:
    return _every_nth_word(text, "ribbit")


def fx_cat(text: str) -> str:
    text = _every_nth_word(text, "meow")

    def sentence_repl(m: re.Match) -> str:
        return f"{m.group(0)} *purrrr*"
    new_text, n = re.subn(r"[.!?]+", sentence_repl, text)
    return new_text if n else f"{text.rstrip()} *purrrr*"


def fx_caveman(text: str) -> str:
    text = _word_swap(text, CAVEMAN_PRONOUN_MAP)

    def repl(m: re.Match) -> str:
        return "" if m.group(0).lower() in CAVEMAN_FILLERS else m.group(0)
    result = re.sub(r"[A-Za-z']+", repl, text)
    result = re.sub(r"\s{2,}", " ", result)
    result = re.sub(r"\s+([,.!?])", r"\1", result).strip()
    if not result:
        result = "Ugh."
    prefix = random.choice(["Ugh. ", "Grr. ", "Hrm. ", ""])
    suffix = random.choice([" Ugh!", " Grr!", ""])
    return f"{prefix}{result}{suffix}".strip()


def _pirate_ing(text: str) -> str:
    """Turn trailing -ing into piratey -in' (skip short / already-swapped bits)."""

    def repl(m: re.Match) -> str:
        word = m.group(0)
        lower = word.lower()
        if lower.endswith("in'") or len(lower) < 5:
            return word
        if not lower.endswith("ing"):
            return word
        # Keep things like "thing", "ring", "king", "sing" alone.
        stem = lower[:-3]
        if stem in {"th", "r", "k", "s", "w", "br", "str", "sw", "cl", "fl", "sl"}:
            return word
        base = word[:-3] + ("in'" if word[-3:].islower() else "IN'")
        return base

    return re.sub(r"[A-Za-z']+", repl, text)


def fx_pirates_tongue(text: str) -> str:
    """Full pirate speech: phrases, lexicon, -in' endings, and salty bookends."""
    if not text or not text.strip():
        return random.choice(["Arr!", "Yarr!", "Ahoy!", "Avast!"])

    out = text
    for pattern, replacement in PIRATES_PHRASE_MAP:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)

    out = _word_swap(out, PIRATES_TONGUE_MAP)
    out = _pirate_ing(out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out).strip()
    if not out:
        out = "Arr"

    # Nearly always bookend with pirate flavor so short messages still feel cursed.
    if random.random() < 0.8 and not re.match(
        r"^(arr|yarr|avast|ahoy|shiver|by the powers|listen well|hear me)\b",
        out,
        flags=re.IGNORECASE,
    ):
        out = random.choice(PIRATE_OPENERS) + out
    if random.random() < 0.9 and not re.search(
        r"(arr+|yarrr*|savvy\?|matey!|scallywag!|plank!|ho!|aye!)\s*$",
        out,
        flags=re.IGNORECASE,
    ):
        out = out.rstrip(".!?") + random.choice(PIRATE_CLOSERS)
    return out


EFFECTS = {
    "reversed": {"name": "Backwards Curse", "description": "Every word comes out back-to-front.",
                "func": fx_reversed},
    "runon": {"name": "Run-On Curse", "description": "Deletes every space and punctuation mark - it all runs "
             "together into one impossible word.", "func": fx_runon},
    "piglatin": {"name": "Pig Latin Curse", "description": "Twists their words into Pig Latin.",
                "func": fx_piglatin},
    "country": {"name": "Cowboy Curse", "description": "Curses them to talk like they've never left the deep "
               "woods - full cowboy, full country twang.", "func": fx_country},
    "stutter": {"name": "Stutter Curse", "description": "Makes them stutter over random syllables.",
               "func": fx_stutter},
    "cheerleader": {"name": "Cheerleader Curse", "description": "Curses them into the most school-spirited Velmora "
                    "cheerleader alive.", "func": fx_cheerleader},
    "frog": {"name": "Frog Curse", "description": "Turns every third word into a ribbit.", "func": fx_frog},
    "cat": {"name": "Cat Curse", "description": "Turns every third word into a meow, and ends every sentence "
           "with a *purrrr*.", "func": fx_cat},
    "caveman": {"name": "Caveman Curse", "description": "Strips out every filler word - grunts and caveman talk "
               "only.", "func": fx_caveman},
    "pirates_tongue": {
        "name": "Pirate Curse",
        "description": "Full pirate speech — every line comes out as salty sailor talk.",
        "func": fx_pirates_tongue,
    },
    "limp_wand": {
        "name": "Limp Wand",
        "description": "Their wand hangs limp — /wand, /patronus, and /broom won't answer for an hour.",
        "func": None,  # not a chat mangle; blocks wand commands instead
        "blocks_wand": True,
        "fixed_minutes": 60,
    },
}

# Player wand-kit slash commands blocked by Limp Wand (staff resets stay usable).
WAND_KIT_COMMANDS = frozenset({"wand", "patronus", "broom"})

CAST_FLOURISHES = [
    "draws their wand with a flourish and levels it at",
    "steps forward, robes billowing, and points their wand straight at",
    "doesn't even blink before aiming their wand at",
    "raises their wand overhead like a conductor and swings it toward",
    "flicks their wand once, almost bored, in the direction of",
    "spins their wand once around a finger before snapping it toward",
]


class Hexes(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self._webhooks: dict[int, discord.Webhook] = {}  # channel_id -> cached relay webhook

    async def cog_load(self):
        self.expiry_sweep.start()

    async def cog_unload(self):
        self.expiry_sweep.cancel()

    # ------------------------------------------------------------- storage

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            state = {}
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read %s", STATE_PATH)
            state = {}
        state.setdefault("hexed", {})
        return state

    def save(self):
        tmp = STATE_PATH.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save %s", STATE_PATH)

    # -------------------------------------------------------- permissions

    @staticmethod
    def _is_headmaster(member: discord.Member) -> bool:
        perms = getattr(member, "guild_permissions", None)
        if perms is not None and (perms.administrator or perms.manage_guild):
            return True
        return any(r.name.lower() == HEADMASTER_ROLE_NAME for r in getattr(member, "roles", []))

    # ------------------------------------------------------------ casting

    def apply_hex(self, *, target_id: int, effect: str, duration_minutes: int | None,
                  cast_by: int) -> tuple[dict, bool]:
        """Apply a hex by effect key. duration_minutes None/0 = until lifted.
        Limp Wand always lasts its fixed_minutes (60). Returns (spell dict,
        was_replacing_existing). Raises KeyError if effect unknown."""
        spell = EFFECTS[effect]
        was_hexed = str(target_id) in self.state["hexed"]
        fixed = spell.get("fixed_minutes")
        if fixed:
            duration_minutes = int(fixed)
        expires_at = None if not duration_minutes else time.time() + duration_minutes * 60
        self.state["hexed"][str(target_id)] = {
            "effect": effect, "expires_at": expires_at, "cast_by": cast_by,
        }
        self.save()
        return spell, was_hexed

    def active_hex(self, user_id: int) -> dict | None:
        """Return the live hex record for this user, or None if expired/absent."""
        rec = self.state["hexed"].get(str(user_id))
        if not rec:
            return None
        if rec["expires_at"] is not None and rec["expires_at"] <= time.time():
            self.state["hexed"].pop(str(user_id), None)
            self.save()
            return None
        return rec

    def is_wand_limp(self, user_id: int) -> bool:
        rec = self.active_hex(user_id)
        if not rec:
            return False
        spell = EFFECTS.get(rec["effect"]) or {}
        return bool(spell.get("blocks_wand"))

    def limp_minutes_left(self, user_id: int) -> int | None:
        """Whole minutes left on a Limp Wand hex, or None if not limp."""
        rec = self.active_hex(user_id)
        if not rec:
            return None
        spell = EFFECTS.get(rec["effect"]) or {}
        if not spell.get("blocks_wand"):
            return None
        if rec["expires_at"] is None:
            return None
        return max(0, int((rec["expires_at"] - time.time() + 59) // 60))

    async def deny_if_limp_wand(self, interaction: discord.Interaction) -> bool:
        """If the invoker is under Limp Wand, reply ephemerally and return True."""
        if not self.is_wand_limp(interaction.user.id):
            return False
        mins = self.limp_minutes_left(interaction.user.id)
        when = "until a Headmaster lifts it" if mins is None else f"for about {mins} more minute(s)"
        msg = (
            "Your wand hangs limp and won't answer. "
            f"`/wand`, `/patronus`, and `/broom` are out {when}."
        )
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return True

    async def hex(self, interaction: discord.Interaction, member: discord.Member,
                  effect: app_commands.Choice[str], duration: app_commands.Range[int, 0, 10080]):
        if not self._is_headmaster(interaction.user):
            await interaction.response.send_message("Only a Headmaster may cast this.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("You can't hex a bot.", ephemeral=True)
            return

        if effect.value not in EFFECTS:
            await interaction.response.send_message(
                "That curse doesn't exist anymore - your Discord app is showing a stale spell list. Force-quit "
                "and reopen Discord (or wait a bit for it to refresh) and try `/hex` again.", ephemeral=True)
            return
        spell, was_hexed = self.apply_hex(
            target_id=member.id, effect=effect.value,
            duration_minutes=duration or None, cast_by=interaction.user.id,
        )

        flourish = random.choice(CAST_FLOURISHES)
        await interaction.response.send_message(embed=discord.Embed(
            title=f"⚡ {spell['name']}!",
            description=f"**{interaction.user.display_name}** {flourish} **{member.display_name}**.",
            color=0x8B5CF6,
        ))

        replaced_note = " (replacing the curse already on them)" if was_hexed else ""
        fixed = spell.get("fixed_minutes")
        if fixed:
            until = f"for {fixed} minute(s)"
        elif not duration:
            until = "until a Headmaster lifts it"
        else:
            until = f"for {duration} minute(s)"
        await interaction.followup.send(
            f"🪄 {member.mention} is hexed with **{spell['name']}** ({spell['description']}){replaced_note}, "
            f"{until}.", ephemeral=True)

    async def unhex(self, interaction: discord.Interaction, member: discord.Member):
        if not self._is_headmaster(interaction.user):
            await interaction.response.send_message("Only a Headmaster may lift this.", ephemeral=True)
            return
        existed = self.state["hexed"].pop(str(member.id), None)
        self.save()
        if existed:
            await interaction.response.send_message(f"The hex on {member.mention} has been lifted.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{member.mention} isn't currently hexed.", ephemeral=True)

    async def hexlist(self, interaction: discord.Interaction):
        if not self._is_headmaster(interaction.user):
            await interaction.response.send_message("Only a Headmaster may see this.", ephemeral=True)
            return
        self._purge_expired()
        entries = self.state["hexed"]
        if not entries:
            await interaction.response.send_message("Nobody is currently hexed.", ephemeral=True)
            return
        lines = []
        for uid, rec in entries.items():
            member = interaction.guild.get_member(int(uid))
            name = member.display_name if member else uid
            spell = EFFECTS.get(rec["effect"], {})
            label = f"{spell.get('name', rec['effect'])} ({spell.get('description', '')})" if spell else rec["effect"]
            if rec["expires_at"] is None:
                remaining = "until lifted"
            else:
                mins_left = max(0, int((rec["expires_at"] - time.time()) // 60))
                remaining = f"~{mins_left} min left"
            lines.append(f"**{name}** — {label} ({remaining})")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # -------------------------------------------------------------- upkeep

    def _purge_expired(self):
        now = time.time()
        stale = [uid for uid, rec in self.state["hexed"].items()
                if rec["expires_at"] is not None and rec["expires_at"] <= now]
        for uid in stale:
            self.state["hexed"].pop(uid, None)
        if stale:
            self.save()

    @tasks.loop(minutes=5)
    async def expiry_sweep(self):
        self._purge_expired()

    @expiry_sweep.before_loop
    async def _before_sweep(self):
        await self.bot.wait_until_ready()

    # --------------------------------------------------------- the prank

    async def _relay_webhook(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        cached = self._webhooks.get(channel.id)
        if cached:
            return cached
        try:
            hooks = await channel.webhooks()
            hook = next((h for h in hooks if h.name == RELAY_WEBHOOK_NAME), None)
            if hook is None:
                hook = await channel.create_webhook(name=RELAY_WEBHOOK_NAME, reason="Headmaster hex relay")
            self._webhooks[channel.id] = hook
            return hook
        except discord.DiscordException:
            log.exception("Could not get/create the hex relay webhook in #%s", getattr(channel, "name", channel.id))
            return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        # Anti-spam runs first (cog load order) and deletes overflowing spam;
        # skip the hex relay when the author is on a personal pause so spam
        # is deleted rather than mangled through the webhook.
        antispam = self.bot.get_cog("AntiSpam")
        if antispam is not None and antispam.should_block(message):
            return
        if not message.content or not message.content.strip():
            return

        rec = self.active_hex(message.author.id)
        if not rec:
            return

        effect = EFFECTS.get(rec["effect"])
        if not effect or effect.get("func") is None:
            # Limp Wand (and any future non-chat hexes) leave messages alone.
            return
        cursed = effect["func"](message.content)
        if not cursed.strip():
            cursed = message.content  # never post an empty message

        # Deleting + reposting via webhook loses Discord's native reply-thread indicator,
        # so if this was a reply, stitch a quoted line back in manually.
        if message.reference is not None and message.reference.message_id is not None:
            ref_msg = message.reference.resolved
            if isinstance(ref_msg, discord.DeletedReferencedMessage) or ref_msg is None:
                try:
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                except discord.DiscordException:
                    ref_msg = None
            if ref_msg is not None:
                snippet = (ref_msg.content or "*(no text)*").replace("\n", " ")
                if len(snippet) > 100:
                    snippet = snippet[:97] + "..."
                quote = f"> ↩️ replying to **{ref_msg.author.display_name}**: {snippet}\n"
                cursed = quote + cursed

        cursed = cursed[:2000]
        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            return
        hook = await self._relay_webhook(channel)
        if hook is None:
            return

        try:
            await message.delete()
        except discord.DiscordException:
            log.exception("Could not delete a hexed message in #%s", channel.name)
            return

        try:
            files = [await a.to_file() for a in message.attachments] if message.attachments else []
            await hook.send(content=cursed[:2000], username=message.author.display_name,
                            avatar_url=message.author.display_avatar.url, files=files)
        except discord.DiscordException:
            log.exception("Could not repost a hexed message in #%s", channel.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Hexes(bot))
