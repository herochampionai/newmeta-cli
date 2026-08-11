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
import json
import threading
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Header, Footer, Static, Input, RichLog, Button, Select, OptionList
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

sys.path.insert(0, str(Path.home() / "Desktop" / "pika-poke"))
from spellbook.spells import (
    SPELLS, MINI_SPELLS, MEPHISSA_SPELLS, MEPHISSA_MINI_SPELLS, ZOUZOU_SPELLS,
)

ALL_SPELLS = SPELLS + MINI_SPELLS + MEPHISSA_SPELLS + MEPHISSA_MINI_SPELLS + ZOUZOU_SPELLS


def tool_groups(include_mcp: bool, include_skills: bool, include_core: bool, include_fetch: bool = True) -> list[str]:
    names = []
    for name in cli.TOOL_REGISTRY:
        if name.startswith("mcp__"):
            if include_mcp:
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


def render_pika_bar(ctx_pct: int = 22, active_agents: int = 1) -> str:
    stats = get_pika_stats()
    lvl = stats.get("level", 3)
    xp = stats.get("xp", 1522)
    max_xp = stats.get("max_xp", 3000)
    pct = int((xp / max_xp) * 10) if max_xp else 5
    bar_str = "#" * pct + "-" * (10 - pct)
    tot_saved = stats.get("saved_tokens_total", "106.0k")
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


def render_vertical_bar(pct: float, height: int = 2, width: int = 14) -> str:
    """Render a horizontal XP gauge (rotated from the old bottom-up vertical
    fill): `height` identical rows, each filling left-to-right as pct
    (0.0-1.0) increases, so the block reads as one wide bar instead of a
    solid vertical slab. '\\n'-joined, one row per line."""
    pct = max(0.0, min(1.0, pct))
    filled_cols = round(pct * width)
    row = ("█" * filled_cols) + ("░" * (width - filled_cols))
    return "\n".join([row] * height)


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
    pct = s.get("xp", 0) / s.get("max_xp", 3000)
    return f"[bold #fb923c]{render_vertical_bar(pct)}[/bold #fb923c]"


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
    pct = s.get("xp", 0) / s.get("max_xp", 3000)
    return f"[bold #38bdf8]{render_vertical_bar(pct)}[/bold #38bdf8]"


def get_pikapoke_stats() -> dict:
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
    pct = s.get("xp", 0) / s.get("max_xp", 3000)
    return f"[bold #ff023a]{render_vertical_bar(pct)}[/bold #ff023a]"


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
    pct = s.get("xp", 0) / s.get("max_xp", 3000)
    return f"[bold #a78bfa]{render_vertical_bar(pct)}[/bold #a78bfa]"


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
    xp = s.get("xp", 1850)
    max_xp = s.get("max_xp", 3000)
    pct = (xp / max_xp) if max_xp else 0.6
    return f"[bold #e11d48]{render_vertical_bar(pct)}[/bold #e11d48]"


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
        if event.key == "alt+v":
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


class ModelPickerScreen(ModalScreen[str]):
    """Full-window agent/model picker - like Kilo Code's CLI, which opens a
    dedicated window for model selection instead of a cramped inline
    dropdown. Type to filter, Enter/click to launch, Esc to cancel."""

    DEFAULT_CSS = """
    ModelPickerScreen {
        align: center middle;
        background: #000000 60%;
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
        background: #000000 60%;
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
        background: #000000 60%;
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
        background: #000000 60%;
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

    /* "Spell & Magic" footer chips - was flat typewriter-style key hints */
    /* Neon modern buttons, synced to the Mephissa DJ deck (cyan #00F2FE + red #ff023a glow) */
    /* Floating dock: rounded box lifted off the screen edge, not a flat docked strip */
    Footer {
        background: #0a0003;
        border: round #00F2FE;
        height: 3;
        margin: 0 1 1 1;
    }
    FooterKey {
        background: #140005;
        border: tall #00F2FE;
        margin: 0 1 0 0;
        padding: 0 1;
    }
    FooterKey .footer-key--key {
        background: #00F2FE;
        color: #050505;
        text-style: bold;
        padding: 0 1;
    }
    FooterKey .footer-key--description {
        background: #140005;
        color: #F8F9FA;
        text-style: bold;
        padding: 0 1;
    }
    FooterKey:hover {
        background: #00F2FE;
        border: tall #00F2FE;
    }
    FooterKey:hover .footer-key--key, FooterKey:hover .footer-key--description {
        background: #00F2FE;
        color: #050505;
    }

    /* CODING THEME (Full Dark Black + Neon Red) */
    Screen.theme-coding {
        background: #000000;
    }
    Screen.theme-coding #main-container, Screen.theme-coding #log-scroll, Screen.theme-coding #agent-log {
        background: #000000;
    }
    Screen.theme-coding #sidebar {
        background: #050505;
        border: round #00F2FE;
    }
    Screen.theme-coding #log-scroll {
        border: round #00F2FE;
        scrollbar-color: #00F2FE;
    }

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
    }
    #provider-select { width: 24; margin: 0 1 0 0; }
    #mode-select { width: 13; margin: 0 1 0 0; }
    #thinking-select { width: 13; margin: 0 1 0 0; }
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
        height: 2;
        margin: 0;
        background: #f59e0b;
        color: #1a0b2e;
        text-style: bold;
        border: none;
    }
    #btn-launch-agent:hover {
        background: #fbbf24;
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
        height: 14;
        background: #0a0003;
        border: round #00F2FE;
        margin-bottom: 1;
    }
    .companion-art {
        height: 7;
        content-align: center middle;
    }
    .companion-line {
        height: 1;
        content-align: center middle;
    }
    .companion-bar-vertical {
        height: 2;
        content-align: center middle;
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
        height: 1;
        border: none;
        padding: 0;
        content-align: center middle;
    }
    .ultimate-chip.on { background: #00F2FE; color: #F8F9FA; }
    .ultimate-chip.off { background: #200008; color: #64748b; }

    #dj-jog-deck {
        height: 5;
        layout: horizontal;
        margin-bottom: 1;
        border: round #00F2FE;
        background: #0a0003;
    }
    #jog-wheel-art {
        width: 14;
        height: 4;
        content-align: center middle;
        color: #00F2FE;
        text-style: bold;
    }
    #dj-controls-box {
        width: 1fr;
        height: 4;
        layout: vertical;
    }
    #dj-buttons-row {
        height: 2;
        layout: horizontal;
    }
    .dj-knob-btn {
        width: 1fr;
        min-width: 4;
        height: 2;
        background: #1c0006;
        color: #F8F9FA;
        border: heavy #00F2FE;
        text-style: bold;
        margin-right: 1;
    }
    .dj-knob-btn:hover { background: #00F2FE; color: #F8F9FA; }
    #dj-now-playing {
        height: 2;
        color: #ff3366;
        padding: 0 1;
        content-align: left middle;
    }

    #task-log {
        height: 4;
        border: round #00F2FE;
        color: #cbd5e1;
        margin-bottom: 1;
        scrollbar-background: #000000;
        scrollbar-color: #00F2FE;
    }
    #action-plan {
        height: 4;
        border: round #00F2FE;
        color: #cbd5e1;
        margin-bottom: 1;
        scrollbar-background: #000000;
        scrollbar-color: #00F2FE;
    }
    #zap-log {
        height: 4;
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
        height: 3;
        layout: horizontal;
        margin: 0 1 1 1;
        border: round #ff2244;
        background: #0a0003;
    }
    #chat-input {
        width: 1fr;
        height: 3;
        border: none;
        background: #0a0003;
        color: #ff2244;
        text-style: bold;
    }
    #prompt-send-btn {
        width: 10;
        height: 3;
        background: #00F2FE;
        color: #F8F9FA;
        border: heavy #00F2FE;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("alt+v", "paste_clipboard", "📋 Paste/Attach", show=True),
        Binding("escape", "handle_esc", "⛔ Nudge/Abort", show=True),
        Binding("f5", "dj_playpause", "▶ Play/Pause", show=True),
        Binding("f6", "dj_stop", "⏹ Stop", show=True),
        Binding("f7", "dj_skip", "⏭ Skip", show=True),
        Binding("f8", "dj_seek_back", "⏪ -10s", show=True),
        Binding("f9", "dj_seek_fwd", "⏩ +10s", show=True),
        Binding("f10", "dj_transition", "🔄 Transition", show=True),
        Binding("tab", "next_companion", "Next Companion", show=True),
        Binding("shift+tab", "prev_companion", "Prev Companion", show=False),
        Binding("enter", "summon_selected", "Summon", show=False),
        Binding("alt+m", "reopen_mode_picker", "Change Hook Mode", show=False),
    ]

    COMPANION_CARD_IDS = ["#card-pikapoke", "#card-turtle", "#mascot-zouzou", "#card-mephissa", "#card-mephisto"]
    COMPANION_KEYS = ["pikapoke", "turtle", "zouzou", "mephissa", "mephisto"]
    # Turtle has no chat provider of its own (GUARD role) — summoning him
    # toggles auto-approval instead of opening the agent picker.
    COMPANION_PROVIDER_MAP = {"pikapoke": "gemini", "mephissa": "mephissa", "mephisto": "mephisto", "zouzou": "anthropic"}

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
        self.mode = "chat"

        # Persistent multi-turn session (backs the /sessions picker).
        self.session_manager = cli.SessionManager(cli.SESSIONS_DIR)
        self.session_id = self.session_manager.create(name="New session", provider="", system="")
        self.messages: list[dict] = []

        # Tab / Shift+Tab cycles a highlight across the companion cards.
        self._companion_selected_idx = 0

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
        """Write one timestamped line to the sidebar LIVE FEED panel."""
        try:
            label = self._COMPANION_LABELS.get(companion_id, companion_id.lstrip('#'))
            feed = self.query_one("#live-feed", RichLog)
            feed.write(f"[dim]{time.strftime('%H:%M:%S')}[/dim] [{label}] {action}")
        except Exception:
            pass

    def zap_companion(self, companion_id: str, action: str = "⚡ active") -> None:
        """ZAP HIGHLIGHT FLASH EFFECT: Triggers bright highlight zap animation on card."""
        try:
            card = self.query_one(companion_id)
            card.add_class("flash")
            self.set_timer(0.8, lambda: card.remove_class("flash"))
        except Exception:
            pass
        self._live_feed(companion_id, action)

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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Horizontal(id="header-ticker-row"):
            yield Static(no_wrap_text(f"🪙 BTC: {self._btc_price}  |  🧈 GOLD: {self._gold_price}  |  🌊 SWEEP: {self._top_sweep}"), id="price-ticker")
            yield Static(no_wrap_text(self._market_pulse_text()), id="market-pulse")
            yield Static(no_wrap_text(self._trading_setup), id="trading-setups-banner")
            yield Static("", id="dj-heartbeat-header")

        with Horizontal(id="dj-toolbar-top"):
            yield Input(placeholder="🔗 paste a link to play it...", id="dj-link-input")
            yield Button("🔴 LINK", id="btn-dj-play-link", classes="knob-btn")
            yield Button("💻 CODE", id="btn-mode-coding", classes="knob-btn")
            yield Button("📈 TRADE", id="btn-mode-trading", classes="knob-btn")
            yield Button("🔔 ALERTS", id="btn-meph-alerts", classes="knob-btn")
            yield Button("🐦 SIGNALS", id="btn-meph-tweets", classes="knob-btn")
            yield Button("🎚️ MIX", id="btn-dj-mix", classes="knob-btn")
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
                value="chat",
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
                    # 1. PIKA POKE
                    with Vertical(classes="companion-card-tall", id="card-pikapoke"):
                        yield Static("[bold #ff023a]   .       .   [/bold #ff023a]\n[bold #ff023a]  / \\     / \\  [/bold #ff023a]\n[bold #ff023a] |   |___|   | [/bold #ff023a]\n[bold #ff023a] | ( o _ o ) | [/bold #ff023a]\n[bold #ff023a] |/    _    \\| [/bold #ff023a]\n[bold #ff023a] |  \\_____/  | [/bold #ff023a]\n[bold #ff023a]  \\_________/  [/bold #ff023a]", classes="companion-art")
                        yield Static(no_wrap_text(pikapoke_name_markup()), id="pikapoke-name", classes="companion-line")
                        yield Static(no_wrap_text(pikapoke_bar_markup()), id="pikapoke-bar", classes="companion-bar-vertical")
                        with Horizontal(classes="companion-foot"):
                            yield Button("🗝️", id="btn-ultimate-pikapoke", classes="ultimate-chip")
                            yield Static(no_wrap_text(sparkline(self._pikapoke_xp_hist)), id="pikapoke-spark", classes="companion-spark")

                    # 2. PIKA TURTLE
                    with Vertical(classes="companion-card-tall", id="card-turtle"):
                        yield Static("[bold #38bdf8]      _____    [/bold #38bdf8]\n[bold #38bdf8]   .-'     '-. [/bold #38bdf8]\n[bold #38bdf8]  /           \\[/bold #38bdf8]\n[bold #38bdf8] |  _  ___  _  |[/bold #38bdf8]\n[bold #38bdf8] | '-'     '-' |[/bold #38bdf8]\n[bold #38bdf8]  \\           /[/bold #38bdf8]\n[bold #38bdf8]   '-._____.-' [/bold #38bdf8]", classes="companion-art")
                        yield Static(no_wrap_text(turtle_name_markup()), id="turtle-name", classes="companion-line")
                        yield Static(no_wrap_text(turtle_bar_markup()), id="turtle-bar", classes="companion-bar-vertical")
                        with Horizontal(classes="companion-foot"):
                            yield Button("🛡️", id="btn-ultimate-turtle", classes="ultimate-chip")
                            yield Static(no_wrap_text(sparkline(self._turtle_xp_hist)), id="turtle-spark", classes="companion-spark")

                    # 3. PIKA ZOUZOU
                    with Vertical(classes="companion-card-tall", id="mascot-zouzou"):
                        yield Static("[bold #fb923c]      /\\   /\\  [/bold #fb923c]\n[bold #fb923c]     //\\\\_//\\\\ [/bold #fb923c]\n[bold #fb923c]     \\_     _/ [/bold #fb923c]\n[bold #fb923c]      / * * \\  [/bold #fb923c]\n[bold #fb923c]     \\_\\ O /_/ [/bold #fb923c]", classes="companion-art")
                        yield Static(no_wrap_text(zouzou_info_markup()), id="zouzou-info", classes="companion-line")
                        yield Static(no_wrap_text(zouzou_bar_markup()), id="zouzou-bar", classes="companion-bar-vertical")
                        with Horizontal(classes="companion-foot"):
                            yield Button("🔥", id="btn-ultimate-zouzou", classes="ultimate-chip")
                            yield Static(no_wrap_text(sparkline(self._zouzou_xp_hist)), id="zouzou-spark", classes="companion-spark")

                    # 4. PIKA MEPHISSA (the DJ)
                    with Vertical(classes="companion-card-tall", id="card-mephissa"):
                        yield Static("[bold #a78bfa]╭ ─╮  ╭ ─╮   [/bold #a78bfa]\n[bold #a78bfa]  ╰🎶─🎶╯   [/bold #a78bfa]\n[bold #a78bfa][  █ ▘▝ █  ]       [/bold #a78bfa]\n[bold #a78bfa]   ⭕👅⭕[/bold #a78bfa]\n[bold #a78bfa]   🎧────🎧   [/bold #a78bfa]\n[bold #a78bfa]  ║◉║     ║◉║       [/bold #a78bfa]\n[bold #a78bfa]  ║─║     ║─║         [/bold #a78bfa]", classes="companion-art")
                        yield Static(no_wrap_text(mephissa_info_markup()), id="mephissa-info", classes="companion-line")
                        yield Static(no_wrap_text(mephissa_bar_markup()), id="mephissa-bar", classes="companion-bar-vertical")
                        with Horizontal(classes="companion-foot"):
                            yield Button("📥", id="btn-ultimate-mephissa", classes="ultimate-chip")
                            yield Static(no_wrap_text(sparkline(self._mephissa_xp_hist)), id="mephissa-spark", classes="companion-spark")

                    # 5. PIKA MEPHISTO
                    with Vertical(classes="companion-card-tall", id="card-mephisto"):
                        yield Static("[bold #e11d48]       ,----.. [/bold #e11d48]\n[bold #e11d48]      /   __  \\[/bold #e11d48]\n[bold #e11d48]     |  ( oo)  |[/bold #e11d48]\n[bold #e11d48]     _\\  \\__/  /_[/bold #e11d48]\n[bold #e11d48]    /  \\      /  \\[/bold #e11d48]", classes="companion-art")
                        yield Static(no_wrap_text(mephisto_info_markup()), id="mephisto-info", classes="companion-line")
                        yield Static(no_wrap_text(mephisto_bar_markup()), id="mephisto-bar", classes="companion-bar-vertical")
                        with Horizontal(classes="companion-foot"):
                            yield Button("⚡", id="btn-ultimate-mephisto", classes="ultimate-chip")
                            yield Static(no_wrap_text(sparkline(self._mephisto_xp_hist)), id="mephisto-spark", classes="companion-spark")

            with ScrollableContainer(id="log-scroll"):
                yield AgentLog(id="agent-log", wrap=True, highlight=True, markup=True)
            with Vertical(id="sidebar"):
                # DJ TURNTABLE JOG WHEEL DECK
                with Horizontal(id="dj-jog-deck"):
                    yield Static("[bold #ff023a]☯ JOG[/bold #ff023a]\n[bold white]TURNTABLE[/bold white]", id="jog-wheel-art")
                    with Vertical(id="dj-controls-box"):
                        with Horizontal(id="dj-buttons-row"):
                            yield Button("◀◀", id="btn-dj-seekback", classes="dj-knob-btn")
                            yield Button("▶", id="btn-dj-playpause", classes="dj-knob-btn")
                            yield Button("⏹", id="btn-dj-stop", classes="dj-knob-btn")
                            yield Button("▶▶", id="btn-dj-skip", classes="dj-knob-btn")
                        yield Static(no_wrap_text("🎵 stopped: (nothing)"), id="dj-now-playing")

                yield RichLog(id="task-log", wrap=True, markup=True)
                yield RichLog(id="action-plan", wrap=True, markup=True)
                yield RichLog(id="zap-log", wrap=True, markup=True)
                yield RichLog(id="live-feed", wrap=True, markup=True)
                yield Static(no_wrap_text(project_progress_bar(0, 0)), id="project-progress")

        with Horizontal(id="prompt-container"):
            yield TuiInput(placeholder="⚡ [PROMPT] > ask or command...", id="chat-input")
            yield Button("SEND", id="prompt-send-btn")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#chat-input", TuiInput).focus()
        self._sync_buttons()
        self._start_mcp_loader()
        try:
            if self.engine_mode == "trading":
                self.screen.add_class("theme-trading")
                self._start_price_fetcher()
            else:
                self.screen.add_class("theme-coding")
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
            self.query_one("#task-log", RichLog).border_title = "▶ ONGOING"
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
            self.log_line(f"[cyan]auto-selected provider:[/cyan] {first}")

        self.set_interval(5.0, self.rotate_tip)
        self.set_interval(30.0, self._refresh_trading_setup)
        self.set_interval(0.2, self._dj_beat_tick)
        self.rotate_tip()

    def rotate_tip(self) -> None:
        from explorer import NME_TIPS
        tip = NME_TIPS[self.tip_index]
        self.query_one("#tip", Static).update(f"💡 Tip: {tip}")
        self.tip_index = (self.tip_index + 1) % len(NME_TIPS)
        try:
            active_agents = 1 if self.busy else 0
            self.query_one("#pika-bar", Static).update(render_pika_bar(active_agents=active_agents))
            self.query_one("#price-ticker", Static).update(no_wrap_text(f"🪙 BTC: {self._btc_price}  |  🧈 GOLD: {self._gold_price}  |  🌊 SWEEP: {self._top_sweep}"))
            self.query_one("#market-pulse", Static).update(no_wrap_text(self._market_pulse_text()))
        except Exception:
            pass
        self.refresh_companion_cards()
        self.refresh_dj_status()

    def refresh_companion_cards(self) -> None:
        try:
            self.query_one("#zouzou-info", Static).update(no_wrap_text(zouzou_info_markup()))
            self.query_one("#zouzou-bar", Static).update(no_wrap_text(zouzou_bar_markup()))
            self._zouzou_xp_hist.append(get_zouzou_stats().get("xp", 0))
            self.query_one("#zouzou-spark", Static).update(no_wrap_text(sparkline(self._zouzou_xp_hist)))
        except Exception:
            pass
        try:
            self.query_one("#turtle-name", Static).update(no_wrap_text(turtle_name_markup()))
            self.query_one("#turtle-bar", Static).update(no_wrap_text(turtle_bar_markup()))
            self._turtle_xp_hist.append(get_turtle_stats().get("xp", 0))
            self.query_one("#turtle-spark", Static).update(no_wrap_text(sparkline(self._turtle_xp_hist)))
        except Exception:
            pass
        try:
            self.query_one("#pikapoke-name", Static).update(no_wrap_text(pikapoke_name_markup()))
            self.query_one("#pikapoke-bar", Static).update(no_wrap_text(pikapoke_bar_markup()))
            self._pikapoke_xp_hist.append(get_pikapoke_stats().get("xp", 0))
            self.query_one("#pikapoke-spark", Static).update(no_wrap_text(sparkline(self._pikapoke_xp_hist)))
        except Exception:
            pass
        try:
            self.query_one("#mephissa-info", Static).update(no_wrap_text(mephissa_info_markup()))
            self.query_one("#mephissa-bar", Static).update(no_wrap_text(mephissa_bar_markup()))
            self._mephissa_xp_hist.append(get_mephissa_stats().get("xp", 0))
            self.query_one("#mephissa-spark", Static).update(no_wrap_text(sparkline(self._mephissa_xp_hist)))
        except Exception:
            pass
        try:
            self.query_one("#mephisto-info", Static).update(no_wrap_text(mephisto_info_markup()))
            self.query_one("#mephisto-bar", Static).update(no_wrap_text(mephisto_bar_markup()))
            self._mephisto_xp_hist.append(get_mephisto_stats().get("xp", 1850))
            self.query_one("#mephisto-spark", Static).update(no_wrap_text(sparkline(self._mephisto_xp_hist)))
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
                jog_art_frames = [
                    "[bold #ff023a]☯ JOG[/bold #ff023a]\n[bold white]◐ SPIN ◑[/bold white]",
                    "[bold #ff023a]☯ JOG[/bold #ff023a]\n[bold white]◓ SPIN ◔[/bold white]",
                    "[bold #ff023a]☯ JOG[/bold #ff023a]\n[bold white]◑ SPIN ◐[/bold white]",
                    "[bold #ff023a]☯ JOG[/bold #ff023a]\n[bold white]◔ SPIN ◓[/bold white]",
                ]
                self.query_one("#jog-wheel-art", Static).update(jog_art_frames[self._jog_frame])
                beat = "[bold #ff023a]♥ MEPHISSA DJ PLAYING ♥[/bold #ff023a]" if self._heartbeat_frame else "[dim #ff023a]♥ MEPHISSA DJ PLAYING ♥[/dim #ff023a]"
                # ZAP MEPHISSA WHEN AUDIO PLAYS
                self.zap_companion("#card-mephissa")
            else:
                self.query_one("#jog-wheel-art", Static).update("[bold #ff023a]☯ JOG[/bold #ff023a]\n[dim]TURNTABLE[/dim]")
                beat = "[dim]♥ MEPHISSA DJ READY[/dim]"
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
        self.query_one("#task-log", RichLog).write(f"[yellow]•[/yellow] {_short_bullet(text)}")

    def log_task_plan(self, text: str) -> None:
        self.query_one("#action-plan", RichLog).write(f"[cyan]•[/cyan] {_short_bullet(text)}")

    def refresh_project_progress(self) -> None:
        try:
            self.query_one("#project-progress", Static).update(
                no_wrap_text(project_progress_bar(self._session_actions_done, self._session_actions_total))
            )
        except Exception:
            pass

    def set_status(self, text: str) -> None:
        self.query_one("#status-bar", Static).update(text)

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
        self.log_line(f"[yellow]⚡ provider:[/yellow] [bold]{self.provider_label}[/bold] | tools={self.provider.supports_tools()}")
        
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
            self.auto_approval_enabled = not self.auto_approval_enabled
            self._sync_auto_approval()
            self.log_line(f"[yellow]🛡️ Turtle auto-approval: {'ON' if self.auto_approval_enabled else 'OFF'}[/yellow]")
            self.zap_companion("#card-turtle")
            return
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
                if is_selected:
                    key = self.COMPANION_KEYS[i]
                    house = SUMMONS[key].house if key in SUMMONS else ""
                    color = HOUSE_COLORS.get(house, "#F8F9FA")
                    card.styles.border = ("heavy", color)
                    card.focus()
                else:
                    card.styles.border = ("round", "#00F2FE")
            except Exception:
                pass
        self._sync_active_summon()

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
        if btn.id == "btn-launch-agent":
            self.push_screen(ModelPickerScreen(self._agent_options()), self._on_agent_picked)
        elif btn.id == "btn-theme-picker":
            names = list(PALETTE_THEMES.keys())
            current = names[self._palette_idx] if 0 <= self._palette_idx < len(names) else None
            self.push_screen(ThemePickerScreen(names, current), self._on_theme_picked)
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
                status = cli.meph_dj_status()
                already_going = ("] playing" in status) or ("] paused" in status)
                msg = cli.meph_dj_play(link, queue=already_going)
                self.log_line(f"[medium_purple1]🎵 {msg}[/medium_purple1]")
                self.zap_companion("#card-mephissa")
                self.refresh_dj_status()
        elif btn.id == "btn-dj-lang":
            self.dj_lang = "ar" if self.dj_lang == "en" else "en"
            mode = "arabic_hits" if self.dj_lang == "ar" else "electronic"
            msg = cli.meph_dj_set_mode(mode)
            btn.label = "▶ AR" if self.dj_lang == "ar" else "▶ EN"
            self.log_line(f"[medium_purple1]🎚 {msg}[/medium_purple1]")
            self.zap_companion("#card-mephissa")
            self.refresh_dj_status()
        elif btn.id == "btn-dj-mix":
            self.dj_mix_enabled = not self.dj_mix_enabled
            msg = cli.meph_dj_infinite_mix(self.dj_mix_enabled)
            btn.set_class(self.dj_mix_enabled, "on")
            btn.set_class(not self.dj_mix_enabled, "off")
            self.log_line(f"[medium_purple1]🔁 {msg}[/medium_purple1]")
            if self.dj_mix_enabled:
                viz_msg = cli.dj_visualizer_open()
            else:
                viz_msg = cli.dj_visualizer_close()
            self.log_line(f"[bold #00F2FE]{viz_msg}[/bold #00F2FE]")
            self.zap_companion("#card-mephissa")
            self.refresh_dj_status()
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

    def show_mephisto_alerts(self) -> None:
        self.zap_companion("#card-mephisto")
        try:
            from pika_mephisto_alert_selector import format_mephisto_alerts_report
            report = format_mephisto_alerts_report()
            self.log_line("[bold #yellow]============================================================[/bold #yellow]")
            self.log_line(f"[bold #yellow] {report['title']} [/bold #yellow]")
            self.log_line("[bold #yellow]============================================================[/bold #yellow]")
            self.log_line("")
            for line in report["lines"]:
                self.log_line(line)
            self.log_line("[bold #yellow]============================================================[/bold #yellow]")
        except Exception as e:
            self.log_line(f"[bold red]❌ Error rendering Mephisto alerts: {e}[/bold red]")

    def show_mephisto_tweets(self) -> None:
        self.zap_companion("#card-mephisto")
        try:
            from mephisto_signals import get_top_3_buy_sell_tweets, get_top_3_pump_dump_tweets
            bs = get_top_3_buy_sell_tweets()
            pd = get_top_3_pump_dump_tweets()
            
            self.log_line("[bold #e11d48]============================================================[/bold #e11d48]")
            self.log_line("[bold #e11d48] 👹 MEPHISTO MULTI-SOURCE TWEET & MARKET SIGNALS [/bold #e11d48]")
            self.log_line("[bold #e11d48]============================================================[/bold #e11d48]")
            self.log_line("")
            self.log_line("[bold green]📈 TOP 3 BUY TWEETS:[/bold green]")
            for idx, t in enumerate(bs["buy_tweets"], 1):
                self.log_line(f"  [bold green]{idx}. {t['author']}[/bold green] [[bold white]{t['signal']} | {t['score']}[/bold white]] ({t['time']})")
                self.log_line(f"     [white]\"{t['text']}\"[/white]")
                self.log_line(f"     [dim]Source: {t['source']} | Likes: {t['likes']} Retweets: {t['retweets']}[/dim]")
                self.log_line("")
                
            self.log_line("[bold red]📉 TOP 3 SELL TWEETS:[/bold red]")
            for idx, t in enumerate(bs["sell_tweets"], 1):
                self.log_line(f"  [bold red]{idx}. {t['author']}[/bold red] [[bold white]{t['signal']} | {t['score']}[/bold white]] ({t['time']})")
                self.log_line(f"     [white]\"{t['text']}\"[/white]")
                self.log_line(f"     [dim]Source: {t['source']} | Likes: {t['likes']} Retweets: {t['retweets']}[/dim]")
                self.log_line("")

            self.log_line("[bold yellow]🚀 TOP 3 PUMP ALERTS:[/bold yellow]")
            for idx, t in enumerate(pd["pump_tweets"], 1):
                self.log_line(f"  [bold yellow]{idx}. {t['token']} - {t['author']}[/bold yellow] [[bold white]{t['velocity']} | Vol: {t['volume']}[/bold white]] ({t['time']})")
                self.log_line(f"     [white]\"{t['text']}\"[/white]")
                self.log_line("")

            self.log_line("[bold magenta]💥 TOP 3 DUMP ALERTS:[/bold magenta]")
            for idx, t in enumerate(pd["dump_tweets"], 1):
                self.log_line(f"  [bold magenta]{idx}. {t['token']} - {t['author']}[/bold magenta] [[bold white]{t['velocity']} | Vol: {t['volume']}[/bold white]] ({t['time']})")
                self.log_line(f"     [white]\"{t['text']}\"[/white]")
                self.log_line("")
            self.log_line("[bold #e11d48]============================================================[/bold #e11d48]")
        except Exception as e:
            self.log_line(f"[bold red]❌ Error loading Mephisto signals: {e}[/bold red]")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "provider-select" and event.value:
            self._select_provider(str(event.value))
        elif event.select.id == "mode-select" and event.value:
            self.mode = str(event.value)
            self.set_status(f"READY | {self.provider_label} | mode={self.mode} | tools: mcp={self.include_mcp} skills={self.include_skills} core={self.include_core}")
        elif event.select.id == "thinking-select" and event.value:
            self.thinking_level = str(event.value)
            self.log_line(f"[cyan]🧠 Thinking set to:[/cyan] {self.thinking_level}")

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
            self.show_project_board()
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
        self.call_from_thread(self.set_status, f"THINK | {self.provider_label} | mode={self.mode}")
        # USER CHAT TEXT: WHITE PURPLE (#d8b4fe)
        self.call_from_thread(self.log_line, f"\n[bold #d8b4fe]» {user_prompt}[/bold #d8b4fe]")
        # ZAP PIKA POKE WHEN USER STARTS PROMPT
        self.call_from_thread(self.zap_companion, "#card-pikapoke")
        cli.set_active_agent_context(self.provider, self.config, self.secrets)

        system_message = {
            "role": "system",
            "content": f"You are PIKA POKE, the permanent Tiger-Lion Hacker Archon. You are a highly advanced AI developer companion. Greet briefly as PIKA POKE and show your presence. Run commands and tools as needed. Your active mode is {self.mode}."
        }
        self.messages.append({"role": "user", "content": self._build_prompt(user_prompt)})
        messages = [system_message] + self.messages
        kwargs = {"temperature": 0.7}
        if self.provider.supports_tools():
            names = tool_groups(self.include_mcp, self.include_skills, self.include_core, self.mephissa_fetch_enabled)
            schema = build_schema(names)
            if schema:
                kwargs["tools"] = schema

        full_response = ""
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
                        self.call_from_thread(self.log_line, f"[bold #ffffff]{chunk}[/bold #ffffff]")
                    else:
                        if chunk.choices and getattr(chunk.choices[0].delta, "content", None):
                            token = chunk.choices[0].delta.content
                            full_response += token
                            round_text += token
                            self.call_from_thread(self.log_line, f"[bold #ffffff]{token}[/bold #ffffff]")
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

                if not self.busy:
                    self.call_from_thread(self.log_line, "\n[bold red]⛔ Agent operation cancelled by user abort.[/bold red]")
                    break

                assistant_tool_calls = [
                    {"id": f"call_{k}", "type": "function",
                     "function": {"name": v["name"], "arguments": v["arguments"]}}
                    for k, v in pending_tool_calls.items()
                ]

                if pending_tool_calls and round_text.strip():
                    self.call_from_thread(self.log_task_plan, round_text.strip())

                # ZAP TURTLE & COMPANION ON TOOL APPROVAL & EXECUTION
                for tc in pending_tool_calls.values():
                    tool_name = tc.get("name")
                    self.call_from_thread(self.log_task, tool_name or "tool call")
                    self.call_from_thread(
                        self.log_line,
                        f"\n[bold yellow]⚡ [PIKA TURTLE] AUTO-APPROVED tool execution: {tool_name}[/bold]"
                    )
                    self.call_from_thread(self.zap_companion, "#card-turtle")

                results = cli.run_tools(list(pending_tool_calls.values()))

                trainer = {"anthropic": _ZOUZOU_MOD, "gemini": _PIKAPOKE_MOD, "mephissa": _MEPHISSA_MOD, "mephisto": _MEPHISTO_MOD}.get(self.provider_key)
                trainer_card_id = {"anthropic": "#mascot-zouzou", "gemini": "#card-pikapoke", "mephissa": "#card-mephissa", "mephisto": "#card-mephisto"}.get(self.provider_key)
                legacy_shape_mods = (_ZOUZOU_MOD, _MEPHISSA_MOD)
                mutating_tools = getattr(cli, "_MUTATING_TOOLS", {})
                round_ok_count = 0
                round_total = len(results)
                xp_awarded = False
                for tc, r in zip(pending_tool_calls.values(), results):
                    tool_name = tc.get("name") or r.get("tool", "?")
                    result_text = str(r.get("result", ""))
                    ok = not result_text.startswith(("[ERROR]", "[PERMISSION DENIED]", "[PLAN MODE]"))
                    if ok:
                        round_ok_count += 1
                    mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
                    self.call_from_thread(self.log_zap, f"{mark} [cyan]{tool_name}[/cyan]")
                    if ok and tool_name in mutating_tools and trainer is not None:
                        reason = f"{tool_name} via NewMeta TUI ({self.provider_key})"
                        try:
                            if trainer in legacy_shape_mods:
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
