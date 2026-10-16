"""
Headmaster / President hexes — a prank spell cast on a student so that,
without warning, whatever they type comes out cursed — or their wand goes limp.

    /staffgame hex cast member:<@user> effect:<pick one> duration:<minutes>
    /staffgame hex lift member:<@user>
    /staffgame hex list

Most curses mangle chat: the bot deletes the cursed member's message and
reposts it through a per-channel webhook wearing their name and avatar.
Limp Wand is different — it leaves chat alone and blocks /wand, /patronus,
/broom, and the green wild-threat spell buttons for one hour.

Yoda, Rhyme, Dramatic Narrator, Haiku, Toddler, Superhero, Sheep, Gen Z,
Dog, Snake, Corporate Email, Overthinker, and Influencer are staff-cast only
(`/staffgame hex cast`). Like Cowboy, each one rephrases the line in character
and keeps the meaning. Hex Scrolls never roll them.

Headmasters and house Presidents may cast, lift, and list. Needs the bot to
hold Manage Messages (to delete the original) and Manage Webhooks (to
create/reuse the relay webhook) in this server.
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
# --------------------------------------------------------------------------- cowboy / country
COUNTRY_PHRASE_MAP = [
    (r"\bi don't know\b", "I can't rightly say"),
    (r"\bi dont know\b", "I can't rightly say"),
    (r"\bi'm going to class\b", "I'm headed to schoolin'"),
    (r"\bim going to class\b", "I'm headed to schoolin'"),
    (r"\bgoing to class\b", "headed to schoolin'"),
    (r"\bi'm going to\b", "I'm fixin' to"),
    (r"\bim going to\b", "I'm fixin' to"),
    (r"\bgoing to\b", "fixin' to"),
    (r"\bgotta\b", "got to"),
    (r"\bhave to\b", "got to"),
    (r"\bhas to\b", "got to"),
    (r"\bwant to\b", "wanna"),
    (r"\btrying to\b", "tryin' to"),
    (r"\bwhat's up\b", "how's it hangin'"),
    (r"\bwhats up\b", "how's it hangin'"),
    (r"\bhow are you\b", "how y'all holdin' up"),
    (r"\bhow r u\b", "how y'all holdin' up"),
    (r"\bthank you\b", "much obliged"),
    (r"\bthanks\b", "much obliged"),
    (r"\bgood morning\b", "mornin'"),
    (r"\bgood night\b", "night y'all"),
    (r"\bgood evening\b", "evenin'"),
    (r"\bsee you later\b", "catch y'all later"),
    (r"\bsee you\b", "see y'all"),
    (r"\bbye bye\b", "so long"),
    (r"\bbye\b", "so long"),
    (r"\bi'm sorry\b", "I'm awful sorry"),
    (r"\bim sorry\b", "I'm awful sorry"),
    (r"\bi am sorry\b", "I'm awful sorry"),
    (r"\bsorry\b", "awful sorry"),
    (r"\bi love you\b", "I'm right sweet on you"),
    (r"\bi like you\b", "I'm mighty fond of you"),
    (r"\blet's go\b", "let's ride"),
    (r"\blets go\b", "let's ride"),
    (r"\bcome on\b", "c'mon now"),
    (r"\bhurry up\b", "get a move on"),
    (r"\bshut up\b", "hush up"),
    (r"\bbe quiet\b", "hush now"),
    (r"\bhelp me\b", "lend me a hand"),
    (r"\bhelp you\b", "lend y'all a hand"),
    (r"\bi think\b", "I reckon"),
    (r"\bi believe\b", "I reckon"),
    (r"\bi guess\b", "I reckon"),
    (r"\bi mean\b", "what I mean is"),
    (r"\bright now\b", "this very minute"),
    (r"\bright away\b", "right quick"),
    (r"\ba lot\b", "a whole heap"),
    (r"\ba bit\b", "a little bit"),
    (r"\bokey dokey\b", "alrighty"),
    (r"\ball right\b", "alrighty"),
    (r"\balright\b", "alrighty"),
    (r"\bno way\b", "no sirree"),
    (r"\boh my god\b", "good gravy"),
    (r"\bomg\b", "good gravy"),
]
COUNTRY_MAP = {
    # pronouns / be-verbs
    "i": "I", "i'm": "I'm", "im": "I'm", "i've": "I've", "ive": "I've",
    "i'll": "I'll", "i'd": "I'd",
    "you": "y'all", "you'd": "y'all'd", "you'll": "y'all'll", "you've": "y'all've",
    "your": "yer", "yours": "yer own", "you're": "y'all're", "youre": "y'all're",
    "yourself": "yerself", "yourselves": "yerselves",
    "my": "my", "mine": "mine", "myself": "myself",
    "we": "we", "we're": "we're", "we've": "we've", "we'll": "we'll",
    "they": "they", "they're": "they're", "theyre": "they're",
    "he": "he", "she's": "she's", "shes": "she's", "he's": "he's", "hes": "he's",
    "it": "it", "it's": "it's", "its": "its",
    "is": "is", "are": "are", "am": "am", "was": "was", "were": "were",
    "isn't": "ain't", "aren't": "ain't", "wasn't": "weren't", "weren't": "weren't",
    "don't": "don't", "doesn't": "don't", "didn't": "didn't",
    "can't": "cain't", "cannot": "cain't", "won't": "won't",
    "gonna": "fixin' to", "wanna": "wanna", "gotta": "got to",
    # greetings / answers
    "hello": "howdy", "hi": "howdy", "hey": "howdy", "hiya": "howdy", "sup": "howdy",
    "yes": "yessir", "yeah": "yeah", "yep": "yessir", "yup": "yessir", "yea": "yessir",
    "no": "nope", "nope": "nope", "nah": "nah",
    "okay": "alrighty", "ok": "alrighty", "k": "alrighty", "kk": "alrighty",
    "please": "if you please", "pls": "if you please", "plz": "if you please",
    # people
    "friend": "partner", "friends": "partners", "buddy": "pardner", "bud": "pardner",
    "dude": "fella", "man": "fella", "guy": "fella", "guys": "folks",
    "girl": "gal", "girls": "gals", "boy": "young'un", "boys": "young'uns",
    "bro": "pardner", "bruh": "pardner", "sis": "sis",
    "person": "somebody", "people": "folks", "everyone": "everybody",
    "somebody": "somebody", "someone": "somebody", "anybody": "anybody",
    "anyone": "anybody", "nobody": "nobody",
    "kid": "young'un", "kids": "young'uns", "child": "young'un", "children": "young'uns",
    "baby": "little one", "boss": "the boss", "teacher": "schoolmarm",
    "idiot": "dang fool", "fool": "dang fool", "loser": "sorry soul",
    # places / things
    "house": "homestead", "home": "homestead", "room": "room", "school": "schoolhouse",
    "bathroom": "outhouse", "toilet": "outhouse", "kitchen": "kitchen",
    "car": "truck", "truck": "pickup", "bus": "bus", "boat": "boat",
    "phone": "talkin' box", "computer": "contraption", "laptop": "contraption",
    "internet": "the world wide web", "online": "online", "message": "holler",
    "chat": "chinwag", "server": "town", "channel": "porch", "discord": "the big saloon",
    "money": "coin", "cash": "coin", "dollars": "dollars", "dollar": "dollar",
    "points": "points", "drink": "sweet tea", "drinks": "sweet tea",
    "beer": "cold one", "coffee": "coffee", "water": "water",
    "food": "grub", "meal": "grub", "dinner": "supper", "lunch": "dinner",
    "breakfast": "breakfast", "snack": "snack",
    "party": "shindig", "fight": "scrap", "game": "contest", "match": "contest",
    "work": "chores", "job": "chores", "homework": "book learnin'",
    "class": "schoolin'", "test": "test", "problem": "pickle", "issue": "pickle",
    "bug": "critter", "error": "mess-up", "mistake": "mess-up",
    # verbs / adjectives
    "stop": "hold up", "wait": "hold yer horses", "hold": "hold",
    "look": "looky", "see": "see", "watch": "keep an eye on",
    "go": "head on", "goes": "heads on", "went": "headed on", "gone": "gone",
    "come": "come on over", "coming": "comin'",
    "leave": "mosey on", "left": "moseyed on", "leaving": "moseyin'",
    "run": "hightail it", "running": "hightailin' it", "ran": "hightailed it",
    "walk": "amble", "walking": "amblin'",
    "talk": "holler", "talking": "hollerin'", "speak": "speak",
    "help": "help", "helping": "helpin'", "helped": "helped",
    "need": "need", "needs": "needs", "want": "want", "wants": "wants",
    "like": "like", "love": "love", "hate": "can't stand",
    "know": "know", "think": "reckon", "thinks": "reckons", "thought": "reckoned",
    "get": "get", "got": "got", "getting": "gettin'",
    "give": "give", "take": "take", "make": "make", "find": "find",
    "kill": "do in", "die": "kick the bucket", "dead": "dead as a doornail",
    "sleep": "catch some shuteye", "sleeping": "catchin' shuteye",
    "eat": "chow down on", "eating": "chowin' down on", "ate": "chowed down on",
    "drink": "drink", "drinking": "drinkin'",
    "good": "mighty fine", "great": "mighty fine", "awesome": "plumb amazing",
    "amazing": "plumb amazing", "cool": "mighty fine", "nice": "right nice",
    "fine": "fine", "bad": "sorry", "terrible": "downright awful", "awful": "downright awful",
    "ugly": "homely", "stupid": "half-witted", "dumb": "half-witted", "weird": "peculiar",
    "crazy": "loco", "funny": "a hoot", "sad": "blue", "happy": "tickled",
    "angry": "madder than a wet hen", "scared": "spooked", "tired": "wore out",
    "bored": "bored stiff", "busy": "busy as a bee",
    "big": "great big", "small": "little ol'", "little": "little ol'", "huge": "whopping",
    "fast": "quick as a whip", "slow": "slower than molasses", "strong": "tough",
    "weak": "puny", "new": "brand-new", "old": "old-timey", "young": "green",
    "very": "plumb", "really": "sure as shootin'", "so": "so", "too": "too",
    "here": "here", "there": "over yonder", "where": "whereabouts",
    "now": "now", "today": "today", "tomorrow": "tomorrow", "yesterday": "yesterday",
    "tonight": "tonight", "morning": "mornin'", "night": "night", "evening": "evenin'",
    "always": "always", "never": "never", "sometimes": "now and again",
    "maybe": "might could", "perhaps": "might could", "probably": "more'n likely",
    "because": "'cause", "about": "'bout", "around": "'round",
    "with": "with", "without": "without", "before": "afore", "after": "after",
    "again": "again", "back": "back", "away": "away",
    "of": "o'", "the": "the", "and": "an'", "for": "fer", "to": "to",
}
COUNTRY_OPENERS = [
    "Well, ", "Howdy — ", "Listen here — ", "Partner, ", "Shoot, ",
    "Well now, ", "I'll tell you what — ", "Looky here — ",
]
COUNTRY_CLOSERS = [
    ", y'all.", ", I reckon.", " — mighty fine, partner.", ", pardner.",
    " ...that's the way it be.", ", sure as shootin'.", ", you hear?",
    " — amen to that.",
]

CHEER_MAP = {
    "yes": "YES OMG YES", "hi": "OMG HII", "hello": "OMG HELLO", "hey": "OMG HEYY",
    "good": "like SO good", "great": "literally amazing", "cool": "so iconic", "nice": "so iconic",
    "no": "no way, like NO", "friend": "bestie", "friends": "besties",
    "happy": "like SO happy", "excited": "SO hyped", "fun": "literally SO fun",
    "love": "am OBSESSED with", "like": "am OBSESSED with",
}

# --------------------------------------------------------------------------- caveman
CAVEMAN_PHRASE_MAP = [
    (r"\bi don't know\b", "Me no know"),
    (r"\bi dont know\b", "Me no know"),
    (r"\bi'm going to\b", "Me go"),
    (r"\bim going to\b", "Me go"),
    (r"\bgoing to\b", "go"),
    (r"\bgotta\b", "must"),
    (r"\bhave to\b", "must"),
    (r"\bhas to\b", "must"),
    (r"\bwant to\b", "want"),
    (r"\bwanna\b", "want"),
    (r"\btrying to\b", "try"),
    (r"\btry to\b", "try"),
    (r"\bwhat's up\b", "What happen"),
    (r"\bwhats up\b", "What happen"),
    (r"\bhow are you\b", "You good"),
    (r"\bhow r u\b", "You good"),
    (r"\bthank you\b", "Me thank"),
    (r"\bthanks\b", "Me thank"),
    (r"\bgood morning\b", "Sun up good"),
    (r"\bgood night\b", "Sun down good"),
    (r"\bgood evening\b", "Sky dark good"),
    (r"\bsee you later\b", "Me see you soon"),
    (r"\bsee you\b", "Me see you"),
    (r"\bbye bye\b", "Me go"),
    (r"\bbye\b", "Me go"),
    (r"\bi'm sorry\b", "Me sorry"),
    (r"\bim sorry\b", "Me sorry"),
    (r"\bi am sorry\b", "Me sorry"),
    (r"\bsorry\b", "Me sorry"),
    (r"\bi love you\b", "Me love you"),
    (r"\bi like you\b", "Me like you"),
    (r"\blet's go\b", "Us go"),
    (r"\blets go\b", "Us go"),
    (r"\bcome on\b", "Come"),
    (r"\bhurry up\b", "Go fast"),
    (r"\bshut up\b", "No talk"),
    (r"\bbe quiet\b", "No talk"),
    (r"\bhelp me\b", "Help me"),
    (r"\bhelp you\b", "Help you"),
    (r"\bi think\b", "Me think"),
    (r"\bi believe\b", "Me think"),
    (r"\bi guess\b", "Me think"),
    (r"\bi mean\b", "Me say"),
    (r"\bright now\b", "Now"),
    (r"\bright away\b", "Now"),
    (r"\ba lot\b", "many"),
    (r"\ba bit\b", "little"),
    (r"\bokey dokey\b", "Okay"),
    (r"\ball right\b", "Okay"),
    (r"\balright\b", "Okay"),
    (r"\bno way\b", "No"),
    (r"\boh my god\b", "Big sky"),
    (r"\bomg\b", "Big sky"),
]
CAVEMAN_WORD_MAP = {
    "i": "me", "i'm": "me", "im": "me", "i've": "me", "ive": "me",
    "i'll": "me", "i'd": "me", "myself": "me",
    "my": "me", "mine": "me",
    "you": "you", "your": "you", "yours": "you", "you're": "you", "youre": "you",
    "yourself": "you",
    "we": "us", "we're": "us", "we've": "us", "we'll": "us", "our": "us", "ours": "us",
    "they": "them", "they're": "them", "their": "them", "theirs": "them",
    "he": "him", "she's": "her", "shes": "her", "he's": "him", "hes": "him",
    "his": "him", "her": "her", "hers": "her",
    "it": "it", "it's": "it", "its": "it",
    "is": "", "are": "", "am": "", "was": "", "were": "", "be": "", "been": "", "being": "",
    "isn't": "no", "aren't": "no", "wasn't": "no", "weren't": "no",
    "don't": "no", "doesn't": "no", "didn't": "no", "can't": "no", "cannot": "no",
    "won't": "no", "wouldn't": "no", "shouldn't": "no", "couldn't": "no",
    "have": "have", "has": "have", "had": "have",
    "do": "do", "does": "do", "did": "do",
    "will": "", "shall": "", "would": "", "could": "", "should": "",
    "may": "", "might": "", "must": "must",
    "hello": "hey", "hi": "hey", "hey": "hey", "hiya": "hey", "sup": "hey",
    "yes": "yes", "yeah": "yes", "yep": "yes", "yup": "yes",
    "no": "no", "nope": "no", "nah": "no",
    "okay": "okay", "ok": "okay", "k": "okay",
    "please": "", "pls": "", "plz": "",
    "friend": "friend", "friends": "friends", "buddy": "friend", "dude": "man",
    "man": "man", "guy": "man", "guys": "men", "girl": "woman", "girls": "women",
    "boy": "boy", "boys": "boys", "bro": "brother", "bruh": "brother",
    "people": "tribe", "everyone": "all", "someone": "one", "somebody": "one",
    "anyone": "one", "anybody": "one", "nobody": "no one",
    "kid": "small one", "kids": "small ones", "baby": "small one",
    "house": "cave", "home": "cave", "room": "cave", "school": "learn place",
    "bathroom": "dirt hole", "car": "fast rock", "truck": "big fast rock",
    "phone": "talk rock", "computer": "magic rock", "internet": "sky talk",
    "money": "shiny", "food": "meat", "drink": "water", "water": "water",
    "party": "big fire", "fight": "smash", "game": "game", "work": "work",
    "problem": "bad thing", "mistake": "oops",
    "stop": "stop", "wait": "wait", "look": "look", "see": "see", "watch": "look",
    "go": "go", "goes": "go", "went": "go", "gone": "go", "going": "go",
    "come": "come", "coming": "come", "came": "come",
    "leave": "go", "left": "go", "leaving": "go",
    "run": "run", "running": "run", "ran": "run",
    "walk": "walk", "walking": "walk",
    "talk": "talk", "talking": "talk", "speak": "talk", "say": "say", "said": "say",
    "tell": "tell", "told": "tell", "ask": "ask", "asked": "ask",
    "help": "help", "helping": "help", "helped": "help",
    "need": "need", "needs": "need", "needed": "need",
    "want": "want", "wants": "want", "wanted": "want",
    "like": "like", "likes": "like", "liked": "like",
    "love": "love", "loves": "love", "loved": "love",
    "hate": "hate", "hates": "hate",
    "know": "know", "knows": "know", "knew": "know",
    "think": "think", "thinks": "think", "thought": "think",
    "feel": "feel", "get": "get", "got": "get", "getting": "get",
    "give": "give", "gave": "give", "take": "take", "took": "take",
    "make": "make", "made": "make", "find": "find", "found": "find",
    "kill": "smash", "die": "fall down", "dead": "dead",
    "sleep": "sleep", "sleeping": "sleep", "slept": "sleep",
    "eat": "eat", "eats": "eat", "ate": "eat", "eating": "eat",
    "drink": "drink", "drinking": "drink", "drank": "drink",
    "good": "good", "great": "big good", "awesome": "big good", "amazing": "big good",
    "cool": "good", "nice": "good", "fine": "good",
    "bad": "bad", "terrible": "big bad", "awful": "big bad",
    "ugly": "ugly", "stupid": "dumb", "dumb": "dumb", "weird": "strange",
    "crazy": "wild", "funny": "funny", "sad": "sad", "happy": "happy",
    "angry": "mad", "scared": "fear", "tired": "tired", "bored": "bored", "busy": "busy",
    "big": "big", "small": "small", "little": "small", "huge": "big big",
    "fast": "fast", "slow": "slow", "strong": "strong", "weak": "weak",
    "new": "new", "old": "old", "young": "young",
    "very": "big", "really": "big", "so": "", "too": "too",
    "just": "", "only": "only", "also": "", "even": "",
    "here": "here", "there": "there", "where": "where",
    "when": "when", "why": "why", "how": "how", "what": "what", "who": "who",
    "this": "this", "that": "", "these": "these", "those": "",
    "now": "now", "then": "then", "today": "today", "tomorrow": "next sun",
    "yesterday": "last sun", "tonight": "night", "morning": "sun up",
    "night": "night", "evening": "sun down",
    "always": "always", "never": "never", "sometimes": "sometime",
    "maybe": "maybe", "perhaps": "maybe", "probably": "maybe",
    "because": "cause", "about": "about", "around": "around",
    "with": "with", "without": "no", "from": "from", "into": "in",
    "before": "before", "after": "after", "again": "again", "back": "back",
    "away": "away", "up": "up", "down": "down", "out": "out", "in": "in",
    "on": "on", "off": "off",
    "of": "", "the": "", "a": "", "an": "", "and": "", "or": "or",
    "but": "but", "if": "if", "as": "", "than": "than", "for": "for",
    "to": "", "at": "at", "by": "",
}
CAVEMAN_FILLERS = {
    # leftover soft words still stripped after the lexicon pass
    "um", "uh", "like", "well", "actually", "basically", "literally",
    "quite", "rather", "somehow", "anyway", "anyways", "though", "however",
}
CAVEMAN_OPENERS = ["Ugh. ", "Grr. ", "Hrm. ", "Ooga. ", "Ugh ugh. ", ""]
CAVEMAN_CLOSERS = [" Ugh!", " Grr!", " Smash!", " Ooga!", ""]


def _split_word(word: str) -> Optional[tuple[str, str, str]]:
    m = WORD_WITH_EDGES.match(word)
    return m.groups() if m else None  # (leading punctuation, core letters, trailing punctuation)


def _match_case(core: str, replacement: str) -> str:
    if not replacement:
        return ""
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


def _apply_phrases(text: str, phrases: list[tuple[str, str]]) -> str:
    out = text
    for pattern, replacement in phrases:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return out


def fx_country(text: str) -> str:
    """Full cowboy / country twang: phrases, lexicon, -in', and porch bookends."""
    if not text or not text.strip():
        return random.choice(["Howdy!", "Well I'll be!", "Yeehaw!", "Alrighty then!"])

    out = _apply_phrases(text, COUNTRY_PHRASE_MAP)
    out = _word_swap(out, COUNTRY_MAP)
    out = _pirate_ing(out)  # same -ing → -in' drop
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out).strip()
    if not out:
        out = "Howdy"

    if random.random() < 0.8 and not re.match(
        r"^(well|howdy|listen|partner|shoot|looky|i'll tell)\b",
        out,
        flags=re.IGNORECASE,
    ):
        out = random.choice(COUNTRY_OPENERS) + out
    if random.random() < 0.9 and not re.search(
        r"(y'all\.|reckon\.|partner\.|pardner\.|shootin'\.|hear\?|amen to that\.)\s*$",
        out,
        flags=re.IGNORECASE,
    ):
        out = out.rstrip(".!?") + random.choice(COUNTRY_CLOSERS)
    return out


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


def _caveman_simplify_ing(text: str) -> str:
    """Collapse leftover -ing verbs to bare stems (going→go already mapped)."""

    def repl(m: re.Match) -> str:
        word = m.group(0)
        lower = word.lower()
        if not lower.endswith("ing") or len(lower) < 5:
            return word
        stem = lower[:-3]
        if stem in {"th", "r", "k", "s", "w", "br", "str", "sw", "cl", "fl", "sl"}:
            return word
        bare = word[:-3]
        if bare.lower().endswith("e"):
            return bare
        # running → runn → run
        if len(bare) >= 2 and bare[-1].lower() == bare[-2].lower():
            bare = bare[:-1]
        return bare

    return re.sub(r"[A-Za-z']+", repl, text)


def fx_caveman(text: str) -> str:
    """Full caveman talk: simple phrases, blunt lexicon, strip fluff, grunt ends."""
    if not text or not text.strip():
        return random.choice(["Ugh!", "Grr!", "Ooga!", "Me smash!"])

    out = _apply_phrases(text, CAVEMAN_PHRASE_MAP)
    out = _word_swap(out, CAVEMAN_WORD_MAP)

    def drop_fillers(m: re.Match) -> str:
        return "" if m.group(0).lower() in CAVEMAN_FILLERS else m.group(0)

    out = re.sub(r"[A-Za-z']+", drop_fillers, out)
    out = _caveman_simplify_ing(out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    # Caveman punctuation stays blunt — mostly periods / bangs.
    out = re.sub(r"[;:]", ".", out)
    out = out.strip(" ,")
    if not out:
        out = "Ugh"
    # Capitalize first letter of the grunt sentence.
    out = out[0].upper() + out[1:] if len(out) > 1 else out.upper()

    if random.random() < 0.85 and not re.match(
        r"^(ugh|grr|hrm|ooga)\b", out, flags=re.IGNORECASE
    ):
        out = random.choice(CAVEMAN_OPENERS) + out
    if random.random() < 0.85 and not re.search(
        r"(ugh!|grr!|smash!|ooga!)\s*$", out, flags=re.IGNORECASE
    ):
        out = out.rstrip(".!?") + random.choice(CAVEMAN_CLOSERS)
    return out.strip()


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

    out = _apply_phrases(text, PIRATES_PHRASE_MAP)
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


# ---------------------------------------------------------------------------
# Staff-only voices. Same shape as Cowboy: a local rephrase (meaning kept,
# voice changed), then the shared delete-and-webhook relay. Not scroll-legal.
# ---------------------------------------------------------------------------

def _normalize_apostrophes(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'").replace("`", "'")


def _tidy_speech(text: str) -> str:
    """Collapse spaces the way Cowboy does, without gluing separate lines."""
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]{2,}", " ", line)
        line = re.sub(r" +([,.!?;:])", r"\1", line)
        lines.append(line.strip())
    return "\n".join(lines).strip()


def _cap_first(text: str) -> str:
    text = text.strip()
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1:]
    return text


def _word_key(token: str) -> str:
    match = re.search(r"[A-Za-z']+", token)
    return match.group(0).lower() if match else ""


def _lower_token(token: str) -> str:
    match = re.match(r"^([^A-Za-z]*)([A-Za-z']+)(.*)$", token)
    if not match:
        return token
    lead, core, trail = match.groups()
    core = "I" if core.lower() == "i" else core.lower()
    return lead + core + trail


def _voice_bookend(text: str, openers: tuple, closers: tuple, opener_re: str,
                   closer_re: str, opener_p: float = 0.8, closer_p: float = 0.9) -> str:
    """Cowboy's porch bookends: usually a voice-y start and end, never a blank."""
    out = text
    if random.random() < opener_p and not re.match(opener_re, out, flags=re.IGNORECASE):
        out = random.choice(openers) + out
    if random.random() < closer_p and not re.search(closer_re, out, flags=re.IGNORECASE):
        out = out.rstrip(".!?") + random.choice(closers)
    return out


def _has_letters(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", text or ""))


# --- Yoda ------------------------------------------------------------------
_YODA_PREP = [
    (r"\bi'm\b", "I am"),
    (r"\bim\b", "I am"),
    (r"\bi've\b", "I have"),
    (r"\bi'll\b", "I will"),
    (r"\bi'd\b", "I would"),
    (r"\byou're\b", "you are"),
    (r"\bwe're\b", "we are"),
    (r"\bthey're\b", "they are"),
    (r"\bhe's\b", "he is"),
    (r"\bshe's\b", "she is"),
    (r"\bit's\b", "it is"),
    (r"\bwhat's\b", "what is"),
    (r"\bthat's\b", "that is"),
    (r"\bdon't\b", "do not"),
    (r"\bdoesn't\b", "does not"),
    (r"\bdidn't\b", "did not"),
    (r"\bcan't\b", "cannot"),
    (r"\bwon't\b", "will not"),
    (r"\bisn't\b", "is not"),
    (r"\baren't\b", "are not"),
    (r"\bwasn't\b", "was not"),
    (r"\bweren't\b", "were not"),
    (r"\blet's\b", "let us"),
]
_YODA_WHOLE = (
    (re.compile(r"^(thank you|thanks)[.!?]*$", re.I), "Thank you, I do"),
    (re.compile(r"^(hello|hi|hey)[.!?]*$", re.I), "Hello, it is"),
    (re.compile(r"^(yes|yeah|yep|yup)[.!?]*$", re.I), "Yes, hmm"),
    (re.compile(r"^(no|nope|nah)[.!?]*$", re.I), "No, I think not"),
    (re.compile(r"^let us go[.!?]*$", re.I), "Go, we must"),
    (re.compile(r"^good morning[.!?]*$", re.I), "A good morning, it is"),
    (re.compile(r"^good night[.!?]*$", re.I), "A good night, it is"),
)
_YODA_AUX = frozenset({
    "am", "is", "are", "was", "were", "be", "been",
    "do", "does", "did", "have", "has", "had",
    "will", "would", "shall", "should", "can", "could", "may", "might", "must",
    "cannot",
})
_YODA_PRONOUNS = frozenset({"i", "you", "we", "they", "he", "she", "it"})
_YODA_OPENERS = ("Hmm. ", "Yes. ", "Hear this. ", "Mmm. ")
_YODA_CLOSERS = (", hmm.", ", yes.", " — yes.")
_YODA_EMPTY = (
    "Hmm. Say more, you must.",
    "Cloudy, the meaning is.",
    "Heard, you were not.",
)


def _yoda_tail(subject_words: list[str], aux_word: str) -> str:
    subject = " ".join(_lower_token(word) for word in subject_words)
    return f"{subject} {_lower_token(aux_word)}".strip()


def _yoda_sentence(sentence: str) -> str:
    """Invert each clause. 'But' stays put so both facts survive."""
    raw = sentence.strip()
    if not raw:
        return raw
    pieces = re.split(r"(\bbut\b)", raw, flags=re.IGNORECASE)
    if len(pieces) == 1:
        return _yoda_clause(raw)
    built = []
    for piece in pieces:
        if re.fullmatch(r"but", piece, flags=re.IGNORECASE):
            built.append("but")
            continue
        if not piece.strip():
            continue
        clause = _yoda_clause(piece.strip())
        if built:
            clause = clause[:1].lower() + clause[1:]
        built.append(clause)
    return " ".join(built)


def _yoda_clause(sentence: str) -> str:
    raw = sentence.strip()
    if not raw:
        return raw
    for pattern, replacement in _YODA_WHOLE:
        if pattern.match(raw):
            if raw.endswith("?"):
                return replacement.rstrip(".") + "?"
            if raw.endswith("!"):
                return replacement.rstrip(".") + "!"
            return replacement

    end = raw[-1] if raw[-1] in ".!?" else ""
    body = raw[:-1].strip() if end else raw
    words = body.split()
    if len(words) < 2:
        return raw

    if _word_key(words[0]) in _YODA_AUX:
        aux = words[0].lower()
        if len(words) >= 3 and _word_key(words[1]) in _YODA_PRONOUNS:
            rest = " ".join(words[2:]).strip()
            if rest:
                return f"{_cap_first(rest)}, {aux} {_lower_token(words[1])}{end}"
        rest = " ".join(words[1:]).strip()
        if rest:
            return f"{_cap_first(rest)}, {aux}{end}"

    aux_i = None
    for i in range(1, len(words) - 1):
        if _word_key(words[i]) in _YODA_AUX:
            aux_i = i
            break
    if aux_i is not None:
        tail = _yoda_tail(words[:aux_i], words[aux_i])
        rest = " ".join(words[aux_i + 1:]).strip()
        low = rest.lower()
        if low == "not":
            return f"Not, {tail}{end}"
        if low.startswith("not "):
            kept = rest[4:].strip()
            if kept:
                return f"{_cap_first(kept)}, {tail} not{end}"
        if rest:
            return f"{_cap_first(rest)}, {tail}{end}"

    if _word_key(words[0]) == "thank" and len(words) > 2 and _word_key(words[1]) == "you":
        return f"{_cap_first(' '.join(words[2:]))}, thank you{end}"
    if _word_key(words[0]) == "please" and len(words) > 1:
        return f"{_cap_first(' '.join(words[1:]))}, please{end}"
    if _word_key(words[0]) in _YODA_PRONOUNS and len(words) >= 3:
        return f"{_cap_first(' '.join(words[2:]))}, {_yoda_tail([words[0]], words[1])}{end}"
    return _cap_first(" ".join(words)) + end


def fx_yoda(text: str) -> str:
    """Rephrase in Yoda's inverted speech. The facts stay; the order flips."""
    if not _has_letters(text):
        return random.choice(_YODA_EMPTY)
    prepared = _apply_phrases(_normalize_apostrophes(text), _YODA_PREP)
    lines_out = []
    for line in prepared.splitlines():
        if not line.strip():
            lines_out.append("")
            continue
        parts = re.split(r"(?<=[.!?])\s+", line.strip())
        lines_out.append(" ".join(_yoda_sentence(part) for part in parts if part.strip()))
    out = _tidy_speech("\n".join(lines_out))
    if not _has_letters(out):
        return random.choice(_YODA_EMPTY)
    return _voice_bookend(
        out,
        openers=_YODA_OPENERS,
        closers=_YODA_CLOSERS,
        opener_re=r"^(hmm|yes|hear this|mmm)\b",
        closer_re=r"(hmm\.|yes\.|— yes\.)\s*$",
    )


# --- Rhyme -----------------------------------------------------------------
_RHYME_PHRASES = [
    (r"\bi am going to\b", "I am headed to"),
    (r"\bi'm going to\b", "I am headed to"),
    (r"\bim going to\b", "I am headed to"),
    (r"\bgoing to\b", "headed to"),
    (r"\bwant to\b", "wish to"),
    (r"\bhave to\b", "must"),
    (r"\bi don't know\b", "I do not know"),
    (r"\bi dont know\b", "I do not know"),
]
# Each frame repeats the message and ends on a real rhyme, so the point
# survives the couplet.
_RHYME_FRAMES = (
    ("{msg}, I say —", "{echo}, and that is so today."),
    ("{msg} — it's true,", "{echo}, I promise you."),
    ("{msg}, you know,", "{echo} — and that is so."),
    ("{msg}, all right?", "{echo}, I mean tonight."),
    ("{msg} — you'll see,", "{echo}, the same with me."),
    ("{msg}, my dear,", "{echo}, the point is here."),
)
_RHYME_PAIRS = (
    ("say", "today"),
    ("true", "you"),
    ("know", "so"),
    ("right", "tonight"),
    ("see", "me"),
    ("dear", "here"),
)


def _clip_sentence(text: str, limit: int = 700) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]).rstrip(".,;:—- ")


def fx_rhyme(text: str) -> str:
    """Rephrase as a rhyming couplet that still states the same thing twice."""
    if not _has_letters(text):
        msg = "Nothing was said"
    else:
        msg = _apply_phrases(_normalize_apostrophes(text), _RHYME_PHRASES)
        msg = _cap_first(_clip_sentence(msg.strip().rstrip(".!?")))
        if not _has_letters(msg):
            msg = "Nothing was said"
    line1, line2 = random.choice(_RHYME_FRAMES)
    return f"{line1.format(msg=msg)}\n{line2.format(echo=msg)}"


# --- Dramatic narrator -----------------------------------------------------
_NARRATOR_PHRASES = [
    (r"\bi am going to\b", "they were going to"),
    (r"\bi'm going to\b", "they were going to"),
    (r"\bim going to\b", "they were going to"),
    (r"\bwe are going to\b", "they were going to"),
    (r"\bwe're going to\b", "they were going to"),
    (r"\bi am\b", "they were"),
    (r"\bi'm\b", "they were"),
    (r"\bim\b", "they were"),
    (r"\bi don't\b", "they do not"),
    (r"\bi dont\b", "they do not"),
    (r"\bi do not\b", "they do not"),
    (r"\bi can't\b", "they cannot"),
    (r"\bi cant\b", "they cannot"),
    (r"\bi cannot\b", "they cannot"),
    (r"\bi won't\b", "they will not"),
    (r"\bi wont\b", "they will not"),
    (r"\bi will not\b", "they will not"),
    (r"\bi'll\b", "they will"),
    (r"\bi will\b", "they will"),
    (r"\bi've\b", "they have"),
    (r"\bi have\b", "they have"),
    (r"\bi'd\b", "they would"),
    (r"\bi would\b", "they would"),
    (r"\bi think\b", "they think"),
    (r"\bi believe\b", "they believe"),
    (r"\bi love\b", "they love"),
    (r"\bi like\b", "they like"),
    (r"\bi need\b", "they need"),
    (r"\bi want\b", "they want"),
    (r"\bi know\b", "they know"),
    (r"\bwe are\b", "they are"),
    (r"\bwe're\b", "they are"),
    (r"\bwe were\b", "they were"),
    (r"\bour\b", "their"),
    (r"\bours\b", "theirs"),
    (r"\bmyself\b", "themself"),
    (r"\bmy\b", "their"),
    (r"\bmine\b", "theirs"),
    (r"\bme\b", "them"),
    (r"\bwe\b", "they"),
    (r"\bus\b", "them"),
    (r"\bi\b", "they"),
]
_NARRATOR_OPENERS = (
    "In a voice thick with destiny, they announced that",
    "The hall held its breath as they declared that",
    "Eyes alight, they proclaimed that",
    "As if the realm were listening, they said that",
    "With terrible gravity, they intoned that",
    "A hush fell, and they swore that",
)
_NARRATOR_QUOTE_OPENERS = (
    "In a voice thick with destiny, they declared",
    "The hall held its breath as they proclaimed",
    "Eyes alight, they cried out",
    "With terrible gravity, they called",
    "A hush fell as they announced",
)
_NARRATOR_EMPTY = (
    "In a voice thick with destiny, they announced that they had nothing to say.",
    "The hall held its breath as they declared that they had nothing to say.",
    "With terrible gravity, they intoned that they had nothing to say.",
)


def fx_narrator(text: str) -> str:
    """Rephrase as dramatic third-person narration of what they said."""
    if not _has_letters(text):
        return random.choice(_NARRATOR_EMPTY)
    body = _tidy_speech(_apply_phrases(_normalize_apostrophes(text), _NARRATOR_PHRASES))
    body = " ".join(body.split())
    if not _has_letters(body):
        return random.choice(_NARRATOR_EMPTY)
    if re.match(r"they\b", body, flags=re.IGNORECASE):
        body = body[0].lower() + body[1:]
        body = body.rstrip(".!")
        return f"{random.choice(_NARRATOR_OPENERS)} {body}."
    quoted = body.rstrip()
    if quoted[-1] not in ".!?":
        quoted += "."
    return f'{random.choice(_NARRATOR_QUOTE_OPENERS)}: "{_cap_first(quoted)}"'


# --- Haiku -----------------------------------------------------------------
_SYLLABLE_1 = frozenset({
    "a", "an", "the", "to", "and", "then", "class", "lunch", "am", "i", "me",
    "my", "you", "we", "he", "she", "it", "is", "are", "was", "were", "go",
    "see", "now", "here", "still", "yes", "oh", "ah", "so", "for", "of", "in",
    "on", "at", "be", "do", "hi", "hey", "no", "not", "but", "or", "if", "up",
    "out", "all", "can", "will", "just", "like", "love", "help", "want", "need",
    "have", "has", "had", "get", "got", "this", "that", "with", "from", "your",
    "our", "them", "they", "their", "when", "what", "where", "who", "why", "how",
    "please", "thanks", "good", "bad", "big", "home", "food", "time", "day",
    "night", "school", "work", "friend", "friends", "come", "came", "back",
    "down", "next", "soon", "late", "wait", "stop", "look", "know", "think",
    "say", "said", "tell", "told", "ask", "eat", "ate", "sleep", "walk", "run",
    "ran", "make", "made", "take", "took", "give", "gave", "find", "found",
    "call", "called", "play", "played", "game", "book", "test", "done", "left",
    "right", "first", "last", "more", "most", "much", "some", "any", "one",
    "two", "three", "four", "five", "six", "ten", "house", "room", "door",
    "car", "bus", "cat", "dog", "mom", "dad", "dont", "air", "words", "was",
    "meet", "should",
})
_SYLLABLE_2 = frozenset({
    "going", "hello", "today", "again", "later", "morning", "evening",
    "someone", "something", "nothing", "really", "very", "little", "people",
    "about", "around", "because", "always", "never", "maybe", "hungry",
    "tired", "sorry", "happy", "angry", "ready", "over", "under", "into",
    "without", "homework", "classroom", "teacher", "student", "tonight",
    "better", "doing", "coming", "leaving", "eating", "sleeping", "playing",
    "working", "trying", "having", "getting", "making", "looking", "walking",
    "running", "talking", "asking", "helping", "headed", "quiet", "further",
    "unsaid", "only", "open", "after", "before", "softly", "lightly",
})
_SYLLABLE_3 = frozenset({
    "tomorrow", "yesterday", "anything", "everything", "another", "beautiful",
    "remember", "important", "together", "however", "everyone", "library",
})
_HAIKU_STOP = frozenset({
    "a", "an", "the", "and", "or", "but", "just", "really", "very", "so",
})
# A one-word line of these is a dangling function word, not a fact.
_HAIKU_WEAK = frozenset({
    "a", "an", "the", "to", "and", "or", "but", "is", "am", "are", "of",
    "i", "me", "my", "we", "do", "be", "it", "so", "if", "at", "in", "on",
})
_HAIKU_WEAK_START = frozenset({"and", "or", "but", "then", "to"})
# Exact syllable fillers. They mark the line as reported speech, not new facts.
_HAIKU_PAD = {
    1: ("now", "here", "still"),
    2: ("again", "softly", "lightly"),
    3: ("so they said", "as they said", "so it goes"),
    4: ("or so they said",),
    5: ("this is what was said",),
    6: ("this is what they said now",),
    7: ("this is what they said again",),
}
_HAIKU_EMPTY_LINES = (
    "Quiet words unsaid",
    "Nothing further was told here",
    "Only open air",
)


def _syllables(word: str) -> int:
    raw = re.sub(r"[^a-z]", "", word.lower())
    if not raw:
        return 1
    if raw in _SYLLABLE_1:
        return 1
    if raw in _SYLLABLE_2:
        return 2
    if raw in _SYLLABLE_3:
        return 3
    groups = re.findall(r"[aeiouy]+", raw)
    count = len(groups)
    if count == 0:
        return 1
    if raw.endswith("e") and not raw.endswith(("le", "ye", "ee")) and count > 1:
        count -= 1
    return max(1, count)


def _haiku_tokens(text: str) -> list[str]:
    return [raw for raw in text.replace("\n", " ").split() if re.search(r"[A-Za-z0-9]", raw)]


def _syllable_total(words: list[str]) -> int:
    return sum(_syllables(word) for word in words)


def _fit_haiku_words(words: list[str]) -> list[str]:
    """Keep the message inside 17 syllables, dropping filler before facts."""
    if _syllable_total(words) <= 17:
        return words
    kept = [word for word in words if _word_key(word) not in _HAIKU_STOP]
    if kept and _syllable_total(kept) <= 17:
        return kept
    if not kept:
        kept = list(words)
    prefix: list[str] = []
    count = 0
    for word in kept:
        syl = _syllables(word)
        if count + syl > 12:
            break
        prefix.append(word)
        count += syl
    suffix: list[str] = []
    tail_count = 0
    for word in reversed(kept[len(prefix):]):
        syl = _syllables(word)
        if tail_count + syl > 5:
            break
        suffix.append(word)
        tail_count += syl
    suffix.reverse()
    fitted = prefix + suffix
    return fitted or kept[:1]


def _haiku_line(words: list[str], target: int, index: int = 0) -> str:
    """One haiku line: the words, then one short aside to hit the count."""
    have = _syllable_total(words)
    deficit = target - have
    if deficit <= 0 or deficit not in _HAIKU_PAD:
        text = " ".join(words)
    else:
        options = _HAIKU_PAD[deficit]
        pad = options[index % len(options)]
        if words:
            head = re.sub(r"[,.;:?!]+$", "", " ".join(words).rstrip())
            text = f"{head}, {pad}" if head else pad
        else:
            text = pad
    return _cap_first(text)


def _line_lonely(words: list[str]) -> bool:
    return len(words) == 1 and _word_key(words[0]) in _HAIKU_WEAK


def _weak_start(words: list[str]) -> bool:
    return bool(words) and _word_key(words[0]) in _HAIKU_WEAK_START


def _greedy_haiku(words: list[str]) -> list[str]:
    targets = (5, 7, 5)
    lines: list[list[str]] = [[], [], []]
    idx = 0
    for line_i, target in enumerate(targets):
        have = 0
        while idx < len(words):
            syl = _syllables(words[idx])
            if have + syl > target and have > 0:
                break
            if syl > target and have == 0:
                lines[line_i].append(words[idx])
                idx += 1
                have = target
                break
            lines[line_i].append(words[idx])
            have += syl
            idx += 1
            if have >= target:
                break
    return [_haiku_line(line, target, index) for index, (line, target) in enumerate(zip(lines, targets))]


def _pack_haiku(words: list[str]) -> list[str]:
    counts = [_syllables(word) for word in words]
    prefix = [0]
    for count in counts:
        prefix.append(prefix[-1] + count)

    def span(start: int, end: int) -> int:
        return prefix[end] - prefix[start]

    best = None
    n = len(words)
    for end_one in range(n + 1):
        for end_two in range(end_one, n + 1):
            first, second, third = span(0, end_one), span(end_one, end_two), span(end_two, n)
            if first > 5 or second > 7 or third > 5:
                continue
            chunks = (words[:end_one], words[end_one:end_two], words[end_two:])
            empties = sum(not chunk for chunk in chunks)
            lonely = sum(_line_lonely(chunk) for chunk in chunks)
            weak = sum(_weak_start(chunk) for chunk in chunks)
            pads = (5 - first, 7 - second, 5 - third)
            score = (empties, lonely, weak, max(pads), sum(pad * pad for pad in pads))
            if best is None or score < best[0]:
                best = (score, chunks)
    if best is None:
        return _greedy_haiku(words)
    chunks = best[1]
    return [
        _haiku_line(chunk, target, index)
        for index, (chunk, target) in enumerate(zip(chunks, (5, 7, 5)))
    ]


def fx_haiku(text: str) -> str:
    """Rephrase into one 5-7-5 haiku. Filler yields; the facts stay."""
    if not _has_letters(text):
        return "\n".join(_HAIKU_EMPTY_LINES)
    words = _fit_haiku_words(_haiku_tokens(text))
    if not words:
        return "\n".join(_HAIKU_EMPTY_LINES)
    return "\n".join(_pack_haiku(words))


# --- Toddler ---------------------------------------------------------------
_TODDLER_PHRASES = [
    (r"\bi don't know\b", "me no know"),
    (r"\bi dont know\b", "me no know"),
    (r"\bi am going to\b", "me gonna"),
    (r"\bi'm going to\b", "me gonna"),
    (r"\bim going to\b", "me gonna"),
    (r"\bgoing to\b", "gonna"),
    (r"\bwant to\b", "wanna"),
    (r"\bhave to\b", "gotta"),
    (r"\bhas to\b", "gotta"),
    (r"\bthank you\b", "fank you"),
    (r"\bthanks\b", "fanks"),
    (r"\bgood morning\b", "mornin"),
    (r"\bgood night\b", "night night"),
    (r"\bsee you later\b", "see you later"),
    (r"\bi am sorry\b", "me sowwy"),
    (r"\bi'm sorry\b", "me sowwy"),
    (r"\bi love you\b", "me wuv you"),
    (r"\bi like you\b", "me wike you"),
    (r"\blet's go\b", "we go"),
    (r"\blets go\b", "we go"),
    (r"\bi think\b", "me fink"),
    (r"\bright now\b", "wight now"),
    (r"\bi am\b", "me"),
    (r"\bi'm\b", "me"),
    (r"\bim\b", "me"),
    # After the longer "I …" phrases, so "I" does not get shouted via case-matching.
    (r"\bi\b", "me"),
]
_TODDLER_MAP = {
    "me": "me", "my": "my", "the": "da", "this": "dis", "that": "dat",
    "they": "dey", "them": "dem", "there": "dere", "please": "pwease",
    "little": "wittle", "love": "wuv", "hello": "hewwo", "hi": "hi",
    "friend": "fwiend", "friends": "fwiends", "water": "wawa", "really": "weawwy",
    "very": "vewy", "sorry": "sowwy", "right": "wight", "from": "fwom",
    "you": "you", "your": "your", "going": "goin", "want": "want",
}
_TODDLER_PROTECT = frozenset({
    "class", "lunch", "homework", "library", "school", "tomorrow", "today",
    "your", "more", "gonna", "wanna", "gotta", "then", "with", "know", "where",
    "meet", "help", "and",
})
_TODDLER_DONE = frozenset(_TODDLER_MAP.values()) | _TODDLER_PROTECT
_TODDLER_VOICELESS_TH = (
    "think", "thing", "thank", "three", "throw", "through", "thief", "thin",
    "thick", "thumb", "thought", "throat", "thrill", "thread", "threat",
    "threw", "thrown", "throne", "thirst", "thunder",
)
_TODDLER_OPENERS = ("Um, ", "Waaah, ", "Ooo, ", "Uh, ")
_TODDLER_CLOSERS = (" ...pwease?", " ...okay?", " ...yeah?")
_TODDLER_EMPTY = (
    "Waaah! Me no talk.",
    "Um, me got no words.",
    "Ooo, me quiet.",
)


def _toddler_one(word: str) -> str:
    if len(word) < 4 or word.isupper() or any(ch.isdigit() for ch in word):
        return word
    if word.lower() in _TODDLER_DONE:
        return word
    new = word.lower()
    if new.startswith("th"):
        voiceless = new.startswith(_TODDLER_VOICELESS_TH)
        new = ("f" if voiceless else "d") + new[2:]
    new = new.replace("r", "w")
    if word[0].isupper():
        new = new[0].upper() + new[1:]
    return new


def fx_toddler(text: str) -> str:
    """Rephrase as toddler talk. Content words stay readable."""
    if not _has_letters(text):
        return random.choice(_TODDLER_EMPTY)
    out = _apply_phrases(_normalize_apostrophes(text), _TODDLER_PHRASES)
    out = _word_swap(out, _TODDLER_MAP)
    out = WORD.sub(lambda match: _toddler_one(match.group(0)), out)
    out = _tidy_speech(out)
    if not _has_letters(out):
        return random.choice(_TODDLER_EMPTY)
    return _voice_bookend(
        out,
        openers=_TODDLER_OPENERS,
        closers=_TODDLER_CLOSERS,
        opener_re=r"^(um|waaah|ooo|uh)\b",
        closer_re=r"(pwease\?|okay\?|yeah\?)\s*$",
    )


# --- Superhero -------------------------------------------------------------
_SUPERHERO_PHRASES = [
    (r"\bi don't know\b", "I do not know"),
    (r"\bi dont know\b", "I do not know"),
    (r"\bi am going to\b", "I am bound for"),
    (r"\bi'm going to\b", "I am bound for"),
    (r"\bim going to\b", "I am bound for"),
    (r"\bgoing to\b", "bound for"),
    (r"\bthank you\b", "you have my gratitude"),
    (r"\bthanks\b", "you have my gratitude"),
    (r"\blet's go\b", "we charge onward"),
    (r"\blets go\b", "we charge onward"),
    (r"\bi love you\b", "my heart stands with you"),
    (r"\bi am sorry\b", "I ask your pardon"),
    (r"\bi'm sorry\b", "I ask your pardon"),
    (r"\bright now\b", "this very moment"),
]
_SUPERHERO_MAP = {
    "hello": "greetings", "hi": "greetings", "hey": "greetings",
    "friend": "ally", "friends": "allies", "help": "aid",
    "bad": "villainous", "good": "noble", "fight": "battle", "stop": "halt",
    "people": "citizens", "everyone": "every citizen", "enemy": "foe",
    "problem": "crisis", "scared": "afraid", "home": "headquarters",
}
_SUPERHERO_OPENERS = (
    "HEAR ME, CITIZENS!",
    "NEVER FEAR!",
    "BY ALL THAT IS HEROIC —",
    "LET ALL BEAR WITNESS!",
    "STAND FAST!",
)
_SUPERHERO_CLOSERS = (
    " SO IT IS PROCLAIMED!",
    " LET ALL BEAR WITNESS!",
    " THIS I SWEAR!",
    " FOR ALL TO HEAR!",
)


def fx_superhero(text: str) -> str:
    """Rephrase as a bold proclamation. The deed they named stays in the line."""
    if not _has_letters(text):
        return "**HEAR ME, CITIZENS!** A hero stands ready! THIS I SWEAR!"
    out = _apply_phrases(_normalize_apostrophes(text), _SUPERHERO_PHRASES)
    out = _tidy_speech(_word_swap(out, _SUPERHERO_MAP))
    out = " ".join(out.split())
    if not _has_letters(out):
        out = "A hero stands ready"
    out = _cap_first(out).rstrip(".!?")
    opener = random.choice(_SUPERHERO_OPENERS)
    closer = random.choice(_SUPERHERO_CLOSERS)
    return f"**{opener}** {out}!{closer}"


# --- Sheep -----------------------------------------------------------------
_SHEEP_PHRASES = [
    (r"\bi don't know\b", "baaa, I do not know"),
    (r"\bi dont know\b", "baaa, I do not know"),
    (r"\bi am going to\b", "I'm trotting off to"),
    (r"\bi'm going to\b", "I'm trotting off to"),
    (r"\bim going to\b", "I'm trotting off to"),
    (r"\bgoing to\b", "trotting to"),
    (r"\bthank you\b", "baaa, thank ewe"),
    (r"\bthanks\b", "baaa, thank ewe"),
    (r"\bhello\b", "baaa, hello"),
    (r"\bhi\b", "baaa, hi"),
    (r"\bhey\b", "baaa, hey"),
    (r"\bgood morning\b", "baaa, good morning"),
    (r"\bgood night\b", "baaa, good night"),
    (r"\blet's go\b", "let the flock go"),
    (r"\blets go\b", "let the flock go"),
    (r"\bi love you\b", "I love ewe"),
    (r"\bi like you\b", "I like ewe"),
    (r"\bsee you\b", "see ewe"),
    (r"\bhelp me\b", "baaa, help me"),
    (r"\bi think\b", "baaa, I think"),
    (r"\byes\b", "baaa yes"),
    (r"\bno\b", "baaa no"),
]
_SHEEP_MAP = {
    "you": "ewe", "people": "flock", "everyone": "the whole flock",
    "friend": "flockmate", "friends": "flockmates", "eat": "graze on",
    "eating": "grazing on", "sleep": "rest in the pasture", "home": "the pen",
    "house": "the pen", "go": "trot", "going": "trotting", "come": "trot over",
    "walk": "trot", "run": "bolt", "we": "the flock", "us": "the flock",
    "together": "as a flock",
}
_SHEEP_OPENERS = ("Baaa... ", "Baaa, ", "Baa-baaa, ")
_SHEEP_CLOSERS = (" ...baaa!", " ...baaa.", " , baaa.")
_SHEEP_EMPTY = (
    "Baaa... baaa.",
    "Baaa. The flock is quiet, baaa.",
    "Baaa... nothing to bleat, baaa.",
)


def _weave_baaa(text: str) -> str:
    words = text.split()
    if not words:
        return "baaa"
    out = []
    since = 0
    for word in words:
        out.append(word)
        if "baaa" in word.lower():
            since = 0
            continue
        since += 1
        if since >= 5:
            out.append("baaa")
            since = 0
    return " ".join(out)


def fx_sheep(text: str) -> str:
    """Rephrase in sheep-speak. Baaa is woven around the words, not instead of them."""
    if not _has_letters(text):
        return random.choice(_SHEEP_EMPTY)
    out = _apply_phrases(_normalize_apostrophes(text), _SHEEP_PHRASES)
    out = _tidy_speech(_word_swap(out, _SHEEP_MAP))
    out = "\n".join(_weave_baaa(line) if line.strip() else line for line in out.splitlines())
    if not _has_letters(out):
        return random.choice(_SHEEP_EMPTY)
    out = _voice_bookend(
        out,
        openers=_SHEEP_OPENERS,
        closers=_SHEEP_CLOSERS,
        opener_re=r"^baa",
        closer_re=r"baaa[.!]?\s*$",
    )
    if "baaa" not in out.lower():
        out = f"Baaa, {out}"
    return out


# --- Gen Z -----------------------------------------------------------------
_GENZ_PHRASES = [
    (r"\bi don't know\b", "no cap I don't know"),
    (r"\bi dont know\b", "no cap I don't know"),
    (r"\bi am going to\b", "lowkey I'm gonna"),
    (r"\bi'm going to\b", "lowkey I'm gonna"),
    (r"\bim going to\b", "lowkey I'm gonna"),
    (r"\bgoing to\b", "gonna"),
    (r"\bthank you\b", "tysm"),
    (r"\bthanks\b", "tysm"),
    (r"\bhello\b", "yo"),
    (r"\bhi\b", "yo"),
    (r"\bhey\b", "yo"),
    (r"\bi think\b", "lowkey I think"),
    (r"\bi love\b", "I'm obsessed with"),
    (r"\bplease\b", "pls"),
    (r"\blet's go\b", "bet let's go"),
    (r"\blets go\b", "bet let's go"),
]
_GENZ_MAP = {
    "friend": "bestie", "friends": "besties", "really": "fr", "very": "lowkey",
    "great": "bussin", "awesome": "bussin", "amazing": "bussin", "good": "bussin",
    "yes": "no cap", "yeah": "fr", "hello": "yo", "hi": "yo",
    "please": "pls",
}
_GENZ_OPENERS = ("No cap, ", "Fr fr, ", "Lowkey, ", "Bestie, ")
_GENZ_CLOSERS = (" fr fr.", ", no cap.", ", bussin.", " — lowkey.")
_GENZ_EMPTY = (
    "No cap, nothing to say, fr fr.",
    "Lowkey silence, bussin.",
    "Fr fr, they said nothing, no cap.",
)
_GENZ_MARKERS = ("no cap", "fr fr", "lowkey", "bussin")


def fx_genz(text: str) -> str:
    """Rephrase in Gen Z slang. The facts stay; the voice is no cap / fr fr."""
    if not _has_letters(text):
        return random.choice(_GENZ_EMPTY)
    out = _apply_phrases(_normalize_apostrophes(text), _GENZ_PHRASES)
    out = _tidy_speech(_word_swap(out, _GENZ_MAP))
    if not _has_letters(out):
        return random.choice(_GENZ_EMPTY)
    out = _voice_bookend(
        out,
        openers=_GENZ_OPENERS,
        closers=_GENZ_CLOSERS,
        opener_re=r"^(no cap|fr fr|lowkey|bestie)\b",
        closer_re=r"(fr fr\.|no cap\.|bussin\.|lowkey\.)\s*$",
    )
    if not any(marker in out.lower() for marker in _GENZ_MARKERS):
        out = f"{out.rstrip('.!?')}, fr fr, no cap."
    return out


# --- Dog -------------------------------------------------------------------
_DOG_PHRASES = [
    (r"\bi don't know\b", "woof, I don't know"),
    (r"\bi dont know\b", "woof, I don't know"),
    (r"\bi am going to\b", "I'm bounding off to"),
    (r"\bi'm going to\b", "I'm bounding off to"),
    (r"\bim going to\b", "I'm bounding off to"),
    (r"\bgoing to\b", "bounding to"),
    (r"\bhello\b", "woof, hello"),
    (r"\bhi\b", "woof, hi"),
    (r"\bhey\b", "woof, hey"),
    (r"\bi think\b", "I think, tail wagging"),
    (r"\bthank you\b", "woof, thank you, treat please"),
    (r"\bthanks\b", "woof, thank you, treat please"),
    (r"\blet's go\b", "walkies, let's go"),
    (r"\blets go\b", "walkies, let's go"),
    (r"\bhelp me\b", "woof, help me"),
    (r"\byes\b", "woof yes"),
    (r"\bfood\b", "treats"),
]
_DOG_MAP = {
    "friend": "buddy", "friends": "buddies", "eat": "have a treat",
    "food": "treats", "walk": "walkies", "walking": "going walkies",
}
_DOG_OPENERS = ("Woof! ", "Squirrel! Wait, woof — ", "Ooh, walkies! ")
_DOG_CLOSERS = (". Squirrel!", ". Walk? Treat? Woof!", ". Woof, treat?")
_DOG_EMPTY = (
    "Woof! Squirrel! Walk? Treat?",
    "Woof woof! Nothing to say — squirrel!",
    "Walkies? Treat? Woof!",
)


def _weave_woof(text: str) -> str:
    words = text.split()
    if not words:
        return "woof"
    out = []
    since = 0
    for word in words:
        out.append(word)
        if "woof" in word.lower():
            since = 0
            continue
        since += 1
        if since >= 6:
            out.append("woof")
            since = 0
    return " ".join(out)


def fx_dog(text: str) -> str:
    """Rephrase as an excited, easily distracted dog. The errand stays in the bark."""
    if not _has_letters(text):
        return random.choice(_DOG_EMPTY)
    out = _apply_phrases(_normalize_apostrophes(text), _DOG_PHRASES)
    out = _tidy_speech(_word_swap(out, _DOG_MAP))
    out = "\n".join(_weave_woof(line) if line.strip() else line for line in out.splitlines())
    if not _has_letters(out):
        return random.choice(_DOG_EMPTY)
    out = _voice_bookend(
        out,
        openers=_DOG_OPENERS,
        closers=_DOG_CLOSERS,
        opener_re=r"^(woof|squirrel|ooh)\b",
        closer_re=r"(squirrel!|woof!|treat\?)\s*$",
    )
    low = out.lower()
    if "woof" not in low:
        out = f"Woof! {out}"
        low = out.lower()
    if "squirrel" not in low and "walk" not in low and "treat" not in low:
        out = f"{out.rstrip('.!')} Squirrel! Walk? Treat?"
    return out


# --- Snake -----------------------------------------------------------------
_SNAKE_PHRASES = [
    (r"\bi don't know\b", "I do not know, yesss"),
    (r"\bi dont know\b", "I do not know, yesss"),
    (r"\bi am going to\b", "I am sslithering to"),
    (r"\bi'm going to\b", "I am sslithering to"),
    (r"\bim going to\b", "I am sslithering to"),
    (r"\bgoing to\b", "sslithering to"),
    (r"\byes\b", "yesss"),
    (r"\byeah\b", "yesss"),
    (r"\bhello\b", "hiss, hello"),
    (r"\bhi\b", "hiss, hello"),
    (r"\bhey\b", "hiss, hello"),
]
_SNAKE_OPENERS = ("Sssss. ", "Hissss — ", "Yesss, ")
_SNAKE_CLOSERS = (" ...sssss.", " yessss.", " — hissss.")
_SNAKE_EMPTY = (
    "Sssss...",
    "Hissss. Nothing was said, yesss.",
    "Sssss. Silence, yessss.",
)


def _stretch_esses(word: str) -> str:
    def repl(match: re.Match) -> str:
        run = match.group(0)
        if len(run) >= 4:
            return run
        extra = "S" * 3 if run[-1].isupper() else "s" * 3
        return run + extra
    return re.sub(r"[sS]+", repl, word)


def fx_snake(text: str) -> str:
    """Rephrase with a hiss: stretched s sounds, the words still readable."""
    if not _has_letters(text):
        return random.choice(_SNAKE_EMPTY)
    out = _apply_phrases(_normalize_apostrophes(text), _SNAKE_PHRASES)
    out = WORD.sub(lambda match: _stretch_esses(match.group(0)), out)
    out = _tidy_speech(out)
    if not _has_letters(out):
        return random.choice(_SNAKE_EMPTY)
    out = _voice_bookend(
        out,
        openers=_SNAKE_OPENERS,
        closers=_SNAKE_CLOSERS,
        opener_re=r"^(ssss|hiss|yess)",
        closer_re=r"(ssss+\.|yesss+\.|hisss+\.)\s*$",
    )
    if "ssss" not in out.lower():
        out = f"Sssss. {out}"
    return out


# --- Corporate email -------------------------------------------------------
_CORP_PHRASES = [
    (r"\bi don't know\b", "I do not yet have visibility; I don't know"),
    (r"\bi dont know\b", "I do not yet have visibility; I don't know"),
    (r"\bi am going to\b", "I will circle back and proceed to"),
    (r"\bi'm going to\b", "I will circle back and proceed to"),
    (r"\bim going to\b", "I will circle back and proceed to"),
    (r"\bgoing to\b", "proceeding to"),
    (r"\bthank you\b", "thanks in advance"),
    (r"\bthanks\b", "thanks in advance"),
    (r"\bhello\b", "Hello, team"),
    (r"\bhi\b", "Hello, team"),
    (r"\bhey\b", "Hello, team"),
    (r"\bi think\b", "per my last message, I think"),
    (r"\bplease help me\b", "kindly action this deliverable for me"),
    (r"\bhelp me\b", "action this deliverable for me"),
    (r"\bplease\b", "kindly"),
    (r"\blet's go\b", "let's align and move the deliverable"),
    (r"\blets go\b", "let's align and move the deliverable"),
]
_CORP_MAP = {
    "problem": "blocker", "problems": "blockers", "meeting": "sync",
    "boss": "stakeholder",
}
_CORP_OPENERS = (
    "Per my last message, ",
    "Circling back, ",
    "Hi team — ",
)
_CORP_CLOSERS = (
    ". Please advise on the deliverables.",
    ". Thanks in advance.",
    ". Happy to circle back.",
)
_CORP_EMPTY = (
    "Per my last message, there is no update. Please advise on the deliverables.",
    "Circling back — nothing was sent. Thanks in advance.",
    "Hi team — no content to action. Happy to circle back.",
)
_CORP_MARKERS = ("per my last", "circle back", "circling back", "deliverable")


def fx_corporate(text: str) -> str:
    """Rephrase as a corporate email. The request stays; the jargon wraps it."""
    if not _has_letters(text):
        return random.choice(_CORP_EMPTY)
    out = _apply_phrases(_normalize_apostrophes(text), _CORP_PHRASES)
    out = _tidy_speech(_word_swap(out, _CORP_MAP))
    out = " ".join(out.split())
    if not _has_letters(out):
        return random.choice(_CORP_EMPTY)
    out = _voice_bookend(
        out,
        openers=_CORP_OPENERS,
        closers=_CORP_CLOSERS,
        opener_re=r"^(per my last|circling back|hi team|hello, team)\b",
        closer_re=r"(deliverables\.|in advance\.|circle back\.)\s*$",
    )
    if not any(marker in out.lower() for marker in _CORP_MARKERS):
        out = f"Per my last message, {out.rstrip('.!?')}. Please advise on the deliverables."
    return out


# --- Overthinker -----------------------------------------------------------
_OVERTHINK_STOP = frozenset({
    "i", "i'm", "im", "am", "a", "an", "the", "to", "and", "then", "is", "my",
    "we", "should", "with", "me", "do", "not", "don't", "dont", "of", "for",
    "it", "that", "this", "be", "was", "are", "on", "in", "at",
})
_OVERTHINK_EXTRAS = (
    " What if I already messed it up?",
    " Am I overthinking this?",
    " Sorry, ignore that — unless I shouldn't?",
    " Wait, should I have said less?",
)
_OVERTHINK_EMPTY = (
    "Wait — did I mean to say nothing? Was that wrong? Sorry. What if silence was the wrong choice?",
    "Sorry. Was saying nothing a mistake? What if they noticed? Am I overthinking this?",
)


def _overthink_words(text: str) -> list[str]:
    found = []
    seen = set()
    for word in re.findall(r"[A-Za-z']+", text):
        key = word.lower()
        if key in _OVERTHINK_STOP or key in seen:
            continue
        seen.add(key)
        found.append(word)
        if len(found) == 3:
            break
    return found


def fx_overthinker(text: str) -> str:
    """Rephrase as an anxious spiral. The original point is said, then second-guessed."""
    if not _has_letters(text):
        return random.choice(_OVERTHINK_EMPTY)
    core = " ".join(_normalize_apostrophes(text).split()).strip()
    if not core:
        return random.choice(_OVERTHINK_EMPTY)
    if core[-1] not in ".!?":
        core += "."
    words = _overthink_words(core)
    bits = ["Wait, is that right?"]
    if words:
        bits.append(f"What if {words[0].lower()} is the wrong word?")
    if len(words) > 1:
        bits.append(f"Or did I mean {words[1].lower()} differently?")
    if len(words) > 2:
        bits.append(f"And {words[2].lower()} — was that too much?")
    bits.append("Sorry. Should I say it again?")
    return core + " " + " ".join(bits) + random.choice(_OVERTHINK_EXTRAS)


# --- Influencer ------------------------------------------------------------
_INFLUENCER_PHRASES = [
    (r"\bi am going to\b", "your girl is going to"),
    (r"\bi'm going to\b", "your girl is going to"),
    (r"\bim going to\b", "your girl is going to"),
    (r"\bgoing to\b", "headed to"),
    (r"\bthank you\b", "thank you, besties"),
    (r"\bthanks\b", "thank you, besties"),
    (r"\bi love\b", "I am obsessed with"),
    (r"\bplease\b", "please, besties"),
    (r"\bi think\b", "okay so I think"),
]
_INFLUENCER_MAP = {
    "friend": "bestie", "friends": "besties", "great": "iconic",
    "awesome": "iconic", "amazing": "iconic",
}
_INFLUENCER_EMOJIS = ("✨", "💅", "💕", "👑", "🔥")
_INFLUENCER_OPENERS = ("Hey besties! ", "Besties, hi! ", "Hey besties, okay so ")
_INFLUENCER_CLOSERS = (
    " Like and comment!",
    " Drop a like and comment, besties!",
    " Like, comment, and share!",
)
_INFLUENCER_EMPTY = (
    "Hey besties! Nothing to share, but like and comment anyway ✨",
    "Hey besties! Back with no tea, like and comment 💅",
)


def fx_influencer(text: str) -> str:
    """Rephrase as an influencer post: hey besties, emojis, like and comment."""
    if not _has_letters(text):
        return random.choice(_INFLUENCER_EMPTY)
    out = _apply_phrases(_normalize_apostrophes(text), _INFLUENCER_PHRASES)
    out = _tidy_speech(_word_swap(out, _INFLUENCER_MAP))
    out = " ".join(out.split()).strip().rstrip(".!?")
    if not _has_letters(out):
        return random.choice(_INFLUENCER_EMPTY)
    if not re.match(r"^(hey )?besties\b", out, flags=re.IGNORECASE):
        out = random.choice(_INFLUENCER_OPENERS) + out
    emoji = random.choice(_INFLUENCER_EMOJIS)
    closer = random.choice(_INFLUENCER_CLOSERS)
    if "like" not in out.lower() or "comment" not in out.lower():
        out = f"{out}!{closer}"
    elif not out.endswith("!"):
        out = f"{out}!"
    if not any(mark in out for mark in _INFLUENCER_EMOJIS):
        out = f"{out} {emoji}"
    return out


EFFECTS = {
    "reversed": {"name": "Backwards Curse", "description": "Every word comes out back-to-front.",
                "func": fx_reversed},
    "runon": {"name": "Run-On Curse", "description": "Deletes every space and punctuation mark - it all runs "
             "together into one impossible word.", "func": fx_runon},
    "piglatin": {"name": "Pig Latin Curse", "description": "Twists their words into Pig Latin.",
                "func": fx_piglatin},
    "country": {
        "name": "Cowboy Curse",
        "description": "Full country twang — howdy, reckon, y'all, the whole porch sermon.",
        "func": fx_country,
    },
    "stutter": {"name": "Stutter Curse", "description": "Makes them stutter over random syllables.",
               "func": fx_stutter},
    "cheerleader": {"name": "Cheerleader Curse", "description": "Curses them into the most school-spirited Velmora "
                    "cheerleader alive.", "func": fx_cheerleader},
    "frog": {"name": "Frog Curse", "description": "Turns every third word into a ribbit.", "func": fx_frog},
    "cat": {"name": "Cat Curse", "description": "Turns every third word into a meow, and ends every sentence "
           "with a *purrrr*.", "func": fx_cat},
    "caveman": {
        "name": "Caveman Curse",
        "description": "Full caveman talk — short words, no fluff, lots of ugh and smash.",
        "func": fx_caveman,
    },
    "pirates_tongue": {
        "name": "Pirate Curse",
        "description": "Full pirate speech — every line comes out as salty sailor talk.",
        "func": fx_pirates_tongue,
    },
    "limp_wand": {
        "name": "Limp Wand",
        "description": "Their wand hangs limp — /wand, /patronus, /patronusreset, /broom, and wild-threat spell buttons won't answer for an hour.",
        "func": None,  # not a chat mangle; blocks wand commands instead
        "blocks_wand": True,
        "fixed_minutes": 60,
    },
    # Staff-cast only. scroll_effect_keys() leaves these out of Hex Scrolls.
    "yoda": {
        "name": "Yoda Curse",
        "description": "Rephrases every line in Yoda's inverted speech, meaning intact.",
        "func": fx_yoda,
        "staff_only": True,
    },
    "rhyme": {
        "name": "Rhyme Curse",
        "description": "Rephrases every line into a rhyming couplet, meaning intact.",
        "func": fx_rhyme,
        "staff_only": True,
    },
    "narrator": {
        "name": "Dramatic Narrator Curse",
        "description": "Rephrases every line as dramatic third-person narration, meaning intact.",
        "func": fx_narrator,
        "staff_only": True,
    },
    "haiku": {
        "name": "Haiku Curse",
        "description": "Rephrases every line into a 5-7-5 haiku, meaning intact.",
        "func": fx_haiku,
        "staff_only": True,
    },
    "toddler": {
        "name": "Toddler Curse",
        "description": "Rephrases every line as toddler baby talk, meaning intact.",
        "func": fx_toddler,
        "staff_only": True,
    },
    "superhero": {
        "name": "Superhero Curse",
        "description": "Rephrases every line as a bold superhero proclamation, meaning intact.",
        "func": fx_superhero,
        "staff_only": True,
    },
    "sheep": {
        "name": "Sheep Curse",
        "description": "Rephrases every line in sheep-speak with baaa woven in, meaning intact.",
        "func": fx_sheep,
        "staff_only": True,
    },
    "genz": {
        "name": "Gen Z Curse",
        "description": "Rephrases every line in Gen Z slang — no cap, fr fr, lowkey, bussin — meaning intact.",
        "func": fx_genz,
        "staff_only": True,
    },
    "dog": {
        "name": "Dog Curse",
        "description": "Rephrases every line as an excited, easily distracted dog, meaning intact.",
        "func": fx_dog,
        "staff_only": True,
    },
    "snake": {
        "name": "Snake Curse",
        "description": "Rephrases every line as hissing speech with stretched s sounds, meaning intact.",
        "func": fx_snake,
        "staff_only": True,
    },
    "corporate": {
        "name": "Corporate Email Curse",
        "description": "Rephrases every line as office jargon — circle back, deliverables — meaning intact.",
        "func": fx_corporate,
        "staff_only": True,
    },
    "overthinker": {
        "name": "Overthinker Curse",
        "description": "Rephrases every line as anxious second-guessing and spiraling questions, meaning intact.",
        "func": fx_overthinker,
        "staff_only": True,
    },
    "influencer": {
        "name": "Influencer Curse",
        "description": "Rephrases every line as a hey-besties post with emojis, likes, and comments, meaning intact.",
        "func": fx_influencer,
        "staff_only": True,
    },
}


def scroll_effect_keys() -> list[str]:
    """Curses a bought Hex Scroll may roll. Staff-only voices are excluded."""
    return [key for key, spell in EFFECTS.items() if not spell.get("staff_only")]

# Player wand-kit slash commands blocked by Limp Wand (staff resets stay usable).
WAND_KIT_COMMANDS = frozenset({"wand", "patronus", "patronusreset", "broom"})

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
        return any(
            (r.name or "").lower() == HEADMASTER_ROLE_NAME
            for r in getattr(member, "roles", [])
        )

    def _can_hex(self, member: discord.Member) -> bool:
        """Headmasters / guild managers, or the Presidents role."""
        store = self.bot.get_cog("Store")
        if store is not None:
            return bool(store.can_hex(member))
        return self._is_headmaster(member)

    # ------------------------------------------------------------ casting

    def apply_hex(self, *, target_id: int, effect: str, duration_minutes: int | None,
                  cast_by: int) -> tuple[dict, bool]:
        """Apply a hex by effect key. duration_minutes None/0 = until lifted.
        Limp Wand always lasts its fixed_minutes (60). Returns (spell dict,
        was_replacing_existing). Raises KeyError if effect unknown.
        Raises PermissionError when the target has active Liquid Luck."""
        potions = self.bot.get_cog("Potions")
        if potions and potions.has_liquid_luck(target_id):
            raise PermissionError("liquid_luck")
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
        potions = self.bot.get_cog("Potions")
        if potions and potions.has_liquid_luck(user_id):
            # Liquid Luck: immune — existing hexes don't apply while luck lasts.
            return None
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
        when = (
            "until a Headmaster or President lifts it"
            if mins is None
            else f"for about {mins} more minute(s)"
        )
        msg = (
            "Your wand hangs limp and won't answer. "
            f"`/wand`, `/patronus`, `/patronusreset`, `/broom`, and wild-threat spell buttons are out {when}."
        )
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return True

    async def hex(self, interaction: discord.Interaction, member: discord.Member,
                  effect: app_commands.Choice[str], duration: app_commands.Range[int, 0, 10080]):
        if not self._can_hex(interaction.user):
            await interaction.response.send_message(
                "Only a Headmaster or President may cast this.", ephemeral=True
            )
            return
        if member.bot:
            await interaction.response.send_message("You can't hex a bot.", ephemeral=True)
            return

        if effect.value not in EFFECTS:
            await interaction.response.send_message(
                "That curse doesn't exist anymore - your Discord app is showing a stale spell list. Force-quit "
                "and reopen Discord (or wait a bit for it to refresh) and try `/hex` again.", ephemeral=True)
            return
        try:
            spell, was_hexed = self.apply_hex(
                target_id=member.id, effect=effect.value,
                duration_minutes=duration or None, cast_by=interaction.user.id,
            )
        except PermissionError:
            await interaction.response.send_message(
                f"🥇 **{member.display_name}** is riding Liquid Luck — the hex slides right off.",
                ephemeral=True,
            )
            return

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
            until = "until a Headmaster or President lifts it"
        else:
            until = f"for {duration} minute(s)"
        await interaction.followup.send(
            f"🪄 {member.mention} is hexed with **{spell['name']}** ({spell['description']}){replaced_note}, "
            f"{until}.", ephemeral=True)

    async def unhex(self, interaction: discord.Interaction, member: discord.Member):
        if not self._can_hex(interaction.user):
            await interaction.response.send_message(
                "Only a Headmaster or President may lift this.", ephemeral=True
            )
            return
        existed = self.state["hexed"].pop(str(member.id), None)
        self.save()
        if existed:
            await interaction.response.send_message(f"The hex on {member.mention} has been lifted.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{member.mention} isn't currently hexed.", ephemeral=True)

    async def hexlist(self, interaction: discord.Interaction):
        if not self._can_hex(interaction.user):
            await interaction.response.send_message(
                "Only a Headmaster or President may see this.", ephemeral=True
            )
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
