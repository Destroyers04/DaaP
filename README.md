# DaaP

Discord bot for our work Discord server, written in Python with [discord.py](https://discordpy.readthedocs.io/).

Features:
- `/ping` checks that the bot is alive (admins only)
- `/ticket [reason]` opens a private ticket with the admins
- **Role menus:** admins post a message with role buttons, members click to get or remove a role
- **Email verification:** members verify their company email with a code to get access, with help tickets for admins

## Requirements

- **Python 3.10 or newer.** The `python3` that ships with macOS is 3.9 and won't work. Install a newer one from [python.org](https://www.python.org/downloads/) or with `brew install python`.
- A Discord bot token. Use your own **test bot** and **test server** for development, not the real one.

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Mac/Linux
# .venv\Scripts\activate         # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your .env file and fill in your values
cp .env.example .env
```

`.env` settings:

| Variable           | What it is                                                                 |
|--------------------|----------------------------------------------------------------------------|
| `DISCORD_TOKEN`    | Bot token from the [Discord Developer Portal](https://discord.com/developers/applications) |
| `DISCORD_GUILD_ID` | ID of the server to register slash commands on                             |
| `DATABASE_PATH`    | Where the SQLite database is stored (default `data/daap.db`)               |
| `EMAIL_HASH_SECRET`| Random secret of 32+ characters: `python -c "import secrets; print(secrets.token_hex(32))"`. **Never change or lose it** |
| `BREVO_API_KEY`    | Brevo API key for sending codes. Use `dev` to log codes instead of emailing them |
| `MAIL_FROM_ADDRESS`| Sender address, verified in Brevo                                          |
| `MAIL_FROM_NAME`   | Sender name (default `DaaP Verification`)                                  |

**Never commit `.env`.** It contains the bot token. It's already in `.gitignore`.

## Run

```bash
python -m bot
```

## Discord setup

When inviting the bot, give it these permissions:
- View Channels
- Send Messages
- Manage Roles
- Manage Channels (for verification tickets)
- Manage Nicknames (sets verified members' nicknames to their name)
- Read Message History
- Embed Links

In **Server Settings → Roles**, the bot's role must be **above** every role it hands out. Otherwise Discord blocks it.

To copy IDs (server, channel, message, role), turn on **Developer Mode** under User Settings → Advanced, then right-click the item and choose **Copy ID**.

## Role menus

Run `/rolemenu` in the channel where the menu should be (admins only):

| Option      | What it does                                                                 |
|-------------|------------------------------------------------------------------------------|
| `text`      | Message shown above the buttons                                              |
| `role1`–`role10` | Roles to offer, one button each                                         |
| `max_roles` | How many roles from this menu a member can have. `1` = pick one (clicking another swaps it). Empty = no limit |

The bot posts the message and it stays in the channel. Clicking a button gives the role, clicking again removes it. Menus keep working after restarts, so you only post them once. To change a menu, delete the message and post a new one.

Admin-only is the default. Server admins can change who sees the command under Server Settings → Integrations.

## Email verification

Members verify their company email to get a Verified role. They click **Verify**, enter their work email, and type the 6-digit code the bot emails them (valid for 10 minutes). One Discord account can link one email, and one email one account. Members who get stuck click **Need help**, which opens a private ticket channel for admins.

Setup on Discord:

1. In the [Developer Portal](https://discord.com/developers/applications), turn on **Server Members Intent** (Bot → Privileged Gateway Intents). The bot uses it to give the role and nickname back when a verified member rejoins.
2. Give the bot **Manage Roles**, **Manage Channels** and **Manage Nicknames**, and move its role above `@Verified` and every role whose members it should rename. It can never rename the server owner.
3. Let `@everyone` see `#verify` but not send there. Hide every other category from `@everyone` and show it to `@Verified`.
4. Create a hidden category for tickets.

Admin commands:

| Command | What it does |
|---|---|
| `/verifymenu role domains [ticket_category] [admin_role]` | Saves the settings and posts the Verify menu in this channel. Run it again to change the settings |
| `/verifydomains list` / `add` / `remove` | Shows or edits the allowed email domains (comma-separated) |
| `/unverify user` | Unlinks the user's email and removes the Verified role, so they or someone else can verify with it again |
| `/forceverify user email` | Verifies a member without a code. The email is linked like a normal verification, so the one-email-per-account rule still applies |

When someone opens a ticket, confirm who they are, then run `/forceverify` with their work email. If it says the email is linked to another account (for example their old account), `/unverify` that account first.

The data lives in an SQLite file (`DATABASE_PATH`, default `data/daap.db`). To move it to another machine, stop the bot (or run `sqlite3 data/daap.db ".backup copy.db"`), copy the file, and **copy `EMAIL_HASH_SECRET` exactly**. If that secret changes, stored emails no longer match and one email could be linked to several accounts.

## Adding a feature

Each feature is a cog: one file in `bot/cogs/`. Every file there is loaded on startup. Copy `bot/cogs/general.py` as a starting point:

```python
class Hello(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="hello", description="Say hello")
    async def hello(self, interaction: discord.Interaction):
        await interaction.response.send_message("Hello!")


async def setup(bot: commands.Bot):
    await bot.add_cog(Hello(bot))
```

Restart the bot and the command is registered on the server.

## Contributing

`main` is protected. All changes go through pull requests:

1. Create a branch: `git checkout -b my-change`
2. Commit and push: `git push -u origin my-change`
3. Open a pull request on GitHub
4. Get an approval from a code owner, then merge

Code owners are listed in [`.github/CODEOWNERS`](.github/CODEOWNERS). GitHub asks them for a review automatically when you open a pull request.

Test your change against your own test server before asking for review.
