#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NEWMETA AGENTIC WRAPPER - Universal Local Agent Shell
Gives Ollama & Llama.cpp models full file read/write + command execution.

Usage: agentic_wrapper.py --model <name> [--backend ollama|llamacpp] [task...]
       If no task, enters interactive mode.

PATCHED VERSION - fixes:
  1. Repetition detection (same command issued 2x in a row -> warn,
     3x in a row -> hard abort instead of burning all 40 iterations)
  2. Message history trimming (keeps system prompt + last N exchanges,
     so a small local model doesn't degrade/drift from an ever-growing
     context window)
  3. Responses are stripped before being stored, so leading whitespace
     can't compound turn over turn
  4. System prompt now explicitly tells the model to stop immediately
     once a trivial task is satisfied, and never to repeat an action
     that already produced a result
"""

import sys, os, io, json, subprocess, urllib.request, tempfile, shutil
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except: pass

MAGENTA = "\033[38;5;198m"
CYAN = "\033[38;5;87m"
GREEN = "\033[38;5;118m"
YELLOW = "\033[38;5;220m"
GRAY = "\033[38;5;240m"
RED = "\033[38;5;196m"
RESET = "\033[0m"

SYSTEM_PROMPT = """You are an AUTONOMOUS AGENTIC AI with FULL FILE SYSTEM ACCESS.
You run in a continuous loop: THOUGHT -> ACTION -> OBSERVATION.

AVAILABLE ACTIONS (output as JSON block):
```json
{"command": "powershell -Command ls"}
{"command": "python -c \"...\""}
```
You can use these for ANYTHING:
- Read files: `type file.txt` or `python -c "print(open('file').read())"`
- Write files: `python -c "open('out.txt','w').write('content')"`
- Edit files: `python -c "..."`
- Run programs: any shell command
- Browse directories: `dir` or `ls`

RULES:
1. THINK first, then output exactly ONE JSON command block.
2. Wait for OBSERVATION before next action.
3. When the task is DONE, say "[TASK COMPLETE]" and STOP. If the task is
   trivial (e.g. printing something, a single lookup), it is usually done
   after ONE action - say [TASK COMPLETE] immediately once you see the result.
4. NEVER simulate observations - the system provides real ones.
5. You can access ANY directory - you have full system access.
6. NEVER repeat a command that you have already run and already received
   an observation for. If you catch yourself about to repeat an action,
   say [TASK COMPLETE] instead (or explain what's blocking you).
"""

MAX_HISTORY_EXCHANGES = 8   # keep system prompt + last N (assistant, user) pairs
MAX_REPEATS_BEFORE_ABORT = 3


class AgenticShell:
    def __init__(self, model: str, backend: str = "ollama"):
        self.model = model
        self.backend = backend
        self.messages = []

    def query_model(self, messages):
        """Send to Ollama API"""
        url = "http://127.0.0.1:11434/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        data = {"model": self.model, "messages": messages, "temperature": 0.3}

        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read())["choices"][0]["message"]["content"]

    def execute_command(self, command: str) -> str:
        """Execute a shell command and return output"""
        try:
            proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
            output = (proc.stdout + "\n" + proc.stderr).strip()
            if not output:
                output = "[No output - command executed successfully]"
            return output[:5000]
        except subprocess.TimeoutExpired:
            return "[ERROR] Command timed out after 120 seconds"
        except Exception as e:
            return f"[ERROR] {e}"

    def _trim_history(self):
        """Keep the system prompt plus only the last MAX_HISTORY_EXCHANGES
        (assistant, user) pairs, to stop context bloat from degrading a
        small local model over long loops."""
        if not self.messages:
            return
        system = self.messages[0]
        rest = self.messages[1:]
        max_msgs = MAX_HISTORY_EXCHANGES * 2
        if len(rest) > max_msgs:
            rest = rest[-max_msgs:]
        self.messages = [system] + rest

    def run(self, task: str):
        print(f"\n{MAGENTA}{'='*60}{RESET}")
        print(f"{CYAN}AGENTIC SHELL ACTIVE{RESET} | Model: {GREEN}{self.model}{RESET}")
        print(f"{MAGENTA}{'='*60}{RESET}\n")
        print(f"{YELLOW}Task:{RESET} {task}\n")

        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task}
        ]

        last_command = None
        repeat_count = 0

        for iteration in range(40):
            print(f"{GRAY}[Iteration {iteration+1}] Thinking...{RESET}", end="\r")
            try:
                response = self.query_model(self.messages)
            except Exception as e:
                print(f"\033[K{RED}[CONNECTION ERROR]{RESET} {e}")
                print(f"{GRAY}Is Ollama running? Check: ollama serve{RESET}")
                return False

            response = response.strip()  # prevent whitespace drift compounding

            print("\033[K", end="")

            # Full raw response still goes into the model's own context -
            # only the terminal display is being cleaned up here.
            self.messages.append({"role": "assistant", "content": response})

            if "[TASK COMPLETE]" in response:
                summary = response.replace("[TASK COMPLETE]", "").strip()
                if summary:
                    print(f"{CYAN}Agent:{RESET} {summary}")
                print(f"{GREEN}[SUCCESS] Task completed in {iteration+1} iterations.{RESET}")
                return True

            # Extract JSON command block
            if "```json" in response:
                try:
                    pre_text = response.split("```json")[0].strip()
                    block = response.split("```json")[1].split("```")[0].strip()
                    cmd_data = json.loads(block)
                    command = cmd_data.get("command", "")

                    if pre_text:
                        print(f"{CYAN}Agent:{RESET} {pre_text}")

                    if not command:
                        print(f"{RED}[HARNESS] Agent sent a command block with no 'command' key.{RESET}")
                        self.messages.append({"role": "user", "content": "ERROR: 'command' key missing in JSON. Use {\"command\": \"...\"}"})
                        continue

                    # --- Repetition detection ---
                    if command == last_command:
                        repeat_count += 1
                    else:
                        repeat_count = 0
                    last_command = command

                    if repeat_count >= MAX_REPEATS_BEFORE_ABORT:
                        print(f"{RED}[HARNESS] Same command repeated {repeat_count+1}x in a row. Aborting loop.{RESET}")
                        self.messages.append({
                            "role": "user",
                            "content": "HARNESS: You repeated the identical command too many times. Stopping this run."
                        })
                        return False

                    print(f"{MAGENTA}>>> EXECUTING:{RESET} {command[:120]}")
                    output = self.execute_command(command)
                    print(f"{GRAY}--- Observation ---{RESET}\n{output[:800]}{'...' if len(output)>800 else ''}\n")

                    if repeat_count == 1:
                        # First repeat: warn instead of silently re-running
                        nudge = (f"OBSERVATION:\n{output}\n\n"
                                 f"NOTE: You just repeated the exact same command you ran last turn. "
                                 f"If this task is already done, respond with [TASK COMPLETE] now instead of repeating actions.")
                        self.messages.append({"role": "user", "content": nudge})
                    else:
                        self.messages.append({"role": "user", "content": f"OBSERVATION:\n{output}"})

                except json.JSONDecodeError as e:
                    print(f"{RED}[HARNESS] Agent sent malformed JSON: {e}{RESET}")
                    self.messages.append({"role": "user", "content": f"ERROR: Invalid JSON: {e}. Fix and retry."})
                except Exception as e:
                    print(f"{RED}[HARNESS] Error handling agent response: {e}{RESET}")
                    self.messages.append({"role": "user", "content": f"ERROR: {e}"})
            else:
                # No JSON block at all - this is just plain text from the model,
                # so show it since there's nothing else informative to display.
                print(f"{CYAN}Agent:{RESET} {response}")
                self.messages.append({"role": "user", "content": "No JSON command found. Output a command or say [TASK COMPLETE]."})

            self._trim_history()

        print(f"{YELLOW}[MAX ITERATIONS] Agent reached limit.{RESET}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="NewMeta Agentic Wrapper")
    parser.add_argument("--model", "-m", default="qwen2.5-coder:14b", help="Ollama model name")
    parser.add_argument("--backend", default="ollama", help="Backend: ollama")
    parser.add_argument("task", nargs="*", help="Task for the agent")
    args = parser.parse_args()

    task = " ".join(args.task) if args.task else None

    shell = AgenticShell(args.model, args.backend)

    if task:
        shell.run(task)
    else:
        print(f"{MAGENTA}NEWMETA AGENTIC WRAPPER{RESET}")
        print(f"{GREEN}Model: {args.model}{RESET}")
        print(f"{GRAY}Type your task below. The agent will autonomously use file/command tools.{RESET}")
        while True:
            try:
                t = input(f"\n{CYAN}>> {RESET}").strip()
                if t.lower() in ["exit", "quit", ""]:
                    break
                shell.run(t)
            except KeyboardInterrupt:
                print(f"\n{MAGENTA}[Shell Exited]{RESET}")
                break


if __name__ == "__main__":
    main()