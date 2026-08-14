import sys
import json
import requests
import os
import re
from cli import OpenRouterProvider

ARCHON_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Reads the contents of a local file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Absolute or relative path to the file to read."}
                },
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Writes code to a local file, overwriting existing content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Absolute or relative path to the file to write."},
                    "content": {"type": "string", "description": "The exact content to write into the file."}
                },
                "required": ["filepath", "content"]
            }
        }
    }
]

class ArchonInterceptor:
    @staticmethod
    def execute_tool_call(tool_name: str, arguments_json: str) -> str:
        try:
            args = json.loads(arguments_json)
            if tool_name == "read_file":
                filepath = args.get("filepath")
                print(f"[Archon Tool] Reading file: {filepath}")
                if not os.path.exists(filepath): return f"Error: File '{filepath}' does not exist."
                with open(filepath, "r", encoding="utf-8") as f: return f.read()
            elif tool_name == "write_file":
                filepath = args.get("filepath")
                content = args.get("content")
                print(f"[Archon Tool] Writing to file: {filepath}")
                with open(filepath, "w", encoding="utf-8") as f: f.write(content)
                return f"Success: File '{filepath}' written."
            else: return f"Error: Unknown tool '{tool_name}'."
        except Exception as e: return f"Error executing tool {tool_name}: {str(e)}"

def main():
    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Read the file agentic_wrapper.py and give me a summary of it."
    
    provider = OpenRouterProvider({}, {})
    
    if not provider.api_key:
        print("[ERROR] No OpenRouter API key found.")
        return

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "HTTP-Referer": "https://newmeta.ai",
        "Content-Type": "application/json"
    }
    
    messages = [
        {
            "role": "system", 
            "content": "You are an autonomous AI. You MUST use the provided tools to read/write files to solve the user's task. If you use a tool, wait for the observation. When you are completely done with the task, output [TASK COMPLETE]."
        },
        {"role": "user", "content": task}
    ]
    
    print(f"\n[ARCHON] Starting Tool-Calling Loop")
    print(f"[ARCHON] Task: {task}\n")
    
    for i in range(15):
        print(f"[ARCHON] Thinking (Turn {i+1})...")
        data = {
            "model": "deepseek/deepseek-chat",
            "messages": messages,
            "tools": ARCHON_TOOLS_SCHEMA,
            "tool_choice": "auto"
        }
        
        r = requests.post(url, headers=headers, json=data)
        if r.status_code != 200:
            print("[ERROR] API Request Failed:", r.text)
            break
            
        resp_json = r.json()
        message = resp_json["choices"][0]["message"]
        
        content = message.get("content")
        tool_calls = message.get("tool_calls")
        
        # Append assistant message to history so the model knows what it did
        messages.append(message)
        
        if content:
            print(f"\n[DeepSeek]: {content}\n")
            if "[TASK COMPLETE]" in content:
                print(f"[ARCHON] Task marked as completed by AI.")
                break
                
        if tool_calls:
            for tool_call in tool_calls:
                func_name = tool_call["function"]["name"]
                func_args = tool_call["function"]["arguments"]
                
                try:
                    args_dict = json.loads(func_args)
                    formatted_args = ", ".join([f"{k}='{v}'" if len(str(v)) < 50 else f"{k}=[...]" for k, v in args_dict.items()])
                except:
                    formatted_args = func_args
                
                action_text = "Writing file" if func_name == "write_file" else "Reading file" if func_name == "read_file" else "Hacking"
                target = args_dict.get('filepath', '') if 'args_dict' in locals() and isinstance(args_dict, dict) else ''
                
                print(f"🧠 Model is thinking... ⚡ {action_text} ({target})... ", end="", flush=True)
                
                # Execute the tool using the Interceptor we injected into cli.py
                result = ArchonInterceptor.execute_tool_call(func_name, func_args)
                
                status_icon = "🟢 Success" if not str(result).startswith("Error") else "🔴 Failed"
                print(f"{status_icon}")
                
                # Feed the tool observation back to the AI
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": func_name,
                    "content": result
                })
        else:
            if not content:
                print("[ARCHON] Model returned empty response and no tools. Exiting.")
            # If there are no tool calls and it didn't say TASK COMPLETE, it might just be chatting.
            # We break to avoid infinite loops, or ask user.
            break

if __name__ == "__main__":
    main()
