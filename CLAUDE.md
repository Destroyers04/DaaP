# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

DaaP is a Discord bot for a work Discord server, built on discord.py 2.x. It uses only slash commands, buttons and modals. There are no prefix commands and no message-content intent. The privileged **members** intent is on (also enable it in the Developer Portal), so `on_member_join` can give the Verified role and saved nickname back when a verified user rejoins.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set DISCORD_TOKEN, DISCORD_GUILD_ID, EMAIL_HASH_SECRET, BREVO_API_KEY, MAIL_FROM_ADDRESS
python -m bot                 # run the bot
```

- Requires Python 3.10+ (it uses `X | None` and `set[int]` syntax). `.python-version` pins 3.14. The macOS system `python3` (3.9) will not work.
- The repo has no test suite, linter or formatter. To verify a change, run the bot against your own test bot and test server, not the real server.
- Dependencies are pinned exactly in `requirements.txt`.

## Architecture

- **Entry point** `bot/__main__.py`: the `Bot` subclass. Its `setup_hook` auto-loads **every module in `bot/cogs/`** as an extension through `pkgutil.iter_modules`. Every file in that folder must therefore define `async def setup(bot)`. Shared helper code must not live in `bot/cogs/`; put it elsewhere in `bot/`.
- **Command registration is guild-scoped.** The bot copies global commands to the single guild in `DISCORD_GUILD_ID` and syncs them on every startup, so new commands show up right after a restart. Global commands are never synced.
- **Error handling:** `tree.on_error` is set to `on_command_error`, which logs the error and sends an ephemeral reply. It picks `followup` or `response` depending on whether the interaction has already been answered. `MissingPermissions` gets "You need to be an admin" without a traceback, and if the interaction has already expired it only logs a warning.
- **Ephemeral replies** use `reply(interaction, text, view=None)` from `bot/ui.py`, which picks `response` or `followup` by itself. Call `interaction.response.send_message` directly only for public replies.
- **Discord drops an interaction that isn't answered within 3 seconds** (`404 Unknown interaction`, error 10062). Any command or button that sends messages, changes roles or channels, or sends email must call `interaction.response.defer(...)` first and reply with `interaction.followup.send`. The one exception is opening a modal: `send_modal` must be the first response, so don't defer before it.
- **Adding a feature** means adding a new cog file in `bot/cogs/` (use `general.py` as the template). Use `app_commands` for slash commands.
- **Role menus** (`bot/cogs/roles.py`) are stateless and need no database or config file. Each button's `custom_id` encodes `rolemenu:<role_id>:<max>` (`max` 0 = no limit). `RoleButton` is a `discord.ui.DynamicItem` that matches this template, and it is registered in `cog_load` through `add_dynamic_items`, so buttons keep working after restarts. The other roles in a menu are worked out from the clicked message's own components (`menu_role_ids`). `max_roles == 1` swaps roles; values above 1 block once the limit is reached. If you change the `custom_id` format, every menu already posted stops working.
- For Discord to allow role changes, the bot's role must be above every role it hands out. `/rolemenu` checks this up front (`r >= bot_top`, as well as managed and `@everyone` roles).
- **Database** `bot/db.py`: SQLite through `aiosqlite`, one connection opened in `setup_hook` **before** cogs load and exposed as `bot.db` (a `Database` with one method per query). The schema is versioned with `PRAGMA user_version`: `MIGRATIONS` is a list of scripts, and new ones run on startup. Never edit a migration that has already run on real data; append a new one. Use `?` placeholders only. Every write goes through `_write`, `_write_many` or `async with self._transaction() as conn:` for several statements. These commit, or roll back on any error, and hold a lock, because all coroutines share one connection. Don't call `self.conn.commit()` directly. Times are UTC unix seconds. The DB file (`DATABASE_PATH`, default `data/daap.db`) is gitignored.
- **Email verification** (`bot/cogs/verify.py`, helpers `bot/security.py` and `bot/mailer.py`): `/verifymenu` saves the role, allowed domains, ticket category and admin role in the DB and posts a Verify button. Unlike role menus, these settings live in the DB (domains must be editable, and `on_member_join` has no button). The buttons (`verify:start`, `verify:code`, `verify:resend`, `ticket:open`, `ticket:resolve`, `ticket:admininfo`) are fixed-`custom_id` `DynamicItem`s that read everything from the DB, so they keep working after restarts. One account has one email and one email has one account (`verified_emails`: PK + UNIQUE).
  - **Decoy:** if someone enters an email already linked to another account, the bot sends nothing but replies exactly as if it had and saves a pending code nobody knows. This hides which emails are in use. Don't add anything user-visible that tells the two cases apart.
  - `normalize_email` lowercases and drops a `+tag` from the part before the `@`, before hashing, sending or picking a nickname, so plus-addresses of one inbox count as one email.
  - Emails and codes are stored only as HMAC hashes keyed by `EMAIL_HASH_SECRET`. **That secret must never change or be lost**: every stored hash depends on it, and uniqueness silently breaks if it does. Never log an email address or a code; log Discord IDs.
  - **Nicknames:** `nickname_from_email` turns `firstname.lastname@…` into "Firstname Lastname" (a single-part `firstname@…` becomes "Firstname"). `fit_nickname` keeps it within Discord's 32-character limit: middle names become initials, then get dropped, then the longer of the first and last name is trimmed, so both stay visible. The name is worked out when the email is entered (the plain email isn't kept), stored in `pending_verifications.nickname`, copied to `verified_emails.nickname` on success and set as the member's nickname. `NULL` means the part before the `@` isn't only letters separated by dots or hyphens. These names are plain text, and a name plus the allowed domains is almost the whole email, so treat them as personal data and don't log them either. Renaming needs **Manage Nicknames** and a bot role above the member's highest role, and it never works on the server owner. A failed rename is logged and doesn't stop verification.
  - **Admin commands** (`/ping`, `/rolemenu`, `/verifymenu`, `/verifydomains`, `/unverify`, `/forceverify`) use the `admin_only` decorator in `bot/checks.py` (`admin_check` for the `/verifydomains` subcommands). They are allowed for Administrator **or** the admin role saved by `/verifymenu` (`is_admin`, also used by the ticket buttons), so the first `/verifymenu` must be run by an Administrator. `default_permissions(administrator=True)` only hides them; the admin role sees them through a Server Settings → Integrations override, and `admin_check` is what enforces it. `/forceverify user email` is the code-free path used from tickets. It saves the link exactly like a normal verification (so uniqueness and rejoin still work) and refuses if the email belongs to another account. Its replies are admin-only, so naming that account doesn't break the decoy.
  - `bot/mailer.py` is the only file that knows about the provider (Brevo HTTP API over `aiohttp`). `BREVO_API_KEY=dev` logs codes instead of sending them.
- **Tickets** (`bot/tickets.py`): the button classes and `open_ticket` live outside `bot/cogs/` because the verify cog uses them too; `bot/cogs/tickets.py` registers them and adds `/ticket [reason]`. The **Open ticket** button and `/ticket` both go through `open_ticket`. The reason is shown at the top of the ticket message; button tickets use "Verification issue" and are the only ones that get **Admin info**. A ticket is a private channel in the configured category, at most one open ticket per user (a partial unique index). The user can read their ticket channel, so anything meant only for admins goes in an ephemeral reply. That's why the steps are behind the **Admin info** button and not in the ticket message. `is_admin` (`bot/checks.py`) decides who can use Resolve and Admin info.
- Logging uses named loggers under `bot` (`bot`, `bot.roles`, `bot.verify`, `bot.tickets`, `bot.mailer`). They are set up by `discord.utils.setup_logging()`.

## Conventions

- User-facing replies are short, and most are `ephemeral=True`.
- `main` is protected, so all changes go through a branch and a PR that needs approval from a code owner (`.github/CODEOWNERS`).
- `.gitattributes` enforces LF line endings (except `*.bat`).
- Never commit `.env` (it holds the bot token and secrets) or the database in `data/`.
