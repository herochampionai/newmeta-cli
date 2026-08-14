import os
import subprocess
import ast
from pathlib import Path

# =====================================================================
# UPGRADE 1: PROMPT CACHING OPTIMIZER
# DeepSeek caches prefixes. We must lock the system prompt and 
# large context at the very top of the message array.
# =====================================================================
class PromptCacheManager:
    def __init__(self, system_prompt: str):
        # The system prompt must remain STATIC across all calls to hit the cache
        self.static_prefix = [{"role": "system", "content": system_prompt}]
        self.history = []

    def build_payload(self, new_prompt: str, repo_context: str = "") -> list:
        """
        Builds the message array. The repo_context is only injected once 
        or kept static to maximize DeepSeek cache hits (costs drop to pennies).
        """
        if repo_context and not any("REPO_CONTEXT" in msg["content"] for msg in self.static_prefix):
            # Inject context into the static prefix so it gets cached globally
            self.static_prefix.append({"role": "system", "content": f"<REPO_CONTEXT>\n{repo_context}\n</REPO_CONTEXT>"})
        
        # Add user query
        self.history.append({"role": "user", "content": new_prompt})
        
        # Return the combined cached prefix + history
        return self.static_prefix + self.history

# =====================================================================
# UPGRADE 2: AUTONOMOUS SELF-CORRECTION LOOP
# Runs generated code. If it crashes, feeds the error back to DeepSeek.
# =====================================================================
class AutonomousLoop:
    def __init__(self, api_provider_callable):
        """api_provider_callable should be a function that takes messages and returns code."""
        self.ask_ai = api_provider_callable

    def execute_and_fix(self, python_code: str, max_retries: int = 3):
        print("\n[Archon] Executing generated code in sandbox...")
        
        for attempt in range(max_retries):
            # Save the code to a temporary sandbox file
            sandbox_file = "sandbox_temp.py"
            with open(sandbox_file, "w", encoding="utf-8") as f:
                f.write(python_code)
            
            # Run the code and capture output/errors
            result = subprocess.run(["python", sandbox_file], capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"[Archon] Flawless Victory! Output:\n{result.stdout}")
                return result.stdout
            else:
                error_msg = result.stderr
                print(f"[Archon] Fed First Blood (Attempt {attempt + 1}/{max_retries}). Raid Wipe:\n{error_msg}")
                print("[Archon] Feeding Raid Wipe back to DeepSeek for automatic correction...")
                
                # Ask AI to fix it
                correction_prompt = f"The code you generated Fed First Blood with this Raid Wipe:\n{error_msg}\n\nPlease output ONLY the corrected Python code."
                # Call the AI (this requires connecting to your provider logic)
                python_code = self.ask_ai(correction_prompt)
                
        print("[Archon] Max retries hit. Manual intervention required.")
        return None

# =====================================================================
# UPGRADE 3: AST SEMANTIC REPO MAPPER
# Maps the repo structure (Classes/Functions) instead of dumping full files.
# =====================================================================
class RepoMapper:
    @staticmethod
    def generate_map(directory: str) -> str:
        """Scans a directory and generates a highly token-efficient map of all Python files."""
        print(f"[Archon] Generating Semantic AST Map for {directory}...")
        repo_map = []
        path = Path(directory)
        
        for py_file in path.rglob("*.py"):
            if "venv" in py_file.parts or "__pycache__" in py_file.parts:
                continue
                
            try:
                with open(py_file, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(py_file))
                
                file_map = [f"\nFile: {py_file.relative_to(path)}"]
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        file_map.append(f"  class {node.name}:")
                    elif isinstance(node, ast.FunctionDef):
                        # Simple extraction of function names
                        file_map.append(f"    def {node.name}(...):")
                
                if len(file_map) > 1:
                    repo_map.extend(file_map)
            except Exception:
                pass # Ignore syntax errors in broken files
                
        final_map = "\n".join(repo_map)
        return final_map

# =====================================================================
# USAGE EXAMPLE
# =====================================================================
if __name__ == "__main__":
    # Test the Repo Mapper
    mapper = RepoMapper()
    repo_structure = mapper.generate_map(os.getcwd())
    print("\n--- REPO MAP PREVIEW ---")
    print(repo_structure[:500] + "\n...[TRUNCATED]")
