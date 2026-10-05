# DaaP

Discord bot for our work Discord server, written in Python with [discord.py](https://discordpy.readthedocs.io/).

Features:
- `/ping` checks that the bot is alive
- **Role menus:** admins post a message with role buttons, members click to get or remove a role

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

In **Server Settings → Roles**, the bot's role must be **above** every role it hands out. Otherwise Discord blocks it.

To copy IDs (server, channel, message, role), turn on **Developer Mode** under User Settings → Advanced, then right-click the item and choose **Copy ID**.

## Role menus

Run `/rolemenu` in the channel where the menu should be (admins only):

| Option      | What it does                                                                 |
|-------------|------------------------------------------------------------------------------|
| `text`      | Message shown above the buttons                                              |
| `role1`–`role5` | Roles to offer, one button each                                          |
| `max_roles` | How many roles from this menu a member can have. `1` = pick one (clicking another swaps it). Empty = no limit |

The bot posts the message and it stays in the channel. Clicking a button gives the role, clicking again removes it. Menus keep working after restarts, so you only post them once. To change a menu, delete the message and post a new one.

Admin-only is the default. Server admins can change who sees the command under Server Settings → Integrations.

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
