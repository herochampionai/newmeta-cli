#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PIKA POKE ARCHON LOCAL
Agentic Loop for Ollama models
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

MAGENTA = "\033[38;5;198m"
CYAN = "\033[38;5;87m"
GREEN = "\033[38;5;118m"
GRAY = "\033[38;5;240m"
RESET = "\033[0m"

OLLAMA_URL = "http://127.0.0.1:11434/v1/chat/completions"

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

def chat_local(messages, model):
    headers = {"Content-Type": "application/json"}
    data = {
        "model": model,
        "messages": messages,
        "temperature": 0.2
    }
    
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(data).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        res = json.loads(resp.read().decode())
        return res["choices"][0]["message"]["content"]

def run_agent(task, model):
    print(f"\n{MAGENTA}┌{'─'*60}┐{RESET}")
    print(f"{MAGENTA}│ {CYAN}PIKA POKE ARCHON LOOP INITIATED {GRAY}(Local {model}){' '*8}{MAGENTA}│{RESET}")
    print(f"{MAGENTA}└{'─'*60}┘{RESET}\n")
    print(f"{GREEN}Task:{RESET} {task}\n")
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task}
    ]
    
    loop_count = 0
    while loop_count < 30:
        loop_count += 1
        print(f"{GRAY}Thinking...{RESET}", end="\r")
        try:
            response = chat_local(messages, model)
        except Exception as e:
            print(f"\033[K{MAGENTA}[SYSTEM ERROR]{RESET} Connection failed: {e}")
            print(f"{GRAY}Make sure Ollama is running locally!{RESET}")
            break
            
        print("\033[K", end="")
        print(f"{CYAN}Archon ({model}):{RESET}\n{response}\n")
        messages.append({"role": "assistant", "content": response})
        
        if "[TASK COMPLETE]" in response:
            print(f"{GREEN}[SUCCESS] Agent has completed the task.{RESET}")
            break
            
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
                    output = output[:4000]
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
        model = sys.argv[1]
    else:
        model = "phi4:latest"
        
    if len(sys.argv) > 2:
        run_agent(" ".join(sys.argv[2:]), model)
    else:
        print(f"{MAGENTA}PIKA POKE - LOCAL AGENTIC TERMINAL ({model}){RESET}")
        print(f"{GRAY}Type your task below to start the autonomous loop.{RESET}")
        while True:
            try:
                task = input(f"\n{CYAN}➤ {RESET}")
                if task.lower() in ["exit", "quit", ""]: break
                if task.strip(): run_agent(task, model)
            except KeyboardInterrupt:
                print(f"\n{MAGENTA}[Archon Retreats]{RESET}")
                break
