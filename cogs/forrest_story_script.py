"""
The Forrest of Caden — Chapters 1 & 2 script (test build).

Nodes are pages of narration + optional choices / mini-games.
Placeholders: {you} {missing} {mhe} {mhim} {mhis} (missing person's pronouns).
"""

from __future__ import annotations

# Art filenames under story_art_assets/
# Missing keys fall back to no image until a plate is locked.
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
    "yuna": "yuna.jpg",
    "ella": "ella.jpg",
    "gus": "gus.jpg",
}

SCHOOL_MATERIALS = [
    ("rope", "Rope & chalk", "For climbs and marks in the woods."),
    ("heal", "Healing kit", "Softens a bad hit later."),
    ("rations", "Extra rations", "Food — and something to share."),
    ("map", "Old map scrap of Caden", "One cleaner path through town or trees."),
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
            "*an 8 part solo interactive immersive story about 2 friends who grew apart*\n\n"
            "You are **Mack**. Yuna didn’t show for fifth year — and you’re going to find her.\n\n"
            "Private test — Chapters 1 & 2.\n"
            "No house points. Your choices are saved to your story."
        ],
        "choices": [
            {"id": "begin", "label": "Begin", "set": {"protagonist": "mack"}, "goto": "dream"},
        ],
    },
    "dream": {
        "art": "dream",
        "pages": [
            "You were ten again, which is a mean trick for a dream to play, because ten still believed summers were endless.\n\n"
            "You and {missing} had the whole afternoon like it owed you something. Same hill behind their old place — "
            "the one with the crooked fence and the tree that always dropped sticky sap on your sleeves. Race you to it. "
            "Last one there has to steal biscuits from the kitchen. You remember the exact sound of {mhis} laugh when "
            "{mhe} cheated and shoved your shoulder, not hard enough to hurt, hard enough to mean *I win either way*.\n\n"
            "Grass stained your knees. Somebody’s mum called from far away and neither of you answered. There was a jar "
            "of fireflies you’d sworn you’d let go by dusk and never did. {missing} held it up between you, green-gold "
            "light on both your faces, and said — soft, almost embarrassed by how much {mhe} meant it — that next year "
            "you’d still be like this. Same stupid races. Same secrets. Same *us*.\n\n"
            "You believed {mhim}. Of course you did. You always did.",
            "Then the light in the jar went out without either of you opening the lid.\n\n"
            "The hill was suddenly the edge of the Forrest of Caden — not the friendly camping kind, not the "
            "“see you before term” kind. The trees stood too close. The air tasted like wet iron and old leaves. "
            "{missing} was ahead of you on the path, looking back, saying your name the way {mhe} used to when you’d "
            "fallen behind on purpose just to make {mhim} wait.\n\n"
            "You tried to catch up. Your legs wouldn’t.\n"
            "Something moved in the dark between the trunks that was the wrong shape for wind.\n\n"
            "{mThey} reached a hand toward you—",
        ],
        "art_pages": {1: "scare"},
        "goto": "dream_wake",
    },
    "dream_wake": {
        "art": "scare",
        "pages": [
            "—and you woke up with your own hand in the empty air above your bed, heart trying to kick its way out of "
            "your ribs, dorm ceiling swimming like you’d been underwater.\n\n"
            "For a long minute you just lay there and hated how quiet the room was without {mhim} in it.\n\n"
            "Fifth year starts today.\n"
            "You told yourself, last spring, that this would be the year it got easier. That growing apart was normal. "
            "That missing someone who still wrote sometimes was a childish kind of ache and you’d grow out of it like "
            "you grew out of the sap-stained sleeves.\n\n"
            "Lying there, still half in the dream, you already knew you were a liar."
        ],
        "empath": "The missing isn’t sharp anymore. It’s weather. You’ve been walking around in it so long you forgot "
                  "other people get sunshine. The dream didn’t create the fear. It just finally gave it a face.",
        "goto": "gus_morning",
    },
    "gus_morning": {
        "art": "gus_talk",
        "pages": [
            "Gus finds you before breakfast the way he always does — like he’s been orbiting your door waiting for a "
            "polite excuse.\n\n"
            "**Gus:** “You look wrecked,” he says, then immediately regrets how honest that was. He pushes his glasses up. "
            "“Not— not bad. Just. Did you sleep at all?”\n\n"
            "You make a noise that could mean anything. He fills the silence because Gus is allergic to silence when he’s nervous.\n\n"
            "He talks around it twice. Ella’s name shows up on the third try, casual as if he didn’t rehearse it in the "
            "stairwell. How she laughed at his terrible joke yesterday. How he can’t tell if that means something or if "
            "Ella laughs at everyone because she’s built that way. How he’s seventeen and somehow still a coward about this one thing.\n\n"
            "**Gus:** “I’m going to sound insane,” he mutters. “But do I… say something? Or do I keep being the funny "
            "friend in the corner until I die of it?”"
        ],
        "choices": [
            {"id": "tell", "label": "Tell her. Waiting doesn’t make it kinder.", "set": {"gus_advice": "tell"}, "goto": "gus_advice_tell"},
            {"id": "dont", "label": "Don’t. Some things are safer left alone.", "set": {"gus_advice": "dont"}, "goto": "gus_advice_dont"},
            {"id": "careful", "label": "I don’t know. Just don’t half-do it.", "set": {"gus_advice": "careful"}, "goto": "gus_advice_careful"},
        ],
    },
    "gus_advice_tell": {
        "art": "gus_talk",
        "pages": [
            "Gus exhales like he’s been holding that breath since second year.\n\n"
            "**Gus:** “Yeah. Okay. Okay. Not today. But… yeah.”"
        ],
        "empath": "His crush is loud in the air between you — warm, clumsy, hopeful. Underneath it, quieter: he’s "
                  "terrified of becoming wallpaper in her life. You feel both. You don’t tell him that the person your "
                  "own chest still turns toward isn’t in the castle.",
        "goto": "missing",
    },
    "gus_advice_dont": {
        "art": "gus_talk",
        "pages": [
            "Gus nods too fast, relieved and disappointed in the same blink.\n\n"
            "**Gus:** “Right. Smart. I’m smart when you say I’m smart.”"
        ],
        "empath": "His crush is loud in the air between you — warm, clumsy, hopeful. Underneath it, quieter: he’s "
                  "terrified of becoming wallpaper in her life. You feel both. You don’t tell him that the person your "
                  "own chest still turns toward isn’t in the castle.",
        "goto": "missing",
    },
    "gus_advice_careful": {
        "art": "gus_talk",
        "pages": [
            "**Gus:** snorts. “Helpful. Truly. Spiritual guidance from the emotionally constipated.” Softens. "
            "“Thanks for not laughing.”"
        ],
        "empath": "His crush is loud in the air between you — warm, clumsy, hopeful. Underneath it, quieter: he’s "
                  "terrified of becoming wallpaper in her life. You feel both. You don’t tell him that the person your "
                  "own chest still turns toward isn’t in the castle.",
        "goto": "missing",
    },
    "missing": {
        "art": "missing_seat",
        "pages": [
            "Morning fills the school the way it always does first day back — trunks scraping, owls, somebody already "
            "late for a class that hasn’t started, the green of Thornmere scarves flashing like it’s a competition.\n\n"
            "You look for {missing} without deciding to. Old habit. Doorway. Usual seat. The stretch of wall where "
            "{mhe} used to wait with {mhis} bag half-open and that look that said *you’re late on purpose again*.\n\n"
            "Nothing.\n\n"
            "You check twice, because once feels like panic and twice feels like proof.\n\n"
            "A prefect shrugs — no check-in under that name yet. Someone in your year says they thought {missing} was "
            "coming later in the week. Someone else didn’t know you two still talked.\n\n"
            "You did. Not enough. Letters with too much space between the lines. Jokes that used to be easy and now "
            "arrive careful. You told yourself distance was geography. Standing in a loud hall with an empty seat in it, "
            "geography feels like a coward’s word for *I let us fade*.\n\n"
            "The dream sits behind your eyes again — jar light dying, hand reaching, trees wrong.\n\n"
            "This year was supposed to be different.\n"
            "Maybe it still will be. Just not the way you meant."
        ],
        "empath": "Absence has a temperature. You’re standing in cold air in a warm room.",
        "choices": [
            {"id": "spot", "label": "Check their usual spot again", "goto": "sebastian"},
            {"id": "ask", "label": "Ask around properly", "goto": "sebastian"},
            {"id": "go", "label": "Go straight to Sebastian", "goto": "sebastian"},
        ],
    },
    "sebastian": {
        "art": "sebastian",
        "pages": [
            "Sebastian listens the whole way through without interrupting, which is how you know it’s bad. He cracks "
            "one joke early — something about truancy and tradition — and then lets it die when he sees your face.\n\n"
            "You tell him about the camping spot before term. About {missing} always showing up smelling like pine and "
            "woodsmoke, late on purpose, grinning like the Forrest was a secret only the two of you were allowed to keep. "
            "About the letters thinning. About the dream you don’t fully admit was a dream."
        ],
        "choices": [
            {"id": "scared", "label": "I’m scared something’s wrong.", "goto": "sebastian_advice"},
            {"id": "firm", "label": "I’m going. Please don’t stop me.", "goto": "sebastian_advice"},
            {"id": "guilt", "label": "If I stay and I’m wrong… I won’t forgive myself.", "goto": "sebastian_advice"},
        ],
    },
    "sebastian_advice": {
        "art": "sebastian",
        "pages": [
            "He doesn’t give you permission like a teacher. He gives it like someone who has carried a quieter version "
            "of your sentence for longer than you’ve been alive.\n\n"
            "Go.\n"
            "Don’t wait until the guilt has a name you can’t put down. He knows what it is to be able to help a friend "
            "and choose the smaller feeling instead. He won’t watch you practice that.\n\n"
            "**Sebastian:** “Come back,” he adds, almost lightly. “I’m terrible at eulogies. And my puns get mean when I’m sad.”"
        ],
        "empath": "His guilt doesn’t reach for you. It recognizes you. That’s worse, somehow — and it’s also the thing "
                  "that steadies your hands.",
        "goto": "party",
    },
    "party": {
        "art": "party",
        "pages": [
            "You find Gus and Ella together, which feels like the universe having a sense of humor. Ella’s got ink on "
            "her thumb. Gus is talking with his hands. For half a second you almost don’t say it — almost let first-day "
            "noise swallow you — and then you hear yourself telling the truth.\n\n"
            "You’re leaving. Tonight if you can. Caden, then the Forrest. You’re going to find {missing}."
        ],
        "choices": [
            {"id": "alone", "label": "I’m going alone if I have to.", "goto": "party_yes"},
            {"id": "need", "label": "I need you both.", "goto": "party_yes"},
            {"id": "feel", "label": "Something’s wrong. I can feel it.", "goto": "party_yes"},
        ],
    },
    "party_yes": {
        "art": "party",
        "pages": [
            "Ella doesn’t hesitate long enough for you to brace. Of course she’s coming. There’s a look she gives you — "
            "soft, stubborn, a little too open — that says this isn’t only about adventure. You feel it land and carefully, "
            "carefully, don’t pick it up.\n\n"
            "Gus makes a joke about snacks and mortal peril in the same breath because that’s how he stays in the room "
            "when things get real. Under the joke: he’s already packing in his head.\n\n"
            "Nobody talks you out of it.\n"
            "That’s how you know they’re your people."
        ],
        "empath": "Ella’s care has your name written through it. Gus’s has hers. Yours still has someone who isn’t "
                  "standing in this hallway. You hold all three truths and walk anyway.",
        "goto": "materials",
    },
    "materials": {
        "art": "materials",
        "pages": [
            "Packing feels like pretending this is a weekend trip. It isn’t. Your hands know that even when your mouth "
            "makes light of it.\n\n"
            "You take what the three of you can carry without looking like you’re fleeing the country. Ella folds things "
            "neater than you. Gus puts a ridiculous extra pair of socks in “for morale.”\n\n"
            "**Choose 2** things to bring from the school stash."
        ],
        "mini": "pick_materials",
        "goto": "sneak_intro",
    },
    "sneak_intro": {
        "art": "sneak",
        "pages": [
            "Curfew turns the castle into a held breath. Every floorboard has opinions. Somewhere a portrait snores. "
            "You move like people who have done this before and also like people who definitely have not done this with stakes.\n\n"
            "Mordy doesn’t need to shout to fill a hallway. He just *is* — old as the stones, sharp as a disappointment, "
            "somehow always between you and the door you want.\n\n"
            "**Sneak past Mordy.** Three moments. Wait, move, or distract — don’t get caught."
        ],
        "mini": "sneak",
        "goto": "sneak_done",
    },
    "sneak_done": {
        "art": "sneak",
        "pages": [],  # filled dynamically
        "goto": "city_arrive",
    },
    "city_arrive": {
        "art": "city_fight",
        "pages": [
            "The city doesn’t care that you’re fifth-years with a noble reason. It cares that you’re young, loaded with "
            "bags, and looking the wrong way at the wrong corner.\n\n"
            "A hooded figure steps out of the dark like they practiced it — phone up, voice flat, no face to read.\n\n"
            "**Robber:** “Bags. Quiet. Nobody has to get clever.”\n\n"
            "You put yourself in front of Ella. She’s already clutching her bag like it’s the only solid thing left. "
            "Gus — if he’s still with you — is somewhere just out of the light, making a sound that is definitely not a joke."
        ],
        "mini": "city_fight",
        "goto": "city_essentials",
    },
    "city_essentials": {
        "art": "materials",
        "pages": [
            "After, your hands shake in a way you pretend is cold. A late stall is still open. You buy what you can carry "
            "before the last train toward Caden’s edge.\n\n"
            "**Choose essentials** for the road. More options if Gus made it out with you."
        ],
        "mini": "pick_essentials",
        "goto": "train",
    },
    "train": {
        "art": "train",
        "pages": [],  # dynamic based on gus_with_party
        "goto": "ch1_end",
    },
    "ch1_end": {
        "art": "train",
        "pages": [
            "**Chapter 1 complete.**\n\n"
            "The cart rattles. Caden waits. The Forrest waits harder.\n\n"
            "You can start Chapter 2 when you’re ready — private test: no weekly gate yet."
        ],
        "choices": [
            {"id": "ch2", "label": "Begin Chapter 2", "goto": "ch2_arrive", "set": {"chapter": 2}},
            {"id": "stop", "label": "Stop here for now", "goto": "paused"},
        ],
    },
    "paused": {
        "art": "title",
        "pages": [
            "Story paused. Use `/forrest resume` when you want to continue (private test)."
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
        "pages": [
            "**Chapter 2 — Caden**\n\n"
            "The train sighs into the closest city before the Forrest. Caden smells like lake water, bread, and woodsmoke. "
            "Somewhere past the rooftops the trees begin, dark even at noon.\n\n"
            "You unfold the picture of {missing}. Gus (if he’s here) clears his throat like that will make this less awful. "
            "Ella takes the photo from you gently, like it’s breakable.\n\n"
            "You start asking."
        ],
        "goto": "ask_shops",
    },
    "ask_shops": {
        "art": "caden",
        "pages": [
            "The baker shakes her head before you finish. The cobbler remembers a girl last week who isn’t {missing}. "
            "A shopkeeper with kind eyes says kids go into the Forrest every year and most come back louder than they left.\n\n"
            "Most."
        ],
        "choices": [
            {"id": "push", "label": "Please — look again.", "goto": "ask_library"},
            {"id": "thanks", "label": "Thank you anyway.", "goto": "ask_library"},
        ],
    },
    "ask_library": {
        "art": "caden",
        "pages": [
            "The librarian is precise and useless in the way careful people are. No register. No rumor she will put her "
            "name on. She offers you a map of walking trails and a look that says *don’t*.\n\n"
            "You take the map anyway."
        ],
        "choices": [
            {"id": "map", "label": "We’ll be careful.", "goto": "ask_food"},
            {"id": "hard", "label": "Careful isn’t finding them.", "goto": "ask_food"},
        ],
    },
    "ask_food": {
        "art": "caden",
        "pages": [
            "A restaurant by the water feeds you something fried and asks no questions until the plates are empty. "
            "Then the owner says, softly, that if someone wanted to disappear before term, the Forrest would help.\n\n"
            "Still no lead. Still {missing}’s face in your hands."
        ],
        "empath": "Hope is getting tired. You don’t let it sit down.",
        "goto": "alley",
    },
    "alley": {
        "art": "alley",
        "pages": [
            "A shout cracks the afternoon — metal, a yelp, sneakers on wet stone. An alley. A kid sprinting hard enough "
            "to mean secrets. And on the grate beside you: a dog, paw wedged wrong, eyes asking the oldest question in the world.\n\n"
            "You can’t do both."
        ],
        "choices": [
            {"id": "chase", "label": "Chase the kid", "set": {"alley": "chase", "cave_tip": True, "has_nox": False}, "goto": "alley_chase"},
            {"id": "dog", "label": "Help the dog", "set": {"alley": "dog", "cave_tip": False, "has_nox": True}, "goto": "alley_dog"},
        ],
    },
    "alley_chase": {
        "art": "cave_tip",
        "pages": [
            "You run. Ella swears behind you. The kid is fast and then cornered and then talking too quick, hands up.\n\n"
            "**Kid:** “I didn’t take nothing — listen — if you’re going in the green, don’t sleep in the cave. People "
            "who sleep in the cave don’t wake up right. That’s all I know. That’s all.”\n\n"
            "He’s gone before you can ask who told him."
        ],
        "empath": "The tip sits in your pocket like a stone. Heavy. Useful.",
        "goto": "inn",
    },
    "alley_dog": {
        "art": "nox",
        "pages": [
            "You drop to your knees. The grate is stubborn; your hands get scraped; the dog doesn’t bite. When the paw "
            "comes free, the dog shakes once, hard, and then leans into your shin like you’ve known each other for years.\n\n"
            "Ella laughs under her breath. “Hi, trouble.”\n\n"
            "You don’t name {mhim} yet. The name arrives anyway, quiet in your head: **Nox**.\n\n"
            "Nox follows. The kid is gone. Whatever he knew goes with him."
        ],
        "empath": "Warmth against your leg. A different kind of lead — the living kind.",
        "goto": "inn",
    },
    "inn": {
        "art": "inn",
        "pages": [
            "Rooms for the night. Thin walls. One window that looks at nothing useful. You split bread you don’t taste.\n\n"
            "Later, Gus knocks like he’s apologizing for existing. He wants to talk about Ella."
        ],
        "goto": "gus_ella_talk",
    },
    "gus_ella_talk": {
        "art": "inn",
        "pages": [],  # dynamic
        "goto": "morning",
    },
    "morning": {
        "art": "edge",
        "pages": [],  # dynamic
        "goto": "ch2_end",
    },
    "ch2_end": {
        "art": "edge",
        "pages": [
            "**Chapter 2 complete.**\n\n"
            "Morning at the tree line. Mist between the pines. No letter. No rumor that matters. No face you came for.\n\n"
            "The Forrest of Caden waits anyway.\n\n"
            "*(Chapters 3–8 not built yet — private test ends here.)*"
        ],
        "end": True,
    },
}

NODES = {**CH1_NODES, **CH2_NODES}
