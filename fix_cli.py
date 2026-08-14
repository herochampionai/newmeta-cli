import os

try:
    with open('cli.py', 'r', encoding='utf-8') as f:
        text = f.read()

    bad_block = """    @staticmethod
    def execute_tool_call(tool_name: str, arguments_json: str) -> str:
        try:
            args = json.loads(arguments_json)
            if tool_name == "read_file":
                filepath = args.get("filepath")
                if not os.path.exists(filepath): return f"Error: File '{filepath}' does not exist."
                with open(filepath, "r", encoding="utf-8") as f: return f.read()
            elif tool_name == "write_file":
                filepath = args.get("filepath")
                content = args.get("content")
                with open(filepath, "w", encoding="utf-8") as f: f.write(content)
                return f"Success: File '{filepath}' written."
            else: return f"Error: Unknown tool '{tool_name}'."
        except Exception as e: return f"Error executing tool {tool_name}: {str(e)}" """
        
    if bad_block in text:
        text = text.replace(bad_block, "")
        print("Fixed syntax error block.")

    old_read_print = 'print(f"[Archon Tool] Reading file: {filepath}")'
    old_write_print = 'print(f"[Archon Tool] Writing to file: {filepath}")'
    
    text = text.replace(old_read_print, '')
    text = text.replace(old_write_print, '')

    with open('cli.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print("CLI fixed successfully.")
except Exception as e:
    print(f"Error: {e}")
