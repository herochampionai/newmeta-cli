import os
import sys
import json
import random
import subprocess
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

console = Console()

QUANTS_DIR = Path(r"D:\DAI_DEV\quants")
KNOWLEDGE_DIR = Path.home() / ".pika_poke" / "knowledge"

# ---------------------------------------------------------------------------
# BOT ARSENAL — mirrored from ~/.pika_poke/knowledge/bots/*.md
# Each entry: slug, name, what, launch (list of cmd choices), keyfiles, runnable
# ---------------------------------------------------------------------------
BOTS = {
    "1": {"slug": "freqtrade", "name": "Freqtrade (framework)",
          "what": "Open-source Python crypto trading bot. Backtest, hyperopt, dry-run, live, Telegram/webUI.",
          "launch": ["docker compose up -d", "freqtrade trade --config config.json"],
          "keyfiles": ["docker-compose.yml", "config.json", "user_data/strategies/"], "runnable": True},
    "2": {"slug": "freqtrade-main", "name": "Freqtrade (2nd copy)",
          "what": "Second/backup freqtrade install. Keep user_data/DB/ports distinct from primary.",
          "launch": ["docker compose up -d"], "keyfiles": ["docker-compose.yml", "config.json"], "runnable": True},
    "3": {"slug": "freqtrade-strategies", "name": "Freqtrade strategies",
          "what": "Community buy/sell strategies for freqtrade (2022.4+). Not a bot — a strategy library.",
          "launch": [], "keyfiles": ["*.py strategies"], "runnable": False},
    "4": {"slug": "freqtrade-vps", "name": "Freqtrade VPS helper",
          "what": "VPS deployment helper scripts/config for freqtrade. No top-level README — inspect scripts.",
          "launch": [], "keyfiles": ["*.sh", "*.yml", "configs"], "runnable": False},
    "5": {"slug": "freqtrade-vps-deploy", "name": "Freqtrade VPS deploy + mgmt",
          "what": "One-click VPS deploy + management bot bundle.",
          "launch": ["docker compose up -d", "start_unified.bat"], "keyfiles": ["docker-compose.yml", "start_unified.bat", "management_bot.py"], "runnable": True},
    "6": {"slug": "hummingbot", "name": "Hummingbot",
          "what": "Open-source market-making / arbitrage bot. Interactive CLI inside the container.",
          "launch": ["docker compose up -d", "docker attach hummingbot"], "keyfiles": ["docker-compose.yml", "hummingbot_files/conf"], "runnable": True},
    "7": {"slug": "mt5-mcp", "name": "MT5 MCP server",
          "what": "MCP server for MetaTrader 5 — Companion(s) reads MT5 data and trades through the terminal.",
          "launch": ["uvx --from mcp-metatrader5-server mt5mcp"], "keyfiles": ["src/mcp_mt5/main.py"], "runnable": True},
    "8": {"slug": "Superalgos", "name": "Superalgos",
          "what": "Visual crypto trading platform with node-based algo network.",
          "launch": ["node platform", "node platform minMemo"], "keyfiles": ["Platform/", "Docker/docker-compose.yml"], "runnable": True},
    "9": {"slug": "crypto-quant-signal-mcp", "name": "Crypto quant signal MCP",
          "what": "Quant signal generator as MCP server (AlgoVault), Postgres-backed.",
          "launch": ["docker compose up -d"], "keyfiles": ["docker-compose.yml"], "runnable": True},
    "10": {"slug": "M2Quant", "name": "M2Quant",
           "what": "Multi-strategy Python quant framework (MIT). Run strategy entrypoints with Python.",
           "launch": [], "keyfiles": ["README.md", "strategy modules"], "runnable": True},
    "11": {"slug": "llm-quant", "name": "LLM Quant",
           "what": "LLM-driven systematic trading research, 4 parallel alpha tracks, multi-asset.",
           "launch": [], "keyfiles": ["track dirs", "README.md"], "runnable": True},
    "12": {"slug": "Claude-Quant", "name": "Claude Quant",
           "what": "Claude-powered quant toolkit; Python entrypoints in src/.",
           "launch": [], "keyfiles": ["src/", "README.md"], "runnable": True},
    "13": {"slug": "cbt-framework", "name": "CBT Framework",
           "what": "AI backtesting framework — idea to live bot in one conversation.",
           "launch": [], "keyfiles": ["README.md", "backtest modules"], "runnable": True},
    "14": {"slug": "trading-skills", "name": "Trading Signals (x70.ai)",
           "what": "Free trading-signal feeds for AI Companion(s). Not a bot — read-only signal source.",
           "launch": [], "keyfiles": ["README.md endpoints"], "runnable": False},
    "15": {"slug": "claude-trading-skills", "name": "Claude Trading Skills",
           "what": "67 Companion(s) skills for trading (info/data playbooks). Not a bot — a skills library.",
           "launch": [], "keyfiles": ["skills/*/SKILL.md"], "runnable": False},
}

# ---------------------------------------------------------------------------
# Drill quiz bank — derived from the knowledge cards
# ---------------------------------------------------------------------------
QUIZ = [
    {"q": "Which bot is an open-source crypto market-making/arbitrage bot whose CLI lives inside its container?", "a": "hummingbot", "hint": "docker attach"},
    {"q": "Which framework repo is the canonical freqtrade (vs a copy)?", "a": "freqtrade", "hint": "the one with freqtrade/ source + frequi"},
    {"q": "Which repo is NOT a bot but a library of buy/sell strategy .py files?", "a": "freqtrade-strategies", "hint": "copy into user_data/strategies"},
    {"q": "What command attaches to the hummingbot container CLI after 'docker compose up -d'?", "a": "docker attach hummingbot", "hint": "attach"},
    {"q": "Which repo is a VPS deployment bundle with start_unified.bat and management_bot.py?", "a": "freqtrade-vps-deploy", "hint": "unified"},
    {"q": "Which repo runs with 'node platform' and has a minMemo mode for ≤8 GB RAM?", "a": "Superalgos", "hint": "visual node-based platform"},
    {"q": "Which repo is an MCP server launched with 'uvx --from mcp-metatrader5-server mt5mcp'?", "a": "mt5-mcp", "hint": "MetaTrader 5"},
    {"q": "Which repo provides free trading-signal feeds for AI Companion(s) at signals.x70.ai?", "a": "trading-skills", "hint": "x70"},
    {"q": "Which repo contains 67 Companion(s) skills for trading (playbooks/instructions)?", "a": "claude-trading-skills", "hint": "67"},
    {"q": "Which repo is an AI backtesting framework billed 'from trading idea to live bot in one conversation'?", "a": "cbt-framework", "hint": "conversation"},
    {"q": "Which repo is an LLM systematic trading research program with four parallel alpha tracks?", "a": "llm-quant", "hint": "four tracks"},
    {"q": "Which repo is a quant signal generator exposed as an MCP server and backed by Postgres 16 (docker compose)?", "a": "crypto-quant-signal-mcp", "hint": "AlgoVault"},
    {"q": "Which repo is a multi-strategy Python quant framework at v1.2.0 (MIT)?", "a": "M2Quant", "hint": "version 1.2.0"},
    {"q": "What command starts the MT5 MCP server with no installation required?", "a": "uvx --from mcp-metatrader5-server mt5mcp", "hint": "uvx"},
    {"q": "True/False: freqtrade-strategies has a docker-compose.yml entrypoint.", "a": "false", "hint": "it's a strategy library"},
    {"q": "Which repo is Claude-branded with Python entrypoints under src/ (CLAUDE QUANT)?", "a": "Claude-Quant", "hint": "branded logo"},
    {"q": "Which bot should you attach to after 'docker compose up -d' to reach its interactive shell?", "a": "hummingbot", "hint": "attach"},
    {"q": "For freqtrade, what is the dry-run-safe recommended config field?", "a": "dry_run: true", "hint": "dry_run"},
]


def pika_learn(note: str) -> None:
    """Mirror mephisto's pika_learn: append timestamped note to today's session file."""
    try:
        day = datetime.now().strftime("%Y-%m-%d")
        f = KNOWLEDGE_DIR / "sessions" / f"{day}.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H:%M")
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(f"- [{stamp}] {note}\n")
    except Exception:
        pass


def bot_dir(slug: str) -> Path:
    return QUANTS_DIR / slug


def render_menu():
    table = Table(show_header=True, header_style="bold yellow", expand=True)
    table.add_column("Key", style="bold white", width=4)
    table.add_column("Bot", style="bold cyan", width=30)
    table.add_column("What it is", style="white", width=60)
    table.add_column("Launch", style="bold green", width=38)

    for k, info in BOTS.items():
        launch = "; ".join(info["launch"]) if info["launch"] else "[dim]— read card / not a launcher —[/dim]"
        table.add_row(f"[{k}]", info["name"], info["what"], launch)

    header = Text()
    header.append("🔮 MEPHISTO BOT-LAUNCHER SPELL\n", style="bold red")
    header.append(f"🕒 {datetime.now().strftime('%H:%M:%S')} | Arsenal: {len(BOTS)} bots in {QUANTS_DIR}\n", style="bold yellow")
    header.append("⚙️  /launch <key>  ·  /quiz  ·  /card <key>  ·  /knowledge  ·  /quit\n", style="bold cyan")

    console.print(Panel(header, border_style="bold red", expand=True))
    console.print(Panel(table, title="[bold yellow]🔮 BOT LAUNCHER SPELL (interactive)[/bold yellow]", border_style="yellow"))
    console.print("[bold cyan]Type a command:[/bold cyan] [white]/launch 6[/white] · [white]/card 8[/white] · [white]/quiz[/white] · [white]/knowledge[/white] · [white]/quit[/white]")


def show_card(key: str):
    info = BOTS.get(key)
    if not info:
        console.print(f"[red]Unknown key '{key}'[/red]")
        return
    lines = [
        f"[bold cyan]{info['name']}[/bold cyan]  ([bold yellow]{info['slug']}[/bold yellow])",
        f"[bold green]What:[/bold green] {info['what']}",
        f"[bold green]Key files:[/bold green] {', '.join(info['keyfiles'])}",
    ]
    if info["launch"]:
        lines.append(f"[bold green]Launch:[/bold green]")
        for i, c in enumerate(info["launch"], 1):
            lines.append(f"    [white]{i}) {c}[/white]")
    else:
        lines.append("[bold yellow]No launcher — not a standalone bot.[/bold yellow]")
    lines.append(f"[bold green]Exists:[/bold green] {bot_dir(info['slug']).exists()} at {bot_dir(info['slug'])}")
    console.print(Panel("\n".join(lines), title=f"📇 BOT CARD — {info['slug']}", border_style="cyan"))


def launch_bot(key: str):
    info = BOTS.get(key)
    if not info:
        console.print(f"[red]Unknown key '{key}'[/red]")
        return
    d = bot_dir(info["slug"])
    if not d.exists():
        console.print(f"[red]Dir missing: {d}[/red]")
        return
    if not info.get("launch"):
        console.print(f"[yellow]{info['name']} has no launcher — not a standalone bot. See /card {key}.[/yellow]")
        return
    console.print(f"[bold green]Launching {info['name']}[/bold green] in [white]{d}[/white]")
    for i, cmd in enumerate(info["launch"], 1):
        console.print(f"  [bold cyan]Choice {i}:[/bold cyan] [white]{cmd}[/white]")
    pick = Prompt.ask("Run which choice? (number)", default="1")
    try:
        idx = int(pick) - 1
        cmd = info["launch"][idx]
    except (ValueError, IndexError):
        console.print("[red]Bad choice. Aborting launch.[/red]")
        return
    console.print(f"[bold yellow]Running:[/bold yellow] {cmd}")
    pika_learn(f"Bot-launcher spell: launched {info['slug']} with '{cmd}'")
    try:
        subprocess.run(cmd, cwd=str(d), shell=True, check=True)
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted.[/red]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Command exited {e.returncode}. See output above.[/red]")


def run_quiz(n=6):
    console.print(Panel("[bold yellow]🔮 DRILL QUIZ — which bot / which command?[/bold yellow]", border_style="yellow"))
    pool = random.sample(QUIZ, min(n, len(QUIZ)))
    score = 0
    for i, item in enumerate(pool, 1):
        ans = Prompt.ask(f"[bold cyan]Q{i}/{len(pool)}:[/bold cyan] {item['q']}")
        if ans.strip().lower() == item["a"].lower():
            console.print("[bold green]✓ CORRECT[/bold green]")
            score += 1
        else:
            console.print(f"[bold red]✗ Answer:[/bold red] [white]{item['a']}[/white]  [dim](hint: {item['hint']})[/dim]")
    pct = int(100 * score / len(pool))
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    verdict = "MASTERED" if pct >= 90 else "SOLID" if pct >= 70 else "KEEP DRILLING"
    console.print(Panel(f"[bold yellow]SCORE {score}/{len(pool)} ({pct}%) {bar}[/bold yellow]\n[bold cyan]Verdict: {verdict}[/bold cyan]", title="🏆 QUIZ RESULT", border_style="green" if pct >= 70 else "yellow"))
    pika_learn(f"Bot-launcher drill quiz: {score}/{len(pool)} ({pct}%) → {verdict}")
    return score, len(pool)


def show_knowledge():
    if not KNOWLEDGE_DIR.exists():
        console.print("[yellow]No knowledge dir yet.[/yellow]")
        return
    files = sorted(KNOWLEDGE_DIR.rglob("*.md"))
    table = Table(show_header=True, header_style="bold yellow")
    table.add_column("Knowledge file", style="cyan")
    table.add_column("Size", style="white")
    for f in files:
        rel = str(f.relative_to(KNOWLEDGE_DIR))
        try:
            sz = f.stat().st_size
        except Exception:
            sz = 0
        table.add_row(rel, f"{sz} B")
    console.print(Panel(table, title="📚 MEPHISTO KNOWLEDGE (pika_memory scope)", border_style="cyan"))
    console.print(f"[dim]Cards loaded from bots/ → engine reads all *.md via rglob.[/dim]")


def main():
    try:
        while True:
            console.clear()
            render_menu()
            cmd = Prompt.ask("[bold red]⚡ spell[/bold red]", default="")
            parts = cmd.strip().split()
            if not parts:
                continue
            verb = parts[0].lower()
            if verb in ("quit", "exit", "/quit", "/exit", "q"):
                console.print("[yellow]Spell dismissed.[/yellow]")
                break
            if verb in ("launch", "/launch"):
                key = parts[1] if len(parts) > 1 else Prompt.ask("Bot key (1-15)")
                launch_bot(key)
            elif verb in ("card", "/card"):
                key = parts[1] if len(parts) > 1 else Prompt.ask("Bot key (1-15)")
                show_card(key)
            elif verb in ("quiz", "/quiz"):
                run_quiz()
            elif verb in ("knowledge", "/knowledge"):
                show_knowledge()
            else:
                console.print(f"[yellow]Unknown: {verb}. Try: launch <key> | card <key> | quiz | knowledge | quit[/yellow]")
            if verb not in ("quit", "exit", "/quit", "/exit", "q"):
                Prompt.ask("[dim]Press Enter to continue[/dim]", default="")
    except KeyboardInterrupt:
        console.print("\n[red]Spell interrupted.[/red]")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[red]Spell interrupted.[/red]")
