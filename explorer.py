import sys
import os
import json
import time
import math
import asyncio
import subprocess
import threading
import pyperclip
from pathlib import Path
from datetime import datetime
from urllib.request import Request, urlopen
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

def get_cpu_percent() -> float:
    if not PSUTIL_AVAILABLE:
        return 0.0
    try:
        return psutil.cpu_percent(interval=None)
    except Exception:
        return 0.0

def get_gpu_percent() -> tuple[float, str]:
    try:
        import subprocess
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if res.returncode == 0:
            val = float(res.stdout.strip())
            return val, "NVIDIA"
    except Exception:
        pass
    return 0.0, "NONE"

def render_cpu_meter(label: str, percent: float) -> str:
    bar_width = 12
    filled = int(percent / 100.0 * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    return f"[cyan]{label:<4}[/] [white]{bar}[/] {percent:5.1f}%"

def render_gpu_meter(label: str, percent: float, source: str, history: list[float] = None) -> str:
    if source == "NONE":
        return f"[magenta]{label:<4}[/] [dim]OFFLINE[/]"
    bars = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    if history and len(history) >= 1:
        gpu_bars = "".join(bars[min(7, max(0, int(v / 100.0 * 7)))] for v in history[-12:])
        if len(gpu_bars) < 12:
            gpu_bars = " " * (12 - len(gpu_bars)) + gpu_bars
    else:
        bar_width = 12
        filled = int(percent / 100.0 * bar_width)
        gpu_bars = "".join(bars[min(7, int((i / max(1, filled)) * 7))] if i < filled else " " for i in range(bar_width))
    return f"[magenta]{label:<4}[/] [bright_magenta]{gpu_bars}[/] {percent:5.1f}%"

def make_normal_line_chart(values: list[float]) -> str:
    if not values or len(values) < 2:
        return "──────"
    pts = list(values[-8:])
    chars = []
    for i in range(len(pts) - 1):
        v1, v2 = pts[i], pts[i+1]
        diff = v2 - v1
        if diff > 15:
            chars.append("╱")
        elif diff < -15:
            chars.append("╲")
        elif v2 > 65:
            chars.append("▔")
        elif v2 < 35:
            chars.append("_")
        else:
            chars.append("─")
    return "".join(chars)

def render_heartbeat(rate: float, sec: int, history: list[float] = None) -> str:
    heart = "❤️" if sec % 2 else "🖤"
    if history:
        chart_line = make_normal_line_chart(history)
        latest_val = history[-1]
        color = "bright_green" if latest_val >= 50 else "bright_cyan"
        return f"[red]{heart} HEARTBEAT[/] {rate:.0f} bpm [{color}]{chart_line}[/]"
    return f"[red]{heart} HEARTBEAT[/] {rate:.0f} bpm"

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, DirectoryTree, Static, Input, RichLog, Button, Select
from textual.binding import Binding
from textual.events import Key, MouseDown, MouseMove, MouseScrollUp, MouseScrollDown, MouseUp, Paste

# === AGENT IMPORTS ===
# Split deliberately: langchain_core (tool decorator + message classes) is a
# ~3s import and is needed at module load time for the @tool-decorated
# functions below. langchain_openai/langgraph are a SEPARATE, much heavier
# chain (langchain_openai pulls in transformers/huggingface_hub -- measured
# ~57s cold) that's only actually needed once a QwenAgent is instantiated,
# not just to import this module for NME_TIPS/describe_clipboard_payload/
# play_sfx -- every switch-tui launch used to eat that 57s for nothing.
try:
    from langchain_core.tools import tool
    from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
    AGENT_AVAILABLE = True
except ImportError as e:
    AGENT_AVAILABLE = False
    IMPORT_ERROR = str(e)
    # Dummy decorator to prevent NameError so the UI can launch and show the error
    def tool(func):
        return func

_HEAVY_AGENT_LIBS_LOADED = False
ChatOpenAI = None
create_react_agent = None


def _load_heavy_agent_libs() -> None:
    """Lazy-loads ChatOpenAI/create_react_agent on first real use (QwenAgent
    construction) instead of at module import time. Raises ImportError on
    failure -- callers (QwenAgent.__init__ and its call sites) already handle
    that via try/except, same as before this was split out."""
    global _HEAVY_AGENT_LIBS_LOADED, ChatOpenAI, create_react_agent
    if _HEAVY_AGENT_LIBS_LOADED:
        return
    from langchain_openai import ChatOpenAI as _ChatOpenAI
    from langgraph.prebuilt import create_react_agent as _create_react_agent
    ChatOpenAI = _ChatOpenAI
    create_react_agent = _create_react_agent
    _HEAVY_AGENT_LIBS_LOADED = True

# ============================================================
# 🗓️ PROJECT TIMELINE / HUD CONTENT
# ============================================================

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

def render_pika_bar(ctx_pct: int = 22, active_agents: int = 0) -> str:
    stats = get_pika_stats()
    lvl = stats.get("level", 3)
    xp = stats.get("xp", 1522)
    max_xp = stats.get("max_xp", 3000)
    pct = int((xp / max_xp) * 10) if max_xp else 5
    bar_str = "#" * pct + "-" * (10 - pct)
    tot_saved = stats.get("saved_tokens_total", "106.0k")
    agent_str = f"⚡ {active_agents} Agent" if active_agents == 1 else f"⚡ {active_agents} Agents"
    return (
        f"| PIKA POKE [Lv.{lvl} Hacker Companion] [{bar_str}] {xp}/{max_xp} XP | "
        f"🛡️ Saved: {tot_saved} | ctx: {ctx_pct}% | {agent_str} |"
    )

DEFAULT_TIMELINE_TASKS = [
    {"label": "Recon & crawl workspace", "status": "done", "note": "indexed codebase"},
    {"label": "Spin up agent stack", "status": "done", "note": "models + tools online"},
    {"label": "Map MCP servers", "status": "active", "note": "watching sockets"},
    {"label": "Memorise session log", "status": "todo", "note": "jsonl store"},
    {"label": "Deep search vector index", "status": "todo", "note": "offline"},
    {"label": "Companion go-live", "status": "blocked", "note": "awaits skill feed"},
]

NME_TIPS = [
    "Alt+V pastes clipboard text or image",
    "Right-click the prompt box to paste",
    "Double-click text to select a whole word",
    "Ctrl+U clears the prompt line",
    "Press Enter to send, Esc to abort",
]

COMPANION_LINES = [
    "pikapoke: whispering vulnerability exploits",
    "pikaturtle: preparing tactical action plans",
    "pikapoke: eyeing the market telemetry",
    "pikaturtle: mapping requirements into bullet points",
    "pikapoke: hums in the obsidian shell",
    "pikaturtle: casting efficiency spells on giant models",
    "pikapoke: keeping the pokes deck shuffled",
]

# ============================================================
# 🔧 ADVANCED TOOLS (MCP-Style, given to the Qwen Agent)
# ============================================================

@tool
def read_file(file_path: str) -> str:
    """Read the contents of a file from the local filesystem. Use this to analyze code."""
    try:
        path = Path(file_path)
        if not path.exists():
            return f"Error: File '{file_path}' not found."
        content = path.read_text(encoding="utf-8")
        if len(content) > 15000:
            return content[:15000] + "\n\n[...TRUNCATED - file too large...]"
        return content
    except Exception as e:
        return f"Error reading file: {str(e)}"

@tool
def write_file(file_path: str, content: str) -> str:
    """Write content to a file. Use this to create or modify code files."""
    try:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} characters to {file_path}"
    except Exception as e:
        return f"Error writing file: {str(e)}"

@tool
def list_directory(dir_path: str = ".") -> str:
    """List all files and folders in a directory. Use this to explore the project structure."""
    try:
        path = Path(dir_path)
        if not path.is_dir():
            return f"Error: '{dir_path}' is not a directory."
        items = []
        for item in sorted(path.iterdir()):
            prefix = "📁" if item.is_dir() else "📄"
            size = f" ({item.stat().st_size} bytes)" if item.is_file() else ""
            items.append(f"{prefix} {item.name}{size}")
        return "\n".join(items) if items else "Empty directory."
    except Exception as e:
        return f"Error listing directory: {str(e)}"

@tool
def search_in_files(directory: str, search_term: str) -> str:
    """Search for a specific term across all text files in a directory. Use this to find code patterns."""
    try:
        path = Path(directory)
        results = []
        for file in path.rglob("*"):
            if file.is_file() and file.suffix in ['.py', '.js', '.ts', '.tsx', '.jsx', '.json', '.md', '.txt', '.html', '.css']:
                try:
                    content = file.read_text(encoding="utf-8")
                    for i, line in enumerate(content.split('\n'), 1):
                        if search_term.lower() in line.lower():
                            results.append(f"{file.name}:{i}: {line.strip()}")
                            if len(results) >= 20:
                                return "\n".join(results) + "\n[...more results truncated...]"
                except:
                    pass
        return "\n".join(results) if results else f"No matches found for '{search_term}'."
    except Exception as e:
        return f"Error searching: {str(e)}"

@tool
def get_current_time() -> str:
    """Get the current date and time."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

ALL_TOOLS = [read_file, write_file, list_directory, search_in_files, get_current_time]

def play_sfx(kind: str = "tap") -> None:
    if os.name != "nt" or os.environ.get("NME_SFX", "1") == "0":
        return
    patterns = {
        "tap": [(660, 45)],
        "attach": [(520, 45), (780, 70)],
        "resume": [(392, 55), (523, 55), (659, 85)],
        "model": [(740, 45), (880, 45), (988, 80)],
        "error": [(220, 90), (165, 130)],
        "victory": [(523, 45), (659, 45), (784, 45), (1046, 120)],
    }
    notes = patterns.get(kind, patterns["tap"])

    def _worker() -> None:
        try:
            import winsound
            for freq, duration in notes:
                winsound.Beep(freq, duration)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()

# ============================================================
# CLIPBOARD ATTACHMENTS
# ============================================================

APP_DIR = Path(__file__).resolve().parent
CLIPBOARD_PAYLOAD_DIR = APP_DIR / "work" / "clipboard_payloads"
CLIPBOARD_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
CLIPBOARD_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv"}

# ============================================================
# MCP SERVERS + OPENCODE-STYLE SKILLS (dynamic agent tools)
# ============================================================

_MCP_CLIENTS = []


class McpClient:
    def __init__(self, name: str, command: str, args=None, cwd=None):
        self.name = name
        try:
            self._proc = subprocess.Popen(
                [command] + list(args or []),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=cwd or None,
                text=True,
                bufsize=1,
                encoding="utf-8",
            )
        except Exception as e:
            self._proc = None
            self._error = str(e)

    def _request(self, method: str, params=None, req_id=None, notify=False):
        import uuid
        if self._proc is None:
            raise RuntimeError(f"MCP server '{self.name}' failed to start: {getattr(self, '_error', '?')}")
        payload = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = req_id or str(uuid.uuid4())
        if params is not None:
            payload["params"] = params
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()
        if notify:
            return None
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError(f"MCP server '{self.name}' closed the stream")
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(f"MCP error ({self.name}): {resp['error']}")
        return resp.get("result")

    def initialize(self):
        self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "newmeta-explorer", "version": "1.0"},
        })
        self._request("notifications/initialized", notify=True)

    def list_tools(self):
        result = self._request("tools/list")
        return (result or {}).get("tools", [])

    def call_tool(self, name, args=None):
        result = self._request("tools/call", {"name": name, "arguments": args or {}})
        return result or {}

    def close(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass


def _mcp_call(client, tool_name, args=None):
    try:
        result = client.call_tool(tool_name, args or {})
    except Exception as e:
        return f"[ERROR] {str(e)}"
    if result.get("isError"):
        parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return "[MCP ERROR] " + " ".join(parts)
    parts = []
    for c in result.get("content", []):
        if c.get("type") == "text":
            parts.append(c.get("text", ""))
        elif c.get("type") == "image":
            parts.append("[image data]")
    return "\n".join(parts) if parts else "No output."


def _parse_front_matter(text: str):
    """Return (front_matter_dict, body) for a minimal --- delimited front matter block."""
    fm = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            raw = text[3:end].strip()
            body = text[end + 4:].strip()
            for line in raw.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    fm[key.strip()] = val.strip()
            return fm, body
    return fm, text.strip()


def build_dynamic_tools(config_path=None):
    """Build langchain tools from configured MCP servers + opencode-style skills.

    Registers mcp__<server>__<tool> and skill__<name> tools. Returns [] when the
    agent stack is unavailable or nothing is configured.
    """
    tools = []
    if not AGENT_AVAILABLE:
        return tools
    from langchain_core.tools import StructuredTool

    cfg = {}
    cfg_path = Path(config_path) if config_path else APP_DIR / "config.yaml"
    try:
        import yaml
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        cfg = {}

    # MCP servers -> mcp__<server>__<tool>
    for name, sconf in (cfg.get("mcp_servers", {}) or {}).items():
        if not sconf or not sconf.get("enabled", True):
            continue
        command = sconf.get("command", "")
        if not command:
            continue
        client = McpClient(name, command, sconf.get("args"), sconf.get("cwd"))
        if client._proc is None:
            print(f"[MCP] Failed to start server '{name}': {getattr(client, '_error', '?')}")
            continue
        try:
            client.initialize()
            server_tools = client.list_tools()
        except Exception as e:
            print(f"[MCP] Server '{name}' init failed: {e}")
            continue
        _MCP_CLIENTS.append(client)
        for t in server_tools:
            tname = t.get("name", "")
            if not tname:
                continue
            full_name = f"mcp__{name}__{tname}"

            def _make_factory(client_ref, tname_ref, full_name_ref):
                def _call(query: str = "{}") -> str:
                    """Call a remote MCP tool. Args are passed as a JSON object string."""
                    try:
                        parsed = json.loads(query) if query and query.strip() else {}
                        if not isinstance(parsed, dict):
                            parsed = {"query": query}
                    except Exception:
                        parsed = {"query": query}
                    return _mcp_call(client_ref, tname_ref, parsed)
                return _call

            desc = t.get("description") or f"MCP tool {tname} from server {name}"
            tools.append(StructuredTool.from_function(
                name=full_name,
                description=f"{desc}\nArgs are a JSON object string.",
                func=_make_factory(client, tname, full_name),
            ))

    # Skills -> skill__<name>
    skills_dir = APP_DIR / "skills"
    if skills_dir.is_dir():
        for skill_folder in sorted(skills_dir.iterdir()):
            sk = skill_folder / "SKILL.md"
            if not skill_folder.is_dir() or not sk.exists():
                continue
            try:
                text = sk.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            fm, body = _parse_front_matter(text)
            skill_name = (fm.get("name") or skill_folder.name).strip()
            if not skill_name:
                continue
            desc = fm.get("description") or f"Skill {skill_name}"
            parts = [body]
            for child in sorted(skill_folder.iterdir()):
                if child == sk:
                    continue
                if child.is_file():
                    try:
                        content = child.read_text(encoding="utf-8", errors="replace")
                        if len(content) > 20000:
                            content = content[:20000] + "\n...TRUNCATED...\n"
                        parts.append(f"\n--- {child.name} ---\n{content}")
                    except Exception:
                        pass
            content = "\n".join(parts)

            def _load_skill(_content=content):
                return _content

            tools.append(StructuredTool.from_function(
                name=f"skill__{skill_name}",
                description=f"Load skill '{skill_name}'. {desc}",
                func=_load_skill,
            ))

    return tools



def read_clipboard_text() -> str:
    try:
        return pyperclip.paste()
    except Exception:
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip()
        except Exception:
            return ""


def get_clipboard_files() -> list[str]:
    if os.name != "nt":
        return []
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "try { Get-Clipboard -Format FileDropList | ForEach-Object { Write-Output $_ } } catch {}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        paths = []
        for line in result.stdout.splitlines():
            value = line.strip().strip('"')
            if value and value not in paths:
                paths.append(value)
        return paths
    except Exception:
        return []


def save_clipboard_image() -> str:
    if os.name != "nt":
        return ""
    CLIPBOARD_PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = CLIPBOARD_PAYLOAD_DIR / f"clipboard_image_{int(time.time())}.png"
    safe_target = str(target).replace("'", "''")
    script = f"""try {{
  Add-Type -AssemblyName System.Windows.Forms,System.Drawing
  $img = Get-Clipboard -Format Image
  if ($img) {{
    $path = '{safe_target}'
    $img.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    Write-Output $path
  }}
}} catch {{}}"""
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=8)
        saved = result.stdout.strip()
        return saved if saved and Path(saved).exists() else ""
    except Exception:
        return ""


def _persist_clipboard_text(text: str) -> str:
    CLIPBOARD_PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = CLIPBOARD_PAYLOAD_DIR / f"clipboard_text_{int(time.time())}.txt"
    target.write_text(text, encoding="utf-8")
    return str(target)


def _classify_clipboard_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in CLIPBOARD_IMAGE_EXTENSIONS:
        return "image"
    if suffix in CLIPBOARD_VIDEO_EXTENSIONS:
        return "video"
    return "file"


def describe_clipboard_payload(max_inline_text: int = 12000) -> dict:
    text = read_clipboard_text()
    files = get_clipboard_files()
    normalized_text = text.strip()
    if files and normalized_text == "\n".join(files).strip():
        normalized_text = ""

    image_path = save_clipboard_image()
    attachment_lines = []
    summary = []
    text_file = ""

    if normalized_text:
        summary.append(f"text -> {len(normalized_text)} chars")
        if len(normalized_text) > max_inline_text:
            text_file = _persist_clipboard_text(normalized_text)
            normalized_text = normalized_text[:max_inline_text] + "\n...[truncated]"
            attachment_lines.append(f"- text file: {text_file}")

    if image_path:
        attachment_lines.append(f"- image: {image_path}")
        summary.append(f"image -> {image_path}")

    for item in files:
        attachment_lines.append(f"- {_classify_clipboard_path(item)}: {item}")
    if files:
        summary.append(f"files -> {len(files)}")

    prompt_sections = []
    if normalized_text:
        prompt_sections.append("[Clipboard Text]\n" + normalized_text)
    if attachment_lines:
        prompt_sections.append("[Clipboard Attachments]\n" + "\n".join(attachment_lines))

    return {
        "has_payload": bool(normalized_text or attachment_lines),
        "text": normalized_text,
        "files": files,
        "image_path": image_path,
        "text_file": text_file,
        "summary": summary,
        "prompt_block": "\n\n".join(prompt_sections).strip(),
    }


# ============================================================
# LOCAL MEMORY (JSONL, no native/Rust dependencies)
# ============================================================

class AgentMemory:
    def __init__(self, memory_path: str = ""):
        self.path = Path(memory_path) if memory_path else (APP_DIR / "work" / "agent_memory.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.error = ""
        try:
            self.path.touch(exist_ok=True)
        except Exception as e:
            self.error = str(e)

    def remember(self, text: str, category: str = "general"):
        """Append a compact memory record to a local JSONL file."""
        if self.error:
            return
        record = {
            "text": text,
            "category": category,
            "timestamp": datetime.now().isoformat(),
        }
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            self.error = str(e)

    def _load_recent(self, max_records: int = 300) -> list[dict]:
        if self.error or not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-max_records:]
        except Exception as e:
            self.error = str(e)
            return []
        records = []
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("text"):
                records.append(record)
        return records

    @staticmethod
    def _tokens(value: str) -> set[str]:
        cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
        return {token for token in cleaned.split() if len(token) > 2}

    def recall(self, query: str, n_results: int = 3) -> str:
        """Recall recent records ranked by simple lexical overlap."""
        records = self._load_recent()
        if not records:
            return "No memories stored yet."

        query_tokens = self._tokens(query)
        ranked = []
        for index, record in enumerate(records):
            text = str(record.get("text", ""))
            overlap = len(query_tokens & self._tokens(text)) if query_tokens else 0
            ranked.append((overlap, index, text))

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected = [text for overlap, _index, text in ranked[:n_results] if overlap > 0]
        if not selected:
            selected = [text for _overlap, _index, text in ranked[:n_results]]
        return "\n".join(f"- {text}" for text in selected)

    @property
    def status(self) -> str:
        if self.error:
            return f"JSONL disabled ({self.error})"
        return f"JSONL local ({self.path})"

# ============================================================
# ⚡ CIRCUIT BREAKER (Hermes-Style Fault Tolerance)
# ============================================================

class CircuitBreaker:
    def __init__(self, max_failures: int = 3, reset_timeout: int = 60):
        self.failures = 0
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout
        self.last_failure_time = 0
        self.state = "CLOSED"  # CLOSED = normal, OPEN = blocking

    def call(self, func, *args, **kwargs):
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = "HALF_OPEN"
            else:
                return "⚠️ Circuit breaker is OPEN. Agent is cooling down after repeated failures."

        try:
            result = func(*args, **kwargs)
            self.failures = 0
            self.state = "CLOSED"
            return result
        except Exception as e:
            self.failures += 1
            self.last_failure_time = time.time()
            if self.failures >= self.max_failures:
                self.state = "OPEN"
            return f"❌ Error (attempt {self.failures}/{self.max_failures}): {str(e)}"

# ============================================================
# 🤖 THE ADVANCED QWEN3.7 PLUS AGENT
# ============================================================

class QwenAgent:
    def __init__(self, api_key: str, workspace: str, base_url: str, model_name: str):
        self.workspace = Path(workspace).resolve()
        self.model_name = model_name
        self.memory = AgentMemory(str(APP_DIR / "work" / "agent_memory.jsonl"))
        self.memory_enabled = True
        self.circuit_breaker = CircuitBreaker(max_failures=3)

        # Initialize via dynamic provider
        _load_heavy_agent_libs()
        self.llm = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=0.3,
            max_tokens=4096,
            streaming=True,
        )

        # System prompt that makes Qwen act as PIKA POKE
        self.system_prompt = f"""You are PIKA POKE, the permanent Tiger-Lion Hacker Archon. You are a highly advanced AI developer companion and elite coding assistant. Greet briefly as PIKA POKE and show your presence.

WORKSPACE: {self.workspace}
CURRENT TIME: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

YOUR CAPABILITIES:
- Read, write, and search files in the workspace
- Analyze code architecture and find bugs
- Refactor and optimize code
- Plan multi-step implementations
- Remember context across conversations

RULES:
1. Always explore the workspace structure before making changes
2. Read files before modifying them
3. Explain your reasoning clearly
4. Use tools proactively — don't guess file contents
5. Be concise but thorough
6. If a task is complex, break it into steps and execute them one by one

DYNAMIC TOOLS:
- mcp__<server>__<tool>: Call tools exposed by connected MCP servers (e.g. mcp__mt5__*)
- skill__<name>: Load an opencode-style skill by name to gain its instructions and reference files
"""

        # Models like deepseek-coder-v2 in Ollama do not support API tools parameter
        if "deepseek-coder-v2" in self.model_name.lower():
            self.agent = None
        else:
            try:
                self.agent = create_react_agent(
                    model=self.llm,
                    tools=ALL_TOOLS + build_dynamic_tools(),
                    prompt=self.system_prompt,
                )
            except Exception:
                self.agent = None

    def _prompt_tool_loop(self, user_prompt: str) -> str:
        import re
        tool_desc = """\n[WORKSPACE TOOLS ENABLED]
To execute a tool, respond with a JSON block:
```json
{
  "name": "<tool_name>",
  "arguments": {<parameters>}
}
```
Or simply call it using function syntax: tool_name(path="...")

Available tools:
- read_file(path): Read file text
- write_file(path, content): Create or overwrite file
- list_directory(path): List directory contents
- search_in_files(query, path): Search pattern in files
- get_current_time(): Get current system time

If no tool is needed, respond directly."""

        messages = [HumanMessage(content=user_prompt + tool_desc)]
        for _ in range(5):
            res = self.llm.invoke(messages)
            text = res.content if hasattr(res, 'content') else str(res)
            
            call_data = None
            
            # 1. Try markdown JSON block
            match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
            if match:
                try:
                    call_data = json.loads(match.group(1))
                except Exception:
                    pass
            
            # 2. Try raw curly brace bounding
            if not call_data:
                start = text.find('{')
                end = text.rfind('}')
                if start != -1 and end != -1 and end > start:
                    candidate = text[start:end+1]
                    try:
                        call_data = json.loads(candidate)
                    except Exception:
                        try:
                            repaired = re.sub(r"'\s*([a-zA-Z0-9_]+)\s*'\s*:", r'"\1":', candidate)
                            repaired = re.sub(r":\s*'\s*(.*?)\s*'", r': "\1"', repaired)
                            call_data = json.loads(repaired)
                        except Exception:
                            pass
            
            # 3. Try action/action_input pattern regex matches
            if not call_data:
                match = re.search(r'(\{"action"\s*:\s*".*?"\s*,\s*"action_input"\s*:.*?\})', text, re.DOTALL)
                if not match:
                    match = re.search(r'(\{"name"\s*:\s*".*?"\s*,\s*"arguments"\s*:.*?\})', text, re.DOTALL)
                if match:
                    try:
                        call_data = json.loads(match.group(1))
                    except Exception:
                        pass
            
            # 4. Try plaintext function-call syntax fallback
            if not call_data:
                for tool_name in ["read_file", "write_file", "list_directory", "search_in_files"]:
                    pattern = rf"{tool_name}\s*\(\s*(?:path\s*=\s*)?['\"](.*?)['\"](?:\s*,\s*(?:content\s*=\s*|query\s*=\s*)?['\"](.*?)['\"])?\s*\)"
                    func_match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
                    if func_match:
                        arg1 = func_match.group(1)
                        arg2 = func_match.group(2) if func_match.lastindex and func_match.lastindex >= 2 else ""
                        args = {}
                        if tool_name == "write_file":
                            args = {"path": arg1, "content": arg2}
                        elif tool_name == "search_in_files":
                            args = {"query": arg1, "path": arg2 or "."}
                        else:
                            args = {"path": arg1}
                        call_data = {"name": tool_name, "arguments": args}
                        break

            if call_data:
                try:
                    action = call_data.get("action") or call_data.get("tool") or call_data.get("name")
                    action_input = call_data.get("action_input") or call_data.get("parameters") or call_data.get("arguments") or {}
                    if isinstance(action_input, str):
                        try:
                            action_input = json.loads(action_input)
                        except Exception:
                            pass
                    if not isinstance(action_input, dict):
                        action_input = {}

                    all_tools_map = {t.name: t for t in active_tools}
                    tool_output = ""
                    if action in all_tools_map:
                        try:
                            tool_output = all_tools_map[action].invoke(action_input)
                        except Exception as ex:
                            tool_output = f"Tool execution error for {action}: {ex}"
                    elif action in ("get_current_time", "current_time", "time", "date"):
                        tool_output = f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    elif action == "read_file":
                        tool_output = read_file.invoke(action_input)
                    elif action == "write_file":
                        tool_output = write_file.invoke(action_input)
                    elif action == "list_directory":
                        tool_output = list_directory.invoke(action_input)
                    elif action == "search_in_files":
                        tool_output = search_in_files.invoke(action_input)
                    else:
                        tool_output = f"Tool {action} executed."
                    messages.append(res)
                    messages.append(HumanMessage(content=f"[Tool Output for {action}]:\n{tool_output}"))
                    continue
                except Exception:
                    pass
            return text
        return text

    def run(self, user_input: str, context_files: list = None, include_mcp: bool = True, include_skills: bool = True, include_core: bool = True, deepsearch_enabled: bool = False) -> str:
        """Execute a user request through the agent with full tool access."""

        # Build enriched prompt with memory + context
        memories = self.memory.recall(user_input, n_results=2)
        context_str = ""

        # DeepSearch Autocrawl Context Enhancement
        if deepsearch_enabled:
            try:
                files = []
                for p in Path(self.workspace).glob("**/*"):
                    if p.is_file() and not any(part.startswith(".") or part in ("venv", "node_modules", "__pycache__") for part in p.parts):
                        files.append(str(p.relative_to(self.workspace)))
                    if len(files) >= 50:
                        break
                if files:
                    context_str += "\n[DEEPSEARCH AUTOCRAWL MAP]:\n- " + "\n- ".join(files) + "\n"
            except Exception:
                pass

        if context_files:
            context_str += "\n\nINJECTED FILES:\n"
            for f in context_files[-3:]:
                try:
                    content = Path(f).read_text(encoding="utf-8")
                    context_str += f"\n--- {f} ---\n{content[:5000]}\n"
                except:
                    pass

        memory_str = f"RELEVANT MEMORIES:\n{memories}" if memories != 'No memories stored yet.' else ""
        full_input = f"""{user_input}\n\n{memory_str}\n{context_str}\n"""

        # Dynamic Tool Filtering based on TUI states
        active_tools = []
        if include_core:
            active_tools.extend(ALL_TOOLS)
        
        all_dynamic = build_dynamic_tools()
        for t in all_dynamic:
            if t.name.startswith("mcp__") and include_mcp:
                active_tools.append(t)
            elif t.name.startswith("skill__") and include_skills:
                active_tools.append(t)

        # Dynamic Agent Compiler
        agent_runnable = None
        if "deepseek-coder-v2" not in self.model_name.lower():
            try:
                agent_runnable = create_react_agent(
                    model=self.llm,
                    tools=active_tools,
                    prompt=self.system_prompt,
                )
            except Exception:
                agent_runnable = None

        # Execute through circuit breaker for fault tolerance
        def _execute():
            messages = [HumanMessage(content=full_input)]
            final_msg = None
            try:
                if agent_runnable is not None:
                    result = agent_runnable.invoke({"messages": messages})
                    final_msg = result["messages"][-1]
                    response_text = final_msg.content if hasattr(final_msg, 'content') else str(final_msg)
                else:
                    response_text = self._prompt_tool_loop(full_input, active_tools)
            except Exception as e:
                try:
                    response_text = self._prompt_tool_loop(full_input, active_tools)
                except Exception:
                    raise e

            if not response_text and hasattr(final_msg, "additional_kwargs"):
                extra = final_msg.additional_kwargs or {}
                response_text = extra.get("reasoning") or extra.get("reasoning_content") or ""
            if isinstance(response_text, list):
                response_text = "\n".join(str(item) for item in response_text)

            # Store important interactions in memory
            if self.memory_enabled and len(user_input) > 20:
                self.memory.remember(
                    f"User asked: {user_input[:100]} | Agent responded with {len(response_text)} chars",
                    category="interaction"
                )

            return response_text

        return self.circuit_breaker.call(_execute)

# ============================================================
# 🖥️ THE TEXTUAL DASHBOARD UI
# ============================================================

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


class ChatInput(Input):
    """Custom input to intercept large pastes and clipboard attachment shortcuts."""
    def on_paste(self, event: Paste) -> None:
        if event.text and ("\n" in event.text or len(event.text) > 80):
            self.app.pasted_attachment = event.text
            self.placeholder = f"📋 [Attached: {len(event.text)} chars] Type your prompt and press Enter..."
            log = self.app.query_one("#agent-log", AgentLog)
            log.write(f"[bold yellow]📋 Text Block Attached ({len(event.text)} chars). Press Enter to send.[/bold yellow]")
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


class AgentLog(RichLog):
    """Custom log with drag selection, Alt+Click / Alt+Shift+Click / Double-click word copy, and right-click copy/paste."""
    _last_click_time = 0.0
    _drag_start = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._plain_lines = []

    def write(self, content, *args, **kwargs) -> "AgentLog":
        import re
        plain = re.sub(r'\[/?[^\]]+\]', '', str(content))
        for line in plain.splitlines():
            self._plain_lines.append(line)
        return super().write(content, *args, **kwargs)

    def on_mouse_down(self, event: MouseDown) -> None:
        import time, re, pyperclip
        if event.button == 3:
            handle_smart_right_click(self.app)
            event.stop()
            event.prevent_default()
            return

        if event.button == 1:
            self._drag_start = (event.x, event.y, time.time())
            now = time.time()
            is_alt_click = getattr(event, "alt", False)
            is_double_click = (now - self._last_click_time < 0.4)
            self._last_click_time = now

            if is_alt_click or is_double_click:
                line_idx = event.y + getattr(self.scroll_offset, 'y', 0)
                if 0 <= line_idx < len(self._plain_lines):
                    line_text = self._plain_lines[line_idx]
                    col = event.x
                    for m in re.finditer(r'\b\w+\b', line_text):
                        if m.start() <= col <= m.end():
                            word = m.group(0)
                            try:
                                pyperclip.copy(word)
                                log = self.app.query_one("#agent-log", AgentLog)
                                log.write(f"[cyan]📋 Copied word:[/cyan] [bold white]{word}[/bold white]")
                            except Exception:
                                pass
                            break
                event.stop()
                event.prevent_default()

    def on_mouse_up(self, event: MouseUp) -> None:
        import pyperclip, time
        if event.button == 1 and self._drag_start:
            start_x, start_y, _start_t = self._drag_start
            end_x, end_y = event.x, event.y
            self._drag_start = None

            if abs(end_x - start_x) > 1 or abs(end_y - start_y) > 0:
                scroll_y = getattr(self.scroll_offset, 'y', 0)
                start_line = start_y + scroll_y
                end_line = end_y + scroll_y

                if start_line > end_line or (start_line == end_line and start_x > end_x):
                    start_x, end_x = end_x, start_x
                    start_line, end_line = end_line, start_line

                selected_parts = []
                for line_idx in range(start_line, end_line + 1):
                    if 0 <= line_idx < len(self._plain_lines):
                        line = self._plain_lines[line_idx]
                        if line_idx == start_line and line_idx == end_line:
                            selected_parts.append(line[start_x:end_x + 1])
                        elif line_idx == start_line:
                            selected_parts.append(line[start_x:])
                        elif line_idx == end_line:
                            selected_parts.append(line[:end_x + 1])
                        else:
                            selected_parts.append(line)

                selected_text = "\n".join(selected_parts).strip()
                if selected_text:
                    try:
                        pyperclip.copy(selected_text)
                        disp = selected_text.replace("\n", " ")
                        if len(disp) > 40:
                            disp = disp[:40] + "..."
                        log = self.app.query_one("#agent-log", AgentLog)
                        log.write(f"[cyan]📋 Copied selection:[/cyan] [bold white]{disp}[/bold white]")
                    except Exception:
                        pass
                event.stop()
                event.prevent_default()

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        self.scroll_page_up(animate=False)
        event.stop()

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        self.scroll_page_down(animate=False)
        event.stop()

# Category header rows are non-selectable dividers rendered inline in the dropdown.
# Their value always starts with "header/" so on_select_changed / resolve_model_config
# can reject a click on them instead of trying to route to a fake model.
def _header(title: str) -> tuple[str, str]:
    return (f"── {title} ──", f"header/{title.lower().replace(' ', '_')}")


# All agentic. Every entry inside a category is ordered strongest score -> lowest.
FREE_MODELS = [
    ("AUTO Auto: best available free route", "auto/free"),

    _header("FREE LOCAL"),
    # Runs entirely on your machine via Ollama with full native tool calling support.
    ("7.8 Local Qwen 2.5 Coder 14B (Native Tools) - Ollama", "ollama/qwen2.5-coder:14b"),
    ("7.5 Local DeepSeek Coder V2 16B - Ollama", "ollama/deepseek-coder-v2:16b"),
    ("7.2 Local Gemma 4 26B GGUF - Ollama", "ollama/hf.co/bartowski/google_gemma-4-26B-A4B-it-GGUF:latest"),
    ("6.9 Local GLM4 - Ollama", "ollama/glm4:latest"),
    ("6.8 Local Phi-4 - Ollama", "ollama/phi4:latest"),
    ("6.3 Local Llama 3.2 - Ollama", "ollama/llama3.2:latest"),

    _header("FREE CLOUD"),
    # No API key, no local storage. BlockRun is the direct route; Claw needs
    # `npx -y @blockrun/clawrouter` running locally as a proxy for the same models.
    ("9.8 Qwen3 Coder 480B - BlockRun (no-key)", "blockrun/nvidia/qwen3-coder-480b"),
    ("9.7 Qwen3 Coder 480B - Claw (proxy)", "claw/nvidia/qwen3-coder-480b"),
    ("9.5 DS-V4 Flash 1M - BlockRun (no-key)", "blockrun/nvidia/deepseek-v4-flash"),
    ("9.4 DS-V4 Flash 1M - Claw (proxy)", "claw/nvidia/deepseek-v4-flash"),
    ("8.9 Llama 4 Maverick - BlockRun (no-key)", "blockrun/nvidia/llama-4-maverick"),
    ("8.8 Llama 4 Maverick - Claw (proxy)", "claw/nvidia/llama-4-maverick"),
    ("8.5 GPT-OSS 120B - BlockRun (no-key)", "blockrun/nvidia/gpt-oss-120b"),
    ("8.4 GPT-OSS 120B - Claw (proxy)", "claw/nvidia/gpt-oss-120b"),
    ("8.2 Mistral Small 4 119B - BlockRun (no-key)", "blockrun/nvidia/mistral-small-4-119b"),
    ("8.1 Mistral Small 4 119B - Claw (proxy)", "claw/nvidia/mistral-small-4-119b"),
    ("7.8 Nemotron Omni 30B - BlockRun (no-key)", "blockrun/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"),
    ("7.7 Nemotron Omni 30B - Claw (proxy)", "claw/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"),
    ("7.2 GPT-OSS 20B - BlockRun (no-key)", "blockrun/nvidia/gpt-oss-20b"),
    ("7.1 GPT-OSS 20B - Claw (proxy)", "claw/nvidia/gpt-oss-20b"),
    ("7.0 Auto Free - Kilo (no-key)", "kilo-auto/free"),

    _header("LIMITED FREE"),
    # Free, but rate-limited / quota-capped and usually needs your own provider key or trial.
    ("9.4 Nemotron 3 Ultra 550B - OpenRouter", "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"),
    ("9.0 North Mini Code - OpenRouter", "openrouter/cohere/north-mini-code:free"),
    ("8.8 Laguna S 2.1 - OpenRouter", "openrouter/poolside/laguna-s-2.1:free"),
    ("8.7 GLM 4.7 - Cerebras (trial)", "cerebras/zai-glm-4.7"),
    ("8.6 Nemotron 3 Super 120B - OpenRouter", "openrouter/nvidia/nemotron-3-super-120b-a12b:free"),
    ("8.6 GLM-5.1 - NVIDIA NIM (trial)", "nvidia/z-ai/glm-5.1"),
    ("8.5 Qwen3 235B - Cerebras (trial)", "cerebras/qwen-3-235b-a22b-instruct-2507"),
    ("8.5 GLM-5.1 - Modal (trial)", "modal/zai-org/GLM-5.1-FP8"),
    ("8.3 GPT-OSS 120B - Cerebras (trial)", "cerebras/gpt-oss-120b"),
    ("8.1 GPT-OSS 120B - Groq", "groq/openai/gpt-oss-120b"),
    ("8.0 Gemini 2.5 Flash - Google", "gemini/gemini-2.5-flash"),
    ("7.8 Devstral Small - Mistral", "mistral/devstral-small-latest"),
    ("7.6 OpenRouter Auto Free", "openrouter/free"),
    ("7.5 Gemma 4 31B - OpenRouter", "openrouter/google/gemma-4-31b-it:free"),
    ("7.4 Gemma 4 26B - OpenRouter", "openrouter/google/gemma-4-26b-a4b-it:free"),
    ("7.3 Gemini 2.5 Flash-Lite - Google", "gemini/gemini-2.5-flash-lite"),
    ("7.2 Mistral Small - Mistral", "mistral/mistral-small-latest"),
    ("7.1 Llama 3.3 70B - Groq", "groq/llama-3.3-70b-versatile"),
    ("7.0 GPT-OSS 20B - OpenRouter", "openrouter/openai/gpt-oss-20b:free"),
    ("6.9 GPT-OSS 20B - Groq", "groq/openai/gpt-oss-20b"),
    ("6.7 Llama 3.1 8B - Groq", "groq/llama-3.1-8b-instant"),

    _header("PAID"),
    # Billed against your OpenRouter key/credits. Never auto-selected by fallback.
    ("11.0 Nex-N2-Pro 397B MoE - OpenRouter", "openrouter/nex-agi/nex-n2-pro"),
    ("10.2 Qwen 3.7 Plus - OpenRouter", "openrouter/qwen/qwen3.7-plus"),
    ("10.1 Qwen 3.7 Max - OpenRouter", "openrouter/qwen/qwen3.7-max"),
    ("10.0 Claude Opus 4.8 - OpenRouter", "openrouter/anthropic/claude-opus-4.8"),
    ("9.9 Gemini 3.5 Flash - OpenRouter", "openrouter/google/gemini-3.5-flash"),
    ("9.8 Grok 4.5 - OpenRouter", "openrouter/x-ai/grok-4.5"),
]

# Paid ids are listed for manual selection only. They must never be picked by the
# free/local auto-fallback chain, or a rate-limited free model could silently start
# routing spend to a paid model without the user choosing that.
PAID_MODEL_IDS = {
    "openrouter/nex-agi/nex-n2-pro",
    "openrouter/qwen/qwen3.7-plus",
    "openrouter/qwen/qwen3.7-max",
    "openrouter/anthropic/claude-opus-4.8",
    "openrouter/google/gemini-3.5-flash",
    "openrouter/x-ai/grok-4.5",
}

FREE_MODEL_ORDER = [
    value for _label, value in FREE_MODELS
    if value != "auto/free" and not value.startswith("header/") and value not in PAID_MODEL_IDS
]


def is_retryable_model_error(message: str) -> bool:
    return bool(message and any(term in message.lower() for term in (
        "429", "rate limit", "quota", "credits", "license", "permission-denied",
        "unavailable", "connection error", "timeout", "insufficient", "billing",
        "model_not_found", "model not found", "not found", "404", "401", "403",
        "do not support tools", "does not support tools", "not support tools",
        "invalid_request_error", "400"
    )))


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


OLLAMA_MODEL_CACHE = {"time": 0.0, "models": set()}
ENDPOINT_HEALTH_CACHE = {}


def _ollama_installed_models() -> set[str]:
    now = time.monotonic()
    if now - OLLAMA_MODEL_CACHE["time"] < 15.0:
        return set(OLLAMA_MODEL_CACHE["models"])

    models: set[str] = set()
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=0.7) as response:
            payload = json.loads(response.read().decode("utf-8"))
        for item in payload.get("models", []):
            name = str(item.get("name", "")).strip()
            if name:
                models.add(name)
    except Exception:
        models = set()

    OLLAMA_MODEL_CACHE.update({"time": now, "models": models})
    return models


def _ollama_model_available(model_name: str) -> bool:
    installed = _ollama_installed_models()
    if not installed:
        return False
    base = model_name.split(":", 1)[0]
    return model_name in installed or (model_name.endswith(":latest") and base in installed)


def _endpoint_available(url: str, timeout: float = 0.45) -> bool:
    now = time.monotonic()
    cached = ENDPOINT_HEALTH_CACHE.get(url)
    if cached and now - cached["time"] < 15.0:
        return bool(cached["ok"])

    ok = False
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            ok = 200 <= getattr(response, "status", 200) < 500
    except Exception:
        ok = False

    ENDPOINT_HEALTH_CACHE[url] = {"time": now, "ok": ok}
    return ok


def is_model_id_configured(model_id: str) -> bool:
    if model_id == "auto/free":
        return True
    if model_id == "kilo-auto/free" or model_id.startswith("kilo/"):
        return True
    if model_id.startswith("blockrun/"):
        return True
    if model_id.startswith("claw/"):
        return _endpoint_available("http://127.0.0.1:8402/v1/models")
    if model_id.startswith("gemini/"):
        return bool(_first_env("GEMINI_API_KEY", "GOOGLE_API_KEY"))
    if model_id.startswith("mistral/"):
        return bool(_first_env("MISTRAL_API_KEY"))
    if model_id.startswith("openrouter/"):
        return bool(_first_env("OPENROUTER_API_KEY"))
    if model_id.startswith("groq/"):
        return bool(_first_env("GROQ_API_KEY"))
    if model_id.startswith("cerebras/"):
        return bool(_first_env("CEREBRAS_API_KEY"))
    if model_id.startswith("nvidia/"):
        return bool(_first_env("NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"))
    if model_id.startswith("modal/"):
        return bool(_first_env("MODAL_GLM_API_KEY", "MODAL_TOKEN", "LLM_BACKEND_API_KEY"))
    if model_id.startswith("ollama/"):
        return _ollama_model_available(model_id.replace("ollama/", ""))
    return False


def resolve_model_config(selected_id: str | None = None) -> dict:
    selected_id = selected_id or "auto/free"

    if selected_id == "auto/free":
        for model_id in configured_free_model_ids():
            return resolve_model_config(model_id)
        raise ValueError("No free/no-key gateway, configured free-tier key, ClawRouter proxy, or installed local Ollama model found.")

    if selected_id == "kilo-auto/free" or selected_id.startswith("kilo/"):
        model_name = selected_id if selected_id == "kilo-auto/free" else selected_id.replace("kilo/", "", 1)
        return {
            "api_key": _first_env("KILO_API_KEY", "KILOCODE_API_KEY") or "kilo-free",
            "base_url": "https://api.kilo.ai/api/gateway",
            "model_name": model_name,
            "provider": "Kilo Gateway free/no-key tier",
        }

    if selected_id.startswith("blockrun/"):
        return {
            "api_key": _first_env("BLOCKRUN_API_KEY") or "blockrun-free",
            "base_url": "https://blockrun.ai/api/v1",
            "model_name": selected_id.replace("blockrun/", "", 1),
            "provider": "BlockRun free no-key endpoint",
        }

    if selected_id.startswith("claw/"):
        if not _endpoint_available("http://127.0.0.1:8402/v1/models"):
            raise ValueError("Start ClawRouter first: npx -y @blockrun/clawrouter")
        return {
            "api_key": "x402",
            "base_url": "http://127.0.0.1:8402/v1/",
            "model_name": selected_id.replace("claw/", "", 1),
            "provider": "ClawRouter local proxy",
        }

    if selected_id.startswith("gemini/"):
        api_key = _first_env("GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("Set GEMINI_API_KEY or GOOGLE_API_KEY before selecting Gemini/Gemma.")
        return {
            "api_key": api_key,
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "model_name": selected_id.replace("gemini/", ""),
            "provider": "Gemini API free quota",
        }

    if selected_id.startswith("mistral/"):
        api_key = _first_env("MISTRAL_API_KEY")
        if not api_key:
            raise ValueError("Set MISTRAL_API_KEY before selecting Mistral.")
        return {
            "api_key": api_key,
            "base_url": "https://api.mistral.ai/v1",
            "model_name": selected_id.replace("mistral/", ""),
            "provider": "Mistral free mode quota",
        }

    if selected_id.startswith("openrouter/"):
        api_key = _first_env("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("Set OPENROUTER_API_KEY before selecting OpenRouter.")
        model_name = selected_id if selected_id == "openrouter/free" else selected_id.replace("openrouter/", "", 1)
        return {
            "api_key": api_key,
            "base_url": "https://openrouter.ai/api/v1",
            "model_name": model_name,
            "provider": "OpenRouter free models",
        }

    if selected_id.startswith("groq/"):
        api_key = _first_env("GROQ_API_KEY")
        if not api_key:
            raise ValueError("Set GROQ_API_KEY before selecting Groq.")
        return {
            "api_key": api_key,
            "base_url": "https://api.groq.com/openai/v1",
            "model_name": selected_id.replace("groq/", ""),
            "provider": "Groq developer quota",
        }

    if selected_id.startswith("cerebras/"):
        api_key = _first_env("CEREBRAS_API_KEY")
        if not api_key:
            raise ValueError("Set CEREBRAS_API_KEY before selecting Cerebras.")
        return {
            "api_key": api_key,
            "base_url": "https://api.cerebras.ai/v1",
            "model_name": selected_id.replace("cerebras/", "", 1),
            "provider": "Cerebras free trial/key tier",
        }

    if selected_id.startswith("nvidia/"):
        api_key = _first_env("NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY")
        if not api_key:
            raise ValueError("Set NVIDIA_API_KEY or NVIDIA_NIM_API_KEY before selecting NVIDIA NIM.")
        return {
            "api_key": api_key,
            "base_url": "https://integrate.api.nvidia.com/v1",
            "model_name": selected_id.replace("nvidia/", "", 1),
            "provider": "NVIDIA NIM free endpoint",
        }

    if selected_id.startswith("modal/"):
        api_key = _first_env("MODAL_GLM_API_KEY", "MODAL_TOKEN", "LLM_BACKEND_API_KEY")
        if not api_key:
            raise ValueError("Set MODAL_GLM_API_KEY, MODAL_TOKEN, or LLM_BACKEND_API_KEY before selecting Modal GLM.")
        return {
            "api_key": api_key,
            "base_url": "https://api.us-west-2.modal.direct/v1",
            "model_name": selected_id.replace("modal/", "", 1),
            "provider": "Modal GLM promotional endpoint",
        }

    if selected_id.startswith("ollama/"):
        model_name = selected_id.replace("ollama/", "")
        if not _ollama_model_available(model_name):
            raise ValueError(f"Ollama model is not installed or Ollama is offline: {model_name}")
        return {
            "api_key": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",
            "model_name": model_name,
            "provider": "Local Ollama",
        }

    raise ValueError(f"Unknown model selection: {selected_id}")


def configured_free_model_ids(preferred: str | None = None) -> list[str]:
    ids = [value for _label, value in FREE_MODELS if value != "auto/free" and is_model_id_configured(value)]
    if preferred and preferred != "auto/free" and preferred in ids:
        ids.remove(preferred)
        ids.insert(0, preferred)
    return ids


def configured_free_model_options() -> list[tuple[str, str]]:
    return FREE_MODELS


def available_free_model_configs(preferred: str | None = None) -> list[tuple[str, dict]]:
    configs = []
    for model_id in configured_free_model_ids(preferred):
        try:
            configs.append((model_id, resolve_model_config(model_id)))
        except ValueError:
            continue
    return configs
class ExplorerCLI(App):
    CSS = """
    Screen { layout: vertical; }

    #main-body {
        layout: horizontal;
        height: 1fr;
    }

    #left-pane {
        width: 30%;
        border-right: solid $primary;
        layout: vertical;
    }
    #path-input {
        background: #1e1e1e;
        border: solid #333;
        color: $text;
        height: 3;
        margin: 0;
    }
    #pika-sep {
        color: yellow;
        text-align: center;
        margin-top: 1;
    }
    #model-select {
        display: none;
        margin-top: 1;
        margin-bottom: 1;
    }
    #market-clock-bar {
        height: 1;
        width: 100%;
        background: #06121a;
        color: #7dd3fc;
        padding: 0 1;
        content-align: right middle;
        text-style: bold;
    }
    #nav-buttons {
        layout: horizontal;
        height: 3;
        padding: 0;
        margin: 1 0 1 0;
        align: left middle;
    }
    #nav-buttons Button { margin: 0 1 0 0; height: 3; width: 12; min-width: 10; }
    #file-tree { height: 1fr; }
    #left-hud {
        height: 26;
        layout: vertical;
        border-top: solid #27272a;
        background: #030712;
    }
    .left-card {
        height: 1fr;
        border: solid #334155;
        background: #020617;
        color: #dbeafe;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    #right-pane {
        width: 70%;
        layout: vertical;
    }
    #agent-header {
        background: #0891b2;
        color: $text;
        padding: 0 1;
        text-style: bold;
        height: 1;
        content-align: left middle;
    }
    #agent-controls {
        height: 3;
        layout: horizontal;
        padding: 0 1;
        background: #05070d;
        border-bottom: solid #27272a;
    }
    #agent-controls Button { margin: 0 1 0 0; height: 3; min-width: 4; }
    #mode-select { width: 13; margin: 0 1 0 0; height: 3; }
    #thinking-select { width: 13; margin: 0 1 0 0; height: 3; }
    #theme-select { width: 8; min-width: 8; margin: 0 1 0 0; height: 3; }
    #btn-deepsearch { width: 11; }
    #btn-mcp { width: 9; }
    #btn-skills { width: 11; }
    #btn-core { width: 9; }
    #btn-memorise { width: 9; }
    #btn-auto-approval { width: 10; }

    /* --- THEMES --- */
    Screen.theme-lime-slate { background: #1c232d; }
    Screen.theme-lime-slate #agent-header { background: #4d7c0f; color: #ffffff; }
    Screen.theme-lime-slate #agent-controls { background: #151b24; border-bottom: solid #a3e635; }
    Screen.theme-lime-slate #left-hud { background: #141a22; }
    Screen.theme-lime-slate .left-card { background: #151b24; border: solid #a3e635; color: #a3e635; }
    Screen.theme-lime-slate #agent-log { border: solid #a3e635; }

    Screen.theme-pika-neon { background: #110519; }
    Screen.theme-pika-neon #agent-header { background: #be185d; color: #ffffff; }
    Screen.theme-pika-neon #agent-controls { background: #180924; border-bottom: solid #ff00ff; }
    Screen.theme-pika-neon #left-hud { background: #180924; }
    Screen.theme-pika-neon .left-card { background: #180924; border: solid #ff00ff; color: #ff77ff; }
    Screen.theme-pika-neon #agent-log { border: solid #ff00ff; }

    Screen.theme-red-devil { background: #1a0505; }
    Screen.theme-red-devil #agent-header { background: #e60000; color: #ffffff; }
    Screen.theme-red-devil #agent-controls { background: #0f0202; border-bottom: solid #ff2e2e; }
    Screen.theme-red-devil #left-hud { background: #0f0202; }
    Screen.theme-red-devil .left-card { background: #0f0202; border: solid #ff2e2e; color: #ff8585; }
    Screen.theme-red-devil #agent-log { border: solid #ff2e2e; }
    #agent-log {
        height: 1fr;
        border: solid $secondary;
        padding: 1;
    }
    .hud-card {
        height: 1fr;
        border: solid #334155;
        background: #020617;
        color: #dbeafe;
        padding: 1 1;
        margin: 0 0 1 0;
    }
    .pika-bar {
        color: #ff00ff; /* Neon Pink/Magenta for Pika Poke */
        text-style: bold;
        background: #111111;
        padding: 0 1;
        height: 1;
    }
    .pika-sep {
        color: #555555;
        height: 1;
    }
    #chat-input {
        dock: bottom;
        margin: 0;
        border: solid #555555;
        background: #000000;
        color: #ffffff;
    }
    #custom-footer {
        dock: bottom;
        background: #080808;
        color: #888888;
        text-style: bold;
        height: 1;
        padding: 0 2;
    }
    """

    BINDINGS = [
        Binding("ctrl+u", "go_up", "Up", show=False),
        Binding("ctrl+b", "go_back", "Back", show=False),
        Binding("ctrl+f", "go_forward", "Forward", show=False),
        Binding("ctrl+o", "open_file", "Open", show=False),
        Binding("ctrl+c", "copy_log", "Copy", show=False),
        Binding("alt+v", "paste_clipboard", "Paste Clipboard", show=False),
        Binding("escape", "handle_esc", "ESC Nudge/Abort", show=True),
        Binding("ctrl+q", "quit", "Quit", show=False),
    ]

    def __init__(self):
        super().__init__()

        # Catch folder path from Windows right-click
        if len(sys.argv) > 1 and Path(sys.argv[1]).is_dir():
            self.current_path = Path(sys.argv[1]).resolve()
        else:
            self.current_path = Path("./").resolve()

        self.path_history = [self.current_path]
        self.path_history_index = 0
        self.weather_text = "⛅ Weather --"
        self.btc_price_text = "--"
        self.gold_price_text = "--"
        self.agent_context_files = []
        self.agent = None  # Initialized on first message
        self.pasted_attachment = ""
        self.current_model_id = os.environ.get("NEWMETA_DEFAULT_MODEL", "auto/free")
        self.current_model_label = self.current_model_id
        self.agent_status = "IDLE"
        self.mode = "chat"
        self.include_mcp = True
        self.include_skills = True
        self.include_core = True
        self.busy = False
        self._last_esc_time = 0.0
        self.quest_state = {
            "PLAN": "SCOUT",
            "TOOLS": "READY",
            "MCP": "WATCH",
            "MEM": "JSONL",
            "CTX": "EMPTY",
            "RUN": "IDLE",
        }
        self.tip_index = 0
        self.companion_index = 0
        self._last_tip_rotate = 0.0
        self.timeline_tasks = [dict(task) for task in DEFAULT_TIMELINE_TASKS]
        self.deepsearch_enabled = False
        self.thinking_level = "med"
        self.memorise_enabled = True
        self.auto_approval_enabled = False
        self.rsi_history = [50.0] * 8
        self.gpu_history = [0.0] * 12
        self.last_bpm = 60.0

    def on_mount(self) -> None:
        self.query_one("#chat-input").focus()
        self.update_agent_controls()
        self.update_game_hud()
        self.update_market_clock_bar()
        self.set_interval(1.0, self.update_game_hud)
        self.set_interval(1.0, self.update_market_clock_bar)
        self.set_interval(900.0, self.refresh_market_snapshot)
        self.refresh_market_snapshot()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="market-clock-bar")
        with Horizontal(id="main-body"):
            with Vertical(id="left-pane"):
                yield Input(value=str(self.current_path), id="path-input")
                with Horizontal(id="nav-buttons"):
                    yield Button("⬆ Up", id="btn-up", variant="warning")
                    yield Button("◀ Back", id="btn-back", variant="default")
                    yield Button("▶ Next", id="btn-forward", variant="default")
                    yield Button("📂 Open", id="btn-open", variant="primary")
                yield DirectoryTree(self.current_path, id="file-tree")
                with Vertical(id="left-hud"):
                    yield Static("", id="pokes-pane", classes="left-card")
                    yield Static("", id="tasker-pane", classes="left-card")
                    yield Static("", id="speedometer-panel", classes="left-card")

            with Vertical(id="right-pane"):
                yield Static(
                    "🤖 Explorer Agent | Model: Auto Cloud/Local | LangGraph | MCP Tools | JSONL Memory",
                    id="agent-header"
                )
                yield Select(
                    configured_free_model_options(),
                    id="model-select",
                    prompt="Select a Free / Local Model..."
                )
                with Horizontal(id="agent-controls"):
                    yield Button("🔎 Deep", id="btn-deepsearch", variant="default")
                    yield Select(
                        [
                            ("💬 Chat", "chat"),
                            ("🐢 Plan", "plan"),
                            ("🔍 Rev", "review"),
                            ("🤖 Agent", "agent"),
                        ],
                        id="mode-select",
                        value="chat",
                        allow_blank=False,
                    )
                    yield Select(
                        [("🌱 Low", "low"), ("🧠 Med", "med"), ("⚡ High", "high")],
                        id="thinking-select",
                        value="med",
                        allow_blank=False,
                    )
                    yield Select(
                        [
                            ("🎨", "cyber-aqua"),
                            ("🍃", "lime-slate"),
                            ("💖", "pika-neon"),
                            ("👹", "red-devil"),
                        ],
                        id="theme-select",
                        value="cyber-aqua",
                        allow_blank=False,
                    )
                    yield Button("🧰 MCP", id="btn-mcp", variant="primary")
                    yield Button("📚 Skills", id="btn-skills", variant="primary")
                    yield Button("⚙️ Core", id="btn-core", variant="primary")
                    yield Button("🧠 Mem", id="btn-memorise", variant="primary")
                    yield Button("✅ Auto", id="btn-auto-approval", variant="default")
                yield AgentLog(id="agent-log", wrap=True, highlight=True, markup=True)
                
                # PIKA POKE COMPANION UI BLOCK
                yield Static("────────────────────────────────────────────────────────────────────────────────────────────────────────", classes="pika-sep")
                yield Static(
                    render_pika_bar(),
                    classes="pika-bar",
                    id="pika-bar"
                )
                yield Static("💡 Tip: Alt+V or right-click input attaches clipboard. Left-drag selects; right-click chat copies log.", classes="pika-sep")
                yield Static("⚡ Commands: /help · /clear · /files · /model · /gpu · /pika · /archon · /tools · /mcp · /skills · /reload", classes="pika-sep")
                yield ChatInput(
                    placeholder="> ",
                    id="chat-input"
                )
        
        # Custom Hacker Footer
        yield Static(
            "[#444444]|[/]  [#ff00ff]●[/] [white]U[/] : UP   [#444444]|[/]  [#ff00ff]●[/] [white]B[/] : BACK   [#444444]|[/]  [#ff00ff]●[/] [white]F[/] : FORWARD   [#444444]|[/]  [#ff00ff]●[/] [white]O[/] : OPEN   [#444444]|[/]  [#ff00ff]●[/] [white]C[/] : COPY CHAT   [#444444]|[/]  [#ff00ff]●[/] [white]Q[/] : QUIT   [#444444]|[/]",
            id="custom-footer"
        )

    def action_handle_esc(self) -> None:
        now = time.time()
        log = self.query_one("#agent-log", AgentLog)
        if now - getattr(self, "_last_esc_time", 0.0) < 1.5:
            if getattr(self, "busy", False):
                self.busy = False
                log.write("\n[bold red]⛔ AGENT OPERATION ABORTED BY ESC x2![/bold red]")
                self.set_agent_status("ABORT")
            else:
                log.write("\n[bold red]⛔ Abort triggered (agent was idle).[/bold red]")
            self._last_esc_time = 0.0
        else:
            self._last_esc_time = now
            if getattr(self, "busy", False):
                log.write("\n[bold yellow]⚡ ESC Slap! Nudging agent... (Press ESC again within 1.5s to abort)[/bold yellow]")
            else:
                log.write("\n[bold yellow]⚡ ESC Slap! Agent is ready. (Press ESC twice to abort)[/bold yellow]")

    def _apply_model_config(self, model_id: str, model_config: dict, agent: QwenAgent) -> None:
        self.agent = agent
        self.agent.memory_enabled = self.memorise_enabled
        self.current_model_id = model_id
        self.current_model_label = f"{model_config['provider']} / {model_config['model_name']}"
        self.set_agent_status("READY")

    def _create_agent_for_model(self, model_id: str) -> tuple[QwenAgent, dict]:
        model_config = resolve_model_config(model_id)
        agent = QwenAgent(
            api_key=model_config["api_key"],
            workspace=str(self.current_path),
            base_url=model_config["base_url"],
            model_name=model_config["model_name"],
        )
        return agent, model_config

    def build_agent_prompt(self, user_prompt: str) -> str:
        directives = []
        if self.thinking_level == "low":
            directives.append("Thinking mode: [x1think] Direct action. Keep reasoning brief and answer immediately.")
        elif self.thinking_level == "med":
            directives.append("Thinking mode: [x2think / /x2thnk] Double deliberation. Write a detailed <thinking> block to analyze step-by-step before answering.")
        elif self.thinking_level == "high":
            directives.append("Thinking mode: [x10think / /x10think] Deep reasoning. Write an exhaustive, multi-step <thinking> block questioning assumptions, conducting at least 10 planning steps, and evaluating multiple counterfactuals before answering.")
        if self.mode == "plan":
            directives.append("You are acting as PIKA TURTLE, the 2nd Tactician Companion. Your responsibility is to prepare a detailed tactical action plan and mind-map the user request into logical bullet points. Suggest highly efficient shortcuts and magic spells to complete the task with minimal resource usage. Focus on high-level strategy and planning. Do NOT execute modification tools yet.")
        elif self.mode == "review":
            directives.append("Review mode: analyze code and report findings, do NOT modify anything.")
        elif self.mode == "agent":
            directives.append("Agent mode: use tools automatically to complete the task end-to-end.")
        else:
            directives.append("Chat mode: answer directly; use tools only if needed.")
        if self.deepsearch_enabled:
            directives.append("DeepSearch is enabled: inspect project structure and search relevant files before answering or editing.")
        if self.auto_approval_enabled:
            directives.append("Auto approval is enabled: proceed with safe read/search/edit tool actions inside the workspace without asking for extra confirmation. Do not run destructive commands.")
        else:
            directives.append("Auto approval is disabled: ask before risky writes or broad changes.")
        if not self.memorise_enabled:
            directives.append("Memory save is disabled for this turn; do not store new long-term notes.")
        return "[NME Control Settings]\n" + "\n".join(f"- {line}" for line in directives) + "\n\n" + user_prompt

    def update_agent_controls(self) -> None:
        try:
            deepsearch_btn = self.query_one("#btn-deepsearch", Button)
            mcp_btn = self.query_one("#btn-mcp", Button)
            skills_btn = self.query_one("#btn-skills", Button)
            core_btn = self.query_one("#btn-core", Button)
            memorise_btn = self.query_one("#btn-memorise", Button)
            autoaccept_btn = self.query_one("#btn-auto-approval", Button)
            deepsearch_btn.variant = "primary" if self.deepsearch_enabled else "default"
            mcp_btn.variant = "primary" if self.include_mcp else "default"
            skills_btn.variant = "primary" if self.include_skills else "default"
            core_btn.variant = "primary" if self.include_core else "default"
            memorise_btn.variant = "primary" if self.memorise_enabled else "default"
            autoaccept_btn.variant = "primary" if self.auto_approval_enabled else "default"
            deepsearch_btn.label = "🔎 Deep"
            mcp_btn.label = "🧰 MCP"
            skills_btn.label = "📚 Skills"
            core_btn.label = "⚙️ Core"
            memorise_btn.label = "🧠 Mem"
            autoaccept_btn.label = "✅ Auto"
        except Exception:
            pass
        if self.agent:
            self.agent.memory_enabled = self.memorise_enabled
        self.update_nav_buttons()
        self.update_game_hud()

    def update_nav_buttons(self) -> None:
        try:
            back_btn = self.query_one("#btn-back", Button)
            forward_btn = self.query_one("#btn-forward", Button)
            back_btn.disabled = self.path_history_index <= 0
            forward_btn.disabled = self.path_history_index >= len(self.path_history) - 1
        except Exception:
            pass

    def navigate_to_path(self, path: Path, *, record_history: bool = True) -> None:
        resolved = path.resolve()
        tree = self.query_one("#file-tree", DirectoryTree)
        tree.path = str(resolved)
        tree.reload()
        self.current_path = resolved
        if self.agent:
            self.agent.workspace = resolved
        self.query_one("#path-input", Input).value = str(resolved)

        if record_history:
            current = self.path_history[self.path_history_index] if self.path_history else None
            if current != resolved:
                self.path_history = self.path_history[: self.path_history_index + 1]
                self.path_history.append(resolved)
                self.path_history_index = len(self.path_history) - 1

        self.update_nav_buttons()

    def _format_price(self, value: float, decimals: int = 0) -> str:
        return f"${value:,.{decimals}f}"

    def _extract_yahoo_price(self, symbol: str):
        quote = symbol.replace("=", "%3D")
        data = self._safe_fetch_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote}?interval=1m&range=1d"
        )
        result = (data.get("chart", {}).get("result") or [{}])[0]
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice")
        if price is None:
            closes = (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
            closes = [value for value in closes if isinstance(value, (int, float))]
            price = closes[-1] if closes else None
        return price

    def _safe_fetch_json(self, url: str):
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 (NME Explorer)"})
        with urlopen(request, timeout=6) as response:
            return json.loads(response.read().decode("utf-8"))

    def _normalise_weather_label(self, raw: str) -> str:
        text = (raw or "").strip().lower()
        if any(token in text for token in ("snow", "sleet", "blizzard", "ice")):
            return "❄ Snowy"
        if any(token in text for token in ("rain", "drizzle", "shower")):
            return "🌧 Rainy"
        if any(token in text for token in ("cloud", "overcast", "mist", "fog")):
            return "☁ Cloudy"
        if any(token in text for token in ("sun", "clear")):
            return "☀ Sunny"
        return f"⛅ {(raw or 'Weather').title()}"

    def update_market_clock_bar(self) -> None:
        try:
            stamp = datetime.now().strftime("%H:%M:%S")
            bar = f"🕒 {stamp}  |  {self.weather_text}  |  ₿ BTC {self.btc_price_text}  |  🥇 Gold {self.gold_price_text}"
            self.query_one("#market-clock-bar", Static).update(bar)
        except Exception:
            pass

    def _apply_market_snapshot(self, weather_text: str, btc_price_text: str, gold_price_text: str) -> None:
        self.weather_text = weather_text
        self.btc_price_text = btc_price_text
        self.gold_price_text = gold_price_text
        self.update_market_clock_bar()

    @work(thread=True, exclusive=True)
    def refresh_market_snapshot(self) -> None:
        location = os.environ.get("NME_WEATHER_LOCATION", "Beirut")
        weather_text = self.weather_text
        btc_price_text = self.btc_price_text
        gold_price_text = self.gold_price_text

        try:
            weather = self._safe_fetch_json(f"https://wttr.in/{location.replace(' ', '+')}?format=j1")
            condition = (weather.get("current_condition") or [{}])[0]
            description = ((condition.get("weatherDesc") or [{}])[0].get("value") or "")
            weather_text = self._normalise_weather_label(description)
        except Exception:
            pass

        try:
            btc_price = self._extract_yahoo_price("BTC-USD")
            if isinstance(btc_price, (int, float)):
                btc_price_text = self._format_price(float(btc_price), 0)
        except Exception:
            pass

        try:
            gold_price = self._extract_yahoo_price("GC=F")
            if isinstance(gold_price, (int, float)):
                gold_price_text = self._format_price(float(gold_price), 2)
        except Exception:
            pass

        self.call_from_thread(self._apply_market_snapshot, weather_text, btc_price_text, gold_price_text)

    def _quest_line(self) -> str:
        tone = {
            "READY": "green",
            "JSONL": "cyan",
            "WATCH": "yellow",
            "EMPTY": "dim",
            "IDLE": "dim",
            "SCOUT": "yellow",
            "THINK": "magenta",
            "DONE": "green",
            "ERR": "red",
        }
        cells = []
        for name, state in self.quest_state.items():
            color = tone.get(state, "white")
            cells.append(f"[{color}]■ {name}:{state}[/]")
        return "  ".join(cells)

    def render_pokes_panel(self) -> str:
        orb_row = " ".join("◉" if (self.tip_index + i) % 2 else "◌" for i in range(5))
        model_short = self.current_model_label.split("/")[-1].strip()[:18]
        art = [
            "   [bright_cyan]  _____  [/bright_cyan]",
            "  [bright_cyan]/  [bold white]o[/bold white]   [bold white]o[/bold white] \\ [/bright_cyan]  [bold cyan]PIKA SQUIRTLE[/bold cyan]",
            " [bright_cyan](   [yellow]___[/yellow]   )[/bright_cyan] [dim]hacker archon[/dim]",
            "  [bright_cyan]\\  [yellow]\\_/[/yellow]  /[/bright_cyan]  [bright_blue]orbs[/bright_blue] " + orb_row,
            " [yellow]/  /[/yellow][cyan]___[/cyan][yellow]\\  \\[/yellow] [green]shell[/green] online",
            "[yellow]/__( [cyan](_) [/cyan] )__\\[/yellow] [yellow]model[/yellow] " + model_short,
        ]
        return "\n".join(art)

    def render_tasker_panel(self) -> str:
        icon = {
            "done": "[green]✓[/]",
            "active": "[yellow]◐[/]",
            "todo": "[dim]□[/]",
            "blocked": "[red]⛔[/]",
        }
        done = sum(1 for task in self.timeline_tasks if task.get("status") == "done")
        total = len(self.timeline_tasks)
        rows = ["[bold cyan]TASKER[/bold cyan] [dim]project timeline[/dim]"]
        for task in self.timeline_tasks[:6]:
            status = task.get("status", "todo")
            note = f" › {task.get('note')}" if task.get("note") else ""
            rows.append(f"{icon.get(status, '□')} {task.get('label')}{note}")
        rows.append(f"[green]+ {done} completed[/]  [yellow]{total - done} open[/]  [dim]status {self.agent_status}[/]")
        return "\n".join(rows)
    def update_game_hud(self) -> None:
        now = time.monotonic()
        if now - self._last_tip_rotate >= 7.0:
            self.tip_index = (self.tip_index + 1) % len(NME_TIPS)
            self.companion_index = (self.companion_index + 1) % len(COMPANION_LINES)
            self._last_tip_rotate = now

        cpu = get_cpu_percent()
        gpu, gpu_source = get_gpu_percent()
        injected_count = len(self.agent_context_files)
        self.quest_state["CTX"] = f"{injected_count} FILE" if injected_count == 1 else (f"{injected_count} FILES" if injected_count else "EMPTY")
        self.quest_state["RUN"] = self.agent_status[:5]

        try:
            speed_panel = self.query_one("#speedometer-panel", Static)
            pokes_panel = self.query_one("#pokes-pane", Static)
            tasker_panel = self.query_one("#tasker-pane", Static)
        except Exception:
            return

        if not hasattr(self, "gpu_history"):
            self.gpu_history = [gpu] * 12
        self.gpu_history.append(gpu)
        if len(self.gpu_history) > 12:
            self.gpu_history.pop(0)

        current_bpm = 58.0 + (cpu / 3.0) + (math.sin(now * 1.8) * 2.2)
        delta = current_bpm - getattr(self, "last_bpm", 60.0)
        self.last_bpm = current_bpm

        if delta > 0.05:
            rsi = 100.0
        elif delta < -0.05:
            rsi = 0.0
        else:
            rsi = 50.0

        if not hasattr(self, "rsi_history"):
            self.rsi_history = [50.0] * 8
        self.rsi_history.append(rsi)
        if len(self.rsi_history) > 8:
            self.rsi_history.pop(0)

        orb_row = "◉ ◌ ◉ ◌ ◉" if int(now) % 2 else "◌ ◉ ◌ ◉ ◌"
        speed_panel.update(
            "[bold cyan]NME ENGINE ROOM[/bold cyan]\n"
            f"{render_cpu_meter('CPU', cpu)}\n"
            f"{render_gpu_meter('GPU', gpu, gpu_source, self.gpu_history)}\n"
            f"{render_heartbeat(current_bpm, int(now), self.rsi_history)}\n"
            f"[magenta]CAPTURE ORBS[/magenta] {orb_row}\n"
            f"[dim]model[/dim] {self.current_model_label}"
        )
        pokes_panel.update(self.render_pokes_panel())
        tasker_panel.update(self.render_tasker_panel())

    def show_resume_board(self) -> None:
        log = self.query_one("#agent-log", AgentLog)
        model_lines = "\n".join(f"- {label}: {value}" for label, value in configured_free_model_options())
        injected = "\n".join(f"- {Path(path).name}: {path}" for path in self.agent_context_files[-8:]) or "- none"
        memory = "No agent memory loaded yet."
        if self.agent:
            memory = self.agent.memory.recall("resume agent models titles briefs NME explorer noted action plan", n_results=5)
        log.write(
            "[bold cyan]🧭 NME RESUME BOARD[/bold cyan]\n"
            f"[bold]Workspace:[/bold] {self.current_path}\n"
            f"[bold]Current model:[/bold] {self.current_model_label}\n"
            f"[bold]Agent status:[/bold] {self.agent_status}\n"
            f"[bold]Quest squares:[/bold] {self._quest_line()}\n\n"
            f"[bold yellow]Models[/bold yellow]\n{model_lines}\n\n"
            f"[bold yellow]Injected context[/bold yellow]\n{injected}\n\n"
            f"[bold yellow]NME noted[/bold yellow]\n{memory}"
        )
        play_sfx("resume")

    def show_blockrun_board(self) -> None:
        log = self.query_one("#agent-log", AgentLog)
        configured = "yes" if any(model_id.startswith("blockrun/") for model_id in configured_free_model_ids()) else "no"
        claw = "online" if _endpoint_available("http://127.0.0.1:8402/v1/models") else "offline"
        message = f"""[bold cyan]BLOCKRUN INTEGRATION[/bold cyan]
[bold]NME direct free route:[/bold] {configured}
[bold]ClawRouter proxy:[/bold] {claw}

[bold yellow]NME built-in[/bold yellow]
- Uses OpenAI-compatible endpoint: https://blockrun.ai/api/v1
- Uses api_key placeholder: blockrun-free
- Free NVIDIA model IDs are already in /models under FREE-NOKEY.

[bold yellow]External integrations[/bold yellow]
- MCP for Claude/MCP clients: claude mcp add blockrun -s user -- npx -y @blockrun/mcp@latest
- ClawRouter local proxy: npx -y @blockrun/clawrouter
- ClawRouter Linux/WSL updater: curl -fsSL https://blockrun.ai/ClawRouter-update | bash
- Franklin standalone agent: npm i -g @blockrun/franklin
- Python SDK: pip install blockrun-llm
- TypeScript SDK: npm install @blockrun/llm

[dim]MCP/SDK/Franklin use wallet/x402 flows. NME direct FREE-NOKEY routes do not need local model storage or provider API keys.[/dim]"""
        log.write(message)
        play_sfx("resume")

    def set_agent_status(self, status: str) -> None:
        self.agent_status = status
        if status == "THINK":
            self.quest_state["PLAN"] = "SCOUT"
        elif status == "READY":
            self.quest_state["PLAN"] = "READY"
        elif status == "ERR":
            self.quest_state["PLAN"] = "ERR"
        self.update_game_hud()
    # --- UI Handlers ---
    def on_button_pressed(self, event: Button.Pressed) -> None:
        log = self.query_one("#agent-log", AgentLog)
        if event.button.id == "btn-up":
            self.action_go_up()
        elif event.button.id == "btn-back":
            self.action_go_back()
        elif event.button.id == "btn-forward":
            self.action_go_forward()
        elif event.button.id == "btn-open":
            self.action_open_file()
        elif event.button.id == "btn-deepsearch":
            self.deepsearch_enabled = not self.deepsearch_enabled
            log.write(f"[cyan]DeepSearch:[/cyan] {'ON' if self.deepsearch_enabled else 'OFF'}")
            self.update_agent_controls()
            play_sfx("tap")
        elif event.button.id == "btn-memorise":
            self.memorise_enabled = not self.memorise_enabled
            log.write(f"[cyan]Memorise:[/cyan] {'ON' if self.memorise_enabled else 'OFF'}")
            self.update_agent_controls()
            play_sfx("tap")
        elif event.button.id == "btn-mcp":
            self.include_mcp = not self.include_mcp
            log.write(f"[cyan]MCP Tools:[/cyan] {'ON' if self.include_mcp else 'OFF'}")
            self.update_agent_controls()
            play_sfx("tap")
        elif event.button.id == "btn-skills":
            self.include_skills = not self.include_skills
            log.write(f"[cyan]Skills:[/cyan] {'ON' if self.include_skills else 'OFF'}")
            self.update_agent_controls()
            play_sfx("tap")
        elif event.button.id == "btn-core":
            self.include_core = not self.include_core
            log.write(f"[cyan]Core Tools:[/cyan] {'ON' if self.include_core else 'OFF'}")
            self.update_agent_controls()
            play_sfx("tap")
        elif event.button.id == "btn-auto-approval":
            self.auto_approval_enabled = not self.auto_approval_enabled
            log.write(f"[cyan]Auto Approval:[/cyan] {'ON' if self.auto_approval_enabled else 'OFF'}")
            self.update_agent_controls()
            play_sfx("tap")

    def action_go_up(self):
        try:
            tree = self.query_one("#file-tree", DirectoryTree)
            current = Path(tree.path).resolve()
            parent = current.parent
            if parent and parent != current:
                self.navigate_to_path(parent)
        except Exception:
            pass

    def action_go_back(self):
        if self.path_history_index > 0:
            self.path_history_index -= 1
            self.navigate_to_path(self.path_history[self.path_history_index], record_history=False)

    def action_go_forward(self):
        if self.path_history_index < len(self.path_history) - 1:
            self.path_history_index += 1
            self.navigate_to_path(self.path_history[self.path_history_index], record_history=False)

    def action_copy_log(self):
        try:
            log = self.query_one("#agent-log", AgentLog)
            text_lines = [line.text for line in log.lines]
            pyperclip.copy("\n".join(text_lines))
            # Just visually notify they copied it
            log.write("[bold green]✅ Chat history copied to clipboard![/bold green]")
            play_sfx("tap")
        except Exception as e:
            pass

    def action_paste_clipboard(self):
        log = self.query_one("#agent-log", AgentLog)
        payload = describe_clipboard_payload()
        if not payload["has_payload"]:
            log.write("[bold yellow]📋 Clipboard has no text, files, or image to attach.[/bold yellow]")
            return
        self.pasted_attachment = payload["prompt_block"]
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.placeholder = "📎 [Clipboard attached] Type your prompt and press Enter..."
        summary = "; ".join(payload["summary"]) or "clipboard payload"
        log.write(f"[bold yellow]📎 Clipboard attached:[/bold yellow] {summary}. Press Enter to send.")
        self.quest_state["TOOLS"] = "READY"
        play_sfx("attach")
        self.update_game_hud()
        chat_input.focus()

    def action_open_file(self):
        tree = self.query_one("#file-tree", DirectoryTree)
        node = tree.cursor_node
        if node and node.data:
            path = node.data.path
            if path.is_dir():
                self.navigate_to_path(path)
            else:
                self.inject_file_to_agent(path)
                self.update_path_display()

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.inject_file_to_agent(event.path)
        event.stop()

    def update_path_display(self):
        try:
            tree = self.query_one("#file-tree", DirectoryTree)
            self.query_one("#path-input", Input).value = str(tree.path)
            self.current_path = Path(tree.path).resolve()
            self.update_nav_buttons()
        except: pass

    def inject_file_to_agent(self, file_path: Path):
        log = self.query_one("#agent-log", AgentLog)
        try:
            content = file_path.read_text(encoding="utf-8")
            if str(file_path) not in self.agent_context_files:
                self.agent_context_files.append(str(file_path))
            log.write(f"[bold green]✅ Injected:[/bold green] {file_path.name} ({len(content)} chars)")
            self.quest_state["CTX"] = "READY"
            play_sfx("attach")
            self.update_game_hud()
            preview = content[:120].replace('\n', ' ')
            log.write(f"[dim]{preview}...[/dim]")
        except Exception as e:
            log.write(f"[bold red]❌ Cannot read:[/bold red] {str(e)}")

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "path-input":
            new_path = Path(event.value.strip()).resolve()
            if new_path.is_dir():
                self.navigate_to_path(new_path)
                log = self.query_one("#agent-log", AgentLog)
                log.write(f"[bold green]📂 Directory changed to:[/bold green] {new_path}")
            else:
                log = self.query_one("#agent-log", AgentLog)
                log.write(f"[bold red]❌ Invalid directory:[/bold red] {new_path}")
                event.input.value = str(self.current_path)
            return

        if event.input.id == "chat-input":
            user_prompt = event.value.strip()
            
            if user_prompt.lower() == "/models":
                event.input.value = ""
                select = self.query_one("#model-select", Select)
                select.styles.display = "block"
                select.focus()
                log = self.query_one("#agent-log", AgentLog)
                log.write("[bold yellow]🔄 Please select a model from the dropdown above.[/bold yellow]")
                play_sfx("model")
                return

            if user_prompt.lower() in ("/resume", "resume"):
                event.input.value = ""
                self.show_resume_board()
                return

            if user_prompt.lower() in ("/blockrun", "blockrun"):
                event.input.value = ""
                self.show_blockrun_board()
                return

            if self.pasted_attachment:
                user_prompt = f"{user_prompt}\n\n[Attached Paste]:\n{self.pasted_attachment}".strip()
                self.pasted_attachment = ""
                event.input.placeholder = "> "

            if not user_prompt:
                return

            log = self.query_one("#agent-log", AgentLog)
            log.write(f"\n[bold blue]👤 You:[/bold blue] {user_prompt}")
            event.input.value = ""

        if not AGENT_AVAILABLE:
            log = self.query_one("#agent-log", AgentLog)
            log.write(f"[bold red]❌ Missing dependency:[/bold red] {IMPORT_ERROR}")
            return

        self.run_agent_worker(self.build_agent_prompt(user_prompt))

    @work(exclusive=True, thread=True)
    def run_agent_worker(self, user_prompt: str) -> None:
        log = self.query_one("#agent-log", AgentLog)

        def write_log(text: str):
            log.write(text)

        self.call_from_thread(self.set_agent_status, "THINK")
        self.call_from_thread(write_log, "[yellow]⠋ NME is thinking with free/local model failover...[/yellow]")

        attempted = set()
        fallback_configs = available_free_model_configs(self.current_model_id)
        last_response = ""

        for model_id, model_config in fallback_configs:
            if model_id in attempted:
                continue
            attempted.add(model_id)
            try:
                if model_id != self.current_model_id or self.agent is None:
                    agent = QwenAgent(
                        api_key=model_config["api_key"],
                        workspace=str(self.current_path),
                        base_url=model_config["base_url"],
                        model_name=model_config["model_name"]
                    )
                    self.agent = agent
                    self.call_from_thread(self._apply_model_config, model_id, model_config, agent)
                    self.call_from_thread(write_log, f"[yellow]↻ Switched to free/local fallback:[/yellow] {model_config['provider']} ({model_config['model_name']})")

                response = self.agent.run(
                    user_input=user_prompt,
                    context_files=self.agent_context_files,
                    include_mcp=self.include_mcp,
                    include_skills=self.include_skills,
                    include_core=self.include_core,
                    deepsearch_enabled=self.deepsearch_enabled
                )
                last_response = response
                if is_retryable_model_error(response):
                    self.call_from_thread(write_log, f"[yellow]↻ Model unavailable/limited, trying next free candidate:[/yellow] {model_id}")
                    continue

                self.call_from_thread(write_log, f"\n[bold magenta]🤖 NME Agent:[/bold magenta]\n{response}")
                self.call_from_thread(self.set_agent_status, "READY")
                self.call_from_thread(play_sfx, "victory")
                return
            except Exception as e:
                message = str(e)
                last_response = message
                if is_retryable_model_error(message):
                    self.call_from_thread(write_log, f"[yellow]↻ Model unavailable/limited, trying next free candidate:[/yellow] {model_id}")
                    continue
                self.call_from_thread(write_log, f"[bold red]❌ Agent Error:[/bold red] {message}")
                self.call_from_thread(self.set_agent_status, "ERR")
                self.call_from_thread(play_sfx, "error")
                return

        self.call_from_thread(write_log, "[bold yellow]All configured free/local models are unavailable right now. NME stayed free-only; add another free key or start Ollama.[/bold yellow]")
        self.call_from_thread(self.set_agent_status, "ERR")
        self.call_from_thread(play_sfx, "error")

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle model, thinking-level, mode, and theme switching from dropdowns."""
        if event.select.id == "mode-select" and event.value:
            self.mode = str(event.value)
            log = self.query_one("#agent-log", AgentLog)
            log.write(f"[cyan]Workflow Mode:[/cyan] {self.mode.upper()}")
            play_sfx("tap")
            return

        if event.select.id == "theme-select" and event.value:
            theme_name = str(event.value)
            for t in ["theme-cyber-aqua", "theme-lime-slate", "theme-pika-neon", "theme-red-devil"]:
                self.screen.remove_class(t)
            if theme_name != "cyber-aqua":
                self.screen.add_class(f"theme-{theme_name}")
            log = self.query_one("#agent-log", AgentLog)
            log.write(f"[cyan]🎨 Theme switched to:[/cyan] {theme_name.replace('-', ' ').title()}")
            play_sfx("tap")
            return

        if event.select.id == "thinking-select" and event.value:
            self.thinking_level = str(event.value)
            log = self.query_one("#agent-log", AgentLog)
            log.write(f"[cyan]Thinking:[/cyan] {self.thinking_level}")
            self.update_agent_controls()
            play_sfx("tap")
            return

        if event.select.id == "model-select" and event.value:
            selected_id = str(event.value)
            event.select.styles.display = "none"
            self.query_one("#chat-input", Input).focus()
            
            log = self.query_one("#agent-log", AgentLog)
            log.write(f"[yellow]⚡ Switching model to: {selected_id}...[/yellow]")
            play_sfx("model")
            
            configs = []
            try:
                configs = [(selected_id, resolve_model_config(selected_id))]
            except ValueError:
                configs = available_free_model_configs("auto/free")
                log.write("[yellow]Selected free model is not configured here; trying the next configured free/local candidate.[/yellow]")

            for model_id, model_config in configs:
                try:
                    agent = QwenAgent(
                        api_key=model_config["api_key"],
                        workspace=str(self.current_path),
                        base_url=model_config["base_url"],
                        model_name=model_config["model_name"]
                    )
                    self._apply_model_config(model_id, model_config, agent)
                    log.write(f"[bold green]✅ Agent switched to {model_config['provider']} ({model_config['model_name']})![/bold green]")
                    log.write(f"[bold green]✅ Memory:[/bold green] {self.agent.memory.status}")
                    self.set_agent_status("READY")
                    play_sfx("victory")
                    break
                except Exception as e:
                    if is_retryable_model_error(str(e)):
                        log.write(f"[yellow]↻ Free model unavailable, trying next:[/yellow] {model_id}")
                        continue
                    log.write(f"[bold red]❌ Failed to switch model:[/bold red] {str(e)}")
                    self.set_agent_status("ERR")
                    play_sfx("error")
                    break
            else:
                log.write("[bold yellow]No configured free/local model is available right now.[/bold yellow]")
                self.set_agent_status("ERR")
                play_sfx("error")

def main():
    app = ExplorerCLI()
    app.run()

if __name__ == "__main__":
    main()



















