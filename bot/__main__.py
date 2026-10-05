import logging
import os
import pkgutil
import sys

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from bot import cogs

log = logging.getLogger("bot")


class Bot(commands.Bot):
    def __init__(self, guild_id: int):
        intents = discord.Intents.none()
        intents.guilds = True
        # Only slash commands and buttons are used, so prefix commands just answer to mentions.
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

        self.guild_id = guild_id
        self.tree.on_error = on_command_error

    async def setup_hook(self):
        for module in pkgutil.iter_modules(cogs.__path__):
            await self.load_extension(f"{cogs.__name__}.{module.name}")
            log.info(f"Loaded cog {module.name}")

        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info(f"Registered {len(synced)} commands on server {self.guild_id}")

    async def on_ready(self):
        if self.get_guild(self.guild_id) is None:
            log.error(f"Bot is not in server {self.guild_id}, check the invite and the ID")


async def on_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.error(f"Command /{interaction.command.name if interaction.command else '?'} failed", exc_info=error)

    message = "Something went wrong :("
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def main():
    load_dotenv()
    discord.utils.setup_logging()

    token = os.getenv("DISCORD_TOKEN") or sys.exit("DISCORD_TOKEN is not set, check your .env file")
    guild_id = os.getenv("DISCORD_GUILD_ID") or sys.exit("DISCORD_GUILD_ID is not set, check your .env file")
    if not guild_id.isdigit():
        sys.exit("DISCORD_GUILD_ID must be a server ID (a number), check your .env file")

    bot = Bot(int(guild_id))
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
