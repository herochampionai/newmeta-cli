---
name: bot-launcher
description: Launch any of the 15 quant/trading bots in D:\DAI_DEV\quants, view their launch cards, and drill the arsenal with a quiz. Commands: python spell_launch_bot.py (menu) or run the skill body steps directly.
---

# bot-launcher

Use this skill when the user wants to launch a quant/trading bot, check how a bot in the arsenal is launched, or drill the bot arsenal (quiz). The arsenal lives in `D:\DAI_DEV\quants` and its knowledge cards live in `~/.pika_poke/knowledge/bots/`.

## Arsenal (15 entries)

| # | Bot | Launch |
|---|-----|--------|
| 1 | freqtrade (framework) | `docker compose up -d` |
| 2 | freqtrade (2nd copy) | `docker compose up -d` |
| 3 | freqtrade-strategies | library only (no launcher) |
| 4 | freqtrade-vps | helper only (no launcher) |
| 5 | freqtrade-vps-deploy | `docker compose up -d` |
| 6 | hummingbot | `docker compose up -d` then `docker attach hummingbot` |
| 7 | mt5-mcp | `uvx --from mcp-metatrader5-server mt5mcp` |
| 8 | Superalgos | `node platform` (or `node platform minMemo`) |
| 9 | crypto-quant-signal-mcp | `docker compose up -d` |
| 10 | M2Quant | python entrypoints (see card) |
| 11 | llm-quant | python entrypoints (see card) |
| 12 | Claude-Quant | python under `src/` (see card) |
| 13 | cbt-framework | python entrypoints (see card) |
| 14 | trading-skills | signal feed (no launcher) |
| 15 | claude-trading-skills | skills library (no launcher) |

## Workflow

1. Interactive menu (recommended): run `python "C:\Users\youha\Desktop\Codes\pika poke\NewMeta\spell_launch_bot.py"`. Commands inside: `/launch <key>` · `/card <key>` · `/quiz` · `/knowledge` · `/quit`.
2. Direct launch: cd into `D:\DAI_DEV\quants\<slug>` and run the launch command for that entry.
3. Drill: ask the user "which bot / which command?" — quiz bank lives in the spell script.

## Notes

- Bots marked "no launcher" are libraries, strategy packs, signal feeds, or skill libraries — never try to `docker compose up` those.
- hummingbot's CLI is reached via `docker attach hummingbot` after compose up.
- mt5-mcp needs no install: `uvx --from mcp-metatrader5-server mt5mcp`.
- If a bot dir is missing, check the slug spelling; the spell script reports `Exists: False`.
