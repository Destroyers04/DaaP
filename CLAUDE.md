# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

DaaP is a Discord bot for a work Discord server, built on discord.py 2.x. It uses only slash commands and buttons. There are no prefix commands, and no message-content or member intents.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set DISCORD_TOKEN and DISCORD_GUILD_ID
python -m bot                 # run the bot
```

- Requires Python 3.10+ (it uses `X | None` and `set[int]` syntax). `.python-version` pins 3.14. The macOS system `python3` (3.9) will not work.
- The repo has no test suite, linter or formatter. To verify a change, run the bot against your own test bot and test server, not the real server.
- Dependencies are pinned exactly in `requirements.txt`.

## Architecture

- **Entry point** `bot/__main__.py`: the `Bot` subclass. Its `setup_hook` auto-loads **every module in `bot/cogs/`** as an extension through `pkgutil.iter_modules`. Every file in that folder must therefore define `async def setup(bot)`. Shared helper code must not live in `bot/cogs/`; put it elsewhere in `bot/`.
- **Command registration is guild-scoped.** The bot copies global commands to the single guild in `DISCORD_GUILD_ID` and syncs them on every startup, so new commands show up right after a restart. Global commands are never synced.
- **Error handling:** `tree.on_error` is set to `on_command_error`, which logs the error and sends an ephemeral reply. It picks `followup` or `response` depending on whether the interaction has already been answered.
- **Adding a feature** means adding a new cog file in `bot/cogs/` (use `general.py` as the template). Use `app_commands` for slash commands.
- **Role menus** (`bot/cogs/roles.py`) are stateless and need no database or config file. Each button's `custom_id` encodes `rolemenu:<role_id>:<max>` (`max` 0 = no limit). `RoleButton` is a `discord.ui.DynamicItem` that matches this template, and it is registered in `cog_load` through `add_dynamic_items`, so buttons keep working after restarts. The other roles in a menu are worked out from the clicked message's own components (`menu_role_ids`). `max_roles == 1` swaps roles; values above 1 block once the limit is reached. If you change the `custom_id` format, every menu already posted stops working.
- For Discord to allow role changes, the bot's role must be above every role it hands out. `/rolemenu` checks this up front (`r >= bot_top`, as well as managed and `@everyone` roles).
- Logging uses named loggers under `bot` (`bot`, `bot.roles`). They are set up by `discord.utils.setup_logging()`.

## Conventions

- User-facing replies are short, and most are `ephemeral=True`.
- `main` is protected, so all changes go through a branch and a PR that needs approval from a code owner (`.github/CODEOWNERS`).
- `.gitattributes` enforces LF line endings (except `*.bat`).
- Never commit `.env`; it holds the bot token.
