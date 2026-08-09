# NEWMETA OPENCODE ENGINE

Opendcode-style features added to `cli.py` by opencode. Everything lives in one
self-contained block inserted just above `run_tools`, plus a handful of wired
hooks. No public API of the existing app was changed.

## 1. Undo / Redo

- Before a mutating tool (`write_file`, `run_python`, `run_javascript`,
  `execute_command`) runs, `_undo_snapshot` saves a copy of the target file to
  `%TEMP%/newmeta_undo` (or the pre-mutation "file was NEW" marker).
- Commands:
  - `/undo`    — restore the last mutation (also keeps the change on the redo stack)
  - `/redo`    — re-apply the last undone change
  - `/undolist`— show the most recent 5 undo entries
- Stack is capped at 50 entries; oldest snapshots are garbage-collected.

## 2. Plan / Build mode

- `/plan`  — read-only. `execute_command`, `run_python`, `run_javascript` are
  denied at the `run_tools` chokepoint and removed from the tool schema sent to
  the model (`get_tools_schema`).
- `/build` — back to full execution.
- Default can be forced at startup via `config.yaml` → `plan_mode: true`.
- Custom commands are also blocked in plan mode unless `allow_plan: true`.
- The agent system prompt now contains RULE 7 telling the model to respect the
  guard and never try to dodge a denied tool.

## 3. Permissions (allow / ask / deny)

- Persisted in `permissions.json` next to `cli.py`.
- Default policy for mutating tools is **ask** (inline prompt:
  `[allow] | once | deny | always`).
- Precedence: `deny` > `allow` > `ask`.
- Rules support globs and tool aliases (`bash`, `shell`, `python`, `write`,
  `read`, ...) plus `provider:<name>` rules. `*` matches everything.
- Commands:
  - `/perms`             — show current policy
  - `/perms allow <tool>` — add a tool to the allow list
  - `/perms ask <tool>`   — always prompt for a tool
  - `/perms deny <tool>`  — block a tool
- Mutating tools (`_MUTATING_TOOLS`) always take an undo snapshot first, so a
  denied action still leaves the pre-mutation state recoverable.

## 4. Data-driven custom commands

- Folder: `commands/` next to `cli.py`.
- Two formats:
  - Markdown: `---frontmatter---` (name/description/allow_plan) + body.
  - JSON: `{ "name", "description", "allow_plan", "body" }`.
- Placeholders in the body:
  - `{{ args }}`          → the raw argument tail passed after the command name
  - `{{ description }}`   → the command's own description
- Commands:
  - `/cmds`          — list all custom commands
  - `/cmds <name> [args]` — run one (expands and echoes the body)
- `/commands` help now also prints the custom-command list.

## Wiring / integration points in `cli.py`

| Feature | Hook |
|---|---|
| Module imports | `re`, `fnmatch` added to the top import block |
| Engine block | inserted immediately above `def run_tools(...)` |
| Tool gating | `run_tools` (plan-mode deny, undo snapshot, permission check) |
| Schema filtering | `get_tools_schema` skips plan-mode-denied tools |
| Startup init | `interactive_chat` loads permissions + commands, applies `plan_mode` |
| Slash commands | `/plan /build /undo /redo /undolist /perms /permissions /cmds` |
| System prompt | `AGENTIC_PROMPT` RULE 7 added |
| `config.yaml` | new `commands_dir` / `plan_mode` / `permissions` settings |

## Files

- `cli.py` — the engine + hooks
- `config.yaml` — engine settings
- `permissions.json` — persistent allow/ask/deny policy
- `commands/deploy.md`, `commands/banner.json` — sample data-driven commands
- `docs/OPENCODE_ENGINE.md` — this file
