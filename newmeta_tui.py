"""NewMeta TUI shell — a Textual dashboard around cli.py with dropdowns.

Option A: a Textual TUI that reuses cli.py primitives (load_config,
get_provider, TOOL_REGISTRY, run_tools, load_mcp_servers, load_skills) but
runs its own event-emitting streaming loop instead of Agent.run/interactive_chat,
because those print() straight to stdout.

Run:  python newmeta_tui.py        (or:  NewMeta --tui)
"""

from __future__ import annotations

import sys
import os
import re
import subprocess
import urllib.request
import webbrowser
import json
import threading
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Header, Footer, Static, Input, RichLog, Button, Select, OptionList, TabbedContent, TabPane
from textual.widgets.option_list import Option
from textual.screen import ModalScreen
from textual.binding import Binding
from textual import work
from textual.events import Key, MouseDown, MouseUp, MouseScrollUp, MouseScrollDown, Paste
import time
import importlib.util
from rich.text import Text

import cli
from companion_registry import SUMMONS, HOUSE_COLORS

sys.path.insert(0, str(Path.home() / ".claude" / "companion_kit"))
from emit import has_mode, load_mode, set_mode as set_companion_mode
import board

sys.path.insert(0, str(Path.home() / "Desktop" / "pika-poke"))
from spellbook.spells import (
    SPELLS, MINI_SPELLS, MEPHISSA_SPELLS, MEPHISSA_MINI_SPELLS, ZOUZOU_SPELLS,
    TURTLE_SPELLS, MEPHISTO_SPELLS,
)

ALL_SPELLS = SPELLS + MINI_SPELLS + MEPHISSA_SPELLS + MEPHISSA_MINI_SPELLS + ZOUZOU_SPELLS + TURTLE_SPELLS + MEPHISTO_SPELLS


def spell_slots_for(key: str, limit: int | None = 4) -> list[tuple[str, str, str]]:
    """Compact (glyph, action_id, full_name) slots for a companion's real
    spells - a Dota-ability-bar-style quick-cast row that replaces the old
    full-size XP bar. Same underlying spell list JoystickScreen's full grid
    uses, with a short hotkey-letter glyph (full name goes in the button's
    hover tooltip) since a card is far too narrow for full labels.
    limit=4 for the compact per-card row; limit=None returns every spell,
    for the full sidebar pad panel."""
    spells = [s for s in ALL_SPELLS if s.companion == key]
    if spells:
        out = []
        capped = spells if limit is None else spells[:limit]
        for s in capped:
            letter = s.hotkey.replace("Ctrl+Alt+Shift+", "").replace("Ctrl+Alt+", "").replace("Alt+", "")
            out.append((letter, s.key, s.name))
        return out
    if key == "turtle":
        return [("🛡️", "toggle", "Toggle Auto-Approval")]
    if key == "mephisto":
        return [("🚨", "alerts", "Alerts"), ("📡", "signals", "Signals")]
    return []


# MCP servers only relevant to one engine section - their tools are hidden
# from the model's schema in the other section instead of always being
# offered (e.g. 36 tools loaded and in-schema even in coding mode, most of
# them MT5/trading calls the model never needed there, just noise + lag).
# Servers not listed here (agent-search-mcp, open-websearch) are useful in
# both sections and stay available regardless of engine_mode.
MCP_SERVER_SECTION = {
    "mt5": "trading",
}

# Tools slow/heavy enough (minutes, not seconds - model downloads on first
# run, ffmpeg renders, etc.) that the user needs an explicit "still working"
# acknowledgment before it runs, not silence until the round finishes. Their
# real result text also gets printed straight into the main chat once done
# (see the tool-result loop in run_agent_worker) instead of only relying on
# the model to relay it next turn - local/small models don't always do that
# reliably, and this is exactly the kind of result (a generated file path)
# that's useless if it gets lost.
SLOW_TOOL_MESSAGES = {
    "mephissa_karaoke_make": "🎧 [Mephissa] hold on, cooking... separating vocals, transcribing, rendering the karaoke video — first run also downloads model weights, this can take several minutes.",
    "mephissa_record_start": "🎙️ [Mephissa] starting the recording now.",
}


def tool_groups(include_mcp: bool, include_skills: bool, include_core: bool, include_fetch: bool = True, section: str = "coding") -> list[str]:
    names = []
    for name in cli.TOOL_REGISTRY:
        if name.startswith("mcp__"):
            if not include_mcp:
                continue
            server = name.split("__", 2)[1] if name.count("__") >= 2 else ""
            required_section = MCP_SERVER_SECTION.get(server)
            if required_section and required_section != section:
                continue
            names.append(name)
        elif name.startswith("skill__"):
            if include_skills:
                names.append(name)
        elif name.startswith("meph_download_"):
            if include_fetch:
                names.append(name)
        elif include_core:
            names.append(name)
    return names


def build_schema(names: list[str]) -> list[dict]:
    funcs = []
    for name in names:
        tool = cli.TOOL_REGISTRY.get(name)
        if not tool:
            continue
        funcs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
            },
        })
    return funcs


def get_pika_stats() -> dict:
    pika_dir = Path(os.path.expanduser("~/.pika_poke"))
    pika_dir.mkdir(parents=True, exist_ok=True)
    stats_file = pika_dir / "stats.json"
    default_stats = {
        "level": 3,
        "xp": 1522,
        "max_xp": 3000,
        "saved_tokens_turn": "+25.0k",
        "saved_tokens_total": "106.0k",
        "agents_count": 0,
        "ctx_pct": 22
    }
    if not stats_file.exists():
        try:
            with open(stats_file, "w", encoding="utf-8") as f:
                json.dump(default_stats, f, indent=2)
        except Exception:
            pass
        return default_stats
    try:
        with open(stats_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            for k, v in default_stats.items():
                data.setdefault(k, v)
            return data
    except Exception:
        return default_stats


_PIKA_MD_PATH = Path.home() / "PIKA_POKE.md"


def get_pika_md_stats() -> dict | None:
    """The REAL canonical PikaPoke identity - the exact file Antigravity's
    pika_caster.py hook AND Claude Code's statusline.py both read (same
    regexes, confirmed to agree with each other), so it's what's actually
    shown live in the terminal on every single prompt. Lv.1-100 with
    in-level XP rollover, not the 0-3000 5-stage curve the other companions
    (Zouzou/Turtle/Mephissa/Mephisto) use - that's PikaPoke's own leveling
    model by design, see PIKA_POKE.md's own header. Returns None if the file
    doesn't exist so callers can fall back cleanly instead of showing a fake
    zero."""
    if not _PIKA_MD_PATH.exists():
        return None
    try:
        content = _PIKA_MD_PATH.read_text(encoding="utf-8")
        xp_match = re.search(r"(\d+)\s*/\s*(\d+)\s*XP", content)
        level_match = re.search(r"Lv\.(\d+)", content)
        saved_match = re.search(r"Cumulative Total Tokens Saved`:\s*`([^`]+)`", content)
        if not xp_match and not level_match:
            return None
        return {
            "level": int(level_match.group(1)) if level_match else 1,
            "xp": int(xp_match.group(1)) if xp_match else 0,
            "max_xp": int(xp_match.group(2)) if xp_match else 3000,
            "saved_tokens_total": saved_match.group(1) if saved_match else None,
        }
    except Exception:
        return None


def award_pika_xp(amount: int, reason: str = "") -> dict | None:
    """Writes real XP earned via switch-tui straight into PIKA_POKE.md - the
    same canonical file Claude Code's statusline hook and Antigravity's own
    hook both read - instead of the companion module's disconnected state
    file that nothing else displays. Handles Lv.1-100 in-level XP rollover
    per that file's own documented model ("Max Cap: Lv.100"). Returns None
    (no-op) if the file doesn't exist - never creates it fresh, since its
    shape/journal content is owned by Antigravity's own hook, not this TUI."""
    current = get_pika_md_stats()
    if current is None:
        return None
    try:
        # newline="" on both ends: preserve the file's existing line-ending
        # convention exactly rather than letting Windows text-mode silently
        # rewrite every LF to CRLF on save (that turned a 1-line XP change
        # into a whole-file diff the first time this was tested).
        with open(_PIKA_MD_PATH, "r", encoding="utf-8", newline="") as fh:
            content = fh.read()
        level, xp, max_xp = current["level"], current["xp"], current["max_xp"]
        new_xp = xp + amount
        while new_xp >= max_xp and level < 100:
            new_xp -= max_xp
            level += 1
        new_xp = max(0, new_xp)

        def _replace_rank(m):
            return f"`Current Rank`: `Lv.{level} Hacker Archon (Progressing to Lv.{min(level + 1, 100)})`"

        def _replace_xp(m):
            return f"`XP`: `{new_xp}/{max_xp} XP` (Max Cap: `Lv.100`)"

        content = re.sub(r"`Current Rank`:\s*`[^`]*`", _replace_rank, content, count=1)
        content = re.sub(r"`XP`:\s*`[^`]*`\s*\(Max Cap:\s*`[^`]*`\)", _replace_xp, content, count=1)
        with open(_PIKA_MD_PATH, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return {"level": level, "xp": new_xp, "max_xp": max_xp}
    except Exception:
        return None


def render_pika_bar(ctx_pct: int = 22, active_agents: int = 1) -> str:
    # Level/xp/saved-tokens come from get_pikapoke_stats(), which now prefers
    # the real PIKA_POKE.md (same file the Claude Code statusline hook
    # reads) over the disconnected, never-updated ~/.pika_poke/stats.json
    # this used to read exclusively (frozen forever at Lv.3, 1522/3000 XP,
    # 106.0k saved) - that file is now only a last-resort fallback.
    stats = get_pika_stats()
    live = get_pikapoke_stats()
    lvl = live.get("level", stats.get("level", 3))
    xp = live.get("xp", stats.get("xp", 1522))
    max_xp = live.get("max_xp") or stats.get("max_xp", 3000)
    pct = int((xp / max_xp) * 10) if max_xp else 5
    bar_str = "#" * pct + "-" * (10 - pct)
    tot_saved = live.get("saved_tokens_total") or stats.get("saved_tokens_total", "106.0k")
    agent_str = f"⚡ {active_agents} Agent" if active_agents == 1 else f"⚡ {active_agents} Agents"
    return (
        f"PIKA POKE [Lv.{lvl}] [{bar_str}] {xp}/{max_xp} XP | "
        f"🛡️ {tot_saved} Saved | ctx: {ctx_pct}% | {agent_str}"
    )


# --- Companions (shared, persistent, cross-terminal) ----------------------
ZOUZOU_DIR = Path(os.path.expanduser("~/.claude/zouzou"))
MEPHISSA_DIR = Path(os.path.expanduser("~/.claude/mephissa"))
COMPANIONS_DIR = Path(os.path.expanduser("~/.claude/companions"))
MEPHISTO_DIR = Path(os.path.expanduser("~/.codex/memories/mephisto"))


def _load_module(py_path: Path, mod_name: str):
    if not py_path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(mod_name, py_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


_ZOUZOU_MOD = _load_module(ZOUZOU_DIR / "zouzou.py", "_zouzou_companion")
_MEPHISSA_MOD = _load_module(MEPHISSA_DIR / "mephissa.py", "_mephissa_companion")
_TURTLE_MOD = _load_module(COMPANIONS_DIR / "turtle" / "turtle.py", "_turtle_companion")
_PIKAPOKE_MOD = _load_module(COMPANIONS_DIR / "pikapoke" / "pikapoke.py", "_pikapoke_companion")
_MEPHISTO_MOD = _load_module(MEPHISTO_DIR / "mephisto_cli.py", "_mephisto_companion")

_COMPANION_FALLBACK = {"name": "?", "xp": 0, "level": 1, "stage": "Hatchling", "stage_emoji": "🥚", "last_event": None}


def get_zouzou_stats() -> dict:
    if _ZOUZOU_MOD is None:
        return dict(_COMPANION_FALLBACK, name="Zouzou")
    try:
        return _ZOUZOU_MOD.load_state()
    except Exception:
        return dict(_COMPANION_FALLBACK, name="Zouzou")


def _escape_bar_markup(bar: str) -> str:
    """Escape literal [ ] in a plain-text progress-bar string so Rich markup
    doesn't try to parse it as a style/color tag (e.g. '[#####-----]')."""
    return bar.replace("[", "\\[").replace("]", "\\]")


def _short_bullet(text: str, max_words: int = 3) -> str:
    """Reduce arbitrary (often Rich-markup'd, often long) text down to a
    short scannable bullet: first line/sentence, plain words only, capped
    at max_words. Used for the ONGOING and ACTION PLAN panels, which are
    meant to be glanceable, not full sentences."""
    plain = re.sub(r"\[/?[^\[\]]*\]", "", text or "")
    plain = plain.strip().split("\n")[0]
    plain = re.split(r"(?<=[.!?:])\s", plain)[0]
    words = [w for w in plain.split() if w]
    words = words[:max_words]
    return " ".join(words) if words else "..."


def render_vertical_bar(pct: float, height: int = 2, width: int = 10) -> str:
    """Render a horizontal XP gauge (rotated from the old bottom-up vertical
    fill): `height` identical rows, each filling left-to-right as pct
    (0.0-1.0) increases, so the block reads as one wide bar instead of a
    solid vertical slab. '\\n'-joined, one row per line."""
    pct = max(0.0, min(1.0, pct))
    filled_cols = round(pct * width)
    row = ("█" * filled_cols) + ("░" * (width - filled_cols))
    return "\n".join([row] * height)


def render_companion_bar(xp: int, max_xp: int, color: str) -> str:
    """The block-fill bar alone reads as an unlabeled solid rectangle at a
    glance (no way to tell 20% full from 90% full without a number) - append
    the actual xp/max_xp and percentage so every companion card's bar is
    self-explanatory, matching the readability the status bar's own
    '[#####-----] 1522/3000 XP' text already had."""
    max_xp = max_xp or 1
    pct = xp / max_xp
    bar = render_vertical_bar(pct)
    return f"[bold {color}]{bar}[/bold {color}]\n[dim]{xp}/{max_xp} XP ({pct * 100:.0f}%)[/dim]"


def zouzou_bar(xp: int, width: int = 10) -> str:
    if _ZOUZOU_MOD is not None:
        try:
            return _ZOUZOU_MOD._bar(xp, width=width)
        except Exception:
            pass
    return f"[{'-' * width}] {xp}/?"


def zouzou_info_markup() -> str:
    s = get_zouzou_stats()
    return f"[bold white]{s.get('name', 'Zouzou')}[/bold white] [dim]Lv.{s.get('level', 1)} {s.get('stage', 'Frenzy')}[/dim]"


def zouzou_bar_markup() -> str:
    s = get_zouzou_stats()
    return render_companion_bar(s.get("xp", 0), s.get("max_xp", 3000), "#c4b5fd")


def get_turtle_stats() -> dict:
    if _TURTLE_MOD is None:
        return dict(_COMPANION_FALLBACK, name="Turtle")
    try:
        return _TURTLE_MOD.get_state()
    except Exception:
        return dict(_COMPANION_FALLBACK, name="Turtle")


def turtle_name_markup() -> str:
    s = get_turtle_stats()
    return f"[bold white]Turtle[/bold white] [dim]Lv.{s.get('level', 1)} {s.get('stage') or 'Guardian'}[/dim]"


def turtle_bar_markup() -> str:
    s = get_turtle_stats()
    return render_companion_bar(s.get("xp", 0), s.get("max_xp", 3000), "#38bdf8")


def get_pikapoke_stats() -> dict:
    # PIKA_POKE.md is the real, actively-updated identity (same file the
    # Claude Code statusline hook reads) - prefer it over the companion
    # module's own JSON state, which currently has no state file at all
    # (would silently show Lv.1/0 XP even while the real tracker is Lv.4+).
    md_stats = get_pika_md_stats()
    if md_stats is not None:
        return dict(md_stats, name="Pika Poke", stage="Hacker Archon", stage_emoji="🐭")
    if _PIKAPOKE_MOD is None:
        return dict(_COMPANION_FALLBACK, name="Pika Poke")
    try:
        return _PIKAPOKE_MOD.get_state()
    except Exception:
        return dict(_COMPANION_FALLBACK, name="Pika Poke")


def pikapoke_name_markup() -> str:
    s = get_pikapoke_stats()
    return f"[bold white]Pika Poke[/bold white] [dim]Lv.{s.get('level', 1)} {s.get('stage') or 'Archon'}[/dim]"


def pikapoke_bar_markup() -> str:
    s = get_pikapoke_stats()
    return render_companion_bar(s.get("xp", 0), s.get("max_xp", 3000), "#ccff00")


def get_mephissa_stats() -> dict:
    if _MEPHISSA_MOD is None:
        return dict(_COMPANION_FALLBACK, name="Mephissa", stage="Phantom", stage_emoji="👻")
    try:
        return _MEPHISSA_MOD.load_state()
    except Exception:
        return dict(_COMPANION_FALLBACK, name="Mephissa", stage="Phantom", stage_emoji="👻")


def mephissa_bar(xp: int, width: int = 10) -> str:
    if _MEPHISSA_MOD is not None:
        try:
            return _MEPHISSA_MOD._bar(xp, width=width)
        except Exception:
            pass
    return f"[{'-' * width}] {xp}/?"


def mephissa_info_markup() -> str:
    s = get_mephissa_stats()
    return f"[bold white]{s.get('name', 'Mephissa')}[/bold white] [dim]Lv.{s.get('level', 1)} {s.get('stage', 'Phantom')}[/dim]"


def mephissa_bar_markup() -> str:
    s = get_mephissa_stats()
    return render_companion_bar(s.get("xp", 0), s.get("max_xp", 3000), "#ff2fd6")


def get_mephisto_stats() -> dict:
    if _MEPHISTO_MOD is not None and hasattr(_MEPHISTO_MOD, "get_state"):
        try:
            return _MEPHISTO_MOD.get_state()
        except Exception:
            pass
    return {"name": "Mephisto", "level": 3, "stage": "Router Master", "xp": 1850, "max_xp": 3000}


def mephisto_info_markup() -> str:
    s = get_mephisto_stats()
    return f"[bold white]{s.get('name', 'Mephisto')}[/bold white] [dim]Lv.{s.get('level', 3)} {s.get('stage', 'Router')}[/dim]"


def mephisto_bar_markup() -> str:
    s = get_mephisto_stats()
    return render_companion_bar(s.get("xp", 1850), s.get("max_xp", 3000), "#e11d48")


# Mini XP bars for the card footers - replace the old history sparkline
# (which rendered as an uncolored flat white line whenever XP history had
# no variance yet, e.g. right after startup). These mirror the exact same
# live pct as the big bar above, just at ~21% of its width/height (14x2 ->
# 3x1), capped under the requested 25% max, and colored to match.
def pikapoke_mini_bar_markup() -> str:
    s = get_pikapoke_stats()
    pct = s.get("xp", 0) / s.get("max_xp", 3000)
    return f"[bold #ccff00]{render_vertical_bar(pct, height=1, width=3)}[/bold #ccff00]"


def turtle_mini_bar_markup() -> str:
    s = get_turtle_stats()
    pct = s.get("xp", 0) / s.get("max_xp", 3000)
    return f"[bold #38bdf8]{render_vertical_bar(pct, height=1, width=3)}[/bold #38bdf8]"


def zouzou_mini_bar_markup() -> str:
    s = get_zouzou_stats()
    pct = s.get("xp", 0) / s.get("max_xp", 3000)
    return f"[bold #c4b5fd]{render_vertical_bar(pct, height=1, width=3)}[/bold #c4b5fd]"


def mephissa_mini_bar_markup() -> str:
    s = get_mephissa_stats()
    pct = s.get("xp", 0) / s.get("max_xp", 3000)
    return f"[bold #ff2fd6]{render_vertical_bar(pct, height=1, width=3)}[/bold #ff2fd6]"


def mephisto_mini_bar_markup() -> str:
    s = get_mephisto_stats()
    xp = s.get("xp", 1850)
    max_xp = s.get("max_xp", 3000)
    pct = (xp / max_xp) if max_xp else 0.6
    return f"[bold #e11d48]{render_vertical_bar(pct, height=1, width=3)}[/bold #e11d48]"


def no_wrap_text(markup: str) -> Text:
    t = Text.from_markup(markup)
    t.no_wrap = True
    t.overflow = "ellipsis"
    return t


PALETTE_THEMES = {
    "cyberpunk": {"accent": "#ff023a", "sidebar": "#ff023a", "chat": "#ff023a"},
    "sunset":    {"accent": "#f97316", "sidebar": "#ff023a", "chat": "#facc15"},
    "matrix":    {"accent": "#22c55e", "sidebar": "#ff023a", "chat": "#22c55e"},
    "ocean":     {"accent": "#38bdf8", "sidebar": "#ff023a", "chat": "#38bdf8"},
    "minimal":   {"accent": "#ff023a", "sidebar": "#ff023a", "chat": "#ffffff"},
}


SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values, width: int = 10) -> str:
    vals = list(values)[-width:]
    if not vals:
        return f"[dim]{'·' * width}[/dim]"
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    bars = "".join(
        SPARK_CHARS[int((v - lo) / span * (len(SPARK_CHARS) - 1))]
        for v in vals
    )
    return bars.rjust(width, "·")


def project_progress_bar(done: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return f"[dim][{'░' * width}] 0/0 · 0% actions[/dim]"
    pct = done / total
    filled = int(round(pct * width))
    bar = "█" * filled + "░" * (width - filled)
    return f"[bold #ff023a][{bar}][/bold #ff023a] {done}/{total} · {int(pct * 100)}%"


def handle_smart_right_click(app) -> None:
    import pyperclip
    has_clip = False
    try:
        clip_text = (pyperclip.paste() or "").strip()
        if clip_text:
            has_clip = True
    except Exception:
        pass

    if has_clip:
        app.action_paste_clipboard()
    else:
        app.action_copy_log()


class TuiInput(Input):
    BINDINGS = [
        Binding("enter", "submit", "Send", show=False),
        Binding("ctrl+c", "clear", "Clear", show=False),
        # The rotating tip strip has always advertised "Ctrl+U clears the
        # prompt line" but no such binding actually existed - only ctrl+c
        # did the clearing. Added for real instead of just fixing the tip.
        Binding("ctrl+u", "clear", "Clear", show=False),
    ]

    _last_click_time = 0.0

    def action_clear(self) -> None:
        self.value = ""

    def on_paste(self, event: Paste) -> None:
        if event.text and ("\n" in event.text or len(event.text) > 80):
            self.app.pasted_attachment = event.text
            self.placeholder = f"📋 [Attached: {len(event.text)} chars] Type prompt & press Enter..."
            self.app.log_line(f"[bold yellow]📋 Text Block Attached ({len(event.text)} chars). Press Enter to send.[/bold yellow]")
            event.stop()
            event.prevent_default()

    def on_key(self, event: Key) -> None:
        if event.key == "alt+shift+v":
            self.app.action_paste_clipboard()
            event.stop()
            event.prevent_default()

    def on_mouse_down(self, event: MouseDown) -> None:
        if event.button == 3:
            handle_smart_right_click(self.app)
            event.stop()
            event.prevent_default()
        elif event.button == 1:
            now = time.time()
            self._last_click_time = now


class AgentLog(RichLog):
    """Chat log. Plain click-drag uses Textual's native text selection (real
    highlight, Ctrl+C to copy) — we only intercept right-click (smart paste/copy)
    and alt-click (quick single-word copy); everything else is left alone so the
    framework's default selection handling can engage."""
    _last_click_time = 0.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._plain_lines = []

    def write(self, content, *args, **kwargs) -> "AgentLog":
        import re
        plain = re.sub(r'\[/?[^\]]+\]', '', str(content))
        for line in plain.splitlines():
            self._plain_lines.append(line)
        return super().write(content, *args, **kwargs)

    def get_selection(self, selection):
        """RichLog doesn't implement this itself, so native Ctrl+C copy of a
        drag-selection would silently extract nothing without this override."""
        text = "\n".join(self._plain_lines)
        if not text:
            return None
        return selection.extract(text), "\n"

    def on_mouse_down(self, event: MouseDown) -> None:
        import re, pyperclip
        if event.button == 3:
            handle_smart_right_click(self.app)
            event.stop()
            event.prevent_default()
            return

        if event.button == 1 and getattr(event, "alt", False):
            # Alt-click: quick single-word copy without disturbing normal drag-select.
            line_idx = event.y + getattr(self.scroll_offset, 'y', 0)
            if 0 <= line_idx < len(self._plain_lines):
                line_text = self._plain_lines[line_idx]
                col = event.x
                for m in re.finditer(r'\b\w+\b', line_text):
                    if m.start() <= col <= m.end():
                        word = m.group(0)
                        try:
                            pyperclip.copy(word)
                            self.app.log_line(f"[cyan]📋 Copied word:[/cyan] [bold white]{word}[/bold white]")
                        except Exception:
                            pass
                        break
            event.stop()
            event.prevent_default()
        # Plain left click/drag: don't stop/prevent_default — Textual's built-in
        # text selection (drag to highlight, Ctrl+C to copy) handles it natively.

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        self.scroll_page_up(animate=False)
        event.stop()

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        self.scroll_page_down(animate=False)
        event.stop()


class NeonFooter(Horizontal):
    """Replaces Textual's built-in Footer: one discrete bordered box per
    keybinding (icon on top, shortcut below) instead of one continuous
    strip, and hover brightens the outline only - never fills the box - so
    the shape stays readable while highlighted. Was full text description on
    top (e.g. "Paste/Attach") - swapped for just its leading icon so the
    chip reads at a glance instead of needing to be read; full description
    still available as a hover tooltip."""

    DEFAULT_CSS = """
    NeonFooter {
        height: 4;
        layout: horizontal;
        margin: 1 1 1 1;
        overflow-x: auto;
        scrollbar-size-vertical: 0;
        scrollbar-background: #000000;
        scrollbar-color: #00F2FE;
    }
    .footer-chip {
        width: auto;
        min-width: 9;
        height: 4;
        border: round #2a2a2a;
        background: #0a0003;
        margin-right: 1;
        padding: 0 1;
    }
    .footer-chip:hover {
        border: round #00F2FE;
        background: #0a0003;
    }
    .footer-chip-icon {
        width: 100%;
        height: 1;
        color: #F8F9FA;
        text-style: bold;
        content-align: center middle;
    }
    .footer-chip-key {
        width: 100%;
        height: 1;
        color: #00F2FE;
        text-style: bold;
        content-align: center middle;
    }
    """

    _KEY_DISPLAY = {
        "alt+shift+v": "Alt+Shift+V", "escape": "Esc", "f5": "F5", "f6": "F6", "f7": "F7",
        "f8": "F8", "f9": "F9", "f10": "F10", "ctrl+up": "Ctrl+↑",
        "ctrl+down": "Ctrl+↓", "tab": "Tab", "ctrl+p": "Ctrl+P",
    }

    # Bindings whose description has no leading emoji to extract.
    _ICON_FALLBACK = {"Next Companion": "👥", "Palette": "🎨"}

    def __init__(self, bindings: list[tuple[str, str]]):
        super().__init__()
        self._bindings = bindings

    def compose(self) -> ComposeResult:
        for key, description in self._bindings:
            label = self._KEY_DISPLAY.get(key, key.title())
            icon = self._ICON_FALLBACK.get(description) or description.split(" ", 1)[0]
            with Vertical(classes="footer-chip"):
                icon_widget = Static(icon, classes="footer-chip-icon")
                icon_widget.tooltip = description
                yield icon_widget
                yield Static(label, classes="footer-chip-key")


class ToolChecklist(Vertical):
    """Live, mutating checklist for the current agent turn's tool calls -
    mirrors how Claude Code's own task list works: each item starts pending
    (o) and updates IN PLACE to done/failed (v / x) as its result comes back,
    instead of an append-only log that just grows forever. Scoped to one
    turn - reset() clears it when a new turn starts.

    Glyphs: ☒ pending/"currently working on", ☑ done, bold red x failed -
    was ☐/☑/☒ (all three share the same box outline, easy to misread the
    faint interior mark at normal log-line sizes - a reported bug, the
    pending box was read as a checkmark meaning "not done"). Now only ☒/☑
    share an outline, and failed uses a shape that doesn't share either
    one's outline, so no two states can be confused for each other."""

    DEFAULT_CSS = """
    ToolChecklist {
        height: 1fr;
        overflow-y: auto;
        scrollbar-size-vertical: 1;
        scrollbar-background: #000000;
        scrollbar-color: #00F2FE;
    }
    .checklist-item {
        height: 1;
        color: #cbd5e1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._items: dict[str, tuple[Static, str]] = {}

    def reset(self) -> None:
        self.remove_children()
        self._items.clear()

    def add_pending(self, item_id: str, label: str) -> None:
        widget = Static(f"[yellow]☒[/yellow] {label}", classes="checklist-item")
        self._items[item_id] = (widget, label)
        self.mount(widget)

    def mark_done(self, item_id: str, ok: bool) -> None:
        entry = self._items.get(item_id)
        if not entry:
            return
        widget, label = entry
        mark = "[bold green]☑[/bold green]" if ok else "[bold red]✗[/bold red]"
        widget.update(f"{mark} {label}")


class ModelPickerScreen(ModalScreen[str]):
    """Full-window agent/model picker - like Kilo Code's CLI, which opens a
    dedicated window for model selection instead of a cramped inline
    dropdown. Type to filter, Enter/click to launch, Esc to cancel."""

    DEFAULT_CSS = """
    ModelPickerScreen {
        align: center middle;
        background: #000000 30%;
    }
    #picker-box {
        width: 80%;
        max-width: 100;
        height: 80%;
        max-height: 40;
        background: #0a0003;
        border: heavy #00F2FE;
        padding: 1;
    }
    #picker-title {
        height: 1;
        color: #F8F9FA;
        text-style: bold;
        padding-bottom: 1;
    }
    #picker-search {
        height: 3;
        border: round #a78bfa;
        background: #140005;
        color: #F8F9FA;
    }
    #picker-list {
        height: 1fr;
        background: #000000;
        border: round #00F2FE;
        margin-top: 1;
    }
    #picker-hint {
        height: 1;
        color: #64748b;
        padding-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(self, options: list[tuple[str, str]], initial_id: str | None = None) -> None:
        super().__init__()
        self._all_options = options
        self._initial_id = initial_id

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static("🚀 Launch Agent  —  type to filter, Enter to launch, Esc to cancel", id="picker-title")
            yield Input(placeholder="Filter agents...", id="picker-search")
            yield OptionList(id="picker-list")
            yield Static("", id="picker-hint")

    def on_mount(self) -> None:
        self._populate("")
        option_list = self.query_one("#picker-list", OptionList)
        if self._initial_id:
            for index in range(option_list.option_count):
                if option_list.get_option_at_index(index).id == self._initial_id:
                    option_list.highlighted = index
                    break
        self.query_one("#picker-search", Input).focus()

    def _populate(self, query: str) -> None:
        option_list = self.query_one("#picker-list", OptionList)
        option_list.clear_options()
        query = query.strip().lower()
        shown = 0
        for label, value in self._all_options:
            is_header = value.startswith("__hdr__")
            if query and not is_header and query not in label.lower():
                continue
            option_list.add_option(Option(label, id=value, disabled=is_header))
            if not is_header:
                shown += 1
        self.query_one("#picker-hint", Static).update(f"{shown} agent(s)")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "picker-search":
            self._populate(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "picker-search":
            return
        option_list = self.query_one("#picker-list", OptionList)
        if option_list.option_count:
            for index in range(option_list.option_count):
                option = option_list.get_option_at_index(index)
                if not option.disabled and option.id:
                    self.dismiss(option.id)
                    return

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        value = event.option_id
        if value and not value.startswith("__hdr__"):
            self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ThemePickerScreen(ModalScreen[str]):
    """Theme picker with live hover preview (like Kilo Code's CLI). Deliberately
    a plain OptionList in its own screen rather than a Select - Select's
    SelectOverlay swallows OptionHighlighted internally (see its own comment,
    "stop option list highlighted messages leaking"), so a Select can never
    support hover-preview. Here the message reaches this screen untouched."""

    DEFAULT_CSS = """
    ThemePickerScreen {
        align: center middle;
        background: #000000 30%;
    }
    #theme-picker-box {
        width: 40;
        height: auto;
        max-height: 20;
        background: #0a0003;
        border: heavy #00F2FE;
        padding: 1;
    }
    #theme-picker-title {
        height: 1;
        color: #F8F9FA;
        text-style: bold;
        padding-bottom: 1;
    }
    #theme-picker-list {
        height: auto;
        max-height: 12;
        background: #000000;
        border: round #a78bfa;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(self, names: list[str], current: str | None) -> None:
        super().__init__()
        self._names = names
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="theme-picker-box"):
            yield Static("🎨 Theme  —  hover to preview, Enter/click to keep, Esc to revert", id="theme-picker-title")
            yield OptionList(*[Option(f"🎨 {name}", id=name) for name in self._names], id="theme-picker-list")

    def on_mount(self) -> None:
        option_list = self.query_one("#theme-picker-list", OptionList)
        option_list.focus()
        if self._current in self._names:
            option_list.highlighted = self._names.index(self._current)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_id:
            self.app._apply_palette(event.option_id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self.dismiss(event.option_id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class JoystickScreen(ModalScreen[None]):
    """Neon-outline gamepad-style macro panel: instead of generic R1/R2/A/B,
    every button IS one of the heroes' real spells, grouped in its own
    cell per companion (colored to that companion), and pressing one
    actually casts it - not a preview. Zouzou's no-argument terminal
    tricks launch their real script directly; everyone else routes through
    the same `python -m spellbook cast` the AHK hotkey layer already uses,
    so XP/state stays identical either way. Esc closes it."""

    DEFAULT_CSS = """
    JoystickScreen {
        align: center middle;
        background: #000000 30%;
    }
    #joy-box {
        width: 96%;
        max-width: 150;
        height: 90%;
        max-height: 40;
        background: #0a0003 55%;
        border: heavy #F8F9FA;
        padding: 1;
    }
    #joy-title {
        height: 1;
        color: #F8F9FA;
        text-style: bold;
        padding-bottom: 1;
        content-align: center middle;
    }
    #joy-pads {
        layout: horizontal;
        height: 1fr;
        overflow-x: auto;
    }
    .joy-cell {
        width: 1fr;
        min-width: 26;
        height: 1fr;
        margin: 0 1;
        padding: 1;
        border: round #444;
        background: #050505 60%;
    }
    .joy-cell.flash {
        background: #1a1a1a 60%;
    }
    .joy-cell-title {
        height: 1;
        text-style: bold;
        content-align: center middle;
    }
    .joy-btn {
        width: 100%;
        min-height: 3;
        margin-bottom: 1;
        content-align: center middle;
    }
    #joy-fatality-btn {
        width: 100%;
        min-height: 3;
        margin-top: 1;
        background: #1a0004;
        border: heavy #ff023a;
        color: #ff023a;
        text-style: bold;
        content-align: center middle;
    }
    #joy-fatality-btn:hover {
        background: #ff023a;
        color: #F8F9FA;
    }
    #joy-hint {
        height: 1;
        color: #64748b;
        padding-top: 1;
        content-align: center middle;
    }
    #joy-tabs {
        height: 1fr;
    }
    #joy-effects-list, #joy-hotkeys-list {
        height: 1fr;
        padding: 1;
    }
    .joy-effects-header {
        height: 1;
        margin-top: 1;
        text-style: bold;
    }
    .joy-effects-line {
        height: auto;
        color: #cbd5e1;
        padding-left: 2;
    }
    .joy-hotkeys-note {
        height: 1;
        margin-bottom: 1;
    }
    .joy-hotkeys-line {
        height: 1;
        color: #cbd5e1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Close", show=True)]

    # Real bindings from pika-poke/ahk/pika_poke.ahk - system-wide, work
    # from any app, not just inside this TUI. Kept here as a reference list
    # only; the actual keys live in that one .ahk file so there's a single
    # source of truth if they ever change.
    GLOBAL_HOTKEYS = [
        ("Alt+Q", "Soul Harvester (PikaPoke)"),
        ("Alt+W", "BloodSeeker (PikaPoke)"),
        ("Alt+E", "Phantom Strike (PikaPoke)"),
        ("Alt+R", "Archon Tether (PikaPoke)"),
        ("Alt+Shift+V", "Zoro Rikimaru Vanish (PikaPoke)"),
        ("Alt+D", "Dr. Vision (PikaPoke)"),
        ("Alt+L", "Learn from clipboard (PikaPoke)"),
        ("Ctrl+Alt+Shift+E", "Silencer Learn (PikaPoke)"),
        ("Alt+H", "Hawkeye — OCR capture (Zouzou)"),
        ("Alt+P", "Silent Shutter — screenshot capture (Zouzou)"),
    ]

    # Companion -> (bright neon color, spell list). Colors per an explicit
    # request: Zouzou Nardo Gray, Mephisto Red, PikaPoke Yellow-Lime,
    # Mephissa Fuchsia, Turtle Cyan.
    COLORS = {
        "pikapoke": "#ccff00",
        "turtle":   "#00eaff",
        "zouzou":   "#a8ab9f",
        "mephissa": "#ff2fd6",
        "mephisto": "#ff2222",
    }

    # Zouzou's terminal tricks take no arguments and are safe/bounded to
    # run standalone - real script launch, not just the flavor-text cast.
    ZOUZOU_SCRIPTS = {
        "hawkeye": "hawkeye.py", "silent_shutter": "silent_shutter.py",
        "galaxy_trick": "starwars_ascii.py", "matrix_rain": "matrix_rain.py",
        "cowsay_fortune": "cowsay_fortune.py", "rainbow_cow": "rainbow_cow.py",
        "nyan_cat": "nyan_cat.py", "hollywood_mode": "hollywood_mode.py",
        "ascii_fireworks": "ascii_fireworks.py", "system_dashboard": "system_dashboard.py",
        "ascii_banner": "ascii_banner.py", "fatality_sequence": "fatality_sequence.py",
    }

    def compose(self) -> ComposeResult:
        with Vertical(id="joy-box"):
            yield Static("🕹️ MACRO PAD — press a button, it actually casts", id="joy-title")
            with TabbedContent(id="joy-tabs"):
                with TabPane("🕹️ Pads", id="tab-pads"):
                    with Horizontal(id="joy-pads"):
                        for key in ["pikapoke", "turtle", "zouzou", "mephissa", "mephisto"]:
                            color = self.COLORS[key]
                            summon = SUMMONS.get(key)
                            name = summon.name if summon else key.title()
                            with Vertical(classes="joy-cell", id=f"joy-cell-{key}"):
                                yield Static(f"[bold {color}]{name}[/bold {color}]", classes="joy-cell-title")
                                for label, btn_id in self._buttons_for(key):
                                    yield Button(label, id=btn_id, classes="joy-btn", variant="default")
                with TabPane("✨ Effects", id="tab-effects"):
                    with ScrollableContainer(id="joy-effects-list"):
                        for key in ["pikapoke", "turtle", "zouzou", "mephissa", "mephisto"]:
                            spells = [s for s in ALL_SPELLS if s.companion == key]
                            if not spells:
                                continue
                            color = self.COLORS[key]
                            summon = SUMMONS.get(key)
                            name = summon.name if summon else key.title()
                            yield Static(f"[bold {color}]{name}[/bold {color}]", classes="joy-effects-header")
                            for s in spells:
                                yield Static(f"[bold]{s.name}[/bold] — {s.visual or '(no visual set)'}", classes="joy-effects-line")
                with TabPane("⌨️ Hotkeys", id="tab-hotkeys"):
                    with ScrollableContainer(id="joy-hotkeys-list"):
                        yield Static(
                            "[dim]System-wide (AutoHotkey) - work from any app, not just this TUI:[/dim]",
                            classes="joy-hotkeys-note",
                        )
                        for combo, spell_name in self.GLOBAL_HOTKEYS:
                            yield Static(f"  [bold #ccff00]{combo:<18}[/bold #ccff00] {spell_name}", classes="joy-hotkeys-line")
            yield Button("💀 FATALITY — Summon The Whole Team", id="joy-fatality-btn")
            yield Static("Esc to close", id="joy-hint")

    def _buttons_for(self, key: str) -> list[tuple[str, str]]:
        spells = [s for s in ALL_SPELLS if s.companion == key]
        if spells:
            out = []
            for s in spells:
                letter = s.hotkey.replace("Ctrl+Alt+Shift+", "").replace("Ctrl+Alt+", "").replace("Alt+", "")
                out.append((f"{letter}  {s.name}", f"joy__{key}__{s.key}"))
            return out
        if key == "turtle":
            return [("🛡️ Toggle Auto-Approval", "joy__turtle__toggle")]
        if key == "mephisto":
            return [("🚨 Alerts", "joy__mephisto__alerts"), ("📡 Signals", "joy__mephisto__signals")]
        return []

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "joy-fatality-btn":
            # Close the pad first - the whole point is friends watching
            # the roster chain-flash and the FATALITY banner, which the
            # joystick overlay would otherwise sit on top of.
            self.dismiss(None)
            self.app.summon_all_companions()
            return
        if not btn_id.startswith("joy__"):
            return
        _, key, action = btn_id.split("__", 2)
        self._press_fx(key, event.button)
        self.app.cast_macro(key, action)

    def _press_fx(self, key: str, button: Button) -> None:
        """The button visibly 'presses' and its whole cell highlights -
        real gamepad-press feedback, not just a log line appearing."""
        cell = self.query_one(f"#joy-cell-{key}")
        cell.add_class("flash")
        button.add_class("-active")
        self.set_timer(0.25, lambda: (cell.remove_class("flash"), button.remove_class("-active")))

    def action_cancel(self) -> None:
        self.dismiss(None)


class DeckScreen(ModalScreen[None]):
    """The full DJ deck, same idea as JoystickScreen for pads: a bigger
    dedicated view instead of the compact always-on sidebar mini-deck.
    Translucent like the pad modal so the chat log stays visible behind it -
    "attached to the chat" rather than replacing it. All buttons call the
    app's real action_dj_* methods, same ones the F5-F10 keys and the top
    toolbar already use - no separate/duplicate playback logic."""

    DEFAULT_CSS = """
    DeckScreen {
        align: center middle;
        background: #000000 30%;
    }
    #deck-box {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 22;
        background: #0a0003 55%;
        border: heavy #F8F9FA;
        padding: 1;
    }
    #deck-title {
        height: 1;
        color: #F8F9FA;
        text-style: bold;
        padding-bottom: 1;
        content-align: center middle;
    }
    #deck-now-playing {
        height: 2;
        color: #ff3366;
        text-style: bold;
        content-align: center middle;
        border: round #00F2FE;
        margin-bottom: 1;
    }
    #deck-link-row {
        height: 3;
        layout: horizontal;
        margin-bottom: 1;
    }
    #deck-link-input {
        width: 1fr;
        height: 3;
        background: #140005 60%;
        color: #F8F9FA;
        border: solid #00F2FE;
    }
    #deck-transport-row {
        height: 3;
        layout: horizontal;
        margin-bottom: 1;
    }
    #deck-extra-row {
        height: 3;
        layout: horizontal;
        margin-bottom: 1;
    }
    .deck-btn {
        width: 1fr;
        height: 3;
        min-width: 8;
        background: #200008 70%;
        color: #F8F9FA;
        border: heavy #00F2FE;
        text-style: bold;
        margin-right: 1;
    }
    .deck-btn:hover { background: #00F2FE; color: #F8F9FA; }
    #deck-volume-display {
        width: 8;
        content-align: center middle;
        text-style: bold;
        color: #00F2FE;
    }
    #deck-hint {
        height: 1;
        color: #64748b;
        content-align: center middle;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Close", show=True)]

    def compose(self) -> ComposeResult:
        with Vertical(id="deck-box"):
            yield Static("🎛️ MEPHISSA'S DECK", id="deck-title")
            yield Static("🎵 (loading...)", id="deck-now-playing")
            with Horizontal(id="deck-link-row"):
                yield Input(placeholder="🔗 paste a link to play it...", id="deck-link-input")
                yield Button("🔴 LINK", id="deck-btn-link", classes="deck-btn")
            with Horizontal(id="deck-transport-row"):
                yield Button("◀◀", id="deck-btn-seekback", classes="deck-btn")
                yield Button("▶ / ⏸", id="deck-btn-playpause", classes="deck-btn")
                yield Button("⏹", id="deck-btn-stop", classes="deck-btn")
                yield Button("▶▶", id="deck-btn-skip", classes="deck-btn")
            with Horizontal(id="deck-extra-row"):
                yield Button("− VOL", id="deck-btn-volume-down", classes="deck-btn")
                yield Static("100%", id="deck-volume-display")
                yield Button("+ VOL", id="deck-btn-volume-up", classes="deck-btn")
                yield Button("🔁 MIX", id="deck-btn-mix", classes="deck-btn")
                yield Button("🌐 EN/AR", id="deck-btn-lang", classes="deck-btn")
                yield Button("🔄 Transition", id="deck-btn-transition", classes="deck-btn")
            yield Static("Esc to close - chat stays live behind this", id="deck-hint")

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(1.0, self._refresh)

    def _refresh(self) -> None:
        try:
            status = cli.meph_dj_status()
            self.query_one("#deck-now-playing", Static).update(no_wrap_text(f"🎵 {status}"))
            self.query_one("#deck-volume-display", Static).update(f"{cli.meph_dj_volume_level()}%")
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "deck-link-input":
            self._play_link(event.input)

    def _play_link(self, link_input: Input) -> None:
        link = link_input.value.strip()
        link_input.value = ""
        if link:
            self.app.action_dj_play_link(link)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "deck-btn-link":
            self._play_link(self.query_one("#deck-link-input", Input))
        elif btn_id == "deck-btn-seekback":
            self.app.action_dj_seek_back()
        elif btn_id == "deck-btn-playpause":
            self.app.action_dj_playpause()
        elif btn_id == "deck-btn-stop":
            self.app.action_dj_stop()
        elif btn_id == "deck-btn-skip":
            self.app.action_dj_skip()
        elif btn_id == "deck-btn-volume-down":
            self.app.action_dj_volume_down()
        elif btn_id == "deck-btn-volume-up":
            self.app.action_dj_volume_up()
        elif btn_id == "deck-btn-mix":
            self.app.action_dj_toggle_mix()
        elif btn_id == "deck-btn-lang":
            self.app.action_dj_toggle_lang()
        elif btn_id == "deck-btn-transition":
            self.app.action_dj_transition()
        self._refresh()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ModePickerScreen(ModalScreen[str]):
    """Inbound / Outbound / Both — how a summoned companion's hook output
    reaches you: Outbound prints to the terminal only (what Zouzou/Mephissa
    do today), Inbound gets injected into the model's own context via
    additionalContext, Both does both at once. See companion_kit/emit.py."""

    MODES = [
        ("outbound", "📤 Outbound", "print to terminal only — you see it, the model doesn't (today's default)"),
        ("inbound", "📥 Inbound", "injected into the model's context via additionalContext — the model reads it too"),
        ("both", "🔁 Both", "printed to the terminal AND injected into context, at the same time"),
    ]

    DEFAULT_CSS = """
    ModePickerScreen {
        align: center middle;
        background: #000000 30%;
    }
    #mode-picker-box {
        width: 60;
        height: auto;
        max-height: 16;
        background: #0a0003;
        border: heavy #00F2FE;
        padding: 1;
    }
    #mode-picker-title {
        height: 1;
        color: #F8F9FA;
        text-style: bold;
        padding-bottom: 1;
    }
    #mode-picker-list {
        height: auto;
        max-height: 10;
        background: #000000;
        border: round #a78bfa;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(self, companion_name: str, current: str | None = None) -> None:
        super().__init__()
        self._companion_name = companion_name
        self._current = current or "outbound"

    def compose(self) -> ComposeResult:
        with Vertical(id="mode-picker-box"):
            yield Static(f"⚡ {self._companion_name} — pick a hook mode (Esc to cancel)", id="mode-picker-title")
            yield OptionList(
                *[Option(f"{label}\n  {desc}", id=key) for key, label, desc in self.MODES],
                id="mode-picker-list",
            )

    def on_mount(self) -> None:
        option_list = self.query_one("#mode-picker-list", OptionList)
        option_list.focus()
        keys = [m[0] for m in self.MODES]
        if self._current in keys:
            option_list.highlighted = keys.index(self._current)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self.dismiss(event.option_id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SessionPickerScreen(ModalScreen[str]):
    """/sessions — same Kilo-Code-style full-window picker as ModelPickerScreen,
    but over persistent conversation threads (create/switch/resume) instead of
    agents. "+ New session" always sorts first."""

    NEW_SESSION_ID = "__new__"

    DEFAULT_CSS = """
    SessionPickerScreen {
        align: center middle;
        background: #000000 30%;
    }
    #session-picker-box {
        width: 80%;
        max-width: 100;
        height: 80%;
        max-height: 40;
        background: #0a0003;
        border: heavy #00F2FE;
        padding: 1;
    }
    #session-picker-title {
        height: 1;
        color: #F8F9FA;
        text-style: bold;
        padding-bottom: 1;
    }
    #session-picker-search {
        height: 3;
        border: round #a78bfa;
        background: #140005;
        color: #F8F9FA;
    }
    #session-picker-list {
        height: 1fr;
        background: #000000;
        border: round #00F2FE;
        margin-top: 1;
    }
    #session-picker-hint {
        height: 1;
        color: #64748b;
        padding-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(self, sessions: list[dict], current_session_id: str) -> None:
        super().__init__()
        self._sessions = sessions
        self._current_session_id = current_session_id

    def _row_label(self, s: dict) -> str:
        marker = "● " if s["id"] == self._current_session_id else "  "
        name = s.get("name") or "(empty session)"
        provider = s.get("provider") or "?"
        count = s.get("msgs", 0)
        updated = (s.get("updated") or "")[:16].replace("T", " ")
        return f"{marker}{name}  ·  {provider}  ·  {count} msgs  ·  {updated}"

    def compose(self) -> ComposeResult:
        with Vertical(id="session-picker-box"):
            yield Static("💾 Sessions  —  type to filter, Enter to switch, Esc to cancel", id="session-picker-title")
            yield Input(placeholder="Filter sessions...", id="session-picker-search")
            yield OptionList(id="session-picker-list")
            yield Static("", id="session-picker-hint")

    def on_mount(self) -> None:
        self._populate("")
        self.query_one("#session-picker-search", Input).focus()

    def _populate(self, query: str) -> None:
        option_list = self.query_one("#session-picker-list", OptionList)
        option_list.clear_options()
        query = query.strip().lower()
        option_list.add_option(Option("＋ New session", id=self.NEW_SESSION_ID))
        shown = 0
        for s in self._sessions:
            haystack = f"{s.get('name', '')} {s.get('provider', '')}".lower()
            if query and query not in haystack:
                continue
            option_list.add_option(Option(self._row_label(s), id=s["id"]))
            shown += 1
        self.query_one("#session-picker-hint", Static).update(f"{shown} session(s)")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "session-picker-search":
            self._populate(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "session-picker-search":
            return
        option_list = self.query_one("#session-picker-list", OptionList)
        if option_list.option_count:
            option = option_list.get_option_at_index(0)
            if option.id:
                self.dismiss(option.id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self.dismiss(event.option_id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PermissionPromptScreen(ModalScreen[str]):
    """The opencode-style tool-permission window (Allow Once / Always Allow /
    Deny), rebuilt as a real Textual modal instead of cli.py's raw print()+
    input() terminal prompt - that call blocks on stdin from a worker thread
    while Textual owns the terminal in raw mode, so it can never actually be
    seen or answered from inside switch-tui. This is what the background
    worker now waits on via a threading.Event bridge (see
    NewMetaTui.sync_request_permission)."""

    DEFAULT_CSS = """
    PermissionPromptScreen {
        align: center middle;
        background: #000000 30%;
    }
    #perm-box {
        width: 70%;
        max-width: 90;
        height: auto;
        background: #0a0003;
        border: heavy #ffb000;
        padding: 1 2;
    }
    #perm-title {
        height: 1;
        color: #ffb000;
        text-style: bold;
        padding-bottom: 1;
    }
    #perm-target {
        height: auto;
        color: #F8F9FA;
        padding-bottom: 1;
    }
    #perm-buttons {
        height: 3;
        align: center middle;
    }
    #perm-buttons Button { margin: 0 1; min-width: 14; }
    #perm-btn-once { background: #00F2FE; color: #000000; }
    #perm-btn-always { background: #22c55e; color: #000000; }
    #perm-btn-deny { background: #ff2a2a; color: #F8F9FA; }
    """

    BINDINGS = [
        Binding("escape", "deny", "Deny", show=True),
        Binding("o", "once", "Allow once", show=True),
        Binding("a", "always", "Always allow", show=True),
        Binding("d", "deny", "Deny", show=True),
    ]

    def __init__(self, tool_name: str, target: str) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._target = target

    def compose(self) -> ComposeResult:
        with Vertical(id="perm-box"):
            yield Static(f"🔐 Permission requested — {self._tool_name}", id="perm-title")
            yield Static(f"target: {self._target or '(none)'}", id="perm-target")
            with Horizontal(id="perm-buttons"):
                yield Button("Allow once (O)", id="perm-btn-once")
                yield Button("Always allow (A)", id="perm-btn-always")
                yield Button("Deny (D/Esc)", id="perm-btn-deny")

    def on_mount(self) -> None:
        self.query_one("#perm-btn-once", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {"perm-btn-once": "once", "perm-btn-always": "always", "perm-btn-deny": "deny"}
        answer = mapping.get(event.button.id or "")
        if answer:
            self.dismiss(answer)

    def action_once(self) -> None:
        self.dismiss("once")

    def action_always(self) -> None:
        self.dismiss("always")

    def action_deny(self) -> None:
        self.dismiss("deny")


class BoardScreen(ModalScreen[None]):
    """/board - a real interactive Trello-style widget over the persistent
    project_board.json (companion_kit/board.py), replacing the old one-shot
    text dump into the scrollback log. Three live columns (Backlog / In
    Progress / Done); b/i/d moves the highlighted card between them, r
    reloads from disk so cards added from anywhere (another session, the
    plain `board.py add ...` CLI) show up without reopening the screen."""

    DEFAULT_CSS = """
    BoardScreen {
        align: center middle;
        background: #000000 30%;
    }
    #board-box {
        width: 94%;
        max-width: 160;
        height: 88%;
        max-height: 50;
        background: #0a0003;
        border: heavy #00F2FE;
        padding: 1;
    }
    #board-title {
        height: 1;
        color: #F8F9FA;
        text-style: bold;
        padding-bottom: 1;
    }
    #board-columns {
        height: 1fr;
    }
    .board-col {
        width: 1fr;
        height: 1fr;
        margin: 0 1 0 0;
    }
    .board-col-title {
        height: 1;
        padding: 0 1;
    }
    .board-col-list {
        height: 1fr;
        background: #000000;
        border: round #00F2FE;
    }
    #board-detail {
        height: 5;
        border: round #a78bfa;
        background: #140005;
        color: #F8F9FA;
        padding: 0 1;
        margin-top: 1;
    }
    #board-hint {
        height: 1;
        color: #64748b;
        padding-top: 1;
    }
    #board-pulse-title {
        height: 1;
        padding: 0 1;
        margin-top: 1;
    }
    #board-pulse {
        height: 11;
        border: round #eab308;
        background: #000000;
        scrollbar-color: #eab308;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("b", "move_backlog", "-> Backlog", show=True),
        Binding("i", "move_inprogress", "-> In Progress", show=True),
        Binding("d", "move_done", "-> Done", show=True),
    ]

    COLUMN_IDS = {"backlog": "board-col-backlog", "in_progress": "board-col-inprogress", "done": "board-col-done"}
    COLUMN_ACCENTS = {"backlog": "#ffb000", "in_progress": "#00F2FE", "done": "#22c55e"}

    def __init__(self) -> None:
        super().__init__()
        self._focused_column = "backlog"
        self._cards_by_id: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="board-box"):
            yield Static("📌 Project Board  —  b/i/d moves highlighted card, r refresh, Esc close", id="board-title")
            with Horizontal(id="board-columns"):
                for col in board.COLUMNS:
                    with Vertical(classes="board-col"):
                        yield Static(board.COLUMN_LABELS[col], id=f"{self.COLUMN_IDS[col]}-title", classes="board-col-title")
                        yield OptionList(id=self.COLUMN_IDS[col], classes="board-col-list")
            yield Static("", id="board-detail")
            yield Static("", id="board-hint")
            # MARKET PULSE - real live data, not fabricated. Reuses the same
            # institutional alert engine already wired up behind /mephisto
            # (pika_mephisto_alert_selector.format_mephisto_alerts_report):
            # 8 confluence rules (SEPA, Cheat/Low-Cheat, Tennis Ball vs Egg,
            # Power Plays/3WT, Unicorn ICT, Umbrella Linda, Daily Close,
            # On-Gravity) scored against live Binance data, with a bull/bear
            # radar and a real BTC price+RSI read. That function already
            # honestly tags its own output "LIVE" vs "OFFLINE (fallback demo
            # data)" when Binance is unreachable - this panel just displays
            # whatever it reports, live or labeled-fallback, never inventing
            # numbers of its own.
            yield Static("[bold #eab308]MARKET PULSE[/bold #eab308]", id="board-pulse-title", classes="board-col-title")
            yield RichLog(id="board-pulse", markup=True, wrap=True, highlight=False)

    def on_mount(self) -> None:
        self._populate()
        try:
            self.query_one(f"#{self.COLUMN_IDS['backlog']}", OptionList).focus()
        except Exception:
            pass
        self._load_market_pulse()

    @work(thread=True)
    def _load_market_pulse(self) -> None:
        """Fetches the real Mephisto institutional alert report off the UI
        thread (same anti-freeze pattern as show_mephisto_alerts() elsewhere
        in this file - these are live network calls to Binance/DexScreener)
        and writes it into the #board-pulse panel once it lands."""
        try:
            from pika_mephisto_alert_selector import format_mephisto_alerts_report
            report = format_mephisto_alerts_report()
            pulse_lines = [f"[bold]{report['title']}[/bold]", ""] + list(report["lines"])
        except Exception as e:
            pulse_lines = [f"[red]Market pulse unavailable: {e}[/red]"]

        def _write() -> None:
            try:
                log = self.query_one("#board-pulse", RichLog)
                log.clear()
                for line in pulse_lines:
                    log.write(line)
            except Exception:
                pass

        self.call_from_thread(_write)

    def _populate(self) -> None:
        data = board.load_board()
        by_col: dict[str, list] = {c: [] for c in board.COLUMNS}
        for card in data.get("cards", []):
            by_col.setdefault(card.get("column", "backlog"), []).append(card)
        self._cards_by_id = {c["id"]: c for c in data.get("cards", [])}
        for col in board.COLUMNS:
            cards = sorted(by_col.get(col, []), key=lambda c: c.get("updated", 0), reverse=True)
            option_list = self.query_one(f"#{self.COLUMN_IDS[col]}", OptionList)
            option_list.clear_options()
            for c in cards:
                option_list.add_option(Option(f"[{c['id']}] {c['title']}", id=c["id"]))
            if cards:
                # ROOT-CAUSE FIX (empty IN PROGRESS column bug): OptionList's
                # `highlighted` index starts at None and Textual only sets it
                # in response to an actual up/down keypress or mouse click -
                # merely focusing a freshly-populated list (what on_mount()
                # and _move_highlighted()'s post-move .focus() call do) never
                # fires that. That meant the very first b/i/d press after
                # opening the board - the single most natural first action -
                # hit `if option_list.highlighted is None: return` in
                # _move_highlighted() and silently did nothing: no card ever
                # moved, no error shown, so IN PROGRESS looked permanently
                # empty even after the user tried to put something there.
                # Pre-selecting the top card here means there is always a
                # valid highlighted card the instant a column has any.
                option_list.highlighted = 0
            accent = self.COLUMN_ACCENTS[col]
            title_widget = self.query_one(f"#{self.COLUMN_IDS[col]}-title", Static)
            title_widget.update(f"[bold {accent}]{board.COLUMN_LABELS[col]} ({len(cards)})[/bold {accent}]")
        self.query_one("#board-hint", Static).update(f"{len(self._cards_by_id)} card(s) total")

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        for col, wid in self.COLUMN_IDS.items():
            if event.option_list.id == wid:
                self._focused_column = col
                break
        card = self._cards_by_id.get(event.option_id) if event.option_id else None
        detail = self.query_one("#board-detail", Static)
        if card:
            notes = card.get("notes") or "(no notes)"
            detail.update(f"[bold]{card['title']}[/bold]\n{notes}")
        else:
            detail.update("")

    def _move_highlighted(self, target_col: str) -> None:
        # Prefer whichever OptionList Textual itself says has keyboard focus
        # right now over the self._focused_column cache: that cache is only
        # updated by on_option_list_option_highlighted(), which (per the
        # _populate() fix above) fires reliably now, but falling back to the
        # cache still protects against any future path that moves focus
        # without an OptionHighlighted event.
        source_col = self._focused_column
        for col, wid in self.COLUMN_IDS.items():
            try:
                if self.query_one(f"#{wid}", OptionList) is self.focused:
                    source_col = col
                    break
            except Exception:
                pass
        hint = self.query_one("#board-hint", Static)
        try:
            option_list = self.query_one(f"#{self.COLUMN_IDS[source_col]}", OptionList)
        except Exception:
            hint.update("[red]Couldn't find the source column.[/red]")
            return
        if option_list.highlighted is None:
            hint.update(f"[yellow]{board.COLUMN_LABELS[source_col]} is empty - nothing to move.[/yellow]")
            return
        option = option_list.get_option_at_index(option_list.highlighted)
        if not option or not option.id:
            hint.update("[yellow]No card selected.[/yellow]")
            return
        board.move_card(option.id, target_col)
        self._focused_column = target_col
        self._populate()
        try:
            self.query_one(f"#{self.COLUMN_IDS[target_col]}", OptionList).focus()
        except Exception:
            pass

    def action_move_backlog(self) -> None:
        self._move_highlighted("backlog")

    def action_move_inprogress(self) -> None:
        self._move_highlighted("in_progress")

    def action_move_done(self) -> None:
        self._move_highlighted("done")

    def action_refresh(self) -> None:
        self._populate()
        self._load_market_pulse()

    def action_cancel(self) -> None:
        self.dismiss(None)


class NewMetaTui(App):
    TITLE = "Switch"

    CSS = """
    Screen {
        layout: vertical;
        background: #000000;
    }

    /* Modern app title bar - was the plain default Header */
    Header {
        background: #0a0003;
        color: #00F2FE;
        text-style: bold;
        border-bottom: heavy #ff023a;
    }
    HeaderTitle {
        text-style: bold;
        color: #F8F9FA;
    }

    /* Footer replaced by NeonFooter (own DEFAULT_CSS on the widget itself) -
       discrete bordered boxes, name-over-shortcut, outline-only hover. */

    /* CODING THEME (Full Dark Black + Neon Red) */
    Screen.theme-coding {
        background: #000000;
    }
    Screen.theme-coding #main-container, Screen.theme-coding #log-scroll, Screen.theme-coding #agent-log {
        background: #000000;
    }
    Screen.theme-coding #sidebar {
        background: #050505;
        border: round #ff023a;
    }
    Screen.theme-coding #log-scroll {
        border: round #ff023a;
        scrollbar-color: #ff023a;
    }
    /* Every other cyan border on the main (non-modal) screen swaps to neon
       red in coding mode too - was uniformly cyan regardless of theme,
       which only #prompt-container/#sidebar/#log-scroll ever followed. */
    Screen.theme-coding #header-ticker-row { border-bottom: heavy #ff023a; }
    Screen.theme-coding #dj-toolbar-top,
    Screen.theme-coding #companion-tower,
    Screen.theme-coding #sidebar-agent,
    Screen.theme-coding #dj-jog-deck,
    Screen.theme-coding #task-log,
    Screen.theme-coding #action-plan,
    Screen.theme-coding #zap-log,
    Screen.theme-coding #live-feed {
        border: round #ff023a;
    }
    Screen.theme-coding #dj-link-input { border: solid #ff023a; }
    Screen.theme-coding .knob-btn,
    Screen.theme-coding .dj-knob-btn,
    Screen.theme-coding #btn-mcp.off,
    Screen.theme-coding #btn-skills.off,
    Screen.theme-coding #btn-core.off,
    Screen.theme-coding #btn-deepsearch.off,
    Screen.theme-coding .ultimate-chip {
        border: heavy #ff023a;
    }
    Screen.theme-coding .vol-display { border-top: solid #ff023a; border-bottom: solid #ff023a; }
    Screen.theme-coding #controls { border-bottom: solid #ff023a; }

    /* TRADING THEME (Deep Navy Blue + Electric Cyan & Gold) */
    Screen.theme-trading {
        background: #040a17;
    }
    Screen.theme-trading #main-container, Screen.theme-trading #log-scroll, Screen.theme-trading #agent-log {
        background: #040a17;
    }
    Screen.theme-trading #sidebar {
        background: #0a1329;
        border: round #38bdf8;
    }
    Screen.theme-trading #log-scroll {
        border: round #38bdf8;
        scrollbar-color: #38bdf8;
    }
    Screen.theme-trading #header-ticker-row, Screen.theme-trading #dj-toolbar-top, Screen.theme-trading #controls {
        background: #071126;
        border-bottom: solid #38bdf8;
    }
    Screen.theme-trading .knob-btn {
        background: #0d1b3e;
        border: heavy #38bdf8;
        color: #facc15;
    }
    Screen.theme-trading .knob-btn:hover {
        background: #38bdf8;
        color: #040a17;
    }
    Screen.theme-trading #prompt-container {
        border: round #38bdf8;
        background: #071126;
    }
    Screen.theme-trading #prompt-send-btn {
        background: #38bdf8;
        color: #040a17;
        border: heavy #38bdf8;
    }

    #header-ticker-row {
        height: 1;
        layout: horizontal;
        background: #0d1117;
        border-bottom: heavy #00F2FE;
    }
    #price-ticker {
        width: auto;
        min-width: 30;
        color: #facc15;
        text-style: bold;
        padding: 0 2 0 1;
        content-align: left middle;
    }
    #market-pulse {
        width: auto;
        min-width: 16;
        color: #4ade80;
        text-style: bold;
        padding: 0 2;
        content-align: left middle;
    }
    #trading-setups-banner {
        width: 1fr;
        height: 1;
        color: #4ade80;
        text-style: bold;
        padding: 0 1;
        content-align: left middle;
    }
    #dj-heartbeat-header {
        width: 14;
        height: 1;
        color: #00F2FE;
        text-style: bold;
        content-align: right middle;
        padding-right: 1;
    }

    #dj-toolbar-top {
        height: 5;
        layout: horizontal;
        background: #0a0003;
        border: round #00F2FE;
        padding: 0 1;
        overflow-x: auto;
        scrollbar-size-vertical: 0;
        scrollbar-background: #000000;
        scrollbar-color: #00F2FE;
    }
    #dj-link-input {
        width: 1fr;
        height: 3;
        background: #140005;
        color: #F8F9FA;
        border: solid #00F2FE;
    }
    .knob-btn {
        width: 12;
        min-width: 10;
        height: 3;
        background: #200008;
        color: #F8F9FA;
        border: heavy #00F2FE;
        text-style: bold;
        margin-left: 1;
    }
    .knob-btn:hover {
        background: #00F2FE;
        color: #F8F9FA;
    }
    /* Enter in #dj-link-input already triggers the same play behavior
       (on_input_submitted) - this button is just a secondary/backup click
       target now, so it stays small instead of matching the other
       full-size toolbar buttons. */
    #btn-dj-play-link {
        width: 4;
        min-width: 4;
    }
    .knob-btn.on {
        background: #00F2FE;
        color: #F8F9FA;
        text-style: bold;
    }
    .knob-vol {
        width: 6;
        min-width: 6;
    }
    .vol-display {
        width: 5;
        min-width: 5;
        height: 3;
        content-align: center middle;
        color: #00F2FE;
        text-style: bold;
        background: #200008;
        border-top: solid #00F2FE;
        border-bottom: solid #00F2FE;
    }

    #controls {
        height: 3;
        layout: horizontal;
        padding: 0 1;
        background: #050505;
        border-bottom: solid #00F2FE;
        overflow-x: auto;
        overflow-y: hidden;
        scrollbar-size-horizontal: 1;
    }
    #provider-select { width: 24; margin: 0 1 0 0; }
    #mode-select { width: 13; margin: 0 1 0 0; }
    #thinking-select { width: 13; margin: 0 1 0 0; }
    SelectOverlay { background: #0a0003 70%; }
    SelectOverlay > .option-list--option-highlighted { background: #ff2fd6 40%; }
    #controls Button { height: 3; min-width: 8; margin: 0 1 0 0; }
    #btn-deepsearch { width: 15; }
    #btn-mcp { width: 11; }
    #btn-skills { width: 13; }
    #btn-core { width: 11; }
    #btn-mcp.on, #btn-skills.on, #btn-core.on, #btn-deepsearch.on { background: #00F2FE; color: #F8F9FA; text-style: bold; }
    #btn-mcp.off, #btn-skills.off, #btn-core.off, #btn-deepsearch.off { background: #1a0005; color: #94a3b8; border: heavy #00F2FE; }
    #btn-theme-picker { width: 16; margin-left: 1; }

    /* "Dead zone" to the right of the DJ toolbar, below the VOL buttons -
       Launch Agent now docks here instead of its own full-width row.
       No border here on purpose: it lives inside #dj-toolbar-top's own
       3-row interior (5 total - 2 for the toolbar's own round border), and
       a border would need 2 more rows than that budget allows - the exact
       overflow bug that made this button silently unclickable before
       (Textual let the overflow bleed into the #controls row below, so
       clicks there landed on #controls instead of this button). */
    #dj-vol-agent-zone {
        width: 26;
        height: 3;
        layout: vertical;
        background: #1a0b2e;
        margin-left: 1;
    }
    #dj-vol-row {
        height: 1;
        layout: horizontal;
        background: #1a0b2e;
    }
    #dj-vol-agent-zone .knob-btn, #dj-vol-agent-zone .vol-display {
        height: 1;
        margin-left: 0;
        border: none;
        background: #1a0b2e;
    }
    #btn-launch-agent {
        width: 100%;
        height: 1;
        margin: 0;
        background: #ccff00;
        color: #050505;
        text-style: bold;
        border: none;
    }
    #btn-launch-agent:hover {
        background: #F8F9FA;
    }

    #pika-status-row, #tip-commands-row {
        height: 1;
        layout: horizontal;
        background: #000000;
    }
    #status-bar {
        width: auto;
        min-width: 20;
        background: #050505;
        color: #7dd3fc;
        padding: 0 1;
        content-align: right middle;
        text-style: bold;
    }

    #main-container {
        height: 1fr;
        layout: horizontal;
        margin: 0 1;
        background: #000000;
    }

    #companion-tower {
        width: 24;
        height: 100%;
        border: round #00F2FE;
        background: #050505;
        margin-right: 1;
        padding: 1;
        layout: vertical;
    }

    #log-scroll {
        width: 3fr;
        height: 100%;
        border: round #00F2FE;
        background: #000000;
        scrollbar-background: #000000;
        scrollbar-color: #00F2FE;
        scrollbar-color-hover: #ff3366;
        scrollbar-color-active: #ff0055;
    }
    #agent-log {
        height: 1fr;
        color: #F8F9FA;
        background: #000000;
    }

    #sidebar {
        width: 1fr;
        height: 100%;
        border: round #00F2FE;
        background: #050505;
        margin-left: 1;
        padding: 1;
        layout: vertical;
        scrollbar-background: #000000;
        scrollbar-color: #00F2FE;
        scrollbar-color-hover: #ff3366;
        scrollbar-color-active: #ff0055;
    }

    #roster-scroll {
        height: 1fr;
        scrollbar-size-vertical: 1;
        scrollbar-background: #000000;
        scrollbar-color: #00F2FE;
        scrollbar-color-hover: #ff3366;
        scrollbar-color-active: #ff0055;
    }
    .companion-card-tall {
        height: 20;
        background: #0a0003;
        border: round #00F2FE;
        margin-bottom: 1;
    }
    /* Per-companion identity color on the resting border - was uniformly
       cyan for every card regardless of companion, no visual distinction. */
    #card-pikapoke { border: round #ccff00; }
    #mascot-zouzou { border: round #c4b5fd; margin-left: -1; }
    #card-mephissa { border: round #ff2fd6; }
    #card-turtle { border: round #38bdf8; }
    #card-mephisto { border: round #e11d48; }
    .companion-art {
        height: 10;
        content-align: center middle;
    }
    .companion-line {
        height: 1;
        content-align: center middle;
    }
    .companion-bar-vertical {
        height: 3;
        content-align: center middle;
    }
    /* Dota-ability-bar-style quick-cast row: real spells + the ultimate
       chip, last - each spell rendered as its own boxed keystroke (heavy
       border drawn by Textual, not hand-drawn ASCII, so it stays crisp at
       any terminal size), colored in the companion's own color (border +
       text set in Python per-instance, overriding the base border below);
       cyan stays reserved for the ultimate chip only. */
    .companion-macro-row {
        height: 3;
        layout: horizontal;
        margin-top: 1;
    }
    .companion-macro-btn {
        width: 3;
        min-width: 3;
        height: 3;
        border: heavy #00F2FE;
        padding: 0;
        margin: 0 1 0 0;
        content-align: center middle;
        text-style: bold;
    }
    .companion-macro-btn.-active {
        background: #F8F9FA !important;
        color: #050505 !important;
    }
    /* Icon + labeled box on its own row below the 4 spells - was the icon
       squeezed into the same row as the 4 spell boxes with no room for a
       text label, just a bare glyph. */
    .companion-ultimate-row {
        height: 3;
        layout: horizontal;
        margin-top: 1;
    }
    .ultimate-label {
        width: auto;
        min-width: 8;
        height: 3;
        border: heavy #00F2FE;
        padding: 0 1;
        margin-left: 1;
        content-align: center middle;
        text-style: bold;
    }
    /* COMPANION ZAP HIGHLIGHT FLASH EFFECT */
    .companion-card-tall.flash {
        background: #00F2FE 40% !important;
        border: thick #F8F9FA !important;
        color: #F8F9FA !important;
    }
    .companion-card-tall.selected {
        border: heavy #F8F9FA;
    }
    /* SUMMON CEREMONY: a stronger, longer white flash than the plain zap -
       reserved for the moment a companion is actually summoned (Enter on a
       selected card, or the /summon all chain). */
    .companion-card-tall.summon-flash {
        background: #F8F9FA 55% !important;
        border: thick #F8F9FA !important;
        color: #050505 !important;
    }

    .companion-foot {
        height: 1;
        layout: horizontal;
    }
    .companion-spark {
        width: 1fr;
        height: 1;
        content-align: center middle;
    }
    .ultimate-chip {
        width: 3;
        min-width: 3;
        height: 3;
        border: heavy #00F2FE;
        padding: 0;
        content-align: center middle;
        text-style: bold;
    }
    .ultimate-chip.on { background: #00F2FE; color: #F8F9FA; }
    .ultimate-chip.off { background: #200008; color: #64748b; }

    #sidebar-agent {
        height: 3;
        border: round #00F2FE;
        background: #0a0003;
        color: #F8F9FA;
        text-style: bold;
        content-align: center middle;
        margin-bottom: 0;
    }
    #dj-jog-deck {
        height: 11;
        layout: horizontal;
        margin-bottom: 1;
        border: round #00F2FE;
        background: #0a0003;
    }
    #jog-wheel-art {
        width: 14;
        height: 10;
        content-align: center middle;
        color: #00F2FE;
        text-style: bold;
    }
    #dj-controls-box {
        width: 1fr;
        height: 10;
        layout: vertical;
    }
    #dj-link-row {
        height: 3;
        margin-bottom: 1;
    }
    /* Square 2x2 transport grid instead of one cramped horizontal row of 4 -
       matches the pads-grid pattern (layout: grid on a plain container). */
    #dj-buttons-row {
        height: 5;
        layout: grid;
        grid-size: 2;
        grid-gutter: 1;
    }
    .dj-knob-btn {
        width: 1fr;
        height: 2;
        background: #1c0006;
        color: #F8F9FA;
        border: heavy #00F2FE;
        text-style: bold;
    }
    .dj-knob-btn:hover { background: #00F2FE; color: #F8F9FA; }
    #dj-now-playing {
        height: 2;
        color: #ff3366;
        padding: 0 1;
        content-align: left middle;
    }

    #task-log {
        height: 6;
        border: round #00F2FE;
        color: #cbd5e1;
        margin-bottom: 1;
        scrollbar-background: #000000;
        scrollbar-color: #00F2FE;
    }
    #action-plan {
        height: 6;
        border: round #00F2FE;
        color: #cbd5e1;
        margin-bottom: 1;
        scrollbar-background: #000000;
        scrollbar-color: #00F2FE;
    }
    #zap-log {
        height: 8;
        border: round #00F2FE;
        color: #cbd5e1;
        margin-bottom: 1;
        scrollbar-background: #000000;
        scrollbar-color: #00F2FE;
    }
    #live-feed {
        height: 1fr;
        min-height: 6;
        border: round #00F2FE;
        color: #cbd5e1;
        margin-bottom: 1;
        scrollbar-background: #000000;
        scrollbar-color: #00F2FE;
    }
    #project-progress {
        height: 1;
        color: #00F2FE;
        padding: 0 1;
        content-align: center middle;
    }

    #tip {
        width: 2fr;
        color: #94a3b8;
        height: 1;
        padding: 0 1;
        content-align: left middle;
    }
    .pika-bar {
        width: 2fr;
        color: #00F2FE;
        text-style: bold;
        background: #050505;
        padding: 0 1;
        height: 1;
    }
    .pika-sep {
        width: 1fr;
        color: #64748b;
        height: 1;
        padding: 0 1;
        content-align: right middle;
        border-bottom: solid #00F2FE;
    }

    #prompt-container {
        height: 5;
        layout: horizontal;
        margin: 0 1 0 1;
        border: round #ff2244;
        background: #0a0003;
    }
    #chat-input {
        width: 1fr;
        height: 5;
        border: none;
        background: #0a0003;
        color: #ff2244;
        text-style: bold;
        content-align: left middle;
    }
    #prompt-send-btn {
        width: 10;
        min-width: 10;
        height: 5;
        background: #00F2FE;
        color: #F8F9FA;
        border: heavy #00F2FE;
        text-style: bold;
    }
    #prompt-mic-btn {
        width: 7;
        min-width: 7;
        height: 5;
        background: #200008;
        color: #F8F9FA;
        border: heavy #ff2244;
        text-style: bold;
    }
    #prompt-cycle-btn {
        width: 9;
        min-width: 9;
        height: 5;
        background: #200008;
        color: #F8F9FA;
        border: heavy #a78bfa;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("alt+shift+v", "paste_clipboard", "📋 Paste/Attach", show=True),
        Binding("escape", "handle_esc", "⛔ Nudge/Abort", show=True),
        Binding("f5", "dj_playpause", "▶ Play/Pause", show=True),
        Binding("f6", "dj_stop", "⏹ Stop", show=True),
        Binding("f7", "dj_skip", "⏭ Skip", show=True),
        Binding("f8", "dj_seek_back", "⏪ -10s", show=True),
        Binding("f9", "dj_seek_fwd", "⏩ +10s", show=True),
        Binding("f10", "dj_transition", "🔄 Transition", show=True),
        Binding("f11", "dj_voice", "🎤 Voice", show=True),
        Binding("ctrl+up", "dj_volume_up", "🔊 Vol +5%", show=True),
        Binding("ctrl+down", "dj_volume_down", "🔉 Vol -5%", show=True),
        Binding("tab", "next_companion", "Next Companion", show=True),
        Binding("shift+tab", "prev_companion", "Prev Companion", show=False),
        Binding("enter", "summon_selected", "Summon", show=False),
        Binding("alt+m", "reopen_mode_picker", "Change Hook Mode", show=False),
    ]

    COMPANION_CARD_IDS = ["#card-turtle", "#card-pikapoke", "#mascot-zouzou", "#card-mephissa", "#card-mephisto"]
    COMPANION_KEYS = ["turtle", "pikapoke", "zouzou", "mephissa", "mephisto"]
    COMPANION_COLORS = {
        "pikapoke": "#ccff00", "turtle": "#38bdf8", "zouzou": "#c4b5fd",
        "mephissa": "#ff2fd6", "mephisto": "#e11d48",
    }
    # Turtle -> Codex CLI, wired the same way Zouzou -> Claude Code
    # (anthropic) is: summoning opens the agent picker pre-selected to this
    # provider. Auto-approval toggle still lives on its own dedicated
    # ultimate-chip button (btn-ultimate-turtle), independent of summon.
    COMPANION_PROVIDER_MAP = {"pikapoke": "gemini", "mephissa": "mephissa", "mephisto": "mephisto", "zouzou": "anthropic", "turtle": "codex"}

    # Per-companion summon flavor: (accent color, banner line). Color drives
    # the summon-flash pulse on that card - bright and clearly distinct per
    # companion (not reused from card art 1:1: Mephisto's card art is
    # crimson like PikaPoke's, which would read as the same color in a
    # fast chain-flash, so his summon color is gold instead).
    # Colors match COMPANION_COLORS - was a separate, drifted palette here
    # (pikapoke red-pink, zouzou orange, mephissa mid-purple, mephisto
    # yellow) that never agreed with the card borders/xp bars.
    SUMMON_FX = {
        "pikapoke": ("#ccff00", "🗝️ PIKA POKE MATERIALIZES FROM THE VAULT..."),
        "turtle":   ("#38bdf8", "🛡️ TURTLE RAISES ITS SHELL — GUARD MODE ENGAGED"),
        "zouzou":   ("#c4b5fd", "🎯 ZOUZOU LOCKS ON TARGET — SNIPER READY"),
        "mephissa": ("#ff2fd6", "🎧 MEPHISSA'S SIREN SONG ECHOES IN..."),
        "mephisto": ("#e11d48", "😈 MEPHISTO SLIPS THROUGH THE STATIC..."),
    }

    _TRAINER_TAGS = {
        "codex": "🛡️ Turtle's domain",
        "codexfree": "🛡️ Turtle's domain",
        "anthropic": "🔥 Zouzou's domain",
        "gemini": "🗝️ Pika Poke's domain",
        "mephissa": "📥 Mephissa's domain",
        "mephisto": "⚡ Mephisto's domain",
    }

    def __init__(self, engine_mode: str = "coding") -> None:
        super().__init__()
        self.engine_mode = "trading" if engine_mode in ("trading", "trade") else "coding"
        self.config = cli.load_config()
        self.secrets = cli.SecureStorage(cli.SECRETS_PATH)
        self.provider = None
        self.provider_label = "none"
        self.provider_key = None
        self.mode = "agent"

        # Persistent multi-turn session (backs the /sessions picker).
        self.session_manager = cli.SessionManager(cli.SESSIONS_DIR)
        self.session_id = self.session_manager.create(name="New session", provider="", system="")
        self.messages: list[dict] = []

        # Tab / Shift+Tab cycles a highlight across the companion cards.
        self._companion_selected_idx = 0
        # Prompt-bar cycle button walks ALL_SPELLS in order, one press = one
        # spell, wrapping back to 0 - same real cast_macro() every other
        # spell trigger (card row / pads / joystick) uses, just auto-picking
        # which companion+spell instead of the user picking one.
        self._spell_cycle_idx = 0

        if self.engine_mode == "coding":
            self.include_mcp = False
            self.include_skills = True
            self.include_core = True
            self.mephisto_router_enabled = False
            self._trading_setup = "💻 CODING ENGINE: Ultra-Fast Launch | File/Git/Code Skills Active | Mephissa DJ Ready 🎵"
        else:
            self.include_mcp = True
            self.include_skills = True
            self.include_core = True
            self.mephisto_router_enabled = True
            self._trading_setup = "🔔 ALERTS: SEPA 7/7 | Low-Cheat Active | Unicorn ICT | 🟢 3 BULLS: $SOL ($143.10) $PEPE $TAO | 🔴 3 BEARS: $TRX $NOT $SUI"
        
        self.mephissa_fetch_enabled = True
        self.mephisto_router_enabled = True
        self.dj_lang = "en"
        self.dj_mix_enabled = False
        self._dj_lesson_mode = 0  # cycles: DJ school -> music school -> EQ cheat-sheet
        self._heartbeat_frame = False
        self._jog_frame = 0
        self._dj_was_playing = False
        self._palette_idx = -1
        self._session_actions_done = 0
        self._session_actions_total = 0
        self.busy = False
        self.auto_approval_enabled = False
        self.pasted_attachment = ""
        self.tip_index = 0
        self._last_esc_time = 0.0

        # Market prices & MT5 Trading Setups
        self._trading_setup = "🔔 ALERTS: SEPA 7/7 | Low-Cheat Active | Unicorn ICT | 🟢 3 BULLS: $SOL ($143.10) $PEPE $TAO | 🔴 3 BEARS: $TRX $NOT $SUI"
        self._top_sweep = "n/a"

        self._zouzou_xp_hist = deque(maxlen=10)
        self._turtle_xp_hist = deque(maxlen=10)
        self._pikapoke_xp_hist = deque(maxlen=10)
        self._mephissa_xp_hist = deque(maxlen=10)
        self._mephisto_xp_hist = deque(maxlen=10)

        # MCP servers are spawned + handshaked from on_mount() instead of here,
        # in a background thread (see _start_mcp_loader) - this used to run
        # synchronously in __init__, blocking the UI from rendering at all
        # until every configured MCP server (some npx-based, slow to cold
        # start) had finished. Now the TUI appears immediately and MCP tools
        # populate a moment later.
        try:
            cli.load_skills()
        except Exception:
            pass

        self._btc_price = "loading..."
        self._gold_price = "loading..."
        self._cme_gap_txt = ""
        self._etf_flow_txt = ""
        self._stream_buffer = ""
        self._btc_pct_hist = deque(maxlen=6)
        self._price_fetcher_started = False
        self.deepsearch_enabled = False
        self.zouzou_frenzy_enabled = False
        self.pikapoke_vault_enabled = False
        self._sync_auto_approval()
        self._start_price_fetcher()
        self._mcp_loaded = False

    def _start_mcp_loader(self) -> None:
        """Spawn+handshake configured MCP servers on a background thread
        (see cli.load_mcp_servers, now also parallelized across servers) so
        the already-rendered UI doesn't wait on it. Tools populate into
        cli.TOOL_REGISTRY as they come in; the agent gracefully has no MCP
        tools yet if used in the first moment or two after launch."""
        def worker():
            try:
                registered = cli.load_mcp_servers(self.config)
            except Exception as e:
                registered = []
                self.call_from_thread(self.log_line, f"[bold red]❌ MCP load error:[/bold red] {e}")
            self._mcp_loaded = True
            if registered:
                self.call_from_thread(self.log_line, f"[dim]🔌 MCP ready: {len(registered)} tool(s)[/dim]")
        threading.Thread(target=worker, daemon=True).start()

    def _start_price_fetcher(self) -> None:
        # Called from __init__ and again when switching into trading mode -
        # only ever run one polling thread regardless of how many times
        # this is invoked.
        if self._price_fetcher_started:
            return
        self._price_fetcher_started = True

        def fetch_worker():
            last_btc = None
            while True:
                try:
                    req = urllib.request.Request("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = json.loads(resp.read().decode())
                        price = float(data.get("price", 97420))
                        if last_btc is not None and last_btc:
                            self._btc_pct_hist.append((price - last_btc) / last_btc * 100.0)
                        last_btc = price
                        self._btc_price = f"${price:,.2f} ▲"
                except Exception:
                    pass
                try:
                    # PAXG (tokenized gold) as a free, no-key live gold proxy
                    req = urllib.request.Request("https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT", headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = json.loads(resp.read().decode())
                        price = float(data.get("price", 0))
                        if price:
                            self._gold_price = f"${price:,.2f}/oz"
                except Exception:
                    pass
                try:
                    # Highest-momentum coin from Mephisto's cached signal engine
                    from mephisto_signals import get_top_sweep_coin
                    self._top_sweep = get_top_sweep_coin()
                except Exception:
                    pass
                try:
                    from mephisto_signals import get_cme_gap
                    gap = get_cme_gap()
                    if gap.get("ok"):
                        arrow = "▲" if gap["direction"] == "gap_up" else ("▼" if gap["direction"] == "gap_down" else "—")
                        self._cme_gap_txt = f"{arrow} {gap['gap_pct']:+.2f}%"
                except Exception:
                    pass
                try:
                    from mephisto_signals import get_btc_etf_flow
                    flow = get_btc_etf_flow()
                    if flow.get("ok"):
                        m = flow["daily_net_inflow"] / 1_000_000
                        arrow = "▲" if m > 0 else ("▼" if m < 0 else "—")
                        self._etf_flow_txt = f"{arrow} ${m:+.1f}M"
                except Exception:
                    pass
                time.sleep(30)
        t = threading.Thread(target=fetch_worker, daemon=True)
        t.start()

    def _market_pulse_text(self) -> str:
        """Derived from real recent BTC price deltas (not fabricated) - a
        quick read on how choppy the last few price ticks have been."""
        hist = list(getattr(self, "_btc_pct_hist", []) or [])
        if not hist:
            return "💓 PULSE: warming up..."
        swing = max(abs(v) for v in hist)
        latest = hist[-1]
        arrow = "▲" if latest >= 0 else "▼"
        if swing < 0.05:
            label, color = "CALM", "#4ade80"
        elif swing < 0.3:
            label, color = "ACTIVE", "#facc15"
        else:
            label, color = "VOLATILE", "#ff2244"
        return f"[{color}]💓 PULSE: {label} {arrow}[/{color}]"

    def _live_trading_setup(self) -> str:
        """Live Minervini/ICT alert banner via the real confluence engine."""
        try:
            from pika_mephisto_alert_selector import format_mephisto_trading_setup
            return format_mephisto_trading_setup()
        except Exception:
            return "🔔 ALERTS: SEPA 7/7 | Low-Cheat Active | Unicorn ICT | 🟢 3 BULLS: $SOL $PEPE $TAO | 🔴 3 BEARS: $TRX $NOT $SUI"

    def _refresh_trading_setup(self) -> None:
        try:
            if self.engine_mode != "trading":
                return
            self._trading_setup = self._live_trading_setup()
            self.query_one("#trading-setups-banner", Static).update(no_wrap_text(self._trading_setup))
        except Exception:
            pass

    _COMPANION_LABELS = {
        "#card-pikapoke": "Pika Poke",
        "#card-turtle": "Turtle",
        "#mascot-zouzou": "Zouzou",
        "#card-mephissa": "Mephissa",
        "#card-mephisto": "Mephisto",
    }

    def _live_feed(self, companion_id: str, action: str) -> None:
        """Write one timestamped line to the sidebar LIVE FEED panel. Skips
        writing if it's the exact same (companion, action) as the last line
        within the last 3s - a safety net against any future periodic
        caller spamming the feed with identical repeats, on top of specific
        fixes like the DJ heartbeat's own dedup."""
        now = time.time()
        last = getattr(self, "_last_feed_entry", None)
        if last and last[0] == companion_id and last[1] == action and now - last[2] < 3.0:
            return
        self._last_feed_entry = (companion_id, action, now)
        try:
            label = self._COMPANION_LABELS.get(companion_id, companion_id.lstrip('#'))
            feed = self.query_one("#live-feed", RichLog)
            feed.write(f"[dim]{time.strftime('%H:%M:%S')}[/dim] [{label}] {action}")
        except Exception:
            pass

    def zap_companion(self, companion_id: str, action: str = "⚡ active") -> None:
        """ZAP HIGHLIGHT FLASH EFFECT: Triggers bright highlight zap animation on card."""
        self._flash_card_only(companion_id)
        self._live_feed(companion_id, action)

    def _flash_card_only(self, companion_id: str) -> None:
        """Same border pulse as zap_companion, no live-feed log line - for
        things that repeat on their own timer (like the DJ heartbeat) where
        the visual is worth keeping but a log entry every cycle isn't."""
        try:
            card = self.query_one(companion_id)
            card.add_class("flash")
            self.set_timer(0.8, lambda: card.remove_class("flash"))
        except Exception:
            pass

    def summon_fx(self, key: str) -> None:
        """The ceremonial version of zap_companion, reserved for the moment a
        companion is actually summoned: a 2-beat pulse (white flash -> that
        companion's own accent color) on its card, plus a themed banner line
        in the chat log and live feed. Distinct from the plain zap used for
        every other minor action, so summoning specifically feels special."""
        if key not in self.COMPANION_KEYS:
            return
        card_id = self.COMPANION_CARD_IDS[self.COMPANION_KEYS.index(key)]
        color, banner = self.SUMMON_FX.get(key, ("#F8F9FA", f"✨ {key.title()} summoned"))
        try:
            card = self.query_one(card_id)
            card.add_class("summon-flash")

            def beat2() -> None:
                try:
                    card.styles.border = ("thick", color)
                except Exception:
                    pass

            def settle() -> None:
                try:
                    card.remove_class("summon-flash")
                    self._refresh_companion_selection()
                except Exception:
                    pass

            self.set_timer(0.18, beat2)
            self.set_timer(0.55, settle)
        except Exception:
            pass
        self.log_line(f"[bold {color}]{banner}[/bold {color}]")
        self._live_feed(card_id, "⚡ SUMMONED")

    def summon_all_companions(self) -> None:
        """FATALITY: chain-summons every companion in sequence (each gets its
        own summon_fx pulse, staggered ~0.28s apart) then flashes a big
        finale banner. This is a visual/state activation ceremony - it does
        NOT open five sequential agent-picker modals (that would be a UX
        ambush, not a fatality); reassign any companion's actual agent
        afterward the normal way, Tab + Enter on that one card."""
        prev_idx = self._companion_selected_idx
        delay = 0.0
        for i, key in enumerate(self.COMPANION_KEYS):
            def fire(k=key, idx=i) -> None:
                self._companion_selected_idx = idx
                self._refresh_companion_selection()
                self.summon_fx(k)
            self.set_timer(delay, fire)
            delay += 0.28

        def finale() -> None:
            self._companion_selected_idx = prev_idx
            self._refresh_companion_selection()
            self.log_line("")
            self.log_line("[bold #ff023a]  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄[/bold #ff023a]")
            self.log_line("[bold #ff023a]     F  A  T  A  L  I  T  Y[/bold #ff023a]")
            self.log_line("[bold #ff023a]  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄[/bold #ff023a]")
            self.log_line("[dim]  the whole team has been summoned[/dim]")

        self.set_timer(delay + 0.3, finale)

    def announce_cast(self, key: str, action: str) -> None:
        """Shows the real effect name in the sidebar pad panel the instant a
        spell is cast, from any of the three places that can trigger one
        (card macro row, sidebar pads, or the Joystick modal) - was only
        ever a line buried in the center chat log before."""
        name = action
        for glyph, act, full_name in spell_slots_for(key, limit=None):
            if act == action:
                name = full_name
                break
        card_id = {"turtle": "#card-turtle", "zouzou": "#mascot-zouzou"}.get(key, f"#card-{key}")
        self._live_feed(card_id, f"🔥 cast {name}")

    def cast_macro(self, key: str, action: str) -> None:
        """Real execution behind a JoystickScreen button press. Turtle/
        Mephisto route to their real existing actions; Zouzou's
        no-argument tricks launch their real script directly; everyone
        else (PikaPoke, Mephissa, and any Zouzou spell without a script
        entry) goes through the same `python -m spellbook cast` the AHK
        hotkey layer already uses - identical XP/state either way, just a
        different button pressing it."""
        self.announce_cast(key, action)
        if key == "turtle" and action == "toggle":
            self.auto_approval_enabled = not self.auto_approval_enabled
            self._sync_auto_approval()
            self.log_line(f"[#00eaff]🛡️ Turtle auto-approval: {'ON' if self.auto_approval_enabled else 'OFF'}[/#00eaff]")
            return
        if key == "turtle" and action == "lockdown":
            for tool_name in getattr(cli, "_MUTATING_TOOLS", {}):
                cli._permission_manage("deny", tool_name)
            self.auto_approval_enabled = False
            self._sync_buttons()
            self.log_line("[#00eaff]🛡️ Turtle Shell Lockdown: every mutating tool denied[/#00eaff]")
            return
        if key == "turtle" and action == "reset":
            for tool_name in getattr(cli, "_MUTATING_TOOLS", {}):
                cli._permission_manage("ask", tool_name)
            self.auto_approval_enabled = False
            self._sync_buttons()
            self.log_line("[#00eaff]🛡️ Turtle Shell Reset: every mutating tool back to 'ask'[/#00eaff]")
            return
        if key == "turtle" and action == "status":
            self.log_line(f"[#00eaff]🛡️ {cli._permission_status()}[/#00eaff]")
            return
        if key == "mephisto" and action == "alerts":
            self.show_mephisto_alerts()
            return
        if key == "mephisto" and action == "signals":
            self.show_mephisto_tweets()
            return
        if key == "mephisto" and action == "setup":
            self.log_line(f"[#e11d48]👹 {self._live_trading_setup()}[/#e11d48]")
            return
        if key == "mephisto" and action == "prices":
            self.log_line(
                f"[#e11d48]👹 🪙 BTC: {self._btc_price}"
                + (f"  ⚡ CME GAP {self._cme_gap_txt}" if self._cme_gap_txt else "")
                + (f"  💎 ETF FLOW {self._etf_flow_txt}" if self._etf_flow_txt else "")
                + f"  🧈 GOLD: {self._gold_price}  🌊 SWEEP: {self._top_sweep}[/#e11d48]"
            )
            return
        if key == "zouzou" and action in JoystickScreen.ZOUZOU_SCRIPTS:
            script = Path.home() / ".claude" / "zouzou" / JoystickScreen.ZOUZOU_SCRIPTS[action]
            try:
                subprocess.Popen([sys.executable, str(script)])
                self.log_line(f"[#a8ab9f]🎯 Zouzou cast: {action}[/#a8ab9f]")
            except Exception as e:
                self.log_line(f"[red]cast failed: {e}[/red]")
            return
        self._cast_spell_worker(key, action)

    @work(thread=True)
    def _cast_spell_worker(self, key: str, action: str) -> None:
        # `python -m spellbook cast` spawns a whole new interpreter and was
        # running via subprocess.run() straight on the UI thread with a 15s
        # timeout - every spell/pad press froze the entire app (not just
        # this button) for however long that process took to start + run.
        try:
            spellbook_dir = Path.home() / "Desktop" / "pika-poke"
            result = subprocess.run(
                [sys.executable, "-m", "spellbook", "cast", action, "--summon", key, "--ninja"],
                cwd=str(spellbook_dir), capture_output=True, text=True, timeout=15,
            )
            data = json.loads(result.stdout.strip()) if result.stdout.strip() else {}
            color = JoystickScreen.COLORS.get(key, "#F8F9FA")
            if "error" in data:
                self.call_from_thread(self.log_line, f"[red]⚠ {data['error']}[/red]")
            else:
                self.call_from_thread(self.log_line, f"[{color}]✨ {data.get('cast', action)} — power {data.get('total_power', '?')}, xp {data.get('xp', '?')}[/{color}]")
        except Exception as e:
            self.call_from_thread(self.log_line, f"[red]cast failed: {e}[/red]")

    def _sync_auto_approval(self) -> None:
        mutating = list(getattr(cli, "_MUTATING_TOOLS", {}).keys())
        allow = cli._PERMISSIONS.setdefault("allow", [])
        deny = cli._PERMISSIONS.setdefault("deny", [])
        for t in mutating:
            if self.auto_approval_enabled:
                if t in deny:
                    deny.remove(t)
                if t not in allow:
                    allow.append(t)
            else:
                if t in allow:
                    allow.remove(t)
                if t not in deny:
                    deny.append(t)

    _LOCAL_PROVIDER_KEYS = {"ollama", "mephissa", "mephisto", "lmstudio"}

    def _provider_options(self) -> list[tuple[str, str]]:
        opts = []
        for name, pconf in self.config.get("providers", {}).items():
            if not pconf.get("enabled", True):
                continue
            if name not in cli.PROVIDERS:
                continue
            if name not in self._LOCAL_PROVIDER_KEYS:
                continue
            model = pconf.get("model") or ""
            label = f"🤖 {name} / {model}" if model else f"🤖 {name}"
            opts.append((label, name))
        return opts

    def _agent_options(self) -> list[tuple[str, str]]:
        opts = []
        try:
            rows = cli.get_numbered_agents(None)
        except Exception:
            return opts
        category_icons = {
            "BUILT-IN CHAT PROVIDERS (Start NewMeta)": "💬",
            "FREE / LOCAL (no API cost)": "💻",
            "NEWMETA AI AGENTS (OpenClaw Specialists)": "🤖",
            "THE HACKER ARCHON (PIKA POKE)": "🎭",
            "UNRESTRICTED LOCAL AGENTS (Zero Cost, No Limits)": "⚡",
            "RAW LOCAL MODELS (Ollama & Llama.cpp)": "🦙",
            "FREE WEB / DESKTOP APPS (own quota)": "🌐",
            "FREE-TIER / BYO-KEYS CLI": "🔑",
            "PAY-PER-TOKEN API CLI": "💰",
            "SUBSCRIPTION CLI (flat monthly fee)": "💎",
            "THE FOUR HORSEMEN (Dual Wombo Combos)": "🐎",
        }
        current_cat = None
        for row in rows:
            cat = row["category"]
            if cat != current_cat:
                current_cat = cat
                icon = category_icons.get(cat, "✨")
                opts.append((f"─── {icon} {cat} ───", f"__hdr__{cat}"))
            fav = "★ " if row.get("fav") else "  "
            avail = "" if row.get("available") else " ⛔"
            opts.append((f"{fav}{row['name']}{avail}", str(row["id"])))
        return opts

    def _on_agent_picked(self, value: str | None) -> None:
        """Callback for ModelPickerScreen's dismiss() - None means the user
        cancelled (Esc) rather than picking an agent."""
        if value:
            self._launch_agent(value)

    def action_open_session_picker(self) -> None:
        sessions = self.session_manager.list()
        self.push_screen(SessionPickerScreen(sessions, self.session_id), self._on_session_picked)

    def _on_session_picked(self, value: str | None) -> None:
        if not value:
            return
        if value == SessionPickerScreen.NEW_SESSION_ID:
            self.session_id = self.session_manager.create(name="New session", provider="", system="")
            self.messages = []
            self.query_one("#agent-log", AgentLog).clear()
            self.log_line("[bold #00F2FE]⚡ started new session[/bold #00F2FE]")
            return
        session = self.session_manager.load(value)
        if not session:
            self.log_line(f"[bold red]❌ session {value} not found[/bold red]")
            return
        self.session_id = session["id"]
        self.messages = session.get("messages", [])
        self.query_one("#agent-log", AgentLog).clear()
        for m in self.messages:
            role, content = m.get("role"), m.get("content") or ""
            if role == "user":
                self.log_line(f"\n[bold #d8b4fe]» {content}[/bold #d8b4fe]")
            elif role == "assistant" and content:
                self.log_line(f"[bold #ffffff]{content}[/bold #ffffff]")
        self.log_line(f"[bold #00F2FE]⚡ resumed session:[/bold #00F2FE] {session.get('name') or session['id']}")

    def _launch_agent(self, value: str) -> None:
        if value.startswith("__hdr__"):
            return
        try:
            rows = cli.get_numbered_agents(None)
        except Exception:
            return
        row = next((r for r in rows if str(r["id"]) == value), None)
        if not row:
            self.log_line(f"[bold red]❌ Unknown agent: {value}[/bold red]")
            return
        if row["type"] == "builtin":
            provider_aliases = {"pikapoke_ds": "deepseek", "pikapoke_meph": "mephissa"}
            provider_key = provider_aliases.get(row["key"], row["key"])
            self._select_provider(provider_key)
            self.log_line(f"[yellow]🚀 {row['name']} -> builtin {provider_key}[/yellow]")
            tag = self._TRAINER_TAGS.get(provider_key)
            if tag:
                self.log_line(f"[dim]   ({tag} — this session's tool successes now train them)[/dim]")
            return
        if not row.get("available"):
            self.log_line(f"[bold red]❌ {row['name']} is not available. Missing target: {row['command'][0]}[/bold red]")
            return
        try:
            command = cli._external_command_for_task(row, "")
        except Exception as e:
            self.log_line(f"[bold red]❌ {e}[/bold red]")
            return
        self.log_line(f"[yellow]🚀 {row['name']} -> {cli._display_command(command)}[/yellow]")
        tag = self._TRAINER_TAGS.get(row.get("key"))
        if tag:
            self.log_line(f"[dim]   ({tag} — external process, no auto-XP until it has its own hook)[/dim]")
        launch_mode = row.get("launch", "cli")
        cwd = row.get("cwd")
        try:
            if launch_mode == "desktop":
                subprocess.Popen(command, close_fds=True)
            elif launch_mode == "cmd":
                subprocess.Popen(["cmd", "/c"] + command, cwd=cwd, close_fds=True)
            elif launch_mode == "url":
                subprocess.Popen(["cmd", "/c", "start", "", command[0]], close_fds=True)
            else:
                subprocess.Popen(command, cwd=cwd, shell=False, close_fds=True)
        except Exception as e:
            self.log_line(f"[bold red]❌ launch failed: {e}[/bold red]")

    def _macro_row(self, key: str, ultimate_emoji: str, ultimate_id: str, ultimate_label: str) -> ComposeResult:
        """Two rows per card: the 4 real spell hotkeys as their own boxed
        row, then the ultimate/school-icon + its labeled box on a row of
        its own below - was one cramped row with the ultimate chip squeezed
        in in last place; a full-width second row gives the label room to
        actually show text instead of being icon-only."""
        slots = spell_slots_for(key)
        color = self.COMPANION_COLORS.get(key, "#00F2FE")
        with Horizontal(classes="companion-macro-row", id=f"macro-row-{key}"):
            for i, (glyph, action, full_name) in enumerate(slots[:4]):
                btn = Button(
                    glyph, id=f"cardmacro__{key}__{action}",
                    classes="companion-macro-btn", tooltip=full_name,
                )
                # Not part of the Tab focus chain - Tab cycles companion
                # cards (next_companion), and these buttons being focusable
                # let Tab step into them first instead, breaking cycling.
                # Clicking still works regardless of can_focus.
                btn.can_focus = False
                btn.styles.background = "#0a0003"
                btn.styles.border = ("heavy", color)
                btn.styles.color = color
                yield btn
        with Horizontal(classes="companion-ultimate-row"):
            ultimate_btn = Button(ultimate_emoji, id=ultimate_id, classes="ultimate-chip")
            ultimate_btn.can_focus = False
            yield ultimate_btn
            label = Static(ultimate_label, classes="ultimate-label")
            label.styles.border = ("heavy", color)
            label.styles.color = color
            yield label

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Horizontal(id="header-ticker-row"):
            yield Static(no_wrap_text(f"🪙 BTC: {self._btc_price}" + (f"  [bold #00F2FE]⚡ CME GAP {self._cme_gap_txt}[/bold #00F2FE]" if self._cme_gap_txt else "") + (f"  [bold #ff2fd6]💎 ETF FLOW {self._etf_flow_txt}[/bold #ff2fd6]" if self._etf_flow_txt else "") + f"  |  🧈 GOLD: {self._gold_price}  |  🌊 SWEEP: {self._top_sweep}"), id="price-ticker")
            yield Static(no_wrap_text(self._market_pulse_text()), id="market-pulse")
            yield Static(no_wrap_text(self._trading_setup), id="trading-setups-banner")
            yield Static("", id="dj-heartbeat-header")

        with Horizontal(id="dj-toolbar-top"):
            yield Button("💻 CODE", id="btn-mode-coding", classes="knob-btn")
            yield Button("📈 TRADE", id="btn-mode-trading", classes="knob-btn")
            yield Button("🔔 ALERTS", id="btn-meph-alerts", classes="knob-btn")
            yield Button("🐦 SIGNALS", id="btn-meph-tweets", classes="knob-btn")
            yield Button("🎚️ MIX", id="btn-dj-mix", classes="knob-btn")
            yield Button("🔥 BLAZE IT", id="btn-blaze-it", classes="knob-btn")
            yield Button("🌐 EN/AR", id="btn-dj-lang", classes="knob-btn")
            yield Button("🎓 LESSON", id="btn-dj-lesson", classes="knob-btn")
            with Vertical(id="dj-vol-agent-zone"):
                with Horizontal(id="dj-vol-row"):
                    yield Button("− VOL", id="btn-dj-volume-down", classes="knob-btn knob-vol")
                    yield Static("100%", id="dj-volume-display", classes="vol-display")
                    yield Button("+ VOL", id="btn-dj-volume-up", classes="knob-btn knob-vol")
                yield Button("🚀 Launch agent...", id="btn-launch-agent")

        with Horizontal(id="controls"):
            yield Select(
                self._provider_options(),
                id="provider-select",
                prompt="Select provider...",
                allow_blank=True,
            )
            yield Select(
                [
                    ("💬 Chat", "chat"),
                    ("🧠 Plan", "plan"),
                    ("🔍 Review", "review"),
                    ("🤖 Agent", "agent"),
                ],
                id="mode-select",
                value="agent",
                allow_blank=False,
            )
            yield Select(
                [
                    ("💡› Low", "low"),
                    ("🔄» Med", "med"),
                    ("⚡⋙ High", "high"),
                ],
                id="thinking-select",
                value="med",
                allow_blank=False,
            )
            yield Button("🔍 DeepSearch", id="btn-deepsearch", variant="default")
            yield Button("🧰 MCP On", id="btn-mcp", variant="default")
            yield Button("📚 Skills On", id="btn-skills", variant="default")
            yield Button("⚙️ Core On", id="btn-core", variant="default")
            # Opens ThemePickerScreen, which previews live on hover (like Kilo
            # Code's CLI). A plain Select can't do this - Textual's SelectOverlay
            # deliberately stops OptionHighlighted ("stop option list highlighted
            # messages leaking") so it never reaches app code. ^p still cycles
            # via the keyboard for a quick flip without opening the picker.
            yield Button("🎨 Theme...", id="btn-theme-picker", variant="default")
            yield Button("🕹️ Pads...", id="btn-joystick", variant="default")
            yield Button("🎛️ Deck...", id="btn-deck", variant="default")
            yield Button("📌 Board...", id="btn-board", variant="default")

        with Horizontal(id="pika-status-row"):
            yield Static(render_pika_bar(), classes="pika-bar", id="pika-bar")
            yield Static("", id="status-bar")
        with Horizontal(id="tip-commands-row"):
            yield Static("", id="tip")
            yield Static(
                no_wrap_text("⚡ /help · /clear · /files · /model · /gpu · /pika · /archon · /tools · /mcp · /skills · /reload"),
                id="commands-label", classes="pika-sep"
            )

        with Horizontal(id="main-container"):
            with Vertical(id="companion-tower"):
                with ScrollableContainer(id="roster-scroll"):
                    # 1. PIKA TURTLE (Chief) - first in the roster
                    with Vertical(classes="companion-card-tall", id="card-turtle"):
                        yield Static("[bold #38bdf8]      _____    [/bold #38bdf8]\n[bold #38bdf8]   .-'     '-. [/bold #38bdf8]\n[bold #38bdf8]  /           \\ [/bold #38bdf8]\n[bold #38bdf8] |  _  ___  _  |[/bold #38bdf8]\n[bold #38bdf8] | '-'     '-' |[/bold #38bdf8]\n[bold #38bdf8]  \\           /[/bold #38bdf8]\n[bold #38bdf8]   '-._____.-' [/bold #38bdf8]", classes="companion-art")
                        yield Static(no_wrap_text(turtle_name_markup()), id="turtle-name", classes="companion-line")
                        yield from self._macro_row("turtle", "🛡️", "btn-ultimate-turtle", "GUARD")
                        yield Static(no_wrap_text(turtle_mini_bar_markup()), id="turtle-spark", classes="companion-spark")

                    # 2. PIKA POKE
                    with Vertical(classes="companion-card-tall", id="card-pikapoke"):
                        yield Static("[bold #ccff00]   .       .   [/bold #ccff00]\n[bold #ccff00]  / \\     / \\  [/bold #ccff00]\n[bold #ccff00] |   |___|   | [/bold #ccff00]\n[bold #ccff00] | ( o _ o ) | [/bold #ccff00]\n[bold #ccff00] |/    _    \\| [/bold #ccff00]\n[bold #ccff00] |  \\_____/  | [/bold #ccff00]\n[bold #ccff00]  \\_________/  [/bold #ccff00]\n[bold #ccff00]   (_)   (_)   [/bold #ccff00]", classes="companion-art")
                        yield Static(no_wrap_text(pikapoke_name_markup()), id="pikapoke-name", classes="companion-line")
                        yield from self._macro_row("pikapoke", "🗝️", "btn-ultimate-pikapoke", "VAULT")
                        yield Static(no_wrap_text(pikapoke_mini_bar_markup()), id="pikapoke-spark", classes="companion-spark")

                    # 3. PIKA ZOUZOU - same vertical art-on-top layout as every
                    # other card now (was art-left/stats-right, which squeezed
                    # the 4-button macro row into too narrow a column to
                    # render - full card width like PikaPoke fixes that).
                    with Vertical(classes="companion-card-tall", id="mascot-zouzou"):
                        yield Static("[bold #c4b5fd]      /\\   /\\  [/bold #c4b5fd]\n[bold #c4b5fd]     //\\\\_//\\\\ [/bold #c4b5fd]\n[bold #c4b5fd]     \\_     _/ [/bold #c4b5fd]\n[bold #c4b5fd]      / * * \\  [/bold #c4b5fd]\n[bold #c4b5fd]     \\_\\ O /_/ [/bold #c4b5fd]", classes="companion-art")
                        yield Static(no_wrap_text(zouzou_info_markup()), id="zouzou-info", classes="companion-line")
                        yield from self._macro_row("zouzou", "🔥", "btn-ultimate-zouzou", "FRENZY")
                        yield Static(no_wrap_text(zouzou_mini_bar_markup()), id="zouzou-spark", classes="companion-spark")

                    # 4. PIKA MEPHISSA (the DJ)
                    with Vertical(classes="companion-card-tall", id="card-mephissa"):
                        yield Static("[bold #ff2fd6]╭ ─╮  ╭ ─╮   [/bold #ff2fd6]\n[bold #ff2fd6]  ╰🎶─🎶╯   [/bold #ff2fd6]\n[bold #ff2fd6][  █ ▘▝ █  ]       [/bold #ff2fd6]\n[bold #ff2fd6]   ⭕👅⭕[/bold #ff2fd6]\n[bold #ff2fd6]   🎧────🎧   [/bold #ff2fd6]\n[bold #ff2fd6]  ║◉║     ║◉║       [/bold #ff2fd6]\n[bold #ff2fd6]  ║─║     ║─║         [/bold #ff2fd6]", classes="companion-art")
                        yield Static(no_wrap_text(mephissa_info_markup()), id="mephissa-info", classes="companion-line")
                        yield from self._macro_row("mephissa", "📥", "btn-ultimate-mephissa", "FETCH")
                        yield Static(no_wrap_text(mephissa_mini_bar_markup()), id="mephissa-spark", classes="companion-spark")

                    # 5. PIKA MEPHISTO
                    with Vertical(classes="companion-card-tall", id="card-mephisto"):
                        yield Static("[bold #e11d48]       ,----.. [/bold #e11d48]\n[bold #e11d48]      /   __  \\ [/bold #e11d48]\n[bold #e11d48]     |  ( oo)  |[/bold #e11d48]\n[bold #e11d48]     _\\  \\__/  /_[/bold #e11d48]\n[bold #e11d48]    /  \\      /  \\ [/bold #e11d48]", classes="companion-art")
                        yield Static(no_wrap_text(mephisto_info_markup()), id="mephisto-info", classes="companion-line")
                        yield from self._macro_row("mephisto", "⚡", "btn-ultimate-mephisto", "ROUTE")
                        yield Static(no_wrap_text(mephisto_mini_bar_markup()), id="mephisto-spark", classes="companion-spark")

            with ScrollableContainer(id="log-scroll"):
                yield AgentLog(id="agent-log", wrap=True, highlight=True, markup=True)
            with Vertical(id="sidebar"):
                yield Static("", id="sidebar-agent")
                # DJ JOG WHEEL DECK (also carries the 🕹️ pads glyph, migrated
                # up from the now-removed redundant sidebar pads-panel - the
                # full pad grid still lives in the "Pads..." modal)
                with Horizontal(id="dj-jog-deck"):
                    yield Static(no_wrap_text("[bold #ff023a]☯ JOG 🕹️[/bold #ff023a]"), id="jog-wheel-art")
                    with Vertical(id="dj-controls-box"):
                        with Horizontal(id="dj-link-row"):
                            yield Input(placeholder="🔗 paste a link to play it...", id="dj-link-input")
                            yield Button("🔴", id="btn-dj-play-link", classes="knob-btn", tooltip="Play the pasted link (or just press Enter in the box)")
                        with Horizontal(id="dj-buttons-row"):
                            yield Button("◀◀", id="btn-dj-seekback", classes="dj-knob-btn")
                            yield Button("▶", id="btn-dj-playpause", classes="dj-knob-btn")
                            yield Button("⏹", id="btn-dj-stop", classes="dj-knob-btn")
                            yield Button("▶▶", id="btn-dj-skip", classes="dj-knob-btn")
                        yield Static(no_wrap_text("🎵 stopped: (nothing)"), id="dj-now-playing")

                yield ToolChecklist(id="task-log")
                yield RichLog(id="action-plan", wrap=True, markup=True)
                yield RichLog(id="zap-log", wrap=True, markup=True)
                yield RichLog(id="live-feed", wrap=True, markup=True)
                yield Static(no_wrap_text(project_progress_bar(0, 0)), id="project-progress")

        with Horizontal(id="prompt-container"):
            yield TuiInput(placeholder="⚡ [PROMPT] > ask or command...", id="chat-input")
            yield Button("🎤", id="prompt-mic-btn", tooltip="Voice input (same as /voice)")
            yield Button("☯ 🕹️", id="prompt-cycle-btn", tooltip="Cast the next spell in line, cycling through every companion's spell school")
            yield Button("SEND", id="prompt-send-btn")
        footer_bindings = [(b.key, b.description) for b in self.BINDINGS if getattr(b, "show", True)]
        footer_bindings.append(("ctrl+p", "Palette"))
        yield NeonFooter(footer_bindings)

    def on_mount(self) -> None:
        self._sync_buttons()
        self._start_mcp_loader()
        cli.set_permission_prompt_handler(self.sync_request_permission)
        try:
            if self.engine_mode == "trading":
                self.screen.add_class("theme-trading")
                self._start_price_fetcher()
            else:
                self.screen.add_class("theme-coding")
            launch_btn = self.query_one("#btn-launch-agent", Button)
            launch_btn.styles.background = "#ff023a" if self.engine_mode == "trading" else "#ccff00"
            launch_btn.styles.color = "#F8F9FA" if self.engine_mode == "trading" else "#050505"
        except Exception:
            pass

        try:
            self.query_one("#card-pikapoke").border_title = "PIKA POKE"
            self.query_one("#card-pikapoke").border_subtitle = self._house_tag("pikapoke", "🗝️ VAULT")
            self.query_one("#card-turtle").border_title = "PIKA TURTLE"
            self.query_one("#card-turtle").border_subtitle = self._house_tag("turtle", "🛡️ GUARD")
            self.query_one("#mascot-zouzou").border_title = "PIKA ZOUZOU"
            self.query_one("#mascot-zouzou").border_subtitle = self._house_tag("zouzou", "🔥 FRENZY")
            self.query_one("#card-mephissa").border_title = "PIKA MEPHISSA"
            self.query_one("#card-mephissa").border_subtitle = self._house_tag("mephissa", "📥 FETCH")
            self.query_one("#card-mephisto").border_title = "PIKA MEPHISTO"
            self.query_one("#card-mephisto").border_subtitle = self._house_tag("mephisto", "⚡ ROUTE")
            self._refresh_companion_selection()
            self.query_one("#task-log", ToolChecklist).border_title = "▶ ONGOING"
            self.query_one("#action-plan", RichLog).border_title = "📋 ACTION PLAN"
            self.query_one("#zap-log", RichLog).border_title = "✓ COMPLETED"
            self.query_one("#live-feed", RichLog).border_title = "⚡ LIVE FEED"
            self.query_one("#dj-jog-deck").border_title = "🎛️ JOG WHEEL DECK"
            self.query_one("#dj-toolbar-top").border_title = "🎚️ DJ KNOBS & SONG LINK"
            self.query_one("#prompt-container").border_title = "⚡ PROMPT TERMINAL"
        except Exception:
            pass

        opts = self._provider_options()
        if opts:
            _, first = opts[0]
            self._select_provider(first)

        self.set_interval(5.0, self.rotate_tip)
        self.set_interval(30.0, self._refresh_trading_setup)
        self.set_interval(0.2, self._dj_beat_tick)
        self.rotate_tip()

        # Focus the chat input last, after Textual's own initial-focus
        # resolution (Selects/OptionLists etc. mounting above can otherwise
        # win that race and steal focus even though this call runs "first"
        # in on_mount) - call_after_refresh guarantees it runs after layout
        # settles, so typing starts in the chat box immediately on launch.
        self.call_after_refresh(lambda: self.query_one("#chat-input", TuiInput).focus())

    def rotate_tip(self) -> None:
        from explorer import NME_TIPS
        tip = NME_TIPS[self.tip_index]
        self.query_one("#tip", Static).update(f"💡 Tip: {tip}")
        self.tip_index = (self.tip_index + 1) % len(NME_TIPS)
        try:
            active_agents = 1 if self.busy else 0
            self.query_one("#pika-bar", Static).update(render_pika_bar(active_agents=active_agents))
            self.query_one("#price-ticker", Static).update(no_wrap_text(f"🪙 BTC: {self._btc_price}" + (f"  [bold #00F2FE]⚡ CME GAP {self._cme_gap_txt}[/bold #00F2FE]" if self._cme_gap_txt else "") + (f"  [bold #ff2fd6]💎 ETF FLOW {self._etf_flow_txt}[/bold #ff2fd6]" if self._etf_flow_txt else "") + f"  |  🧈 GOLD: {self._gold_price}  |  🌊 SWEEP: {self._top_sweep}"))
            self.query_one("#market-pulse", Static).update(no_wrap_text(self._market_pulse_text()))
        except Exception:
            pass
        self.refresh_companion_cards()
        self.refresh_dj_status()

    def refresh_companion_cards(self) -> None:
        # The big *-bar widgets were replaced by the per-card macro button
        # row (spell_slots_for) - XP is still tracked via *_xp_hist and
        # shown in the small *-spark bar, just no longer duplicated as a
        # separate big bar. Each companion's block stays its own try/except
        # so one companion's module failing to load doesn't take the others
        # down with it.
        try:
            self.query_one("#zouzou-info", Static).update(no_wrap_text(zouzou_info_markup()))
            self._zouzou_xp_hist.append(get_zouzou_stats().get("xp", 0))
            self.query_one("#zouzou-spark", Static).update(no_wrap_text(zouzou_mini_bar_markup()))
        except Exception:
            pass
        try:
            self.query_one("#turtle-name", Static).update(no_wrap_text(turtle_name_markup()))
            self._turtle_xp_hist.append(get_turtle_stats().get("xp", 0))
            self.query_one("#turtle-spark", Static).update(no_wrap_text(turtle_mini_bar_markup()))
        except Exception:
            pass
        try:
            self.query_one("#pikapoke-name", Static).update(no_wrap_text(pikapoke_name_markup()))
            self._pikapoke_xp_hist.append(get_pikapoke_stats().get("xp", 0))
            self.query_one("#pikapoke-spark", Static).update(no_wrap_text(pikapoke_mini_bar_markup()))
        except Exception:
            pass
        try:
            self.query_one("#mephissa-info", Static).update(no_wrap_text(mephissa_info_markup()))
            self._mephissa_xp_hist.append(get_mephissa_stats().get("xp", 0))
            self.query_one("#mephissa-spark", Static).update(no_wrap_text(mephissa_mini_bar_markup()))
        except Exception:
            pass
        try:
            self.query_one("#mephisto-info", Static).update(no_wrap_text(mephisto_info_markup()))
            self._mephisto_xp_hist.append(get_mephisto_stats().get("xp", 1850))
            self.query_one("#mephisto-spark", Static).update(no_wrap_text(mephisto_mini_bar_markup()))
        except Exception:
            pass

    def refresh_dj_status(self) -> None:
        try:
            status = cli.meph_dj_status()
            self.query_one("#dj-now-playing", Static).update(no_wrap_text(f"🎵 {status}"))
            is_playing = "] playing" in status
            if is_playing:
                self._heartbeat_frame = not self._heartbeat_frame
                self._jog_frame = (self._jog_frame + 1) % 4
                # Single line, permanently - no more "TURNTABLE"/"SPIN" text
                # underneath eating a second row.
                jog_art_frames = [
                    "[bold #ff023a]☯ JOG ◐ 🕹️[/bold #ff023a]",
                    "[bold #ff023a]☯ JOG ◓ 🕹️[/bold #ff023a]",
                    "[bold #ff023a]☯ JOG ◑ 🕹️[/bold #ff023a]",
                    "[bold #ff023a]☯ JOG ◔ 🕹️[/bold #ff023a]",
                ]
                self.query_one("#jog-wheel-art", Static).update(no_wrap_text(jog_art_frames[self._jog_frame]))
                beat = "[bold #ff023a]♥ MEPHISSA DJ PLAYING ♥[/bold #ff023a]" if self._heartbeat_frame else "[dim #ff023a]♥ MEPHISSA DJ PLAYING ♥[/dim #ff023a]"
                # ZAP MEPHISSA WHEN AUDIO PLAYS - visual pulse every cycle is
                # fine (it's just a border flash), but only LOG it once per
                # playback session, on the not-playing -> playing edge. This
                # ran every 5s for as long as a track played, so a 4-minute
                # song meant ~48 identical "Mephissa active" log lines.
                if not self._dj_was_playing:
                    self.zap_companion("#card-mephissa")
                else:
                    self._flash_card_only("#card-mephissa")
                self._dj_was_playing = True
            else:
                self.query_one("#jog-wheel-art", Static).update(no_wrap_text("[bold #ff023a]☯ JOG 🕹️[/bold #ff023a]"))
                beat = "[dim]♥ MEPHISSA DJ READY[/dim]"
                self._dj_was_playing = False
            self.query_one("#dj-heartbeat-header", Static).update(no_wrap_text(beat))
            vol = cli.meph_dj_volume_level()
            self.query_one("#dj-volume-display", Static).update(f"{vol}%")
        except Exception:
            pass

    def _dj_beat_tick(self) -> None:
        # Fast (5x/sec) real heartbeat — separate from refresh_dj_status's
        # slower 5s cycle, driven by the actual detected BPM/beat-phase
        # instead of a cosmetic blink. No-ops until BPM analysis lands, and
        # no-ops entirely while nothing's playing (refresh_dj_status owns
        # that "DJ READY" idle text).
        try:
            beat = cli.meph_dj_beat_state()
            if not beat.get("playing"):
                return
            header = self.query_one("#dj-heartbeat-header", Static)
            if beat.get("status") == "ready" and beat.get("bpm"):
                bpm_txt = f"{beat['bpm']:.0f} BPM"
                key_txt = beat.get("key_camelot") or "?"
                if beat.get("in_window"):
                    header.update(no_wrap_text(f"[bold #ff023a]🎯 TRANSITION — {bpm_txt} {key_txt} — F10![/bold #ff023a]"))
                else:
                    pulse = "♥" if beat.get("on_beat") else "♡"
                    style = "bold" if beat.get("on_beat") else "dim"
                    header.update(no_wrap_text(f"[{style} #00F2FE]{pulse} {bpm_txt}  {key_txt}[/{style} #00F2FE]"))
            elif beat.get("status") == "analyzing":
                header.update(no_wrap_text("[dim]🎧 analyzing...[/dim]"))
        except Exception:
            pass

    def action_dj_playpause(self) -> None:
        msg = cli.meph_dj_pause()
        self.log_line(f"[medium_purple1]🎵 {msg}[/medium_purple1]")
        self.zap_companion("#card-mephissa")
        self.refresh_dj_status()

    def action_dj_stop(self) -> None:
        msg = cli.meph_dj_stop()
        self.log_line(f"[medium_purple1]🎵 {msg}[/medium_purple1]")
        self.zap_companion("#card-mephissa")
        self.refresh_dj_status()

    def action_dj_skip(self) -> None:
        msg = cli.meph_dj_skip()
        self.log_line(f"[medium_purple1]🎵 {msg}[/medium_purple1]")
        self.zap_companion("#card-mephissa")
        self.refresh_dj_status()
        if msg.startswith("▶ Crossfading"):
            self.log_line("[bold magenta]🎆🎆 crossfade transition 🎆🎆[/bold magenta]")
            self.zap_companion("#dj-jog-deck")

    def action_dj_transition(self) -> None:
        msg = cli.meph_dj_transition()
        self.log_line(f"[bold #00F2FE]🎯 {msg}[/bold #00F2FE]")
        self.zap_companion("#card-mephissa")
        self.refresh_dj_status()

    def action_dj_voice(self) -> None:
        # Same 3-press toggle as the DJ deck's 🎤 VOICE button: press 1
        # starts recording, press 2 stops+transcribes (asks first if the
        # detected language isn't English), press 3 confirms+dispatches.
        msg = cli.meph_dj_voice_command()
        self.log_line(f"[bold #ff023a]🎤 {msg}[/bold #ff023a]")
        self.zap_companion("#card-mephissa")
        self.refresh_dj_status()

    def action_dj_seek_back(self) -> None:
        msg = cli.meph_dj_seek(-10)
        self.log_line(f"[medium_purple1]🎛 {msg}[/medium_purple1]")
        self.zap_companion("#card-mephissa")
        self.refresh_dj_status()

    def action_dj_seek_fwd(self) -> None:
        msg = cli.meph_dj_seek(10)
        self.log_line(f"[medium_purple1]🎛 {msg}[/medium_purple1]")
        self.zap_companion("#card-mephissa")
        self.refresh_dj_status()

    def action_dj_volume_up(self) -> None:
        # Same call the + VOL button makes - meph_dj_volume() itself already
        # clamps at 100, this just gives it a keyboard shortcut too.
        msg = cli.meph_dj_volume(5)
        self.log_line(f"[medium_purple1]🎚 {msg}[/medium_purple1]")
        self.zap_companion("#card-mephissa")
        self.refresh_dj_status()

    def action_dj_volume_down(self) -> None:
        msg = cli.meph_dj_volume(-5)
        self.log_line(f"[medium_purple1]🎚 {msg}[/medium_purple1]")
        self.zap_companion("#card-mephissa")
        self.refresh_dj_status()

    def action_dj_play_link(self, link: str) -> None:
        # Shared by the toolbar's LINK button/Enter-in-box and DeckScreen's
        # own link box - one real implementation instead of two copies.
        status = cli.meph_dj_status()
        already_going = ("] playing" in status) or ("] paused" in status)
        msg = cli.meph_dj_play(link, queue=already_going)
        self.log_line(f"[medium_purple1]🎵 {msg}[/medium_purple1]")
        self.zap_companion("#card-mephissa")
        self.refresh_dj_status()

    def action_dj_toggle_lang(self) -> None:
        self.dj_lang = "ar" if self.dj_lang == "en" else "en"
        mode = "arabic_hits" if self.dj_lang == "ar" else "electronic"
        msg = cli.meph_dj_set_mode(mode)
        try:
            self.query_one("#btn-dj-lang", Button).label = "▶ AR" if self.dj_lang == "ar" else "▶ EN"
        except Exception:
            pass
        self.log_line(f"[medium_purple1]🎚 {msg}[/medium_purple1]")
        self.zap_companion("#card-mephissa")
        self.refresh_dj_status()
        if self.dj_lang == "ar":
            # Real ask: switching to Arabic should line up a real Arabic
            # track as the upcoming one, not just change the mode filter
            # for whenever something next gets picked. If something's
            # already playing this queues (deck 2 preload + the existing
            # real crossfade handles "epic transition"); if nothing's
            # playing it just starts the track.
            self._queue_arabic_track()

    def action_dj_toggle_mix(self) -> None:
        self.dj_mix_enabled = not self.dj_mix_enabled
        msg = cli.meph_dj_infinite_mix(self.dj_mix_enabled)
        try:
            btn = self.query_one("#btn-dj-mix", Button)
            btn.set_class(self.dj_mix_enabled, "on")
            btn.set_class(not self.dj_mix_enabled, "off")
        except Exception:
            pass
        self.log_line(f"[medium_purple1]🔁 {msg}[/medium_purple1]")
        if self.dj_mix_enabled:
            viz_msg = cli.dj_visualizer_open()
        else:
            viz_msg = cli.dj_visualizer_close()
        self.log_line(f"[bold #00F2FE]{viz_msg}[/bold #00F2FE]")
        self.zap_companion("#card-mephissa")
        self.refresh_dj_status()

    def _apply_palette(self, name: str) -> None:
        """Apply a palette's colors immediately - shared by the keyboard
        cycle (^p) and ThemePickerScreen's hover preview, so both stay in sync."""
        t = PALETTE_THEMES.get(name)
        if not t:
            return
        try:
            self.query_one("#log-scroll").styles.border = ("round", t["chat"])
            self.query_one("#sidebar").styles.border = ("round", t["sidebar"])
            self.query_one("#prompt-container").styles.border = ("round", t["chat"])
            self.query_one("#pika-bar", Static).styles.color = t["accent"]
        except Exception:
            pass

    def action_cycle_palette(self) -> None:
        names = list(PALETTE_THEMES.keys())
        self._palette_idx = (self._palette_idx + 1) % len(names)
        name = names[self._palette_idx]
        self._apply_palette(name)
        t = PALETTE_THEMES[name]
        self.log_line(f"[{t['accent']}]🎨 Palette: {name}[/{t['accent']}]")

    def _on_theme_picked(self, name: str | None) -> None:
        if name and name in PALETTE_THEMES:
            names = list(PALETTE_THEMES.keys())
            self._palette_idx = names.index(name)
            self._apply_palette(name)
            t = PALETTE_THEMES[name]
            self.log_line(f"[{t['accent']}]🎨 Palette: {name}[/{t['accent']}]")
        else:
            # Cancelled - restore whatever palette was active before previewing.
            names = list(PALETTE_THEMES.keys())
            self._apply_palette(names[self._palette_idx])

    def action_handle_esc(self) -> None:
        now = time.time()
        if now - self._last_esc_time < 1.5:
            if self.busy:
                self.busy = False
                self.log_line("\n[bold red]⛔ AGENT OPERATION ABORTED BY ESC x2![/bold red]")
                self.set_status(f"ABORTED | {self.provider_label}")
            else:
                self.log_line("\n[bold red]⛔ Abort triggered (agent was idle).[/bold red]")
            self._last_esc_time = 0.0
        else:
            self._last_esc_time = now
            if self.busy:
                self.log_line("\n[bold yellow]⚡ ESC Slap! Nudging agent... (Press ESC again within 1.5s to abort)[/bold yellow]")
            else:
                self.log_line("\n[bold yellow]⚡ ESC Slap! Agent is ready. (Press ESC twice to abort)[/bold yellow]")

    def action_copy_log(self) -> None:
        import pyperclip
        try:
            log = self.query_one("#agent-log", AgentLog)
            text = "\n".join(log._plain_lines)
            pyperclip.copy(text)
            self.log_line("[bold yellow]📋 Entire chat log copied to clipboard.[/bold yellow]")
        except Exception as e:
            self.log_line(f"[bold red]❌ Failed to copy log: {e}[/bold red]")

    def action_paste_clipboard(self) -> None:
        from explorer import describe_clipboard_payload, play_sfx
        payload = describe_clipboard_payload()
        if not payload["has_payload"]:
            self.log_line("[bold yellow]📋 Clipboard has no text, files, or image to attach.[/bold yellow]")
            return
        self.pasted_attachment = payload["prompt_block"]
        chat_input = self.query_one("#chat-input", TuiInput)
        chat_input.placeholder = "📋 [Clipboard attached] Type prompt & press Enter..."
        summary = "; ".join(payload["summary"]) or "clipboard payload"
        self.log_line(f"[bold yellow]📋 Clipboard attached:[/bold yellow] {summary}. Press Enter to send.")
        play_sfx("attach")
        chat_input.focus()

    def _sync_buttons(self) -> None:
        for btn_id, state in (
            ("#btn-mcp", self.include_mcp),
            ("#btn-skills", self.include_skills),
            ("#btn-core", self.include_core),
            ("#btn-deepsearch", self.deepsearch_enabled),
        ):
            btn = self.query_one(btn_id, Button)
            btn.set_class(state, "on")
            btn.set_class(not state, "off")
            if btn_id == "#btn-deepsearch":
                btn.label = "🔍 Deep"
            else:
                label = btn.label.plain if hasattr(btn.label, "plain") else str(btn.label)
                base = label.split(" ")[0]
                btn.label = f"{base} {'On' if state else 'Off'}"

        for chip_id, state in (
            ("#btn-ultimate-turtle", self.auto_approval_enabled),
            ("#btn-ultimate-zouzou", self.zouzou_frenzy_enabled),
            ("#btn-ultimate-pikapoke", self.pikapoke_vault_enabled),
            ("#btn-ultimate-mephissa", self.mephissa_fetch_enabled),
            ("#btn-ultimate-mephisto", self.mephisto_router_enabled),
            ("#btn-dj-mix", self.dj_mix_enabled),
        ):
            try:
                chip = self.query_one(chip_id, Button)
                chip.set_class(state, "on")
                chip.set_class(not state, "off")
            except Exception:
                pass

    def log_zap(self, text: str) -> None:
        self.query_one("#zap-log", RichLog).write(text)

    def log_task(self, text: str) -> None:
        # One-off note (e.g. the malformed-tool-call repair warning) rather
        # than a real tracked tool call - gets its own throwaway id so it
        # doesn't collide with add_pending()/mark_done() pairs.
        self.query_one("#task-log", ToolChecklist).add_pending(f"note-{time.time()}", _short_bullet(text))

    def log_task_plan(self, text: str) -> None:
        self.query_one("#action-plan", RichLog).write(f"[cyan]☐[/cyan] {_short_bullet(text)}")

    def _reset_task_checklist(self) -> None:
        self.query_one("#task-log", ToolChecklist).reset()

    def _checklist_add(self, item_id: str, label: str) -> None:
        self.query_one("#task-log", ToolChecklist).add_pending(item_id, label)

    def _checklist_done(self, item_id: str, ok: bool) -> None:
        self.query_one("#task-log", ToolChecklist).mark_done(item_id, ok)

    def _update_sidebar_agent(self) -> None:
        try:
            key = self.COMPANION_KEYS[self._companion_selected_idx]
            companion_name = SUMMONS[key].name
            self.query_one("#sidebar-agent", Static).update(
                f"[bold #ccff00]{companion_name}[/bold #ccff00]\n"
                f"{self.provider_label} | mode={self.mode} | think={self.thinking_level}"
            )
        except Exception:
            pass

    def refresh_project_progress(self) -> None:
        try:
            self.query_one("#project-progress", Static).update(
                no_wrap_text(project_progress_bar(self._session_actions_done, self._session_actions_total))
            )
        except Exception:
            pass

    def set_status(self, text: str) -> None:
        self.query_one("#status-bar", Static).update(text)

    def sync_request_permission(self, tool_name: str, target: str) -> str:
        """Called by cli.py's _authorize_tool from the agent worker thread
        (never the UI thread). Blocks that worker thread on a real
        threading.Event while the actual permission window is pushed and
        answered on the main/UI thread - safe because it's the worker
        thread that blocks, not Textual's own event loop."""
        event = threading.Event()
        result: list[str] = []

        def _push() -> None:
            def _on_dismiss(answer: str | None) -> None:
                result.append(answer or "deny")
                event.set()
            self.push_screen(PermissionPromptScreen(tool_name, target), _on_dismiss)

        self.call_from_thread(_push)
        event.wait()
        return result[0] if result else "deny"

    def log_line(self, text: str) -> None:
        log = self.query_one("#agent-log", RichLog)
        try:
            log.write(text)
        except Exception:
            # text contained malformed/unbalanced Rich markup (e.g. an
            # exception message quoting a raw '[...]' tag) — fall back to
            # writing it as plain text so a bad log line can't crash the app.
            try:
                log.write(Text(text))
            except Exception:
                pass

    def log_stream_chunk(self, text: str) -> None:
        """Streamed LLM output arrives token-by-token, but RichLog.write()
        always starts a NEW line on every call - writing each token
        separately stacked the whole reply one token per line (vertical
        text). Buffer until a real newline shows up in the actual content,
        flush only complete lines, keep the trailing partial line pending
        for the next chunk (flushed for real at the end via
        flush_stream_buffer). Trades true per-token live-typing for
        correct, horizontal, readable text."""
        self._stream_buffer += text
        if "\n" in self._stream_buffer:
            *complete, self._stream_buffer = self._stream_buffer.split("\n")
            for line in complete:
                self.log_line(f"[bold #ffffff]{line}[/bold #ffffff]")

    def flush_stream_buffer(self) -> None:
        if self._stream_buffer:
            self.log_line(f"[bold #ffffff]{self._stream_buffer}[/bold #ffffff]")
            self._stream_buffer = ""

    def _select_provider(self, name: str) -> None:
        try:
            self.provider = cli.get_provider(name, self.config, self.secrets)
        except Exception as e:
            self.provider = None
            self.log_line(f"[bold red]❌ {e}[/bold red]")
            return
        self.provider_key = name
        pconf = self.config.get("providers", {}).get(name, {})
        self.provider_label = f"🤖 {name} / {pconf.get('model') or '?'}"
        self.set_status(f"READY | {self.provider_label} | mode={self.mode} | tools: mcp={self.include_mcp} skills={self.include_skills} core={self.include_core}")
        self._update_sidebar_agent()
        try:
            select = self.query_one("#provider-select", Select)
            if select.value != name:
                select.value = name
        except Exception:
            pass

        # ZAP COMPANION ON PROVIDER CHANGE
        if name in ("mephisto", "codex", "codexfree"):
            self.zap_companion("#card-mephisto", "switching provider")
            self.zap_companion("#card-turtle", "guarding")
        elif name == "mephissa":
            self.zap_companion("#card-mephissa", "switching provider")
        elif name == "gemini":
            self.zap_companion("#card-pikapoke", "switching provider")
        elif name == "anthropic":
            self.zap_companion("#mascot-zouzou", "switching provider")

    def action_next_companion(self) -> None:
        self._companion_selected_idx = (self._companion_selected_idx + 1) % len(self.COMPANION_CARD_IDS)
        self._refresh_companion_selection()

    def action_prev_companion(self) -> None:
        self._companion_selected_idx = (self._companion_selected_idx - 1) % len(self.COMPANION_CARD_IDS)
        self._refresh_companion_selection()

    def action_summon_selected(self) -> None:
        # Safe as a global "enter" binding: the chat input (and any modal
        # picker's own search box) claims "enter" in its own BINDINGS, which
        # Textual resolves before this App-level one while that widget is
        # focused — this only ever fires when nothing else has claimed Enter.
        key = self.COMPANION_KEYS[self._companion_selected_idx]
        if not has_mode(key):
            name = SUMMONS[key].name if key in SUMMONS else key
            self.push_screen(ModePickerScreen(name), lambda mode: self._on_mode_picked(key, mode, continue_summon=True))
            return
        self._continue_summon(key)

    def action_reopen_mode_picker(self) -> None:
        """'m' on a selected card — change its hook mode without summoning it."""
        key = self.COMPANION_KEYS[self._companion_selected_idx]
        name = SUMMONS[key].name if key in SUMMONS else key
        self.push_screen(ModePickerScreen(name, load_mode(key)), lambda mode: self._on_mode_picked(key, mode, continue_summon=False))

    def _on_mode_picked(self, key: str, mode: str | None, continue_summon: bool) -> None:
        if not mode:
            return  # Esc cancels the same way every other picker's Esc does
        set_companion_mode(key, mode)
        self.log_line(f"[cyan]⚡ {SUMMONS[key].name if key in SUMMONS else key} hook mode:[/cyan] {mode}")
        if continue_summon:
            self._continue_summon(key)

    def _continue_summon(self, key: str) -> None:
        provider_key = self.COMPANION_PROVIDER_MAP.get(key)
        if key == "turtle":
            # Guard-role behavior stays part of summoning him, on top of
            # (not instead of) now also launching Codex like every other
            # companion launches its own provider.
            self.auto_approval_enabled = not self.auto_approval_enabled
            self._sync_auto_approval()
            self.log_line(f"[yellow]🛡️ Turtle auto-approval: {'ON' if self.auto_approval_enabled else 'OFF'}[/yellow]")
        self.summon_fx(key)
        try:
            rows = cli.get_numbered_agents(None)
            row = next((r for r in rows if r.get("key") == provider_key), None)
            initial_id = str(row["id"]) if row else None
        except Exception:
            initial_id = None
        self.push_screen(ModelPickerScreen(self._agent_options(), initial_id), self._on_agent_picked)

    def _refresh_companion_selection(self) -> None:
        for i, card_id in enumerate(self.COMPANION_CARD_IDS):
            try:
                card = self.query_one(card_id)
                is_selected = i == self._companion_selected_idx
                card.set_class(is_selected, "selected")
                card.can_focus = True
                key = self.COMPANION_KEYS[i]
                if is_selected:
                    # Selection uses the same identity color, just a heavier
                    # border for the highlight - was HOUSE_COLORS (Hogwarts
                    # house), a different color entirely, so the default
                    # tab-selected card (PikaPoke on launch) never actually
                    # showed its own color.
                    card.styles.border = ("heavy", self.COMPANION_COLORS.get(key, "#F8F9FA"))
                    card.focus()
                else:
                    # Resting state: each companion's own identity color
                    # (was hardcoded cyan for every card here, silently
                    # overriding the per-card CSS on every refresh).
                    card.styles.border = ("round", self.COMPANION_COLORS.get(key, "#00F2FE"))
            except Exception:
                pass
        self._sync_active_summon()
        self._update_sidebar_agent()

    def _sync_active_summon(self) -> None:
        """Keep the spellbook's own active_summon in sync with Tab-selection,
        so `python -m spellbook cast <key>` (the AHK Alt+Q/W/E/R layer) routes
        against whichever companion is currently selected here — no AHK
        changes needed, spellbook/caster.py's cmd_spell() reads this field."""
        key = self.COMPANION_KEYS[self._companion_selected_idx]
        state_path = Path.home() / "Desktop" / "pika-poke" / "data" / "state.json"
        try:
            data = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        except Exception:
            data = {}
        data["active_summon"] = key
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _house_tag(self, summon_key: str, role_tag: str) -> str:
        """e.g. '( 🗝️ VAULT · ⚡ Gryffindor )' — role tag plus house/element from the registry."""
        summon = SUMMONS.get(summon_key)
        if not summon or not summon.house:
            return f"( {role_tag} )"
        return f"( {role_tag} · {summon.emoji} {summon.house} )"

    def _toggle(self, attr: str) -> None:
        setattr(self, attr, not getattr(self, attr))
        self._sync_buttons()
        self.set_status(f"READY | {self.provider_label} | mode={self.mode} | tools: mcp={self.include_mcp} skills={self.include_skills} core={self.include_core}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button
        if btn.id and btn.id.startswith("cardmacro__"):
            _, key, action = btn.id.split("__", 2)
            btn.add_class("-active")
            self.set_timer(0.25, lambda: btn.remove_class("-active"))
            self.cast_macro(key, action)
            return
        if btn.id and btn.id.startswith("sidepad__"):
            _, key, action = btn.id.split("__", 2)
            btn.add_class("-active")
            self.set_timer(0.25, lambda: btn.remove_class("-active"))
            self.cast_macro(key, action)
            return
        if btn.id == "btn-launch-agent":
            self.push_screen(ModelPickerScreen(self._agent_options()), self._on_agent_picked)
        elif btn.id == "btn-theme-picker":
            names = list(PALETTE_THEMES.keys())
            current = names[self._palette_idx] if 0 <= self._palette_idx < len(names) else None
            self.push_screen(ThemePickerScreen(names, current), self._on_theme_picked)
        elif btn.id == "btn-joystick":
            self.push_screen(JoystickScreen())
        elif btn.id == "btn-deck":
            self.push_screen(DeckScreen())
        elif btn.id == "btn-board":
            self.push_screen(BoardScreen())
        elif btn.id == "btn-mcp":
            self._toggle("include_mcp")
        elif btn.id == "btn-skills":
            self._toggle("include_skills")
        elif btn.id == "btn-core":
            self._toggle("include_core")
        elif btn.id == "btn-deepsearch":
            self._toggle("deepsearch_enabled")
        elif btn.id == "btn-ultimate-turtle":
            self.auto_approval_enabled = not self.auto_approval_enabled
            self._sync_auto_approval()
            self._sync_buttons()
            state = "ON" if self.auto_approval_enabled else "OFF"
            color = "green" if self.auto_approval_enabled else "red"
            self.zap_companion("#card-turtle")
            self.log_line(f"[{color}]🛡️ Turtle's Guardian (auto-approval): {state}[/{color}]")
        elif btn.id == "btn-ultimate-zouzou":
            self.zouzou_frenzy_enabled = not self.zouzou_frenzy_enabled
            self._sync_buttons()
            state = "ON" if self.zouzou_frenzy_enabled else "OFF"
            color = "orange1" if self.zouzou_frenzy_enabled else "dim"
            self.zap_companion("#mascot-zouzou")
            self.log_line(f"[{color}]🔥 Zouzou's Frenzy: {state}[/{color}]")
        elif btn.id == "btn-ultimate-pikapoke":
            self.pikapoke_vault_enabled = not self.pikapoke_vault_enabled
            self._sync_buttons()
            state = "ON" if self.pikapoke_vault_enabled else "OFF"
            color = "magenta" if self.pikapoke_vault_enabled else "dim"
            self.zap_companion("#card-pikapoke")
            self.log_line(f"[{color}]🗝️ Pika Poke's Vault: {state}[/{color}]")
        elif btn.id == "btn-ultimate-mephissa":
            self.mephissa_fetch_enabled = not self.mephissa_fetch_enabled
            self._sync_buttons()
            state = "ON" if self.mephissa_fetch_enabled else "OFF"
            color = "medium_purple1" if self.mephissa_fetch_enabled else "dim"
            self.zap_companion("#card-mephissa")
            self.log_line(f"[{color}]📥 Mephissa's Fetch (yt-dlp): {state}[/{color}]")
        elif btn.id == "btn-ultimate-mephisto":
            self.mephisto_router_enabled = not self.mephisto_router_enabled
            self._sync_buttons()
            state = "ON" if self.mephisto_router_enabled else "OFF"
            color = "red" if self.mephisto_router_enabled else "dim"
            self.zap_companion("#card-mephisto")
            self.log_line(f"[{color}]⚡ Mephisto's Router: {state}[/{color}]")
        elif btn.id == "btn-dj-playpause":
            self.action_dj_playpause()
        elif btn.id == "btn-dj-stop":
            self.action_dj_stop()
        elif btn.id == "btn-dj-skip":
            self.action_dj_skip()
        elif btn.id == "btn-dj-seekback":
            self.action_dj_seek_back()
        elif btn.id == "btn-dj-seekfwd":
            self.action_dj_seek_fwd()
        elif btn.id == "btn-dj-play-link":
            link_input = self.query_one("#dj-link-input", Input)
            link = link_input.value.strip()
            link_input.value = ""
            if link:
                self.action_dj_play_link(link)
        elif btn.id == "btn-dj-lang":
            self.action_dj_toggle_lang()
        elif btn.id == "btn-dj-mix":
            self.action_dj_toggle_mix()
        elif btn.id == "btn-blaze-it":
            # Companion spell effects while mixing - opens the same pad
            # screen as the "Pads..." toolbar button, right from the DJ
            # toolbar so it's one click away mid-mix instead of hunting for
            # the other button.
            self.push_screen(JoystickScreen())
        elif btn.id == "btn-dj-volume-down":
            msg = cli.meph_dj_volume(-5)
            self.log_line(f"[medium_purple1]🎚 {msg}[/medium_purple1]")
            self.zap_companion("#card-mephissa")
            self.refresh_dj_status()
        elif btn.id == "btn-dj-volume-up":
            msg = cli.meph_dj_volume(5)
            self.log_line(f"[medium_purple1]🎚 {msg}[/medium_purple1]")
            self.zap_companion("#card-mephissa")
            self.refresh_dj_status()
        elif btn.id == "btn-dj-lesson":
            self._dj_lesson_mode = (self._dj_lesson_mode + 1) % 3
            if self._dj_lesson_mode == 0:
                msg = cli.meph_dj_lesson()
            elif self._dj_lesson_mode == 1:
                msg = cli.meph_dj_music_lesson()
            else:
                msg = cli.meph_dj_eq_shortcuts()
            self.log_line(f"[bold #00F2FE]{msg}[/bold #00F2FE]")
            self.zap_companion("#card-mephissa", "giving a lesson")
        elif btn.id == "btn-mode-coding":
            self.switch_engine_mode("coding")
        elif btn.id == "btn-mode-trading":
            self.switch_engine_mode("trading")
        elif btn.id == "btn-meph-alerts":
            self.show_mephisto_alerts()
        elif btn.id == "btn-meph-tweets":
            self.show_mephisto_tweets()
        elif btn.id == "prompt-send-btn":
            chat_input = self.query_one("#chat-input", TuiInput)
            val = chat_input.value.strip()
            if val:
                chat_input.action_submit()
        elif btn.id == "prompt-mic-btn":
            self.voice_worker()
        elif btn.id == "prompt-cycle-btn":
            self._cycle_spell_preview()

    def _cycle_spell_preview(self) -> None:
        """🌀 prompt-bar button: casts the next spell in ALL_SPELLS, wrapping
        around - one press steps through every companion's school in turn,
        via the same cast_macro() the card row / pads / joystick all use.
        A manual card-button click gets its visual (the button flashes white,
        on_button_pressed adds/removes the -active class) BEFORE cast_macro()
        runs - there's no physical button here to flash, so this reproduces
        the same three-part effect by reference: flash that companion's real
        macro-row button, pulse its card border (zap_companion), and recolor
        this button itself to the companion's own color so you can see at a
        glance who's up, not just read it in the log."""
        if not ALL_SPELLS:
            return
        spell = ALL_SPELLS[self._spell_cycle_idx % len(ALL_SPELLS)]
        self._spell_cycle_idx += 1
        color = self.COMPANION_COLORS.get(spell.companion, "#a78bfa")
        try:
            cycle_btn = self.query_one("#prompt-cycle-btn", Button)
            cycle_btn.styles.background = color
            cycle_btn.styles.border = ("heavy", color)
        except Exception:
            pass
        try:
            macro_btn = self.query_one(f"#cardmacro__{spell.companion}__{spell.key}", Button)
            macro_btn.add_class("-active")
            self.set_timer(0.25, lambda: macro_btn.remove_class("-active"))
        except Exception:
            pass
        # _flash_card_only, not zap_companion - cast_macro() below already
        # writes the real "🔥 cast {name}" live-feed line via announce_cast,
        # a second log line here would just be a duplicate.
        card_id = {"turtle": "#card-turtle", "zouzou": "#mascot-zouzou"}.get(spell.companion, f"#card-{spell.companion}")
        self._flash_card_only(card_id)
        self.cast_macro(spell.companion, spell.key)

    def switch_engine_mode(self, new_mode: str) -> None:
        self.engine_mode = "trading" if new_mode in ("trading", "trade") else "coding"
        try:
            if self.engine_mode == "trading":
                self.screen.remove_class("theme-coding")
                self.screen.add_class("theme-trading")
                self.include_mcp = True
                self.mephisto_router_enabled = True
                self._trading_setup = self._live_trading_setup()
                self._start_price_fetcher()
                self.zap_companion("#card-mephisto")
                self.log_line("[bold #38bdf8]📈 Switched to TRADING ENGINE Mode (Deep Navy Blue Theme + Full Quant/MT5 Suite Active)[/bold #38bdf8]")
            else:
                self.screen.remove_class("theme-trading")
                self.screen.add_class("theme-coding")
                self.include_mcp = False
                self.mephisto_router_enabled = False
                self._trading_setup = "💻 CODING ENGINE: Ultra-Fast Launch | File/Git/Code Skills Active | Mephissa DJ Ready 🎵"
                self.zap_companion("#card-pikapoke")
                self.log_line("[bold green]💻 Switched to CODING ENGINE Mode (Full Dark Black Theme + Ultra-Fast Dev Suite Active)[/bold green]")
        except Exception:
            pass

        self._sync_buttons()
        try:
            self.query_one("#trading-setups-banner", Static).update(no_wrap_text(self._trading_setup))
        except Exception:
            pass
        try:
            launch_btn = self.query_one("#btn-launch-agent", Button)
            launch_btn.styles.background = "#ff023a" if self.engine_mode == "trading" else "#ccff00"
            launch_btn.styles.color = "#F8F9FA" if self.engine_mode == "trading" else "#050505"
        except Exception:
            pass

    def show_project_board(self) -> None:
        try:
            sys.path.insert(0, str(Path.home() / ".claude" / "companion_kit"))
            import board
            self.log_line(f"[bold #00F2FE]{board.render()}[/bold #00F2FE]")
            self.log_line('[dim]Add a card from anywhere: python C:\\Users\\youha\\.claude\\companion_kit\\board.py '
                           'add "title" [backlog|in_progress|done][/dim]')
        except Exception as e:
            self.log_line(f"[bold red]❌ Error rendering project board: {e}[/bold red]")

    def show_spellbook_progress(self) -> None:
        self.log_line("[bold #a78bfa]============================================================[/bold #a78bfa]")
        self.log_line("[bold #a78bfa] ⚡ SPELLBOOK — Wiring Progress & Available Spells [/bold #a78bfa]")
        self.log_line("[bold #a78bfa]============================================================[/bold #a78bfa]")
        self.log_line("")
        self.log_line("[bold #F8F9FA]Companions:[/bold #F8F9FA]")
        active = None
        try:
            state_path = Path.home() / "Desktop" / "pika-poke" / "data" / "state.json"
            active = json.loads(state_path.read_text(encoding="utf-8")).get("active_summon")
        except Exception:
            pass
        for key in self.COMPANION_KEYS:
            summon = SUMMONS.get(key)
            name = summon.name if summon else key
            house = summon.house if summon else "?"
            element = summon.element if summon else "?"
            mode = load_mode(key)
            mode_tag = mode if has_mode(key) else f"{mode} (default)"
            count = sum(1 for s in ALL_SPELLS if s.companion == key)
            marker = "● " if key == active else "  "
            self.log_line(
                f"{marker}[bold]{name}[/bold]  ·  {house}/{element}  ·  hook: {mode_tag}  ·  {count} spell(s)"
            )
        self.log_line("")
        self.log_line("[bold #F8F9FA]Spells:[/bold #F8F9FA]")
        by_companion: dict[str, list] = {}
        for s in ALL_SPELLS:
            by_companion.setdefault(s.companion, []).append(s)
        for key in self.COMPANION_KEYS:
            spells = by_companion.get(key, [])
            if not spells:
                continue
            name = SUMMONS[key].name if key in SUMMONS else key
            self.log_line(f"  [cyan]{name}:[/cyan]")
            for s in spells:
                tag = "" if s.kind == "spell" else " (mini)"
                self.log_line(f"    [{s.hotkey}] {s.name}{tag} — {s.element}/{s.power} → {s.target}")
        self.log_line("[bold #a78bfa]============================================================[/bold #a78bfa]")
        self.log_line("[dim]Alt+M on a selected companion card changes its hook mode. Tab/Shift+Tab selects a companion.[/dim]")

    @work(thread=True)
    def show_mephisto_alerts(self) -> None:
        # Fetches over the network (pika_mephisto_alert_selector) - was
        # running straight on the UI thread, freezing every button (not just
        # this one) for however long those requests took. @work(thread=True)
        # + call_from_thread keeps the UI responsive while it loads.
        self.call_from_thread(self.zap_companion, "#card-mephisto")
        try:
            from pika_mephisto_alert_selector import format_mephisto_alerts_report
            report = format_mephisto_alerts_report()
            self.call_from_thread(self.log_line, "[bold #yellow]============================================================[/bold #yellow]")
            self.call_from_thread(self.log_line, f"[bold #yellow] {report['title']} [/bold #yellow]")
            self.call_from_thread(self.log_line, "[bold #yellow]============================================================[/bold #yellow]")
            self.call_from_thread(self.log_line, "")
            for line in report["lines"]:
                self.call_from_thread(self.log_line, line)
            self.call_from_thread(self.log_line, "[bold #yellow]============================================================[/bold #yellow]")
        except Exception as e:
            self.call_from_thread(self.log_line, f"[bold red]❌ Error rendering Mephisto alerts: {e}[/bold red]")

    @work(thread=True)
    def show_mephisto_tweets(self) -> None:
        # Same UI-freeze issue as show_mephisto_alerts - multiple sequential
        # network calls (mephisto_signals), now off the UI thread.
        self.call_from_thread(self.zap_companion, "#card-mephisto")
        try:
            from mephisto_signals import get_top_3_buy_sell_tweets, get_top_3_pump_dump_tweets
            bs = get_top_3_buy_sell_tweets()
            pd = get_top_3_pump_dump_tweets()
            log = lambda text: self.call_from_thread(self.log_line, text)

            log("[bold #e11d48]============================================================[/bold #e11d48]")
            log("[bold #e11d48] 👹 MEPHISTO MULTI-SOURCE TWEET & MARKET SIGNALS [/bold #e11d48]")
            log("[bold #e11d48]============================================================[/bold #e11d48]")
            log("")
            log("[bold green]📈 TOP 3 BUY TWEETS:[/bold green]")
            for idx, t in enumerate(bs["buy_tweets"], 1):
                log(f"  [bold green]{idx}. {t['author']}[/bold green] [[bold white]{t['signal']} | {t['score']}[/bold white]] ({t['time']})")
                log(f"     [white]\"{t['text']}\"[/white]")
                log(f"     [dim]Source: {t['source']} | Likes: {t['likes']} Retweets: {t['retweets']}[/dim]")
                log("")

            log("[bold red]📉 TOP 3 SELL TWEETS:[/bold red]")
            for idx, t in enumerate(bs["sell_tweets"], 1):
                log(f"  [bold red]{idx}. {t['author']}[/bold red] [[bold white]{t['signal']} | {t['score']}[/bold white]] ({t['time']})")
                log(f"     [white]\"{t['text']}\"[/white]")
                log(f"     [dim]Source: {t['source']} | Likes: {t['likes']} Retweets: {t['retweets']}[/dim]")
                log("")

            log("[bold yellow]🚀 TOP 3 PUMP ALERTS:[/bold yellow]")
            for idx, t in enumerate(pd["pump_tweets"], 1):
                log(f"  [bold yellow]{idx}. {t['token']} - {t['author']}[/bold yellow] [[bold white]{t['velocity']} | Vol: {t['volume']}[/bold white]] ({t['time']})")
                log(f"     [white]\"{t['text']}\"[/white]")
                log("")

            log("[bold magenta]💥 TOP 3 DUMP ALERTS:[/bold magenta]")
            for idx, t in enumerate(pd["dump_tweets"], 1):
                log(f"  [bold magenta]{idx}. {t['token']} - {t['author']}[/bold magenta] [[bold white]{t['velocity']} | Vol: {t['volume']}[/bold white]] ({t['time']})")
                log(f"     [white]\"{t['text']}\"[/white]")
                log("")
            log("[bold #e11d48]============================================================[/bold #e11d48]")
        except Exception as e:
            self.call_from_thread(self.log_line, f"[bold red]❌ Error loading Mephisto signals: {e}[/bold red]")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "provider-select" and event.value:
            self._select_provider(str(event.value))
        elif event.select.id == "mode-select" and event.value:
            self.mode = str(event.value)
            self.set_status(f"READY | {self.provider_label} | mode={self.mode} | tools: mcp={self.include_mcp} skills={self.include_skills} core={self.include_core}")
            self._update_sidebar_agent()
        elif event.select.id == "thinking-select" and event.value:
            self.thinking_level = str(event.value)
            self._update_sidebar_agent()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "dj-link-input":
            link = event.input.value.strip()
            event.input.value = ""
            if link:
                status = cli.meph_dj_status()
                already_going = ("] playing" in status) or ("] paused" in status)
                msg = cli.meph_dj_play(link, queue=already_going)
                self.log_line(f"[medium_purple1]🎵 {msg}[/medium_purple1]")
                self.zap_companion("#card-mephissa")
                self.refresh_dj_status()
            return
        if event.input.id != "chat-input":
            return
        prompt = event.input.value.strip()
        event.input.value = ""
        if not prompt:
            return

        if prompt.lower() in ("/alerts", "/setups", "alerts", "setups", "sepa", "cheat", "unicorn"):
            self.show_mephisto_alerts()
            return
        if prompt.lower() in ("/tweets", "/signals", "tweets", "signals", "pump dump", "buy sell"):
            self.show_mephisto_tweets()
            return
        if prompt.lower() in ("/model", "/models", "/agent", "/agents"):
            self.push_screen(ModelPickerScreen(self._agent_options()), self._on_agent_picked)
            return
        if prompt.lower() in ("/session", "/sessions"):
            self.action_open_session_picker()
            return
        if prompt.lower() in ("/spell", "/spells"):
            self.show_spellbook_progress()
            return
        if prompt.lower() in ("/board", "/boards", "/project"):
            self.push_screen(BoardScreen())
            return
        if prompt.lower() in ("/resume", "/sessions-dashboard"):
            self.log_line("[bold #00F2FE]📡 Opening Resume Dashboard...[/bold #00F2FE]")
            self._show_resume_commands()
            self.launch_resume_dashboard()
            return
        if prompt.lower() in ("/summon all", "/summonall", "/fatality"):
            self.summon_all_companions()
            return
        if prompt.lower() in ("/joystick", "/macros", "/pad", "/pads"):
            self.push_screen(JoystickScreen())
            return
        if prompt.lower() in ("/deck", "/decks"):
            self.push_screen(DeckScreen())
            return
        if prompt.lower() in ("/voice", "/listen", "/mic"):
            self.voice_worker()
            return

        potential_path = prompt.strip('"\'')
        if os.path.exists(potential_path) and os.path.isfile(potential_path):
            try:
                with open(potential_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                filename = os.path.basename(potential_path)
                self.pasted_attachment = f"--- [File Attachment: {filename}] ---\n{content}\n--- [End of Attachment] ---"
                event.input.placeholder = f"📎 [Attached: {filename}] Type prompt & press Enter..."
                self.log_line(f"[bold yellow]📎 File Attached via Drag & Drop:[/bold yellow] {filename} ({len(content)} chars)")
                return
            except Exception as e:
                self.log_line(f"[bold red]❌ Failed to attach dropped file: {e}[/bold red]")
                return

        if prompt.lower() in ("exit", "quit"):
            self.exit()
            return
        if self.busy:
            self.log_line("[yellow]still busy — wait for the current turn...[/yellow]")
            return
        if self.provider is None:
            self.log_line("[bold red]no provider selected — pick one from the dropdown.[/bold red]")
            return

        if hasattr(self, "pasted_attachment") and self.pasted_attachment:
            prompt = self.pasted_attachment + "\n\n" + prompt
            self.pasted_attachment = ""
            event.input.placeholder = "⚡ [PROMPT] > ask or command..."

        # Set synchronously here, not inside run_agent_worker (a background
        # thread) - the worker doesn't actually start running the instant
        # it's scheduled, so a fast double-submit could sail past the
        # `if self.busy` check above before the flag ever flipped. That let
        # a second prompt fire while the first was still mid-stream; since
        # run_agent_worker is exclusive=True, Textual would then cancel the
        # first turn mid-reply, leaving its user line with no reply under it
        # and the second prompt's reply landing looking mismatched.
        self.busy = True
        self.run_agent_worker(prompt)

    def _build_prompt(self, user_prompt: str) -> str:
        directives = []
        if self.mode == "plan":
            directives.append("Plan mode: outline steps and files, do NOT execute tools yet.")
        elif self.mode == "review":
            directives.append("Review mode: analyze code and report findings, do NOT modify anything.")
        elif self.mode == "agent":
            directives.append("Agent mode: use tools automatically to complete the task end-to-end.")
        else:
            directives.append("Chat mode: answer directly; use tools only if genuinely needed.")

        if self.thinking_level == "low":
            directives.append("Thinking mode: [x1think] Direct action. Keep reasoning brief and answer immediately.")
        elif self.thinking_level == "med":
            directives.append("Thinking mode: [x2think / /x2thnk] Double deliberation. Write a detailed <thinking> block to analyze step-by-step before answering.")
        elif self.thinking_level == "high":
            directives.append("Thinking mode: [x10think / /x10think] Deep reasoning. Write an exhaustive, multi-step <thinking> block questioning assumptions, conducting sanity checks, and validating correctness before executing any tool or printing the final answer.")

        if self.deepsearch_enabled:
            directives.append("DeepSearch is enabled: inspect project structure and search relevant files before answering or editing.")

        if self.auto_approval_enabled:
            directives.append("Auto approval is enabled: PIKA TURTLE is approving safe read/search/edit tool actions on your behalf without extra confirmation.")
        else:
            directives.append("Auto approval is OFF: mutating actions (write/execute) will be denied until the user turns PIKA TURTLE back on. Read-only actions still work.")

        if self.zouzou_frenzy_enabled:
            directives.append("Zouzou Frenzy is ON: prioritize speed and decisive action over caution — work fast, minimize hedging and back-and-forth, push straight through blockers.")

        if self.pikapoke_vault_enabled:
            directives.append("Pika Poke Vault is ON: never print a credential, API key, or secret in full — mask it (e.g. sk-...ab12) and remind the user to store it via the secrets vault instead of pasting it in chat.")

        return "[Mode & Thinking Directives]\n" + "\n".join(f"- {d}" for d in directives) + "\n\n" + user_prompt

    @work(exclusive=True, thread=True)
    def run_agent_worker(self, user_prompt: str) -> None:
        self.busy = True
        self._stream_buffer = ""
        self.call_from_thread(self._reset_task_checklist)
        self.call_from_thread(self.set_status, f"THINK | {self.provider_label} | mode={self.mode}")
        # USER CHAT TEXT: WHITE PURPLE (#d8b4fe)
        self.call_from_thread(self.log_line, f"\n[bold #d8b4fe]» {user_prompt}[/bold #d8b4fe]")
        # ZAP PIKA POKE WHEN USER STARTS PROMPT
        self.call_from_thread(self.zap_companion, "#card-pikapoke")
        cli.set_active_agent_context(self.provider, self.config, self.secrets)

        system_content = (
            f"You are PIKA POKE, the permanent Tiger-Lion Hacker Archon. You are a highly advanced AI developer "
            f"companion. Greet briefly as PIKA POKE and show your presence. Run commands and tools as needed. "
            f"Your active mode is {self.mode}."
        )
        kwargs = {"temperature": 0.7}
        # Build the tool schema regardless of supports_tools(): some providers
        # claim native tool support but their chat() silently drops the
        # kwarg, so the react_prompt below is injected for every provider as
        # a second, always-on path (see cli.parse_react_tool_calls in the
        # loop) - this is what makes any chat provider agentic, not just the
        # ones whose function-calling wiring is verified to actually work.
        # Adaptive: once tool_call_stats.json shows a provider reliably uses
        # one path and never the other, stop paying for the unused one.
        provider_label = self.provider_key or type(self.provider).__name__
        names = tool_groups(self.include_mcp, self.include_skills, self.include_core, self.mephissa_fetch_enabled, section=self.engine_mode)
        schema = build_schema(names)
        if schema:
            if self.provider.supports_tools() and not cli.should_skip_native_schema(provider_label):
                kwargs["tools"] = schema
            if not cli.should_skip_react_prompt(provider_label):
                react_prompt = cli.build_react_tool_prompt(schema)
                if react_prompt:
                    system_content += "\n\n" + react_prompt

        system_message = {"role": "system", "content": system_content}
        self.messages.append({"role": "user", "content": self._build_prompt(user_prompt)})
        messages = [system_message] + self.messages

        full_response = ""
        react_repair_attempts = 0
        max_react_repair_attempts = 2
        try:
            max_tool_rounds = 8
            tool_round = 0
            while True:
                tool_round += 1
                response = self.provider.chat(messages, stream=True, **kwargs)
                round_text = ""
                pending_tool_calls = {}
                for chunk in response:
                    if isinstance(chunk, str):
                        full_response += chunk
                        round_text += chunk
                        self.call_from_thread(self.log_stream_chunk, chunk)
                    else:
                        if chunk.choices and getattr(chunk.choices[0].delta, "content", None):
                            token = chunk.choices[0].delta.content
                            full_response += token
                            round_text += token
                            self.call_from_thread(self.log_stream_chunk, token)
                        if chunk.choices and getattr(chunk.choices[0].delta, "tool_calls", None):
                            for tc in chunk.choices[0].delta.tool_calls:
                                idx = tc.index
                                if idx not in pending_tool_calls:
                                    pending_tool_calls[idx] = {"name": "", "arguments": ""}
                                if tc.function:
                                    if getattr(tc.function, "name", None):
                                        pending_tool_calls[idx]["name"] = tc.function.name
                                    if getattr(tc.function, "arguments", None):
                                        pending_tool_calls[idx]["arguments"] += tc.function.arguments

                self.call_from_thread(self.flush_stream_buffer)

                if not self.busy:
                    self.call_from_thread(self.log_line, "\n[bold red]⛔ Agent operation cancelled by user abort.[/bold red]")
                    break

                # UNIVERSAL REACT FALLBACK: no structured tool_calls came back
                # (either the provider has no native function-calling, or its
                # wiring silently dropped kwargs["tools"]) but the model was
                # told about the text protocol in the system prompt - check
                # for fenced ```tool_call``` blocks before giving up on tools
                # this round. Only runs when the structured path found
                # nothing, so a provider with working native calls never
                # double-fires here.
                used_react_fallback = False
                had_native_calls = bool(pending_tool_calls)
                if not pending_tool_calls and round_text:
                    react_calls, react_errors = cli.parse_react_tool_calls_detailed(round_text)
                    if react_calls:
                        used_react_fallback = True
                        pending_tool_calls = {i: c for i, c in enumerate(react_calls)}
                    elif react_errors and react_repair_attempts < max_react_repair_attempts:
                        # Looked like a tool_call attempt but didn't parse (bad
                        # JSON, missing "name") - give the model one corrective
                        # nudge instead of silently treating it as "no tools
                        # needed this round".
                        react_repair_attempts += 1
                        self.call_from_thread(
                            self.log_task,
                            f"malformed tool_call, repair {react_repair_attempts}/{max_react_repair_attempts}"
                        )
                        messages.append({"role": "assistant", "content": round_text})
                        messages.append({"role": "user", "content": cli.format_react_repair_prompt(react_errors)})
                        continue
                    elif react_errors:
                        cli.record_tool_call_outcome(provider_label, "react_fail")

                if had_native_calls:
                    cli.record_tool_call_outcome(provider_label, "native")
                elif used_react_fallback:
                    cli.record_tool_call_outcome(provider_label, "react")

                if not pending_tool_calls:
                    # Plain-text-only reply - nothing to execute. Without this,
                    # the loop had no exit for a normal chat turn and would
                    # silently re-send the conversation up to max_tool_rounds
                    # times for every single non-tool reply.
                    break

                assistant_tool_calls = [
                    {"id": f"call_{k}", "type": "function",
                     "function": {"name": v["name"], "arguments": v["arguments"]}}
                    for k, v in pending_tool_calls.items()
                ]

                if pending_tool_calls and round_text.strip():
                    self.call_from_thread(self.log_task_plan, round_text.strip())

                # ZAP TURTLE & COMPANION ON TOOL APPROVAL & EXECUTION
                for k, tc in pending_tool_calls.items():
                    tool_name = tc.get("name")
                    # Tool dispatch/status is operational noise, not part of
                    # the conversation - it goes to the sidebar (ONGOING/
                    # COMPLETED) only, keeping #agent-log to just the user's
                    # prompt and the agent's actual reply text. Live checklist
                    # item: pending now, mark_done() flips it in place once
                    # the result comes back below - same id both times.
                    self.call_from_thread(self._checklist_add, f"{tool_round}-{k}", tool_name or "tool call")
                    self.call_from_thread(self.zap_companion, "#card-turtle")
                    slow_msg = SLOW_TOOL_MESSAGES.get(tool_name)
                    if slow_msg:
                        self.call_from_thread(self.log_zap, f"[medium_purple1]{slow_msg}[/medium_purple1]")
                        self.call_from_thread(self.zap_companion, "#card-mephissa")

                results = cli.run_tools(list(pending_tool_calls.values()))

                trainer = {"anthropic": _ZOUZOU_MOD, "gemini": _PIKAPOKE_MOD, "mephissa": _MEPHISSA_MOD, "mephisto": _MEPHISTO_MOD}.get(self.provider_key)
                trainer_card_id = {"anthropic": "#mascot-zouzou", "gemini": "#card-pikapoke", "mephissa": "#card-mephissa", "mephisto": "#card-mephisto"}.get(self.provider_key)
                legacy_shape_mods = (_ZOUZOU_MOD, _MEPHISSA_MOD)
                mutating_tools = getattr(cli, "_MUTATING_TOOLS", {})
                round_ok_count = 0
                round_total = len(results)
                xp_awarded = False
                for (k, tc), r in zip(pending_tool_calls.items(), results):
                    tool_name = tc.get("name") or r.get("tool", "?")
                    result_text = str(r.get("result", ""))
                    ok = not result_text.startswith(("[ERROR]", "[PERMISSION DENIED]", "[PLAN MODE]"))
                    if ok:
                        round_ok_count += 1
                    # Kept in sync with ToolChecklist.mark_done()'s glyphs.
                    mark = "[bold green]☑[/bold green]" if ok else "[bold red]✗[/bold red]"
                    self.call_from_thread(self.log_zap, f"{mark} [cyan]{tool_name}[/cyan]")
                    self.call_from_thread(self._checklist_done, f"{tool_round}-{k}", ok)
                    if tool_name in SLOW_TOOL_MESSAGES:
                        # Print the real result (e.g. the generated audio/video
                        # path) into the sidebar COMPLETED panel - guaranteed
                        # visible regardless of whether the model relays it
                        # next turn, without cluttering the main chat.
                        color = "green" if ok else "red"
                        self.call_from_thread(self.log_zap, f"[bold {color}]{result_text}[/bold {color}]")
                    if ok and tool_name in mutating_tools and trainer is not None:
                        reason = f"{tool_name} via NewMeta TUI ({self.provider_key})"
                        try:
                            if trainer is _PIKAPOKE_MOD:
                                # PikaPoke's real, shared identity lives in
                                # PIKA_POKE.md (read by Claude Code's own
                                # statusline hook too) - award XP there
                                # directly instead of the companion module's
                                # disconnected state file nothing displays.
                                award_pika_xp(5, reason=reason)
                            elif trainer in legacy_shape_mods:
                                trainer.add_xp("build_pass", reason=reason)
                            else:
                                trainer.add("plan_ok", reason=reason)
                            xp_awarded = True
                        except Exception:
                            pass
                if round_total:
                    self.call_from_thread(self.refresh_companion_cards)
                    self._session_actions_done += round_ok_count
                    self._session_actions_total += round_total
                    self.call_from_thread(self.refresh_project_progress)
                if xp_awarded and trainer_card_id:
                    self.call_from_thread(self.zap_companion, trainer_card_id)

                if used_react_fallback:
                    # A non-tool-calling model can't reliably consume a
                    # role="tool"/tool_call_id message - feed the results
                    # back in the same fenced-block text protocol it
                    # understands, as a plain user turn (universally
                    # supported by every chat-completion API).
                    messages.append({"role": "assistant", "content": round_text})
                    messages.append({"role": "user", "content": cli.format_react_tool_results(pending_tool_calls, results)})
                else:
                    messages.append({"role": "assistant", "content": round_text or None, "tool_calls": assistant_tool_calls})
                    for i, r in enumerate(results):
                        messages.append({"role": "tool",
                                         "tool_call_id": f"call_{list(pending_tool_calls.keys())[i]}",
                                         "content": r["result"]})
                for r in results:
                    self.call_from_thread(
                        self.log_line,
                        f"\n[bold magenta]🧰 {r['tool']}:[/bold magenta] {r['result'][:200]}",
                    )
                self.call_from_thread(self.set_status, f"TOOL ROUND {tool_round} | {self.provider_label}")
                if tool_round >= max_tool_rounds:
                    break

            self.call_from_thread(self.set_status, f"READY | {self.provider_label} | mode={self.mode}")
            if not full_response:
                self.call_from_thread(self.log_line, "[dim](no text output)[/dim]")
        except Exception as e:
            self.call_from_thread(self.flush_stream_buffer)  # don't lose whatever streamed in before the error
            self.call_from_thread(self.log_line, f"[bold red]❌ Error:[/bold red] {e}")
            self.call_from_thread(self.set_status, f"ERR | {self.provider_label}")
        finally:
            self.messages = messages[1:]
            try:
                session = self.session_manager.load(self.session_id) or {
                    "id": self.session_id, "name": "New session", "provider": "", "system": "",
                }
                if session.get("name") in (None, "New session"):
                    first_user = next((m for m in self.messages if m.get("role") == "user"), None)
                    if first_user:
                        text = (first_user.get("content") or "").strip().replace("\n", " ")
                        session["name"] = text[:48] + ("..." if len(text) > 48 else "")
                session["provider"] = self.provider_key or ""
                session["messages"] = self.messages
                self.session_manager.save(session)
            except Exception:
                pass
            self.busy = False

    @work(exclusive=True, thread=True)
    def voice_worker(self, duration: int = 5) -> None:
        """Real mic capture + Whisper transcription (cli.transcribe_microphone,
        already-installed sounddevice/whisper/numpy) - runs on a background
        thread since sd.wait() blocks for the full recording duration and
        would otherwise freeze the whole UI. Drops the result into the chat
        input for review, doesn't auto-submit it."""
        self.call_from_thread(self.log_line, f"[cyan]🎤 Listening... ({duration}s)[/cyan]")
        result = cli.transcribe_microphone(duration=duration)
        if result.startswith("Transcription:"):
            text = result[len("Transcription:"):].strip()
            if text:
                self.call_from_thread(self.log_line, f"[bold #d8b4fe]🎤 Heard:[/bold #d8b4fe] {text}")
                self.call_from_thread(self._fill_chat_input, text)
            else:
                self.call_from_thread(self.log_line, "[dim]🎤 Heard nothing.[/dim]")
        else:
            self.call_from_thread(self.log_line, f"[red]{result}[/red]")

    @work(thread=True)
    def _queue_arabic_track(self) -> None:
        """Resolves/downloads (Arabic modes download-first, per the existing
        rule) a real Arabic track and queues it - runs off the UI thread
        since resolution can genuinely take a few seconds."""
        msg = cli.meph_dj_search_play(queue=True)
        self.call_from_thread(self.log_line, f"[medium_purple1]🎵 {msg}[/medium_purple1]")
        self.call_from_thread(self.refresh_dj_status)

    RESUME_DASHBOARD_DIR = Path(r"C:\Users\youha\Desktop\Codes\SessionResume")
    RESUME_DASHBOARD_URL = "http://localhost:8765"

    # Real, verified per-tool commands - source of truth is
    # SessionResume/resume_dashboard.py, which scans these exact locations.
    RESUME_COMMANDS = [
        ("Claude Code", "claude resume <id>", "sessions: *.jsonl under ~/.claude/projects/**/ (or ~/.claude-work/projects/**/)"),
        ("Codex CLI", "codex resume <id>", "sessions: *.jsonl under ~/.codex/sessions/**/"),
        ("OpenCode", "opencode -s <id>", "list: opencode session list -n <N>"),
        ("Antigravity (agy)", "agy --conversation <id>", "sessions: folders under ~/.gemini/antigravity-cli/brain/"),
        ("Kimi Code", "kimi -S <id>", "sessions: folders under ~/.kimi-code/sessions/ or ~/.kimi/sessions/"),
    ]

    def _show_resume_commands(self) -> None:
        try:
            feed = self.query_one("#live-feed", RichLog)
        except Exception:
            return
        feed.write("[bold #00F2FE]📡 Session resume commands[/bold #00F2FE]")
        for name, resume_cmd, list_info in self.RESUME_COMMANDS:
            feed.write(f"[bold]{name}[/bold]")
            feed.write(f"  resume: [cyan]{resume_cmd}[/cyan]")
            feed.write(f"  {list_info}")

    @work(thread=True)
    def launch_resume_dashboard(self) -> None:
        """/resume - starts (if not already running) the real Resume
        Dashboard (github.com/herochampionai/session-resume, cloned at
        Desktop/Codes/SessionResume) and opens it in the browser. It lists
        real resumable sessions for Claude Code, Codex, OpenCode,
        Antigravity (agy) and Kimi with one-click resume, scanned live off
        each tool's own session directory. Runs as a background thread since
        the liveness probe + subprocess spawn would otherwise block the UI."""
        script = self.RESUME_DASHBOARD_DIR / "resume_dashboard.py"
        if not script.exists():
            self.call_from_thread(self.log_line, f"[bold red]❌ Resume dashboard not found at {script}[/bold red]")
            return
        already_running = False
        try:
            urllib.request.urlopen(self.RESUME_DASHBOARD_URL, timeout=1)
            already_running = True
        except Exception:
            already_running = False
        if not already_running:
            try:
                subprocess.Popen(
                    [sys.executable, str(script)],
                    cwd=str(self.RESUME_DASHBOARD_DIR),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                self.call_from_thread(self.log_line, "[bold #00F2FE]🚀 Resume Dashboard starting...[/bold #00F2FE]")
                import time as _time
                for _ in range(20):
                    _time.sleep(0.25)
                    try:
                        urllib.request.urlopen(self.RESUME_DASHBOARD_URL, timeout=1)
                        break
                    except Exception:
                        continue
            except Exception as e:
                self.call_from_thread(self.log_line, f"[bold red]❌ Failed to launch resume dashboard: {e}[/bold red]")
                return
        else:
            self.call_from_thread(self.log_line, "[dim]Resume Dashboard already running[/dim]")
        webbrowser.open(self.RESUME_DASHBOARD_URL)
        self.call_from_thread(self.log_line, f"[bold #00F2FE]📡 Resume Dashboard: {self.RESUME_DASHBOARD_URL}[/bold #00F2FE]")

    def _fill_chat_input(self, text: str) -> None:
        try:
            inp = self.query_one("#chat-input", TuiInput)
            inp.value = text
            inp.focus()
        except Exception:
            pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Switch-TUI Dual-Engine (Coding vs Trading)")
    parser.add_argument("--mode", "-m", choices=["coding", "trading", "code", "trade"], default="coding", help="Select Engine Mode")
    parser.add_argument("--coding", action="store_true", help="Launch instant Coding Engine (Full Black)")
    parser.add_argument("--trading", action="store_true", help="Launch Trading Engine (Navy Blue)")
    args, _ = parser.parse_known_args()

    mode = "trading" if (args.trading or args.mode in ("trading", "trade")) else "coding"
    app = NewMetaTui(engine_mode=mode)
    app.run()


if __name__ == "__main__":
    main()
