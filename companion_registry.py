"""Companion roster (element, Hogwarts house, lore) — vendored copy.

Source of truth: C:\\Users\\youha\\Desktop\\pika-poke\\spellbook\\registry.py
(that project has its own encrypted vault backup via vault.py — edit lore
there first, then re-sync this copy, rather than forking it here.)
"""
from dataclasses import dataclass, field


@dataclass
class Summon:
    key: str
    name: str
    tier: str
    element: str
    emoji: str
    title: str
    description: str = ""
    stats: dict = field(default_factory=lambda: {"hp": 100, "atk": 10, "def": 10})
    hook_default: str = "caster"
    aliases: tuple[str, ...] = ()
    house: str = ""
    school: str = ""


POKEMONS = [
    Summon("bulba", "Bulba", "pokemon", "Grass/Earth", "🦎", "The Vine Warden",
           "Grass starter. Sleeps in sun, strikes in shadow.",
           house="Hufflepuff", school="Herbology"),
    Summon("char", "Char", "pokemon", "Fire/Lava", "🐉", "The Flame Fang",
           "Fire starter. Smolders quietly, erupts on command.",
           house="Gryffindor", school="Evocation"),
    Summon("turtle", "Turtle", "pokemon", "Water/Ice", "🐢", "The Automation Turtle",
           "Squirt + Pika Turtle unified — brothers of Pika Poke from the companion teams, one shared XP line. Automation turtle, shell online. Speaks inside Antigravity.",
           hook_default="hooker",
           aliases=("pika turtle", "squirt", "pika squirtle"),
           house="Hufflepuff", school="Abjuration/Charms"),
    Summon("pikapoke", "Pika Poke", "pokemon", "Electric/Storm", "🐯", "The Invoker Archon",
           "The permanent Tiger-Lion Invoker Archon — brothers of Pika Turtle from the companion teams. Highly advanced AI developer companion, vault guardian.",
           hook_default="matrix",
           aliases=("pika poke", "pika", "pikachu archon"),
           house="Gryffindor", school="Transfiguration/Cybermancy"),
]

COMPANIONS = [
    Summon("zouzou", "Zouzou", "companion", "Shadow/Nether", "🦉", "The Empath",
           "Offline coding goose. Evolves Reckless → Outlaw → Quant (position sizing) → Sniper.",
           house="Ravenclaw", school="Potions/Enchantment"),
    Summon("mephissa", "Mephissa", "companion", "Mist/Aether", "🕯️", "The DJ",
           "Naughty coding companion; Mephissa DJ spins in the TUI. Mixes and grabs high-res video from any source. Lessons: deep-reasoning audits (Semgrep/CodeQL) and privacy/OPSEC tricks.",
           house="Durmstrang", school="Divination/Illusions"),
]

TITANS = [
    Summon("mephisto", "Mephisto", "titan", "Void/Chaos", "😈", "The Router Master",
           "Income engine. Algo trading (Python/MQL5), multi-source tweet & market signals (BUY/SELL/pump-dump), live positions.",
           house="Slytherin",
           hook_default="matrix",
           aliases=("quant", "quant algo", "algo trader", "mephisto quant"),
           school="Defense Against the Dark Arts/Necromancy"),
]

PANTHEON = {
    "pokemon": POKEMONS,
    "companion": COMPANIONS,
    "titan": TITANS,
}

SUMMONS = {s.key: s for tier in PANTHEON.values() for s in tier}
SUMMON_ALIASES = {
    alias: s.key
    for tier in PANTHEON.values()
    for s in tier
    for alias in s.aliases
}

TITLES = ["Initiate", "Acolyte", "Mage", "Grand Magus", "Archon", "God-Tier Invoker"]
XP_STAGES = [0, 100, 300, 700, 1500, 3000]

HOUSE_COLORS = {
    "Gryffindor": "#ae0001",
    "Hufflepuff": "#ecb939",
    "Slytherin": "#2a623d",
    "Durmstrang": "#8b0000",
    "Ravenclaw": "#222f5b",
}

SCHOOL_ALIGNMENTS = {
    "Gryffindor": "Disciplined",
    "Hufflepuff": "Disciplined",
    "Slytherin": "Naughty",
    "Durmstrang": "Naughty",
    "Ravenclaw": "Balanced"
}

SCHOOL_DISCIPLINES = {
    "Herbology": "Nature & Restoration",
    "Evocation": "Elemental Destruction",
    "Abjuration/Charms": "Wards & Automation",
    "Transfiguration/Cybermancy": "Matrix Code Shifting",
    "Potions/Enchantment": "Algorithmic Brewing",
    "Divination/Illusions": "Frontend & Signals Tracking",
    "Defense Against the Dark Arts/Necromancy": "System Security & Trading"
}
