import discord
from discord import app_commands
from discord.ext import commands

# Admin = the Administrator permission. Moderator = the moderator role saved by /verifymenu.
# Admins count as moderators too.


def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator


async def is_moderator(interaction: discord.Interaction) -> bool:
    if is_admin(interaction):
        return True
    settings = await interaction.client.db.get_settings(interaction.guild.id)
    moderator_role_id = settings and settings["moderator_role_id"]
    return any(r.id == moderator_role_id for r in interaction.user.roles)


class NotAllowed(app_commands.CheckFailure):
    pass


class _LevelCog(commands.Cog):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for value in vars(cls).values():
            if isinstance(value, (app_commands.Command, app_commands.Group)) and value.parent is None:
                # Only hides the command. Moderators see theirs through a Server Settings → Integrations
                # override; interaction_check is what enforces the level
                app_commands.default_permissions(administrator=True)(value)
                app_commands.guild_only(value)


class AdminCog(_LevelCog):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_admin(interaction):
            raise NotAllowed("Only admins can use this.")
        return True


class ModeratorCog(_LevelCog):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await is_moderator(interaction):
            raise NotAllowed("Only moderators and admins can use this.")
        return True
