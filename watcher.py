import subprocess
import sys
import time
import os
from pathlib import Path

# NewMeta Supervisor v1.0
# Ensures the CLI stays alive and recovers from crashes.

CLI_SCRIPT = Path(__file__).parent / "cli.py"
RESTART_DELAY = 2  # Seconds to wait before restarting
MAX_CRASHES = 5    # Max crashes before giving up in a short window
WINDOW_SIZE = 60   # Seconds for crash window

def run_cli():
    crash_times = []
    
    while True:
        # Launch silently
        
        # Pass all arguments to the child process
        cmd = [sys.executable, str(CLI_SCRIPT)] + sys.argv[1:]
        
        try:
            process = subprocess.Popen(cmd)
            process.wait()
            
            exit_code = process.returncode
            
            if exit_code == 0:
                print("[SUPERVISOR] NewMeta CLI exited cleanly.")
                break
            elif exit_code == 130: # Ctrl+C
                print("\n[SUPERVISOR] Interrupted by user.")
                break
            else:
                print(f"[SUPERVISOR] NewMeta CLI crashed with exit code {exit_code}.")
                
                # Check for crash loop
                now = time.time()
                crash_times = [t for t in crash_times if now - t < WINDOW_SIZE]
                crash_times.append(now)
                
                if len(crash_times) >= MAX_CRASHES:
                    print("[SUPERVISOR] CRITICAL: Too many crashes in a short period. Aborting.")
                    sys.exit(1)
                
                print(f"[SUPERVISOR] Restarting in {RESTART_DELAY}s...")
                time.sleep(RESTART_DELAY)
                
        except KeyboardInterrupt:
            print("\n[SUPERVISOR] Supervisor stopped.")
            break
        except Exception as e:
            print(f"[SUPERVISOR] Unexpected error: {e}")
            break

if __name__ == "__main__":
    if not CLI_SCRIPT.exists():
        print(f"[ERROR] Could not find {CLI_SCRIPT}")
        sys.exit(1)
        
    run_cli()
