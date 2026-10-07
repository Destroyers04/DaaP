import logging
import os
import pkgutil
import sys
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from bot import cogs
from bot.db import Database
from bot.mailer import Mailer
from bot.security import MIN_SECRET_LENGTH
from bot.ui import reply

log = logging.getLogger("bot")


class Bot(commands.Bot):
    def __init__(self, guild_id: int, db_path: str, mailer: Mailer):
        intents = discord.Intents.none()
        intents.guilds = True
        # Privileged: needed for on_member_join, which gives the verified role back on rejoin
        intents.members = True
        # Only slash commands and buttons are used, so prefix commands just answer to mentions.
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

        self.guild_id = guild_id
        self.db_path = db_path
        self.mailer = mailer
        self.tree.on_error = on_command_error

    async def setup_hook(self):
        # Before loading cogs, since they use self.db
        self.db = await Database.connect(self.db_path)
        await self.mailer.start()
        if self.mailer.dev:
            log.warning("BREVO_API_KEY is 'dev', codes are logged instead of emailed")

        for module in pkgutil.iter_modules(cogs.__path__):
            await self.load_extension(f"{cogs.__name__}.{module.name}")
            log.info(f"Loaded cog {module.name}")

        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info(f"Registered {len(synced)} commands on server {self.guild_id}")

    async def close(self):
        await self.mailer.close()
        if getattr(self, "db", None):
            await self.db.close()
        await super().close()

    async def on_ready(self):
        if self.get_guild(self.guild_id) is None:
            log.error(f"Bot is not in server {self.guild_id}, check the invite and the ID")


async def on_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need to be an admin to use this."
    else:
        log.error(f"Command /{interaction.command.name if interaction.command else '?'} failed", exc_info=error)
        message = "Something went wrong :("

    try:
        await reply(interaction, message)
    except discord.NotFound:
        # 10062: the command didn't answer within 3 seconds, so Discord dropped the interaction.
        # Fix it by calling interaction.response.defer() before slow work.
        log.warning("Could not tell the user about the error, the interaction had already expired")
    except discord.HTTPException:
        log.warning("Could not tell the user about the error")


def main():
    load_dotenv()
    discord.utils.setup_logging()

    token = os.getenv("DISCORD_TOKEN") or sys.exit("DISCORD_TOKEN is not set, check your .env file")
    guild_id = os.getenv("DISCORD_GUILD_ID") or sys.exit("DISCORD_GUILD_ID is not set, check your .env file")
    if not guild_id.isdigit():
        sys.exit("DISCORD_GUILD_ID must be a server ID (a number), check your .env file")

    if len(os.getenv("EMAIL_HASH_SECRET", "")) < MIN_SECRET_LENGTH:
        sys.exit(f"EMAIL_HASH_SECRET is missing or shorter than {MIN_SECRET_LENGTH} characters, check your .env file")
    api_key = os.getenv("BREVO_API_KEY") or sys.exit("BREVO_API_KEY is not set, check your .env file")
    from_address = os.getenv("MAIL_FROM_ADDRESS") or sys.exit("MAIL_FROM_ADDRESS is not set, check your .env file")
    mailer = Mailer(api_key, from_address, os.getenv("MAIL_FROM_NAME") or "DaaP Verification")

    db_path = os.getenv("DATABASE_PATH") or "data/daap.db"
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    bot = Bot(int(guild_id), db_path, mailer)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
