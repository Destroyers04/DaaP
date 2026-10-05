import discord
from discord import app_commands
from discord.ext import commands


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Check that the bot is alive")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"Pong! ({round(self.bot.latency * 1000)} ms)")


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
