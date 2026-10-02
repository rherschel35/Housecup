"""
The Forrest of Caden — Chapters 1 & 2 script (private test).

Source draft: uploaded forrest-of-caden-ch1-2 rewrite.
Placeholders: {you} {missing} {mhe} {mhim} {mhis} {mThey}.
"""

from __future__ import annotations

ART = {
    "title": "title_mack_yuna.jpg",
    "dream": "dream_childhood.jpg",
    "scare": "dream_scare.jpg",
    "gus_talk": "gus_hallway.jpg",
    "sebastian": "sebastian_talk.jpg",
    "party": "party_table.jpg",
    "materials": "materials_pack.jpg",
    "missing_seat": "empty_seat.jpg",
    "sneak": "mordy_sneak.jpg",
    "city_fight": "city_fight.jpg",
    "city_fight_trio": "city_fight_trio.jpg",
    "city_fight_duo": "city_fight.jpg",
    "train": "train_trio.jpg",
    "train_trio": "train_trio.jpg",
    "train_hand": "train_mack_ella.jpg",
    "caden": "caden_arrive.jpg",
    "alley": "alley_kid.jpg",
    "cave_tip": "cave_tip_kid.jpg",
    "nox": "nox.jpg",
    "inn": "inn_gus_mack.jpg",
    "edge": "forrest_edge.jpg",
    "mack": "mack.jpg",
    "mack_skills": "mack_skills.jpg",
    "ask_shops": "ask_shops.jpg",
    "ask_food": "ask_food.jpg",
    "yuna": "yuna.jpg",
    "ella": "ella.jpg",
    "gus": "gus.jpg",
}

SCHOOL_MATERIALS = [
    ("rope", "Rope & chalk", "For climbing, and for marking trees so you can find your way back."),
    ("heal", "Healing kit", "Softens a bad hit later."),
    ("rations", "Extra rations", "Food, and something to share."),
    ("map", "Old map scrap of Caden", "One cleaner path through the town or the trees."),
]

CITY_ESSENTIALS_3 = [
    ("lantern", "Lantern oil", "Light that lasts past dusk."),
    ("blanket", "Wool blanket", "Cold nights in the Forrest."),
    ("knife", "Trail knife", "More tool than weapon — still sharp."),
    ("charm", "Cheap luck charm", "The shopkeeper swears by it. Maybe."),
    ("jerky", "Dried meat", "Gus already has opinions about seasoning."),
    ("salve", "Burn salve", "City medicine, forest useful."),
]

CITY_ESSENTIALS_2 = [
    ("lantern", "Lantern oil", "Light that lasts past dusk."),
    ("blanket", "Wool blanket", "Cold nights in the Forrest."),
    ("knife", "Trail knife", "More tool than weapon — still sharp."),
    ("jerky", "Dried meat", "It isn't good. It is food."),
]

MATERIAL_LABELS = {k: label for k, label, _ in SCHOOL_MATERIALS + CITY_ESSENTIALS_3}

# Three story skills. Points spent = modifier (base +0).
ABILITIES = [
    ("atk", "Attack", "Monster fights and the city robbery."),
    ("wis", "Wisdom", "Notes, letters, dialog, noticing what matters."),
    ("ste", "Stealth", "Sneaking, eavesdropping, not getting caught."),
]
ABILITY_KEYS = [a[0] for a in ABILITIES]
ABILITY_LABELS = {k: label for k, label, _ in ABILITIES}
SKILL_POINTS_TOTAL = 3
SKILL_POINTS_MAX_PER = 2

# Story check / mini-game name → skill key.
CHECK_ABILITY = {
    "Attack": "atk",
    "Wisdom": "wis",
    "Stealth": "ste",
    # Legacy / alias names from older script wording → Wisdom
    "Investigation": "wis",
    "Perception": "wis",
    "Charisma": "wis",
    "Persuasion": "wis",
}

# Timed Attack mini-game: enemy casts a hex; pick the correct counter before it lands.
# Window: 5s alone / 10s with Gus. Options: 4 at Attack +0, 3 at +1, 2 at +2.
HEX_SECONDS_SOLO = 5
HEX_SECONDS_WITH_GUS = 10
HEX_OPTION_BASE = 4  # Attack +0
HEX_ROUNDS_TO_WIN = 2
HEX_ROUNDS_MAX = 3
HEX_PAIRS = [
    {
        "hex": "Stinging Hex",
        "counter": "Protego",
        "hit": "Needles of light stitch into your arm before you can finish the counter.",
        "block": "Your shield catches the sting and sheds it as sparks.",
    },
    {
        "hex": "Leg-Locker",
        "counter": "Finite",
        "hit": "Your legs snap together; cobbles rush up to meet you.",
        "block": "Finite cracks the lock — you stumble, free.",
    },
    {
        "hex": "Disarming Hex",
        "counter": "Expelliarmus",
        "hit": "Your wand wrenches free; you snatch it back a heartbeat too late.",
        "block": "You throw their disarm back. Their wand jumps; they swear.",
    },
    {
        "hex": "Bind Hex",
        "counter": "Relashio",
        "hit": "Invisible rope cinches your wrists and yanks you off balance.",
        "block": "Relashio blows the binding apart in a hot snap.",
    },
    {
        "hex": "Knockback Hex",
        "counter": "Arresto",
        "hit": "The blast throws you into a crate. Breath gone.",
        "block": "Arresto kills the force a foot from your chest.",
    },
    {
        "hex": "Blinding Sparks",
        "counter": "Protego",
        "hit": "White fire claws across your eyes. The street vanishes.",
        "block": "Sparks sheet off your shield and die on the stones.",
    },
]
HEX_SPELL_POOL = sorted(
    {
        *(p["counter"] for p in HEX_PAIRS),
        "Stupefy",
        "Lumos",
        "Accio",
        "Wingardium",
        "Rictusempra",
    }
)


def hex_option_count(attack_mod: int) -> int:
    """Fewer wrong answers as Attack rises: +0→4, +1→3, +2→2."""
    return max(2, HEX_OPTION_BASE - max(0, int(attack_mod or 0)))


def hex_seconds(gus_with_party: bool) -> int:
    return HEX_SECONDS_WITH_GUS if gus_with_party else HEX_SECONDS_SOLO


def default_abilities() -> dict[str, int]:
    return {k: 0 for k in ABILITY_KEYS}


def ability_mod(abilities: dict | None, skill: str) -> int:
    """Modifier for a check or skill name (e.g. Wisdom, Attack)."""
    key = CHECK_ABILITY.get(skill) or CHECK_ABILITY.get(skill.title())
    if not key:
        return 0
    try:
        return int((abilities or {}).get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def format_abilities(abilities: dict | None) -> str:
    ab = abilities or {}
    parts = []
    for key, label, _ in ABILITIES:
        mod = int(ab.get(key, 0) or 0)
        sign = f"+{mod}" if mod >= 0 else str(mod)
        parts.append(f"**{label}** {sign}")
    return " · ".join(parts)


def pronouns(_protagonist: str | None = None) -> dict[str, str]:
    """Always Mack looking for Yuna. m* = Yuna's pronouns."""
    return {
        "you": "Mack",
        "missing": "Yuna",
        "mhe": "she",
        "mhim": "her",
        "mhis": "her",
        "mThey": "She",
    }


def render(text: str, p: dict[str, str]) -> str:
    out = text
    for k, v in p.items():
        out = out.replace("{" + k + "}", v)
    return out


# ---------------------------------------------------------------------------
# Chapter 1
# ---------------------------------------------------------------------------

CH1_NODES: dict[str, dict] = {
    "title": {
        "art": "title",
        "pages": [
            "**The Forrest of Caden**\n"
            "*An eight-part solo adventure about two friends who drifted apart*\n\n"
            "**You are Mack.** Yuna didn't come back for fifth year, and you're going to find her.\n\n"
            "*Private test: Chapters 1 & 2. No house points. Your choices are written into your story.*"
        ],
        "choices": [
            {"id": "begin", "label": "Begin", "set": {"protagonist": "mack"}, "goto": "skills"},
        ],
    },
    "skills": {
        "art": "mack_skills",
        "pages": [
            "Before the dream takes you, you take stock of what kind of wizard you've become — not grades, not house "
            "points. Three things decide whether a moment goes your way.\n\n"
            f"**Assign {SKILL_POINTS_TOTAL} skill points.** Each point is a **+1** "
            f"(max **+{SKILL_POINTS_MAX_PER}** in any one skill).\n\n"
            "• **Attack** — monster fights and the city robbery\n"
            "• **Wisdom** — notes, letters, dialog, noticing what matters\n"
            "• **Stealth** — sneaking, eavesdropping, not getting caught\n\n"
            "Checks are **d20 + your bonus** vs a DC. Spend all your points, then Continue."
        ],
        "mini": "assign_skills",
        "goto": "dream",
    },
    "dream": {
        "art": "dream",
        "pages": [
            "You were ten again, which is a rotten trick for a dream to play, because at ten you still believed "
            "summers were endless and grown-ups knew what they were doing.\n\n"
            "You and {missing} had the whole afternoon, and you spent it as if it owed you something. It was the same "
            "hill behind her family's old cottage: the crooked fence nobody ever mended, and the old sapwood tree that "
            "dripped something sticky onto your sleeves no matter where you stood. *Race you to the top. Loser nicks a "
            "fistful of honeycakes from the pantry, and loser gets caught.*\n\n"
            "You remember exactly how she laughed when she cheated. She shoved your shoulder just before the finish, "
            "not hard enough to hurt, just hard enough to say *I win either way.*\n\n"
            "There was grass on your knees and a mum calling from somewhere very far off, and neither of you answered. "
            "There was a jar of glimmerbugs you'd both sworn to let go by dusk and never did. {missing} held it up "
            "between you, and green-gold light swam across both your faces. Then she said, quietly, as if she was a "
            "little embarrassed by how much she meant it, that next year you'd still be like this. *Same stupid races. "
            "Same secrets. Same us.*\n\n"
            "You believed her. Of course you did. You always did.",
        ],
        "goto": "dream_scare",
    },
    "dream_scare": {
        "art": "scare",
        "pages": [
            "Then the light in the jar went out, though neither of you had touched the lid.\n\n"
            "The hill was gone. In its place stood the edge of the **Forrest of Caden**, and not the friendly sort of "
            "forest with picnic blankets and *see you before term starts.* The trees stood too close together, as if "
            "they had something to hide. The air tasted of wet iron and old leaves. {missing} was ahead of you on the "
            "path, looking back, saying your name the way she used to when you'd fallen behind on purpose just to make "
            "her wait.\n\n"
            "You tried to catch up. Your legs wouldn't move.\n\n"
            "Something shifted in the dark between the trunks. It was the wrong shape to be the wind.\n\n"
            "🎲 **Wisdom saving throw. DC 12.** *(d20 + your Wisdom)*\n"
            "*Do you see what it is before the dream lets go of you?*"
        ],
        "mini": "check_roll",
        "check": {
            "skill": "Wisdom",
            "dc": 12,
            "button": "Roll",
            "success_flag": "saw_the_hand",
            "success": (
                "For half a heartbeat you see it clearly: a long, pale hand with too many joints, reaching from behind "
                "a tree toward {missing}'s shoulder, not yours."
            ),
            "failure": "You see only shadow, and the dark closing like a door.",
        },
        "goto": "dream_bridge",
    },
    "dream_bridge": {
        "art": "scare",
        "pages": [],  # dynamic from roll
        "goto": "dream_wake",
    },
    "dream_wake": {
        "art": "scare",
        "pages": [
            "— and you woke with your own hand stretched into the empty air above your bed, your heart trying to kick "
            "its way out through your ribs, and the canopy of your four-poster swimming above you as if you'd been "
            "underwater.\n\n"
            "For a long minute you simply lay there and hated how quiet the dormitory was without her in it.\n\n"
            "Fifth year starts today.\n\n"
            "Last spring you told yourself this would be the year it got easier. People grow apart; everybody says so. "
            "Missing someone who still sends you an owl now and then was a childish sort of ache, and you'd grow out of "
            "it the way you grew out of sap-stained sleeves.\n\n"
            "Lying there, still half inside the dream, you knew you'd been lying to yourself.\n\n"
            "The missing isn't sharp any more. It's become the weather. You've walked around in it so long you forgot "
            "other people get sunshine. The dream didn't put the fear there. It just gave it a face.\n\n"
            "On the windowsill sits the last letter {missing} ever sent you. It's from July, and it ends mid-sentence."
        ],
        "choices": [
            {
                "id": "letter",
                "label": "Reread Yuna's letter",
                "check": {"skill": "Wisdom", "dc": 12},
                "set": {"wake_choice": "letter"},
                "goto": "wake_letter",
            },
            {
                "id": "breakfast",
                "label": "Go down to breakfast",
                "check": {"skill": "Wisdom", "dc": 12},
                "set": {"wake_choice": "breakfast"},
                "goto": "wake_breakfast",
            },
            {
                "id": "write",
                "label": "Write to her right now",
                "check": {"skill": "Wisdom", "dc": 12},
                "set": {"wake_choice": "write"},
                "goto": "wake_write",
            },
        ],
    },
    "wake_letter": {
        "art": "scare",
        "pages": [],  # dynamic
        "goto": "gus_morning",
    },
    "wake_breakfast": {
        "art": "missing_seat",
        "pages": [],  # dynamic
        "goto": "gus_morning",
    },
    "wake_write": {
        "art": "scare",
        "pages": [],  # dynamic
        "goto": "gus_morning",
    },
    "gus_morning": {
        "art": "gus_talk",
        "pages": [
            "Gus finds you before breakfast, the way he always does, like a small moon that's been circling your door "
            "waiting for a polite excuse to land.\n\n"
            '**Gus:** "You look wrecked." He immediately regrets how honest that was and pushes his glasses up his nose, '
            "which is what Gus does with his hands when he doesn't know what else to do with them. "
            '"Not — not *bad.* Just. Did you sleep at all?"\n\n'
            "You make a noise that could mean anything. Gus fills the silence, because Gus is allergic to silence when "
            "he's nervous. It brings him out in a rash of words.\n\n"
            "He talks around it twice. On the third try Ella's name slips out, casually, as if he hadn't rehearsed it "
            "all the way down the tower stairs. She laughed at his terrible joke yesterday, the one about the troll and "
            "the toll bridge. He can't tell if that means something, or if Ella just laughs at everyone because she's "
            "built that way, like a lantern that can't help giving off light. He's seventeen, he points out, which is "
            "practically ancient, and somehow he's still a coward about this one thing.\n\n"
            '**Gus:** "I\'m going to sound mad," he mutters. "But do I… *say* something? Or do I keep being the funny '
            'friend in the corner until I die of it and they carve *He Was Hilarious* on my headstone?"'
        ],
        "choices": [
            {
                "id": "tell",
                "label": "Tell her. Waiting doesn't make it kinder.",
                "set": {"gus_advice": "tell"},
                "goto": "gus_advice_tell",
            },
            {
                "id": "dont",
                "label": "Don't. Some things are safer left alone.",
                "set": {"gus_advice": "dont"},
                "goto": "gus_advice_dont",
            },
            {
                "id": "careful",
                "label": "I don't know. Just don't half-do it.",
                "set": {"gus_advice": "careful"},
                "goto": "gus_advice_careful",
            },
        ],
    },
    "gus_advice_tell": {
        "art": "gus_talk",
        "pages": [
            "Gus lets out a breath he seems to have been holding since second year.\n\n"
            '**Gus:** "Yeah. Okay. *Okay.* Not today. But… yeah."'
        ],
        "empath": (
            "His crush hangs loud in the air between you: warm, clumsy, hopeful, like a jumper knitted by someone who "
            "has never knitted before. Underneath it is something quieter. He's terrified of turning into wallpaper in "
            "her life, someone she walks past every day and never really sees.\n\n"
            "You feel both, as clearly as if they were your own.\n\n"
            "You don't tell him that the person your own chest still turns toward isn't in the castle at all."
        ),
        "goto": "missing",
    },
    "gus_advice_dont": {
        "art": "gus_talk",
        "pages": [
            "Gus nods too fast, relieved and disappointed in the same blink.\n\n"
            '**Gus:** "Right. Smart. I\'m very smart when you say I\'m smart."'
        ],
        "empath": (
            "His crush hangs loud in the air between you: warm, clumsy, hopeful, like a jumper knitted by someone who "
            "has never knitted before. Underneath it is something quieter. He's terrified of turning into wallpaper in "
            "her life, someone she walks past every day and never really sees.\n\n"
            "You feel both, as clearly as if they were your own.\n\n"
            "You don't tell him that the person your own chest still turns toward isn't in the castle at all."
        ),
        "goto": "missing",
    },
    "gus_advice_careful": {
        "art": "gus_talk",
        "pages": [
            "Gus snorts.\n\n"
            '**Gus:** "Helpful. Truly. Spiritual guidance from the emotionally constipated." Then, softer: '
            '"Thanks for not laughing."'
        ],
        "empath": (
            "His crush hangs loud in the air between you: warm, clumsy, hopeful, like a jumper knitted by someone who "
            "has never knitted before. Underneath it is something quieter. He's terrified of turning into wallpaper in "
            "her life, someone she walks past every day and never really sees.\n\n"
            "You feel both, as clearly as if they were your own.\n\n"
            "You don't tell him that the person your own chest still turns toward isn't in the castle at all."
        ),
        "goto": "missing",
    },
    "missing": {
        "art": "missing_seat",
        "pages": [
            "The first morning back always fills the castle the same way. Trunks scrape over flagstones. Owls arrive in "
            "a great feathery avalanche and drop the wrong post on the wrong heads. Somebody is already late for a "
            "lesson that hasn't started yet, and the green Thornmere scarves flash down every corridor as if the house "
            "is in a competition with itself.\n\n"
            "You look for {missing} without meaning to. It's an old habit, like reaching for a stair that isn't there. "
            "The doorway. Her usual seat. The stretch of wall by the Great Hall where she used to wait, bag half open, "
            "with a look that said *you're late on purpose again.*\n\n"
            "Nothing.\n\n"
            "You check twice, because once feels like panic and twice feels like proof.\n\n"
            "A prefect consults a long scroll and shrugs: no one by that name has signed in yet. A girl in your year "
            "thinks {missing} was coming later in the week. A boy from the next table didn't know the two of you still "
            "talked.\n\n"
            "You did. Just not enough. There were letters with too much space between the lines, and jokes that used to "
            "be easy arrived careful and polite. You told yourself the distance was just miles. Standing in a loud hall "
            "beside an empty seat, \"miles\" feels like a coward's word for *I let us fade.*\n\n"
            "The dream is back behind your eyes: the jar light dying, the hand reaching, the trees standing wrong.\n\n"
            "This year was supposed to be different. Maybe it still will be, just not the way you meant.\n\n"
            "Absence has a temperature. You're standing in cold air in a warm room."
        ],
        "choices": [
            {"id": "spot", "label": "Check her usual spot again", "goto": "sebastian"},
            {
                "id": "ask",
                "label": "Ask around properly",
                "check": {"skill": "Wisdom", "dc": 12},
                "goto": "sebastian",
            },
            {"id": "go", "label": "Go straight to Sebastian", "goto": "sebastian"},
        ],
    },
    "sebastian": {
        "art": "sebastian",
        "pages": [
            "Sebastian Thornmere hears you out without interrupting once, which is how you know it's bad. Ghosts usually "
            "love the sound of their own voices; it's one of the few pleasures they have left.\n\n"
            "He tries one joke early, something about truancy being a fine old Velmora tradition, then lets it fade when "
            "he sees your face, the way his own outline fades at the edges when he's upset.\n\n"
            "You tell him about the camping spot before term. About {missing} always turning up smelling of pine and "
            "woodsmoke, late on purpose, grinning as if the Forrest of Caden were a secret only the two of you were "
            "allowed to keep. About the letters thinning out. About the dream you won't quite admit was only a dream."
        ],
        "choices": [
            {"id": "scared", "label": "I'm scared something's wrong.", "goto": "sebastian_advice"},
            {"id": "firm", "label": "I'm going. Please don't stop me.", "goto": "sebastian_advice"},
            {
                "id": "guilt",
                "label": "If I stay and I'm wrong… I won't forgive myself.",
                "goto": "sebastian_advice",
            },
        ],
    },
    "sebastian_advice": {
        "art": "sebastian",
        "pages": [
            "He doesn't give you permission the way a teacher would. He gives it like someone who has been carrying a "
            "quieter version of your sentence for longer than you've been alive.\n\n"
            '**Sebastian:** "Go."\n\n'
            "Don't wait until the guilt has a name you can't put down, he tells you. He knows what it is to be able to "
            "help a friend and choose the smaller feeling instead. He won't watch you practise it.\n\n"
            '**Sebastian:** "Come back," he adds, almost lightly. "I\'m terrible at eulogies. And my puns get mean when '
            'I\'m sad."'
        ],
        "empath": (
            "His guilt doesn't reach out for you. It *recognises* you. Somehow that's worse, and it's also the thing "
            "that steadies your hands."
        ),
        "goto": "party",
    },
    "party": {
        "art": "party",
        "pages": [
            "You find Gus and Ella together, which feels like the universe showing off its sense of humour. Ella has ink "
            "on her thumb. Gus is talking with his hands, which means he's nervous, which means Ella is nearby, which, "
            "of course, she is.\n\n"
            "For half a second you almost don't say it. You almost let the first-day noise swallow you whole. Then you "
            "hear yourself telling the truth.\n\n"
            "You're leaving. Tonight, if you can. The train to Caden, then the Forrest. You're going to find {missing}."
        ],
        "choices": [
            {"id": "alone", "label": "I'm going alone if I have to.", "goto": "party_yes"},
            {"id": "need", "label": "I need you both.", "goto": "party_yes"},
            {"id": "feel", "label": "Something's wrong. I can feel it.", "goto": "party_yes"},
        ],
    },
    "party_yes": {
        "art": "party",
        "pages": [
            "Ella doesn't hesitate long enough for you to brace. Of course she's coming. She gives you a look, soft and "
            "stubborn and a little too open, that says this isn't only about adventure. You feel it land, and carefully, "
            "very carefully, you don't pick it up.\n\n"
            "Gus makes a joke about snacks and mortal peril in the same breath, because that's how Gus stays in the room "
            "when things get real. Under the joke he's already packing in his head.\n\n"
            "Nobody talks you out of it. That's how you know they're your people."
        ],
        "empath": (
            "Ella's care has your name written all through it. Gus's has hers. Yours still has the name of someone who "
            "isn't standing in this corridor. You hold all three truths and walk anyway."
        ),
        "goto": "materials",
    },
    "materials": {
        "art": "materials",
        "pages": [
            "Packing feels like pretending this is a weekend trip. It isn't, and your hands know it even when your mouth "
            "makes light of it.\n\n"
            "You take what three fifth-years can carry without looking as though they're fleeing the country. Ella folds "
            "things far more neatly than you do. Gus packs a ridiculous extra pair of socks \"for morale.\"\n\n"
            "**Choose 2** items for your pack."
        ],
        "mini": "pick_materials",
        "goto": "sneak_intro",
    },
    "sneak_intro": {
        "art": "sneak",
        "pages": [
            "After curfew the castle becomes a held breath. Every floorboard has opinions. Somewhere a portrait snores, "
            "and somewhere else a suit of armour is pretending very hard to be empty. You move like people who have done "
            "this before, and also like people who have definitely never done it when it mattered this much.\n\n"
            "Mordy doesn't need to shout to fill a hallway. He simply *is*: as old as the stones, sharp as a "
            "disappointment, and somehow always floating between you and the door you want.\n\n"
            "🎲 **Stealth: three rounds.** Each round, choose **Wait**, **Move** or **Distract**. You need **2 or more** "
            "right."
        ],
        "mini": "sneak",
        "goto": "sneak_done",
    },
    "sneak_done": {
        "art": "sneak",
        "pages": [],
        "goto": "city_arrive",
    },
    "city_arrive": {
        "art": "city_fight",
        "pages": [],
        "mini": "city_fight",
        "goto": "city_essentials",
    },
    "city_essentials": {
        "art": "materials",
        "pages": [],  # dynamic recap after the hex fight
        "mini": "pick_essentials",
        "goto": "train",
    },
    "train": {
        "art": "train",
        "pages": [],
        "goto": "ch1_end",
    },
    "ch1_end": {
        "art": "train",
        "pages": [
            "**✦ Chapter One Complete ✦**\n\n"
            "The carriage rattles on. Caden waits. The Forrest waits harder.\n\n"
            "*You can begin Chapter Two whenever you're ready. Private test: no weekly gate yet.*"
        ],
        "choices": [
            {"id": "ch2", "label": "Begin Chapter Two", "goto": "ch2_arrive", "set": {"chapter": 2}},
            {"id": "stop", "label": "Stop here for now", "goto": "paused"},
        ],
    },
    "paused": {
        "art": "title",
        "pages": [
            "Story paused. Use `/forrest resume` when you want to continue."
        ],
        "end": True,
    },
}

# ---------------------------------------------------------------------------
# Chapter 2
# ---------------------------------------------------------------------------

CH2_NODES: dict[str, dict] = {
    "ch2_arrive": {
        "art": "caden",
        "chapter": 2,
        "pages": [],  # dynamic (Gus present line)
        "goto": "ask_shops",
    },
    "ask_shops": {
        "art": "ask_shops",
        "pages": [
            "The baker shakes her head before you've finished the question. The cobbler remembers a girl last week, but "
            "it wasn't {missing}. A shopkeeper with kind, crinkled eyes says students go into the Forrest every year, "
            "and most come back louder than they left.\n\n"
            "*Most.*"
        ],
        "choices": [
            {
                "id": "push",
                "label": "Please, look again.",
                "check": {"skill": "Wisdom", "dc": 12},
                "goto": "ask_library",
            },
            {"id": "thanks", "label": "Thank you anyway.", "goto": "ask_library"},
        ],
    },
    "ask_library": {
        "art": "caden",
        "pages": [
            "The archivist is precise and useless, the way careful people often are. There's no register of visitors, "
            "and no rumour she'll put her name to. She offers you a map of the walking trails along with a look that "
            "clearly says *don't.*\n\n"
            "You take the map anyway."
        ],
        "choices": [
            {"id": "map", "label": "We'll be careful.", "goto": "ask_food"},
            {"id": "hard", "label": "Careful isn't finding her.", "goto": "ask_food"},
        ],
    },
    "ask_food": {
        "art": "ask_food",
        "pages": [
            "A tavern by the water feeds you something fried and asks no questions until the plates are empty. Then the "
            "landlady wipes her hands on her apron and says, softly, that if someone wanted to disappear before term, "
            "the Forrest would be glad to help.\n\n"
            "Still no lead. Still {missing}'s face in your hands."
        ],
        "empath": "Hope is getting tired. You don't let it sit down.",
        "goto": "alley",
    },
    "alley": {
        "art": "alley",
        "pages": [
            "A shout cracks the afternoon open: clattering metal, a yelp, boots slapping wet cobblestones. Down an alley, "
            "a boy is sprinting hard enough to mean he's carrying secrets. And on the drain grate right beside you is a "
            "black dog with one paw wedged at a bad angle, its eyes asking the oldest question in the world.\n\n"
            "You can't do both."
        ],
        "choices": [
            {
                "id": "chase",
                "label": "Chase the boy",
                "set": {"alley": "chase", "cave_tip": True, "has_nox": False},
                "goto": "alley_chase",
            },
            {
                "id": "dog",
                "label": "Help the dog",
                "set": {"alley": "dog", "cave_tip": False, "has_nox": True},
                "goto": "alley_dog",
            },
        ],
    },
    "alley_chase": {
        "art": "cave_tip",
        "pages": [
            "You run. Ella swears behind you. The boy is fast, then cornered, then talking far too quickly with his "
            "hands up.\n\n"
            '**Boy:** "I didn\'t take nothing, I swear. Listen. If you\'re going into the green, *don\'t sleep in the '
            'cave.* People who sleep in the cave don\'t wake up right. That\'s all I know. That\'s all."\n\n'
            "He's gone before you can ask who told him.\n\n"
            "The warning sits in your pocket like a stone. Heavy. Useful."
        ],
        "goto": "inn",
    },
    "alley_dog": {
        "art": "nox",
        "pages": [
            "You drop to your knees. The grate is stubborn, your knuckles get scraped raw, and the dog doesn't bite "
            "once. When the paw finally comes free, the dog gives one hard shake, then leans its whole weight against "
            "your shin as if the two of you have known each other for years.\n\n"
            'Ella laughs under her breath. "Hi, trouble."\n\n'
            "You don't name the dog yet. The name arrives anyway, quietly, in your head: **Nox.**\n\n"
            "Nox follows. The boy is gone, and whatever he knew went with him.\n\n"
            "There's warmth against your leg now. It's a different kind of lead: the living kind."
        ],
        "goto": "inn",
    },
    "inn": {
        "art": "inn",
        "pages": [
            "You take rooms for the night. The walls are thin, and the one window looks out at nothing useful. You split "
            "a loaf of bread you can't taste.\n\n"
            "Later, Gus knocks, the way someone knocks when they're apologising for existing. He wants to talk about Ella."
        ],
        "goto": "gus_ella_talk",
    },
    "gus_ella_talk": {
        "art": "inn",
        "pages": [],
        "goto": "morning",
    },
    "morning": {
        "art": "edge",
        "pages": [],
        "goto": "ch2_end",
    },
    "ch2_end": {
        "art": "edge",
        "pages": [
            "**✦ Chapter Two Complete ✦**\n\n"
            "Morning at the tree line. Mist between the pines. No letter. No rumour that matters. No sign of the face "
            "you came for.\n\n"
            "The Forrest of Caden waits anyway.\n\n"
            "*(Chapters Three to Eight aren't written yet. The private test ends here.)*"
        ],
        "end": True,
    },
}

NODES = {**CH1_NODES, **CH2_NODES}
