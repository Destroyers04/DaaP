import discord
from discord import app_commands


async def is_admin(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if member.guild_permissions.administrator:
        return True
    settings = await interaction.client.db.get_settings(interaction.guild.id)
    admin_role_id = settings and settings["admin_role_id"]
    return any(r.id == admin_role_id for r in member.roles)


async def _admin_predicate(interaction: discord.Interaction) -> bool:
    if not await is_admin(interaction):
        raise app_commands.MissingPermissions(["administrator"])
    return True


# For subcommands, where default_permissions and guild_only are set on the group
admin_check = app_commands.check(_admin_predicate)


def admin_only(func):
    # default_permissions only hides the command (the admin role sees it through a server Integrations
    # override); admin_check is what actually enforces it
    func = app_commands.default_permissions(administrator=True)(func)
    func = admin_check(func)
    return app_commands.guild_only()(func)
