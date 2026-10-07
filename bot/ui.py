import discord


async def reply(
    interaction: discord.Interaction, text: str, view: discord.ui.View | None = None
) -> discord.WebhookMessage | None:
    view = view or discord.utils.MISSING
    if interaction.response.is_done():
        return await interaction.followup.send(text, view=view, ephemeral=True, wait=True)
    await interaction.response.send_message(text, view=view, ephemeral=True)
    return None
