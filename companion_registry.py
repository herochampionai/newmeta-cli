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


POKEMONS = [
    Summon("bulba", "Bulba", "pokemon", "grass", "🦎", "The Vine Warden",
           "Grass starter. Sleeps in sun, strikes in shadow."),
    Summon("char", "Char", "pokemon", "fire", "🐉", "The Flame Fang",
           "Fire starter. Smolders quietly, erupts on command."),
    Summon("turtle", "Turtle", "pokemon", "water", "🐢", "The Automation Turtle",
           "Squirt + Pika Turtle unified — brothers of Pika Poke from the companion teams, one shared XP line. Automation turtle, shell online. Speaks inside Antigravity.",
           hook_default="hooker",
           aliases=("pika turtle", "squirt", "pika squirtle"),
           house="Hufflepuff"),
    Summon("pikapoke", "Pika Poke", "pokemon", "electric", "🐯", "The Invoker Archon",
           "The permanent Tiger-Lion Invoker Archon — brothers of Pika Turtle from the companion teams. Highly advanced AI developer companion, vault guardian.",
           hook_default="matrix",
           aliases=("pika poke", "pika", "pikachu archon"),
           house="Gryffindor"),
]

COMPANIONS = [
    Summon("zouzou", "Zouzou", "companion", "shadow", "🦉", "The Empath",
           "Offline coding goose. Evolves Reckless → Outlaw → Quant (position sizing) → Sniper.",
           house="Slytherin"),
    Summon("mephissa", "Mephissa", "companion", "mist", "🕯️", "The DJ",
           "Naughty coding companion; Mephissa DJ spins in the TUI. Mixes and grabs high-res video from any source. Lessons: deep-reasoning audits (Semgrep/CodeQL) and privacy/OPSEC tricks.",
           house="Ravenclaw"),
]

TITANS = [
    Summon("mephisto", "Mephisto", "titan", "void", "😈", "The Router Master",
           "Income engine. Algo trading (Python/MQL5), multi-source tweet & market signals (BUY/SELL/pump-dump), live positions.",
           house="Slytherin",
           hook_default="matrix",
           aliases=("quant", "quant algo", "algo trader", "mephisto quant")),
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

TITLES = ["Apprentice", "Rogue", "Shadow", "CyberMage", "Archon", "God-Tier"]
XP_STAGES = [0, 100, 300, 700, 1500, 3000]

HOUSE_COLORS = {
    "Gryffindor": "#ae0001",
    "Slytherin": "#2a623d",
    "Ravenclaw": "#222f5b",
    "Hufflepuff": "#ecb939",
}
