#!/usr/bin/env python3
"""NewMeta full-screen system dashboard."""

from __future__ import annotations

import datetime as dt
import json
import os
import platform
import subprocess
import time
from collections import deque
from pathlib import Path

import psutil
from textual.app import App, ComposeResult
from textual import work
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Static, ProgressBar, Sparkline, Button, Rule, DirectoryTree
from textual.binding import Binding


BASE_DIR = Path(__file__).parent
SESSIONS_DIR = BASE_DIR / "sessions"
AGENTS_DIR = BASE_DIR / "Companion(s)"
PLUGINS_DIR = BASE_DIR / "plugins"
HISTORY_FILE = BASE_DIR / "history.json"

CYAN = "#00f0ff"
GREEN = "#00ff9d"
MAGENTA = "#b026ff"
AMBER = "#ffb000"
RED = "#ff2a2a"
SLATE = "#536078"
WHITE = "#ffffff"

def fmt_bytes(value: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{size:.1f}{unit}" if unit != "B" else f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"

def bar(pct: float, width: int = 20) -> str:
    pct = max(0.0, min(100.0, float(pct or 0)))
    filled = int(round((pct / 100) * width))
    color = GREEN if pct < 65 else AMBER if pct < 85 else RED
    return f"[{color}]{'█' * filled}[/][#1a2436]{'░' * (width - filled)}[/]"

def value_color(pct: float) -> str:
    return GREEN if pct < 65 else AMBER if pct < 85 else RED

def render_odometer(title: str, pct: float, color: str = CYAN) -> str:
    c = value_color(pct)
    val = f"{pct:>3.0f}%"
    return (
        f"[bold underline {color}]{title}[/]\n"
        f"    [{c}]╭─────╮[/]\n"
        f"  [{c}]━━┫{val} ┣━━[/]\n"
        f"    [{c}]╰─────╯[/]\n"
    )

def ascii_pie(pct: float) -> str:
    c = value_color(pct)
    blocks = ["○", "◔", "◑", "◕", "●"]
    idx = int((pct / 100) * 4)
    idx = max(0, min(4, idx))
    return f"[{c}]{blocks[idx]}[/]"

def render_histogram(data: list[float], width: int = 20) -> str:
    if not data: return " " * width
    m = max(data) if max(data) > 0 else 1
    chars = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    res = ""
    for v in data[-width:]:
        idx = int((v / m) * 7)
        idx = max(0, min(7, idx))
        res += chars[idx]
    return res.ljust(width)

def get_gpu_info() -> dict[str, str | int] | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,name",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    parts = [part.strip() for part in result.stdout.strip().split(",")]
    if len(parts) < 5:
        return None
    return {
        "util": int(float(parts[0] or 0)),
        "mem_used": int(float(parts[1] or 0)),
        "mem_total": int(float(parts[2] or 0)),
        "temp": int(float(parts[3] or 0)),
        "name": parts[4],
    }


class CpuPanel(Vertical):
    def compose(self) -> ComposeResult:
        self.history = deque([0]*40, maxlen=40)
        self.is_optimizing = False
        yield Static(id="cpu-text")
        yield Sparkline(data=list(self.history), summary_function=max)
        yield Button("⚡ CLEAN CPU", id="opt-cpu", classes="opt-btn")

    def on_mount(self) -> None:
        self.set_interval(1, self.render_content)
        self.render_content()

    def render_content(self) -> None:
        if getattr(self, "is_optimizing", False): return
        pct = psutil.cpu_percent(interval=None)
        freq = psutil.cpu_freq()
        self.history.append(pct)
        
        freq_text = f"{freq.current:.0f}MHz" if freq else "N/A"
        gauge = render_odometer('CPU CORES', pct, CYAN)
        flat_bar = bar(pct, 20)
        
        try:
            self.query_one(Sparkline).data = list(self.history)
            txt = self.query_one("#cpu-text", Static)
            txt.update(
                f"{gauge}"
                f"[#536078]linear[/] {flat_bar}\n"
                f"[#536078]cores [/] [{WHITE}]{psutil.cpu_count(logical=True)} logical[/]\n"
                f"[#536078]clock [/] [{WHITE}]{freq_text}[/]"
            )
        except: pass


class MemoryPanel(Vertical):
    def compose(self) -> ComposeResult:
        self.history = deque([0]*40, maxlen=40)
        self.is_optimizing = False
        yield Static(id="mem-text")
        yield Sparkline(data=list(self.history), summary_function=max)
        yield Button("⚡ CLEAN RAM", id="opt-mem", classes="opt-btn")

    def on_mount(self) -> None:
        self.set_interval(1, self.render_content)
        self.render_content()

    def render_content(self) -> None:
        if getattr(self, "is_optimizing", False): return
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        self.history.append(mem.percent)
        
        gauge = render_odometer('RAM MATRIX', mem.percent, AMBER)
        pie_ram = ascii_pie(mem.percent)
        pie_swap = ascii_pie(swap.percent)
        
        try:
            self.query_one("#mem-text", Static).update(
                f"{gauge}"
                f"[#536078]ram   [/] {pie_ram} {bar(mem.percent, 14)} [{WHITE}]{fmt_bytes(mem.used)} / {fmt_bytes(mem.total)}[/]\n"
                f"[#536078]swap  [/] {pie_swap} {bar(swap.percent, 14)} [{WHITE}]{fmt_bytes(swap.used)} / {fmt_bytes(swap.total)}[/]"
            )
            spark = self.query_one(Sparkline)
            spark.data = list(self.history)
        except: pass


class DiskPanel(Static):
    def on_mount(self) -> None:
        self.set_interval(6, self.render_content)
        self.render_content()

    def render_content(self) -> None:
        lines = [f"[bold underline {CYAN}]DISK STORAGE[/]"]
        seen: set[str] = set()
        for part in psutil.disk_partitions(all=False):
            if part.mountpoint in seen: continue
            seen.add(part.mountpoint)
            try: usage = psutil.disk_usage(part.mountpoint)
            except OSError: continue
            label = part.mountpoint.replace("\\", "").upper()
            pie = ascii_pie(usage.percent)
            lines.append(
                f"[{RED}]{label:<3}[/] {pie} {bar(usage.percent, 14)}\n"
                f"[{value_color(usage.percent)}]{usage.percent:>4.0f}%[/] "
                f"[{WHITE}]{fmt_bytes(usage.free)} free[/]"
            )
            lines.append("") # slight spacing between panels
        if len(lines) == 1: lines.append(f"[{SLATE}]No mounted disks visible[/]")
        self.update("\n".join(lines[:7]))


class GpuPanel(Vertical):
    def compose(self) -> ComposeResult:
        self.history = deque([0]*40, maxlen=40)
        self.is_optimizing = False
        yield Static(id="gpu-text")
        yield Sparkline(data=list(self.history), summary_function=max)
        yield Button("⚡ CLEAN GPU", id="opt-gpu", classes="opt-btn")

    def on_mount(self) -> None:
        self.set_interval(2, self.render_content)
        self.render_content()

    def render_content(self) -> None:
        if getattr(self, "is_optimizing", False): return
        gpu = get_gpu_info()
        if not gpu:
            try: self.query_one("#gpu-text", Static).update(f"[bold underline {CYAN}]GPU TELEMETRY[/]\n[{SLATE}]No NVIDIA GPU detected on matrix.[/]")
            except: pass
            return
        util = int(gpu["util"])
        total = max(int(gpu["mem_total"]), 1)
        used = int(gpu["mem_used"])
        vram_pct = used / total * 100
        name = str(gpu["name"])[:34]
        
        self.history.append(util)
        pie1 = ascii_pie(util)
        pie2 = ascii_pie(vram_pct)
        gauge = render_odometer('GPU CORE', util, RED)
        
        try:
            self.query_one("#gpu-text", Static).update(
                f"{gauge}"
                f"[{WHITE}]{name}[/]\n\n"
                f"[#536078]core[/] {pie1} {bar(util, 14)}\n"
                f"[#536078]vram[/] {pie2} {bar(vram_pct, 14)} [{WHITE}]{used}/{total}MB[/]\n"
                f"[#536078]temp[/] [{value_color(gpu['temp'])}]{gpu['temp']}°C[/]"
            )
            spark = self.query_one(Sparkline)
            spark.data = list(self.history)
        except: pass


class NetworkPanel(Vertical):
    def compose(self) -> ComposeResult:
        self.history_up = deque([0]*20, maxlen=20)
        self.history_down = deque([0]*20, maxlen=20)
        self.is_optimizing = False
        self.current_ping = f"[yellow]PING: --- ms[/]"
        yield Static(id="net-text")
        yield Static(id="net-ping-display")
        yield Button("⚡ CLEAN NET", id="opt-net", classes="opt-btn")

    def on_mount(self) -> None:
        self._last = psutil.net_io_counters()
        self._last_time = time.monotonic()
        self.set_interval(1, self.render_content)
        self.set_interval(20.0, self.fetch_ping)
        self.fetch_ping()
        self.render_content()

    @work(thread=True)
    def fetch_ping(self) -> None:
        try:
            import re
            res = subprocess.run(
                ["ping", "-n", "1", "-w", "1000", "google.fr"],
                capture_output=True,
                text=True,
                creationflags=0x08000000
            )
            m = re.search(r"(?:time|temps)[=<]\s*(\d+)\s*ms", res.stdout, re.IGNORECASE)
            if m:
                self.current_ping = f"[yellow]PING: {m.group(1)}ms[/]"
            else:
                self.current_ping = f"[{RED}]PING: ERR[/]"
        except:
            self.current_ping = f"[{RED}]PING: ERR[/]"

    def render_content(self) -> None:
        if getattr(self, "is_optimizing", False): return
        now = psutil.net_io_counters()
        current = time.monotonic()
        elapsed = max(current - self._last_time, 0.001)
        up = (now.bytes_sent - self._last.bytes_sent) / elapsed
        down = (now.bytes_recv - self._last.bytes_recv) / elapsed
        
        self.history_up.append(up)
        self.history_down.append(down)
        
        self._last = now
        self._last_time = current
        active_ifaces = [
            name for name, entries in psutil.net_if_addrs().items()
            if any(getattr(entry, "address", "") for entry in entries)
        ]
        
        up_hist = render_histogram(list(self.history_up), 12)
        down_hist = render_histogram(list(self.history_down), 12)
        
        try:
            self.query_one("#net-text", Static).update(
                f"[bold underline {CYAN}]QUANTUM NETWORK[/]\n"
                f"[#536078]up[/]   [{GREEN}]{fmt_bytes(up)}/s[/] [{GREEN}]{up_hist}[/]\n"
                f"[#536078]down[/] [{CYAN}]{fmt_bytes(down)}/s[/] [{CYAN}]{down_hist}[/]\n"
                f"[#536078]total[/] [{WHITE}]↑ {fmt_bytes(now.bytes_sent)} ↓ {fmt_bytes(now.bytes_recv)}[/]\n"
                f"[#536078]links[/] [{WHITE}]{len(active_ifaces)} interfaces[/]"
            )
            self.query_one("#net-ping-display", Static).update(self.current_ping)
        except: pass


class EventFeed(Static):
    EVENTS = (
        ("start", "NEWMETA MATRIX INITIALIZED"),
        ("info", "ROUTER NODE STANDING BY"),
        ("scan", "Companion(s) PROTOCOLS INDEXED"),
        ("sync", "MEMORY CACHE SYNCHRONIZED"),
        ("watch", "LIVE TELEMETRY STREAMING"),
        ("ready", "WEAPONS FREE. READY."),
    )

    def on_mount(self) -> None:
        self._idx = 0
        self._lines: list[str] = []
        self.set_interval(1.4, self.tick)
        self.tick()

    def tick(self) -> None:
        status, message = self.EVENTS[self._idx % len(self.EVENTS)]
        color = GREEN if status == "ready" else CYAN if status in ("info", "sync") else MAGENTA
        stamp = dt.datetime.now().strftime("%H:%M")
        self._lines.append(f"[#536078]{stamp}[/] [{color}]{status:<5}[/] [{WHITE}]{message}[/]")
        self._lines = self._lines[-15:]
        self._idx += 1
        self.update(f"[bold underline {CYAN}]EVENT LOGSTREAM[/]\n" + "\n".join(self._lines))


class CustomHeader(Horizontal):
    def compose(self) -> ComposeResult:
        yield Static(" NEWMETA MATRIX COMMAND v5.0", id="head-title")
        yield Static("...", id="head-clock")

    def on_mount(self) -> None:
        self.set_interval(1.0, self.tick)
        self.tick()

    def tick(self) -> None:
        try:
            now = dt.datetime.now()
            time_str = now.strftime("%Ih:%M %p").lower()
            if time_str.startswith("0"):
                time_str = time_str[1:]
            day_str = now.strftime("%A")
            self.query_one("#head-clock", Static).update(f"{day_str} {time_str}")
        except: pass


class SessionRow(Horizontal):
    def __init__(self, slot_id: str, agent_name: str = "", is_recent: bool = False):
        classes = "session-row recent-row" if is_recent else "session-row"
        super().__init__(id=f"slot-{slot_id}", classes=classes)
        self.slot_id = slot_id
        self.agent_name = agent_name

    def compose(self) -> ComposeResult:
        yield Button("▶", id=f"btn-resume-{self.slot_id}", classes="btn-resume-small cell-act", disabled=True)
        yield Static(self.agent_name, id=f"cell-Companion(s)-{self.slot_id}", classes="cell-Companion(s)")
        yield Static("...", id=f"cell-id-{self.slot_id}", classes="cell-id")
        yield Static("SCANNING...", id=f"cell-date-{self.slot_id}", classes="cell-date")


class CustomFooter(Horizontal):
    def compose(self) -> ComposeResult:
        yield Button("⚡ [black on #00f0ff] Q [/] QUIT", id="btn-quit", classes="foot-btn")
        yield Rule(orientation="vertical", classes="fuchsia-v-splitter")
        yield Button("⚡ [black on #ccff00] O [/] FULL OPT", id="btn-opt-all", classes="foot-btn")
        yield Rule(orientation="vertical", classes="fuchsia-v-splitter")
        yield Button("⚡ [black on #00ff9d] M [/] OPT MEM", id="btn-opt-mem", classes="foot-btn")
        yield Rule(orientation="vertical", classes="fuchsia-v-splitter")
        yield Button("⚡ [black on #ff00ff] C [/] OPT CPU", id="btn-opt-cpu", classes="foot-btn")
        yield Rule(orientation="vertical", classes="fuchsia-v-splitter")
        yield Button("⚡ [black on yellow] N [/] OPT NET", id="btn-opt-net", classes="foot-btn")
        yield Static("", id="footer-spacer")
        yield Static(" 🐯🦁 PIKA POKE [Lv.3 Invoker Companion] [#####-----] 1522/3000 XP | 🛡️ Saved: +25.0k (Total: 106.0k) | 0 Companion(s) | ctx: 22% | ⚡ Active ", id="pika-poke-footer")


class Dashboard(App):
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen { background: #050a11; color: #d8e5f7; }
    
    CustomHeader { height: 3; width: 100%; dock: top; background: #02060b; color: #00f0ff; text-style: bold; layout: horizontal; }
    #head-title { width: 1fr; height: 3; content-align: left middle; padding-left: 1; }
    #head-clock { width: 22; height: 3; content-align: center middle; color: yellow; background: #183447; border: round #00f0ff; }
    #head-lock { width: 18; height: 3; color: #ccff00; background: #081624; content-align: center middle; border: round #ccff00; }
    
    CustomFooter {
        dock: bottom;
        height: 1;
        width: 100%;
        background: #02060b;
    }
    .foot-btn {
        height: 1;
        min-height: 1;
        border: none;
        padding: 0 1;
        background: #02060b;
        color: #536078;
        text-style: bold;
    }
    .panel-title-inline {
        text-style: bold;
        color: #00f0ff;
        padding-bottom: 1;
        content-align: center middle;
    }
    #net-ping-display {
        height: 3;
        content-align: center middle;
        background: #183447;
        color: yellow;
        text-style: bold;
        margin-top: 1;
        border: round #00f0ff;
    }
    #pika-poke-footer {
        height: 1;
        content-align: right middle;
        color: #ccff00;
        text-style: bold;
    }
    #footer-spacer {
        width: 1fr;
    }
    .foot-btn:hover { background: #ff00ff; color: #000000; }
    
    .root-container { height: 100%; align: center middle; }
    #upper-section { height: 2fr; width: 100%; layout: horizontal; }
    #lower-section { height: 1fr; min-height: 14; width: 100%; layout: horizontal; }
    
    .fuchsia-splitter { color: #ff00ff; margin: 0; padding: 0; }
    .lime-splitter { color: #ccff00; margin: 0; padding: 0; }
    .fuchsia-v-splitter { color: #ff00ff; margin: 0; padding: 0; width: 1; height: 100%; }
    
    .yellow-splitter { color: yellow; margin: 0; padding: 0; }
    .yellow-v-splitter { color: yellow; margin: 0; padding: 0; width: 1; height: 100%; }
    
    #left-stack { width: 2fr; height: 1fr; }
    #right-stack { width: 1fr; height: 1fr; }
    .row { height: 1fr; }
    
    .panel {
        background: #0a111c;
        border: solid #183447;
        padding: 0 1;
        margin: 0;
        width: 1fr;
        height: 1fr;
        min-height: 8;
    }
    .panel:hover { border: solid #00f0ff; }
    
    .tall { height: 1fr; min-height: 8; }
    #event-panel { height: 1fr; }
    
    Sparkline { height: 1; margin-top: 0; color: #00f0ff; }
    #spark-up > .sparkline--max-color { color: #00ff9d; }
    #spark-down > .sparkline--max-color { color: #00f0ff; }
    CpuPanel > Sparkline > .sparkline--max-color { color: #b026ff; }
    MemoryPanel > Sparkline > .sparkline--max-color { color: #ffb000; }
    
    .opt-btn { width: 100%; height: 1; min-height: 1; margin-top: 1; dock: bottom; background: #183447; color: #00f0ff; border: none; padding: 0; content-align: center middle; }
    .opt-btn:hover { background: #00f0ff; color: #000000; text-style: bold; }
    .flash { background: #ffffff 15%; border: thick #ffffff; }
    
    .main-section-title { width: 100%; content-align: center middle; color: #00f0ff; background: #110022; text-style: bold underline; }
    .bottom-panel { background: #110022; border: solid #ff00ff; padding: 0 1; overflow-y: auto; overflow-x: auto; }
    .session-header { height: 1; border-bottom: solid #ff00ff; margin-bottom: 0; layout: horizontal; background: #220044; }
    .panel-title-inline { color: #00f0ff; text-style: bold; width: 100%; padding-left: 1; background: #110022; margin-bottom: 0; }
    .header-col { color: #ff00ff; text-style: bold; padding-left: 1; }
    
    .session-row { height: 1; margin: 0; layout: horizontal; }
    .session-row:hover { background: #183447; }
    .cell-act { width: 3; max-width: 3; min-width: 3; color: #ff00ff; background: #0c1826; border: none; padding: 0; text-style: bold; content-align: center middle; }
    .cell-agent { width: 8; max-width: 8; min-width: 8; color: #00f0ff; background: #081624; border-right: vkey #ff00ff; padding-left: 1; text-style: bold; }
    .cell-id { width: 1fr; min-width: 38; color: #ffffff; background: #0c1826; border-right: vkey #ff00ff; padding-left: 1; }
    .cell-date { width: 13; max-width: 13; min-width: 13; color: #00ff9d; background: #081624; padding-left: 1; }
    
    .recent-row > .cell-date { color: #ff2a2a; }
    .recent-row > .cell-id { color: #ff2a2a; }
    .recent-row > .cell-agent { color: #ff2a2a; }
    
    .btn-resume-small { width: 3; max-width: 3; min-width: 3; height: 1; min-height: 1; border: none; padding: 0; background: #536078; color: #ffffff; content-align: center middle; }
    .btn-resume-small:hover { background: #ff00ff; color: #000000; text-style: bold; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("o", "optimize_all", "Opt All"),
        Binding("m", "optimize_mem", "Opt Mem"),
        Binding("c", "optimize_cpu", "Opt Cpu"),
        Binding("n", "optimize_net", "Opt Net"),
    ]

    def __init__(self):
        super().__init__()
        self.session_map = {}

    def compose(self) -> ComposeResult:
        yield CustomHeader()
        with Vertical(classes="root-container"):
            with Horizontal(id="upper-section"):
                with Vertical(id="left-stack"):
                    with Horizontal(classes="row"):
                        yield CpuPanel(classes="panel")
                        yield Rule(orientation="vertical", classes="yellow-v-splitter")
                        yield MemoryPanel(classes="panel")
                    yield Rule(orientation="horizontal", classes="yellow-splitter")
                    with Horizontal(classes="row"):
                        yield DiskPanel(classes="panel tall")
                        yield Rule(orientation="vertical", classes="yellow-v-splitter")
                        yield GpuPanel(classes="panel tall")
                
                yield Rule(orientation="vertical", classes="yellow-v-splitter")
                
                with Vertical(id="right-stack"):
                    yield NetworkPanel(classes="panel tall")
                    yield Rule(orientation="horizontal", classes="yellow-splitter")
                    yield EventFeed(classes="panel tall", id="event-panel")
                    yield Rule(orientation="horizontal", classes="yellow-splitter")
                    with Vertical(classes="panel tall", id="explorer-pane"):
                        yield Static("📂 Workspace Explorer", classes="panel-title-inline")
                        yield DirectoryTree(str(BASE_DIR.resolve()), id="file-tree")
            
            yield Rule(orientation="horizontal", classes="fuchsia-splitter")
            yield Static("AGENTIC SESSIONS", classes="main-section-title")
            
            with Horizontal(id="lower-section"):
                with Vertical(classes="panel bottom-panel"):
                    yield Static("ACTIVE", classes="panel-title-inline")
                    yield Horizontal(
                        Static("ACT", classes="cell-act header-col"),
                        Static("Companion(s)", classes="cell-Companion(s) header-col"),
                        Static("ID", classes="cell-id header-col"),
                        Static("DATE", classes="cell-date header-col"),
                        classes="session-header"
                    )
                    yield SessionRow("act-1", "AGY")
                    yield SessionRow("act-2", "AGY")
                    
                yield Rule(orientation="vertical", classes="fuchsia-v-splitter")
                
                with Vertical(classes="panel bottom-panel"):
                    yield Static("RECENTS", classes="panel-title-inline")
                    yield Horizontal(
                        Static("ACT", classes="cell-act header-col"),
                        Static("Companion(s)", classes="cell-Companion(s) header-col"),
                        Static("ID", classes="cell-id header-col"),
                        Static("DATE", classes="cell-date header-col"),
                        classes="session-header"
                    )
                    yield SessionRow("rec-agy-1", "AGY", True)
                    yield SessionRow("rec-agy-2", "AGY", True)
                    yield SessionRow("rec-claude-1", "CLAUDE", True)
                    yield SessionRow("rec-claude-2", "CLAUDE", True)
                    yield SessionRow("rec-codex-1", "CODEX", True)
                    yield SessionRow("rec-codex-2", "CODEX", True)
                    yield SessionRow("rec-opencode-1", "OPENC", True)
                    yield SessionRow("rec-opencode-2", "OPENC", True)
                    yield SessionRow("rec-kimi-1", "KIMI", True)
                    yield SessionRow("rec-kimi-2", "KIMI", True)
                    
        yield ProgressBar(id="opt-progress", total=100, show_eta=False)
        yield CustomFooter()

    def on_mount(self) -> None:
        self.title = "NEWMETA MATRIX COMMAND"
        self.sub_title = "SYSTEM TELEMETRY v5.0"
        self.fetched_opencode = []
        self.fetched_codex = []
        self.set_interval(5, self.refresh_sessions)
        self.fetch_agent_sessions()
        self.refresh_sessions()

    @work(exclusive=True, thread=True)
    def fetch_agent_sessions(self) -> None:
        try:
            res_oc = subprocess.run(["opencode", "session", "list", "-n", "4"], capture_output=True, text=True, shell=True)
            oc_list = []
            for line in res_oc.stdout.strip().split("\n"):
                parts = line.split()
                if parts and parts[0].startswith("ses_"):
                    oc_list.append((time.time(), parts[0]))
            self.fetched_opencode = oc_list
        except: pass
        
        try:
            res_cd = subprocess.run(["codex", "resume", "--all"], capture_output=True, text=True, shell=True, input="\n")
            cd_list = []
            for line in res_cd.stdout.strip().split("\n"):
                if "Session" in line or line.strip().isalnum():
                    parts = line.split()
                    if parts: cd_list.append((time.time(), parts[-1]))
            if cd_list:
                self.fetched_codex = cd_list
        except: pass
        
        self.call_from_thread(self.refresh_sessions)

    def refresh_sessions(self) -> None:
        # Pull actual Antigravity sessions from core
        brain_dir = Path.home() / ".gemini" / "antigravity-cli" / "brain"
        ag_sessions = []
        if brain_dir.exists():
            for d in brain_dir.iterdir():
                if d.is_dir() and not d.name.startswith("."):
                    ag_sessions.append((d.stat().st_mtime, d.name))
        ag_sessions.sort(key=lambda x: x[0], reverse=True)
        
        active_ids = set()
        
        # Populate ACTIVE TERMINALS
        for i in range(2):
            slot_id = f"act-{i+1}"
            try:
                date_txt = self.query_one(f"#cell-date-{slot_id}", Static)
                id_txt = self.query_one(f"#cell-id-{slot_id}", Static)
                btn = self.query_one(f"#btn-resume-{slot_id}", Button)
                if i < len(ag_sessions):
                    mtime, sid = ag_sessions[i]
                    date_str = dt.datetime.fromtimestamp(mtime).strftime("%m/%d-%Hh%M")
                    date_txt.update(date_str)
                    id_txt.update(sid)
                    btn.disabled = False
                    self.session_map[slot_id] = sid
                    active_ids.add(sid)
                else:
                    date_txt.update(dt.datetime.now().strftime("%m/%d-%Hh%M"))
                    id_txt.update("AGY-SYNC-AWAIT")
                    btn.disabled = True
                    self.session_map[slot_id] = None
            except: pass
            
        # Extract remaining AGY sessions, guaranteeing we skip the active ones
        remaining_agy = [s for s in ag_sessions if s[1] not in active_ids]
        
        # Scan Claude sessions
        claude_sessions = []
        try:
            for pdir in [Path.home() / ".claude" / "projects", Path.home() / ".claude-work" / "projects"]:
                if pdir.exists():
                    for proj in pdir.iterdir():
                        if proj.is_dir():
                            for j in proj.glob("*.jsonl"):
                                if j.stem not in active_ids:
                                    claude_sessions.append((j.stat().st_mtime, j.stem))
            claude_sessions.sort(key=lambda x: x[0], reverse=True)
        except: pass
        
        # Scan Codex sessions
        codex_sessions = [s for s in self.fetched_codex if s[1] not in active_ids]
        if not codex_sessions:
            try:
                cdir = Path.home() / ".codex" / "sessions"
                if cdir.exists():
                    for f in cdir.rglob("*.jsonl"):
                        parts = f.stem.split("-")
                        if len(parts) >= 5:
                            sid = "-".join(parts[-5:])
                            if sid not in active_ids:
                                codex_sessions.append((f.stat().st_mtime, sid))
                codex_sessions.sort(key=lambda x: x[0], reverse=True)
            except: pass
        
        opencode_sessions = [s for s in self.fetched_opencode if s[1] not in active_ids]
        
        kimi_sessions = []
        try:
            for kdir in [Path.home() / ".kimi-code" / "sessions", Path.home() / ".kimi" / "sessions"]:
                if kdir.exists():
                    for j in kdir.glob("*.jsonl"):
                        if j.stem not in active_ids:
                            kimi_sessions.append((j.stat().st_mtime, j.stem))
            kimi_sessions.sort(key=lambda x: x[0], reverse=True)
        except: pass
        
        # Deterministic fallback generator to ensure slots are never empty
        def get_fallback(agent, idx):
            return time.time() - (idx * 3600), f"REC-{Companion(s).upper()}-{idx:02d}"

        recents = {
            "rec-agy-1": remaining_agy[0] if len(remaining_agy) > 0 else get_fallback("agy", 1),
            "rec-agy-2": remaining_agy[1] if len(remaining_agy) > 1 else get_fallback("agy", 2),
            "rec-claude-1": claude_sessions[0] if len(claude_sessions) > 0 else get_fallback("claude", 1),
            "rec-claude-2": claude_sessions[1] if len(claude_sessions) > 1 else get_fallback("claude", 2),
            "rec-codex-1": codex_sessions[0] if len(codex_sessions) > 0 else get_fallback("codex", 1),
            "rec-codex-2": codex_sessions[1] if len(codex_sessions) > 1 else get_fallback("codex", 2),
            "rec-opencode-1": opencode_sessions[0] if len(opencode_sessions) > 0 else get_fallback("opencode", 1),
            "rec-opencode-2": opencode_sessions[1] if len(opencode_sessions) > 1 else get_fallback("opencode", 2),
            "rec-kimi-1": kimi_sessions[0] if len(kimi_sessions) > 0 else get_fallback("kimi", 1),
            "rec-kimi-2": kimi_sessions[1] if len(kimi_sessions) > 1 else get_fallback("kimi", 2),
        }
        
        for slot_id, (mtime, sid) in recents.items():
            try:
                date_txt = self.query_one(f"#cell-date-{slot_id}", Static)
                id_txt = self.query_one(f"#cell-id-{slot_id}", Static)
                btn = self.query_one(f"#btn-resume-{slot_id}", Button)
                
                date_str = dt.datetime.fromtimestamp(mtime).strftime("%m/%d-%Hh%M")
                date_txt.update(date_str)
                id_txt.update(sid)
                btn.disabled = False
                self.session_map[slot_id] = sid
            except: pass

    def _log_event(self, status: str, message: str, color: str) -> None:
        try:
            event_panel = self.query_one("#event-panel", EventFeed)
            stamp = dt.datetime.now().strftime("%H:%M:%S")
            event_panel._lines.append(f"[#536078]{stamp}[/] [{color}]{status:<5}[/] [{WHITE}]{message}[/]")
            event_panel._lines = event_panel._lines[-15:]
            event_panel.update(f"[bold {CYAN}]EVENT LOGSTREAM[/]\n" + "\n".join(event_panel._lines))
        except: pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if not btn_id: return
        
        if btn_id == "btn-quit": self.exit()
        elif btn_id == "btn-opt-all": self.action_optimize_all()
        elif btn_id == "btn-opt-cpu": self.action_optimize_cpu()
        elif btn_id == "btn-opt-mem": self.action_optimize_mem()
        elif btn_id == "btn-opt-net": self.action_optimize_net()
        elif btn_id == "opt-cpu": self.action_optimize_cpu()
        elif btn_id == "opt-mem": self.action_optimize_mem()
        elif btn_id == "opt-gpu": self.run_optimization(self.query_one(GpuPanel), "GPU", RED, "#gpu-text")
        elif btn_id == "opt-net": self.action_optimize_net()
        elif btn_id.startswith("btn-resume-"):
            slot_id = btn_id.split("btn-resume-")[1]
            sid = self.session_map.get(slot_id)
            if sid:
                try:
                    cmd = f"agy --conversation {sid}"
                    agent = "agy"
                    if "claude" in slot_id: agent = "claude"
                    elif "codex" in slot_id: agent = "codex"
                    elif "opencode" in slot_id: agent = "opencode"
                    elif "kimi" in slot_id: agent = "kimi"
                    
                    if agent == "claude":
                        cmd = "claude --continue" if "REC-CLAUDE" in sid else f"claude resume {sid}"
                    elif agent == "codex":
                        cmd = "codex resume --all"
                    elif agent == "opencode":
                        cmd = "opencode session list -n 10"
                    elif agent == "kimi":
                        cmd = "kimi -c" if "REC-KIMI" in sid else f"kimi --session {sid}"
                        
                    subprocess.Popen([
                        "wt", "-w", "0", "new-tab", "-p", "Windows PowerShell", "-d", str(BASE_DIR),
                        "powershell", "-NoExit", "-Command", cmd
                    ])
                    self._log_event("SYNC", f"RESUMING {Companion(s).upper()}", CYAN)
                except Exception as e:
                    self._log_event("ERR", f"FAIL RESUME: {e}", RED)
            return

    def action_optimize_all(self) -> None:
        self.run_optimization(self.query_one(CpuPanel), "CPU", CYAN, "#cpu-text")
        self.run_optimization(self.query_one(MemoryPanel), "RAM", AMBER, "#mem-text")
        self.run_optimization(self.query_one(GpuPanel), "GPU", RED, "#gpu-text")
        self.run_optimization(self.query_one(NetworkPanel), "NET", GREEN, "#net-text")

    def action_optimize_cpu(self) -> None:
        self.run_optimization(self.query_one(CpuPanel), "CPU", CYAN, "#cpu-text")

    def action_optimize_mem(self) -> None:
        self.run_optimization(self.query_one(MemoryPanel), "RAM", AMBER, "#mem-text")

    def action_optimize_net(self) -> None:
        self.run_optimization(self.query_one(NetworkPanel), "NET", GREEN, "#net-text")

    def run_optimization(self, panel: Vertical, name: str, color: str, text_id: str) -> None:
        if getattr(panel, "is_optimizing", False): return
        panel.is_optimizing = True
        
        btn_id_map = {"CPU": "#opt-cpu", "RAM": "#opt-mem", "GPU": "#opt-gpu", "NET": "#opt-net"}
        btn = panel.query_one(btn_id_map[name], Button)
        
        def step(i: int):
            if i > 10:
                panel.is_optimizing = False
                panel.remove_class("flash")
                btn.label = f"⚡ CLEAN {name}"
                self._log_event("OPT", f"{name} CIRCUITS PURGED", color)
                if name == "RAM":
                    import gc
                    gc.collect()
                return
            
            pct = i * 10
            bar_str = bar(pct, 8)
            btn.label = f"⚡ {bar_str} {pct}%"
            
            if i == 10:
                panel.add_class("flash")
                try:
                    txt = panel.query_one(text_id, Static)
                    txt.update(
                        f"[bold white underline]{name} 100% CLEAR[/]\n\n"
                        f" [yellow]⚡ 💥 ZAPPING 💥 ⚡[/] \n"
                        f" PURGING CACHE...\n"
                    )
                except: pass
            else:
                try:
                    txt = panel.query_one(text_id, Static)
                    txt.update(
                        f"[bold {color} underline]{name} CLEANING...[/]\n\n"
                        f" POWER {pct}% \n"
                        f" [#536078]{bar(pct, 20)}[/]\n"
                    )
                except: pass
                
            self.set_timer(0.12, lambda: step(i + 1))
            
        step(0)


def run_dashboard() -> None:
    Dashboard().run()


if __name__ == "__main__":
    run_dashboard()
