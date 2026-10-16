"""Card catalog for Velmora Curse & Crown.

The spell list is the Oct 16 2026 spec. House keys match the House Cup
ledger (``cogs.store.HOUSES``): Asterwilde is still ``moonveil`` internally,
Oakmont is still ``vashara``.
"""

from __future__ import annotations

from dataclasses import dataclass

# Printed schools. House cards are not a school for Starfall.
SCHOOLS = ("fire", "frost", "nature", "shadow", "star")

SPECIALISTS = frozenset({
    "kindle",
    "wildgrowth",
    "wishing-coin",
    "fate-shuffle",
    "hush",
    "pickpocket-charm",
    "curse-of-clumsy-fingers",
    "shade-step",
    "hex-swap",
})

# Ledger house key -> that house's only house card.
HOUSE_CARD = {
    "moonveil": "squirrels-stash",     # Asterwilde
    "vashara": "bear-hug",             # Oakmont
    "caldrin": "lemurs-sleight",
    "veyren": "antelope-dash",
    "thornmere": "wolf-howl",
}

HOUSE_LABEL = {
    "moonveil": {"name": "Asterwilde", "emoji": "\U0001F43F\uFE0F"},
    "vashara": {"name": "Oakmont", "emoji": "\U0001F43B"},
    "caldrin": {"name": "Caldrin", "emoji": "\U0001F435"},
    "veyren": {"name": "Veyren", "emoji": "\U0001F98C"},
    "thornmere": {"name": "Thornmere", "emoji": "\U0001F43A"},
}

SCHOOL_LABEL = {
    "fire": "Fire",
    "frost": "Frost",
    "nature": "Nature",
    "shadow": "Shadow",
    "star": "Star",
    "house": "House",
}


@dataclass(frozen=True)
class Card:
    id: str
    name: str
    school: str          # fire/frost/nature/shadow/star/house
    cost: int
    slot: str            # "spell" (attack/defense) or "specialist"
    tag: str             # Attack, Defense, Specialist, House
    text: str
    house: str | None = None

    @property
    def art(self) -> str:
        return f"{self.id}.jpg"

    @property
    def school_name(self) -> str:
        if self.house:
            return HOUSE_LABEL[self.house]["name"]
        return SCHOOL_LABEL[self.school]


def _c(cid, name, school, cost, slot, tag, text, house=None) -> Card:
    return Card(cid, name, school, cost, slot, tag, text, house)


# Slot is the rules bucket. Tag is the label on the card face.
CARDS: dict[str, Card] = {
    c.id: c
    for c in (
        _c("ember-lance", "Ember Lance", "fire", 1, "spell", "Attack", "Deal 2 damage."),
        _c("cinder-spray", "Cinder Spray", "fire", 2, "spell", "Attack", "Deal 1 damage twice."),
        _c("kindle", "Kindle", "fire", 1, "specialist", "Specialist", "Your next Fire card deals +2 damage."),
        _c("blaze-wall", "Blaze Wall", "fire", 3, "spell", "Attack",
           "Deal 3 damage. If they hit you on their next turn, they take 1."),
        _c("phoenix-spark", "Phoenix Spark", "fire", 2, "spell", "Attack",
           "Deal 2 damage. If this finishes them, heal 3 Ward."),
        _c("inferno", "Inferno", "fire", 6, "spell", "Attack",
           "Deal 6 damage. Both players discard a random card."),
        _c("frostveil", "Frostveil", "frost", 1, "spell", "Defense", "Gain 2 Block."),
        _c("chill-touch", "Chill Touch", "frost", 2, "spell", "Attack",
           "Deal 1 damage. Their next card costs +1 mana."),
        _c("ice-mirror", "Ice Mirror", "frost", 3, "spell", "Defense",
           "Bounce their next attack back at them."),
        _c("snow-shroud", "Snow Shroud", "frost", 2, "spell", "Defense", "Gain 3 Block and draw 1 card."),
        _c("glacier-ward", "Glacier Ward", "frost", 4, "spell", "Defense", "Gain 5 Ward."),
        _c("permafrost", "Permafrost", "frost", 5, "spell", "Defense", "They skip their next turn."),
        _c("bloom", "Bloom", "nature", 1, "spell", "Defense", "Heal 2 Ward."),
        _c("thornlash", "Thornlash", "nature", 2, "spell", "Attack", "Deal 2 damage, ignoring Block."),
        _c("sapling-shield", "Sapling Shield", "nature", 2, "spell", "Defense",
           "Gain 2 Block. Heal 1 Ward at the start of your next turn."),
        _c("rootbind", "Rootbind", "nature", 3, "spell", "Defense", "They can't play Fire on their next turn."),
        _c("moonwell", "Moonwell", "nature", 3, "spell", "Defense", "Heal 4 Ward."),
        _c("wildgrowth", "Wildgrowth", "nature", 5, "specialist", "Specialist",
           "+1 max mana for the rest of the game."),
        _c("hush", "Hush", "shadow", 1, "specialist", "Specialist", "Cancel their next 1-cost card."),
        _c("pickpocket-charm", "Pickpocket Charm", "shadow", 2, "specialist", "Specialist",
           "Steal a random card from their hand."),
        _c("curse-of-clumsy-fingers", "Curse of Clumsy Fingers", "shadow", 2, "specialist", "Specialist",
           "They discard a random card."),
        _c("shade-step", "Shade Step", "shadow", 2, "specialist", "Specialist",
           "Dodge the next attack completely."),
        _c("hex-swap", "Hex Swap", "shadow", 4, "specialist", "Specialist",
           "Swap Ward totals. The difference moved is capped at 5."),
        _c("eclipse", "Eclipse", "shadow", 6, "spell", "Attack",
           "Wipe all their Block and cancel their next card."),
        _c("wishing-coin", "Wishing Coin", "star", 0, "specialist", "Specialist",
           "Flip a coin. Heads: draw 2 cards."),
        _c("lantern-seal", "Lantern Seal", "star", 1, "spell", "Defense",
           "Cancel the last Shadow card played, if its effect still lingers."),
        _c("fate-shuffle", "Fate Shuffle", "star", 2, "specialist", "Specialist",
           "Both players shuffle their hand into their deck and draw 4."),
        _c("comet-call", "Comet Call", "star", 3, "spell", "Attack",
           "Deal 1 damage for each card in your hand, max 4."),
        _c("starfall", "Starfall", "star", 4, "spell", "Attack",
           "Deal 1 damage for each school you've played."),
        _c("supernova", "Supernova", "star", 6, "spell", "Attack", "Deal 8 damage. You take 3."),
        _c("squirrels-stash", "Squirrel's Stash", "house", 2, "spell", "House",
           "Draw 2 cards and keep the one you want. Discard the other.",
           house="moonveil"),
        _c("bear-hug", "Bear Hug", "house", 3, "spell", "House",
           "Gain 4 Block and deal 1 damage.", house="vashara"),
        _c("lemurs-sleight", "Lemur's Sleight", "house", 2, "spell", "House",
           "Play 1 extra attack or defense card this turn.", house="caldrin"),
        _c("antelope-dash", "Antelope Dash", "house", 1, "spell", "House",
           "Take an extra turn. Once per game.", house="veyren"),
        _c("wolf-howl", "Wolf Howl", "house", 4, "spell", "House",
           "Deal 3 damage, plus 1 for each Fire card you've played.", house="thornmere"),
    )
}

SPELL_IDS = tuple(cid for cid, card in CARDS.items() if card.school != "house")
HOUSE_IDS = tuple(HOUSE_CARD.values())


def card(cid: str) -> Card:
    return CARDS[cid]
