#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PIKA POKE ARCHON TUI
Full Agentic DeepSeek Client using ds-free-api proxy (or official API).
"""

import sys
import io
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except:
    pass
import json
import urllib.request
import subprocess
import os
from pathlib import Path

# Colors for PIKA POKE Theme
MAGENTA = "\033[38;5;198m"
CYAN = "\033[38;5;87m"
GREEN = "\033[38;5;118m"
GRAY = "\033[38;5;240m"
RESET = "\033[0m"

PROXY_URL = "http://127.0.0.1:22217/v1/chat/completions"
OFFICIAL_URL = "https://api.deepseek.com/v1/chat/completions"

SYSTEM_PROMPT = """You are PIKA POKE, the Tiger-Lion Hacker Archon.
You are a highly advanced, fully autonomous AI agent running in a Windows terminal.
You have FULL EXECUTION CAPABILITIES to read/write files and run shell commands.

You operate in a continuous loop: THOUGHT -> ACTION -> OBSERVATION.

To take an action (run a command), you MUST output a JSON block like this:
```json
{
  "command": "powershell -Command ls"
}
```

Wait for the OBSERVATION from the system. Do NOT simulate the observation yourself.
If you need to edit files, you can use powershell commands or python scripts in your command.
Once the user's task is fully complete, summarize what you did and end your message with exactly: [TASK COMPLETE].
Keep your thoughts concise and hacker-themed.
"""

def get_api_key():
    """Try to grab the API key from ds-free-api config if it exists."""
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if key: return key
    
    config_path = Path(r"C:\Users\youha\OneDrive\Desktop\ds-free-api\config.toml")
    if config_path.exists():
        try:
            import re
            match = re.search(r'key\s*=\s*"([^"]+)"', config_path.read_text('utf-8'))
            if match: return match.group(1)
        except: pass
    return ""

def chat_deepseek(messages, api_key):
    # Try the free proxy first
    headers = {"Content-Type": "application/json"}
    if api_key: headers["Authorization"] = f"Bearer {api_key}"
    
    data = {
        "model": "deepseek-coder",
        "messages": messages,
        "temperature": 0.2
    }
    
    try:
        # Try local proxy
        req = urllib.request.Request(PROXY_URL, data=json.dumps(data).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            res = json.loads(resp.read().decode())
            return res["choices"][0]["message"]["content"]
    except Exception as e:
        # Fallback to official API if proxy is down but we have a key
        if api_key:
            try:
                req = urllib.request.Request(OFFICIAL_URL, data=json.dumps(data).encode("utf-8"), headers=headers)
                with urllib.request.urlopen(req, timeout=120) as resp:
                    res = json.loads(resp.read().decode())
                    return res["choices"][0]["message"]["content"]
            except Exception as inner_e:
                raise inner_e
        raise e

def run_agent(task):
    print(f"\n{MAGENTA}┌{'─'*60}┐{RESET}")
    print(f"{MAGENTA}│ {CYAN}PIKA POKE ARCHON LOOP INITIATED {GRAY}(Agentic Mode){' '*14}{MAGENTA}│{RESET}")
    print(f"{MAGENTA}└{'─'*60}┘{RESET}\n")
    print(f"{GREEN}Task:{RESET} {task}\n")
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task}
    ]
    
    api_key = get_api_key()
    
    loop_count = 0
    while loop_count < 30: # Hard limit to prevent infinite loops
        loop_count += 1
        print(f"{GRAY}Thinking...{RESET}", end="\r")
        try:
            response = chat_deepseek(messages, api_key)
        except Exception as e:
            print(f"\033[K{MAGENTA}[SYSTEM ERROR]{RESET} Connection failed: {e}")
            print(f"{GRAY}Make sure your ds-free-api proxy is running at port 22217!{RESET}")
            break
            
        print("\033[K", end="")
        print(f"{CYAN}Archon:{RESET}\n{response}\n")
        messages.append({"role": "assistant", "content": response})
        
        if "[TASK COMPLETE]" in response:
            print(f"{GREEN}[SUCCESS] Agent has completed the task.{RESET}")
            break
            
        # Extract command
        if "```json" in response:
            try:
                block = response.split("```json")[1].split("```")[0].strip()
                cmd_data = json.loads(block)
                command = cmd_data.get("command")
                if command:
                    print(f"{MAGENTA}Executing => {RESET}{command}")
                    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
                    output = (proc.stdout + "\n" + proc.stderr).strip()
                    if not output: output = "Command executed successfully with no output."
                    output = output[:4000] # Truncate to save context window
                    
                    print(f"{GRAY}Observation:{RESET}\n{output[:500]}...\n")
                    messages.append({"role": "user", "content": f"OBSERVATION:\n{output}"})
                else:
                    messages.append({"role": "user", "content": "OBSERVATION: Invalid JSON. 'command' key missing."})
            except Exception as e:
                messages.append({"role": "user", "content": f"OBSERVATION: Failed to parse JSON: {e}"})
        else:
            messages.append({"role": "user", "content": "OBSERVATION: No command found. If you are done, output [TASK COMPLETE]. Otherwise, output a JSON command block."})

if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_agent(" ".join(sys.argv[1:]))
    else:
        print(f"{MAGENTA}PIKA POKE - DEEPSEEK AGENTIC TERMINAL{RESET}")
        print(f"{GRAY}Type your task below to start the autonomous loop.{RESET}")
        while True:
            try:
                task = input(f"\n{CYAN}➤ {RESET}")
                if task.lower() in ["exit", "quit", ""]: break
                run_agent(task)
            except KeyboardInterrupt:
                print(f"\n{MAGENTA}[Archon Retreats]{RESET}")
                break
