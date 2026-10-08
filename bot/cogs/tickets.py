import discord
from discord import app_commands
from discord.ext import commands

from bot.tickets import ModeratorInfoButton, OpenTicketButton, ResolveTicketButton, open_ticket

REASON_MAX = 200


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(OpenTicketButton, ResolveTicketButton, ModeratorInfoButton)

    @app_commands.command(name="ticket", description="Open a private ticket with the moderators")
    @app_commands.guild_only()
    @app_commands.describe(reason="What you need help with")
    async def ticket(
        self, interaction: discord.Interaction, reason: app_commands.Range[str, 1, REASON_MAX] = "No reason given"
    ):
        await open_ticket(interaction, reason)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
