#!/usr/bin/env python3
"""
NEWMETA UNIVERSAL AGENTIC WRAPPER v2.0
Supports: Mistral AI, Groq, OpenRouter (Orc)
FULL TOOLS: File R/W, Image Read (Ctrl+V), Command Exec, Web Search, Dir Browse

Usage: universal_agent.py --provider <mistral|groq|orc> [--model <name>] [task...]
"""

import sys, os, io, json, subprocess, urllib.request, base64, tempfile
from pathlib import Path
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except: pass

# ── COLORS ──
M = "\033[38;5;198m"
C = "\033[38;5;87m"
G = "\033[38;5;118m"
Y = "\033[38;5;220m"
D = "\033[38;5;240m"
R = "\033[38;5;196m"
W = "\033[1;97m"
RESET = "\033[0m"

# ── PROVIDER CONFIGS ──
PROVIDERS = {
    "mistral": {
        "name": "Mistral AI",
        "url": "https://api.mistral.ai/v1/chat/completions",
        "model": "mistral-small-latest",
        "env_key": "MISTRAL_API_KEY",
        "vision_model": "pixtral-12b-2409",
    },
    "groq": {
        "name": "Groq",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
        "env_key": "GROQ_API_KEY",
        "vision_model": "llama-3.2-90b-vision-preview",
    },
    "orc": {
        "name": "OpenRouter",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "deepseek/deepseek-chat",
        "env_key": "OPENROUTER_API_KEY",
        "vision_model": "google/gemini-2.0-flash-001",
    },
}

# ── TOOLS DEFINITION ──
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the complete contents of a file. Use to inspect source code, configs, logs, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Full path to the file to read"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or completely overwrite an existing file with new content. Use to save code, configs, reports, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Full path where to write the file"},
                    "content": {"type": "string", "description": "The complete content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and folders in a directory. Use to explore project structure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to list"},
                    "recursive": {"type": "boolean", "description": "Recurse subdirectories? Default false"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell/PowerShell command on the Windows system. Use to run scripts, build, install packages, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute (cmd or powershell)"},
                    "cwd": {"type": "string", "description": "Working directory for the command (optional)"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "paste_image",
            "description": "Read an image from the system clipboard (Ctrl+V). Returns base64-encoded image data for vision analysis.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_image",
            "description": "Read an image from a file path and return base64-encoded data for vision analysis. Supports png, jpg, gif, webp.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Full path to the image file"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using DuckDuckGo and return results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Max results (default 5)"}
                },
                "required": ["query"]
            }
        }
    },
]

SYSTEM_PROMPT = """You are an AUTONOMOUS AGENTIC AI with UNLIMITED system access.

YOU HAVE THESE TOOLS - USE THEM:
- read_file(path)          → Read any file on the system
- write_file(path, content)→ Create or overwrite any file  
- list_directory(path)     → Browse folders recursively
- run_command(command)     → Execute ANY shell/powershell command
- paste_image()            → Read image from clipboard (Ctrl+V)
- read_image(path)         → Read image from file
- web_search(query)        → Search the web

RULES:
1. NEVER simulate - ALWAYS use real tools
2. After each tool call, wait for the REAL result
3. Be thorough - read files before editing them
4. When task is DONE, say exactly: [TASK COMPLETE]
5. You have FULL system access - no restrictions
6. You CAN read images - use paste_image() if user mentions Ctrl+V or clipboard"""

class UniversalAgent:
    def __init__(self, provider: str, model: str = None):
        cfg = PROVIDERS[provider]
        self.provider = provider
        self.name = cfg["name"]
        self.url = cfg["url"]
        self.model = model or cfg["model"]
        self.vision_model = cfg.get("vision_model", self.model)
        self.api_key = self._get_key(cfg["env_key"])
        self.messages = []
        self.tool_results = []
        
    def _get_key(self, env_key: str) -> str:
        key = os.environ.get(env_key, "")
        if not key:
            # Try reading from secrets
            secrets_path = Path(__file__).parent / ".secrets.enc"
            if secrets_path.exists():
                try:
                    sys.path.insert(0, str(Path(__file__).parent))
                    from cli import SecureStorage
                    s = SecureStorage()
                    key = s.get(env_key.lower().replace("_api_key", "")) or ""
                except: pass
        return key

    # ── TOOL EXECUTORS ──
    
    def _read_file(self, path: str) -> str:
        try:
            p = Path(path)
            if not p.exists():
                return f"[ERROR] File not found: {path}"
            if p.stat().st_size > 5_000_000:
                return f"[ERROR] File too large (>5MB): {path}"
            content = p.read_text(encoding="utf-8", errors="replace")
            if len(content) > 8000:
                content = content[:8000] + f"\n... [TRUNCATED: {len(content)} total chars]"
            return content
        except Exception as e:
            return f"[ERROR] {e}"
    
    def _write_file(self, path: str, content: str) -> str:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"[OK] Written {len(content)} chars to {path}"
        except Exception as e:
            return f"[ERROR] {e}"
    
    def _list_directory(self, path: str, recursive: bool = False) -> str:
        try:
            p = Path(path)
            if not p.exists():
                return f"[ERROR] Directory not found: {path}"
            lines = []
            for item in sorted(p.iterdir()):
                prefix = "📁" if item.is_dir() else "📄"
                size = ""
                if item.is_file():
                    try: size = f" ({item.stat().st_size:,} bytes)"
                    except: pass
                lines.append(f"{prefix} {item.name}{size}")
                if recursive and item.is_dir():
                    try:
                        for sub in sorted(item.rglob("*")):
                            sp = "  " + ("📁" if sub.is_dir() else "📄")
                            lines.append(f"{sp} {sub.relative_to(p)}")
                    except: pass
            result = "\n".join(lines[:200])
            if len(lines) > 200:
                result += f"\n... [{len(lines)} total items, showing 200]"
            return result or "[Empty directory]"
        except Exception as e:
            return f"[ERROR] {e}"
    
    def _run_command(self, command: str, cwd: str = None) -> str:
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=120, cwd=cwd or os.getcwd()
            )
            out = (proc.stdout + "\n" + proc.stderr).strip()
            if not out:
                out = "[No output - command executed successfully]"
            if len(out) > 4000:
                out = out[:4000] + f"\n... [TRUNCATED]"
            return out
        except subprocess.TimeoutExpired:
            return "[ERROR] Command timed out (120s)"
        except Exception as e:
            return f"[ERROR] {e}"
    
    def _paste_image(self) -> str:
        """Read image from clipboard using PowerShell"""
        try:
            ps_script = """
            Add-Type -AssemblyName System.Windows.Forms
            Add-Type -AssemblyName System.Drawing
            $img = [System.Windows.Forms.Clipboard]::GetImage()
            if ($img -eq $null) {
                Write-Output "NO_IMAGE"
            } else {
                $tmp = [System.IO.Path]::GetTempFileName() + ".png"
                $img.Save($tmp, [System.Drawing.Imaging.ImageFormat]::Png)
                Write-Output $tmp
            }
            """
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, timeout=15
            )
            tmp_path = proc.stdout.strip()
            if tmp_path == "NO_IMAGE" or not tmp_path:
                return "[INFO] No image found in clipboard. Copy an image first (Ctrl+C), then paste (Ctrl+V)."
            
            b64 = base64.b64encode(Path(tmp_path).read_bytes()).decode()
            Path(tmp_path).unlink(missing_ok=True)
            return f"[IMAGE_DATA]\ndata:image/png;base64,{b64}"
        except Exception as e:
            return f"[ERROR] Clipboard read failed: {e}"
    
    def _read_image(self, path: str) -> str:
        try:
            p = Path(path)
            if not p.exists():
                return f"[ERROR] Image not found: {path}"
            ext = p.suffix.lower()
            mime_map = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif", ".webp": "webp"}
            mime = mime_map.get(ext, "png")
            b64 = base64.b64encode(p.read_bytes()).decode()
            return f"[IMAGE_DATA]\ndata:image/{mime};base64,{b64}"
        except Exception as e:
            return f"[ERROR] {e}"
    
    def _web_search(self, query: str, max_results: int = 5) -> str:
        try:
            from duckduckgo_search import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append(f"- {r['title']}\n  {r['href']}\n  {r['body'][:200]}")
            return "\n\n".join(results) or "[No results]"
        except ImportError:
            return "[INFO] duckduckgo-search not installed. pip install duckduckgo-search"
        except Exception as e:
            return f"[ERROR] Search failed: {e}"
    
    def execute_tool(self, name: str, args: dict) -> str:
        tools = {
            "read_file": lambda: self._read_file(args.get("path", "")),
            "write_file": lambda: self._write_file(args.get("path", ""), args.get("content", "")),
            "list_directory": lambda: self._list_directory(args.get("path", ""), args.get("recursive", False)),
            "run_command": lambda: self._run_command(args.get("command", ""), args.get("cwd")),
            "paste_image": lambda: self._paste_image(),
            "read_image": lambda: self._read_image(args.get("path", "")),
            "web_search": lambda: self._web_search(args.get("query", ""), args.get("max_results", 5)),
        }
        fn = tools.get(name)
        if fn:
            return fn()
        return f"[ERROR] Unknown tool: {name}"
    
    # ── API CALL ──
    
    def chat(self, messages, tools=None):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.provider == "orc":
            headers["HTTP-Referer"] = "https://newmeta.local"
            headers["X-Title"] = "NewMeta"
        
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.url, data=data, headers=headers)
        
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read())
    
    def run(self, task: str):
        if not self.api_key:
            env_key = PROVIDERS[self.provider]["env_key"]
            print(f"{R}[ERROR] {env_key} not set!{RESET}")
            print(f"{D}Set via: set {env_key}=your_key{RESET}")
            return False
        
        print(f"\n{M}{'='*60}{RESET}")
        print(f"{C}UNIVERSAL AGENTIC SHELL{RESET} | {W}{self.name}{RESET} | Model: {G}{self.model}{RESET}")
        print(f"{M}{'='*60}{RESET}\n")
        print(f"{Y}Task:{RESET} {task}\n")
        
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task}
        ]
        
        for iteration in range(30):
            print(f"{D}[Iter {iteration+1}] Thinking...{RESET}", end="\r")
            
            try:
                resp = self.chat(self.messages, TOOLS)
            except urllib.error.HTTPError as e:
                print(f"\033[K{R}[API ERROR]{RESET} HTTP {e.code}: {e.reason}")
                return False
            except Exception as e:
                print(f"\033[K{R}[CONNECTION ERROR]{RESET} {e}")
                return False
            
            print("\033[K", end="")
            choice = resp["choices"][0]
            msg = choice["message"]
            
            # Handle tool calls
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn = tc["function"]
                    tool_name = fn["name"]
                    try:
                        tool_args = json.loads(fn["arguments"])
                    except:
                        tool_args = {}
                    
                    print(f"{M}>>> {tool_name}{RESET}", end="")
                    if tool_args:
                        args_short = str(tool_args)[:80]
                        print(f"({args_short})")
                    else:
                        print()
                    
                    result = self.execute_tool(tool_name, tool_args)
                    result_short = result[:600]
                    print(f"{D}--- Result ---{RESET}\n{result_short}{'...' if len(result)>600 else ''}\n")
                    
                    self.messages.append(msg)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result
                    })
            
            # Handle text response
            elif msg.get("content"):
                content = msg["content"]
                print(f"{C}Agent:{RESET}\n{content}\n")
                self.messages.append({"role": "assistant", "content": content})
                
                if "[TASK COMPLETE]" in content:
                    print(f"{G}[SUCCESS] Task completed in {iteration+1} iterations.{RESET}")
                    return True
            else:
                self.messages.append({"role": "assistant", "content": "[No response]"})
        
        print(f"{Y}[MAX ITERATIONS] Agent reached limit.{RESET}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Universal Agentic Wrapper v2")
    parser.add_argument("--provider", "-p", required=True, choices=["mistral", "groq", "orc"],
                       help="Provider: mistral, groq, orc")
    parser.add_argument("--model", "-m", help="Override default model")
    parser.add_argument("task", nargs="*", help="Task for the agent")
    args = parser.parse_args()
    
    agent = UniversalAgent(args.provider, args.model)
    task = " ".join(args.task) if args.task else None
    
    if task:
        agent.run(task)
    else:
        print(f"{M}UNIVERSAL AGENTIC SHELL v2{RESET}")
        print(f"{W}Provider: {agent.name} | Model: {agent.model}{RESET}")
        print(f"{D}Tools: read_file | write_file | list_directory | run_command | paste_image | read_image | web_search{RESET}")
        while True:
            try:
                t = input(f"\n{C}>> {RESET}").strip()
                if t.lower() in ["exit", "quit", ""]:
                    break
                agent.run(t)
            except KeyboardInterrupt:
                print(f"\n{M}[Shell Exited]{RESET}")
                break


if __name__ == "__main__":
    main()
