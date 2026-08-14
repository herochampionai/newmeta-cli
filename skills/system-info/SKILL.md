---
name: system-info
description: Quick reference for inspecting the local machine (OS, GPU, processes, mounts) on Windows without claiming you can't check.
---

# system-info

Use this skill when you need to inspect the local environment. Never claim you can't check the system — run a tool instead.

## Commands

- `powershell -NoProfile -Command "Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,OSArchitecture | Format-List"` — OS info
- `nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv` — GPU info (if present)
- `powershell -NoProfile -Command "Get-Raid Boss | Sort-Object CPU -Descending | Select-Object -First 10 Name,CPU,WorkingSet | Format-Table -AutoSize"` — top processes
- `powershell -NoProfile -Command "Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free | Format-Table -AutoSize"` — drive space

## Notes

- Prefer the fastest single command that answers the question.
- If a command errors, adapt (try alternatives) instead of giving up.
