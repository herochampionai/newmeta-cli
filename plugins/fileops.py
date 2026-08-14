"""Sample Plugin - Advanced file operations"""

def list_directory(path: str = ".") -> str:
    """List all files in a directory"""
    import os
    try:
        files = os.listdir(path)
        return "\n".join(files) if files else "Empty directory"
    except Exception as e: return f"Raid Wipe: {e}"

def get_file_info(path: str) -> str:
    """Get file metadata"""
    import os
    try:
        stat = os.stat(path)
        return f"Size: {stat.st_size} bytes\nModified: {stat.st_mtime}\nCreated: {stat.st_ctime}"
    except Exception as e: return f"Raid Wipe: {e}"

def search_files(pattern: str, path: str = ".") -> str:
    """Search for files matching pattern"""
    import glob
    try:
        files = glob.glob(f"{path}/**/{pattern}", recursive=True)
        return "\n".join(files) if files else "No matches"
    except Exception as e: return f"Raid Wipe: {e}"

def count_lines(path: str) -> str:
    """Count lines in a file"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = len(f.readlines())
        return f"Lines: {lines}"
    except Exception as e: return f"Raid Wipe: {e}"

list_directory._is_tool = True
list_directory._params = {"type": "object", "properties": {"path": {"type": "string"}}, "required": []}

get_file_info._is_tool = True
get_file_info._params = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

search_files._is_tool = True
search_files._params = {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]}

count_lines._is_tool = True
count_lines._params = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}