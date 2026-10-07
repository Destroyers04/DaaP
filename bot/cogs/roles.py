import asyncio
import logging
import re
import weakref

import discord
from discord import app_commands
from discord.ext import commands

from bot.checks import admin_only
from bot.ui import reply

log = logging.getLogger("bot.roles")

# Role and limit live in the button's custom_id, so menus survive restarts without any config.
# max 0 = no limit
BUTTON_ID = r"rolemenu:(?P<role_id>\d+):(?P<max>\d+)"
MAX_MENU_ROLES = 10  # must match the role1..role10 parameters of /rolemenu


# Weak values: a member's lock disappears once no click holds it, so this doesn't grow forever
_member_locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()


def _member_lock(member_id: int) -> asyncio.Lock:
    lock = _member_locks.get(member_id)
    if lock is None:
        lock = _member_locks[member_id] = asyncio.Lock()
    return lock


def menu_role_ids(message: discord.Message) -> set[int]:
    ids = set()
    for row in message.components:
        for child in row.children:
            match = re.fullmatch(BUTTON_ID, child.custom_id or "")
            if match:
                ids.add(int(match["role_id"]))
    return ids


class RoleButton(discord.ui.DynamicItem[discord.ui.Button], template=BUTTON_ID):
    def __init__(self, role_id: int, max_roles: int, label: str = "Role"):
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                custom_id=f"rolemenu:{role_id}:{max_roles}",
            )
        )
        self.role_id = role_id
        self.max_roles = max_roles

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(int(match["role_id"]), int(match["max"]))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        role = interaction.guild.get_role(self.role_id)
        if role is None:
            log.warning(f"Role {self.role_id} does not exist on this server")
            await reply(interaction, "That role no longer exists, ask an admin.")
            return

        menu_ids = menu_role_ids(interaction.message)
        # One click per member at a time, otherwise two quick clicks both see the old roles and
        # a pick-one menu ends up with two roles, or a limited menu goes over its limit
        async with _member_lock(interaction.user.id):
            try:
                # Fetched fresh: the cache only updates when Discord's event arrives, which can be after the next click
                member = await interaction.guild.fetch_member(interaction.user.id)
                current = [r for r in member.roles if r.id in menu_ids]
                if role in member.roles:
                    await member.remove_roles(role)
                    current.remove(role)
                    text = f"Removed **{role.name}**."
                elif self.max_roles == 1:
                    # Add first, so a failure never leaves the member with no role from the menu
                    await member.add_roles(role)
                    if current:
                        await member.remove_roles(*current)
                    current = [role]
                    text = f"You now have **{role.name}**."
                elif self.max_roles and len(current) >= self.max_roles:
                    text = f"You can have at most {self.max_roles} roles from this menu. Remove one first."
                else:
                    await member.add_roles(role)
                    current.append(role)
                    text = f"You now have **{role.name}**."
            except discord.HTTPException:
                log.exception(f"Could not change role {role.name} - is the bot's role above it?")
                await reply(interaction, "I couldn't change your roles, ask an admin.")
                return

        log.info(f"Role menu: {member} clicked {role.name} -> {text}")

        # member.roles isn't refreshed after the change, so `current` is kept up to date by hand above.
        # Pick-one menus skip the list, since the reply already names their only role.
        if self.max_roles != 1:
            if current:
                text += "\nYou currently have these roles: " + ", ".join(f"**{r.name}**" for r in current)
            else:
                text += "\nYou currently have no roles from this menu."
        await reply(interaction, text)


class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(RoleButton)

    @app_commands.command(name="rolemenu", description="Post a role menu with buttons in this channel")
    @admin_only
    @app_commands.describe(
        text="Message shown above the buttons",
        max_roles="How many roles a member can have from this menu (1 = pick only one). Empty = no limit",
    )
    async def rolemenu(
        self,
        interaction: discord.Interaction,
        text: str,
        role1: discord.Role,
        role2: discord.Role | None = None,
        role3: discord.Role | None = None,
        role4: discord.Role | None = None,
        role5: discord.Role | None = None,
        role6: discord.Role | None = None,
        role7: discord.Role | None = None,
        role8: discord.Role | None = None,
        role9: discord.Role | None = None,
        role10: discord.Role | None = None,
        max_roles: app_commands.Range[int, 1, MAX_MENU_ROLES] | None = None,
    ):
        picked = (role1, role2, role3, role4, role5, role6, role7, role8, role9, role10)
        roles = list(dict.fromkeys(r for r in picked if r))

        bot_top = interaction.guild.me.top_role
        bad = [r for r in roles if r.is_default() or r.managed or r >= bot_top]
        if bad:
            names = ", ".join(r.name for r in bad)
            await interaction.response.send_message(
                f"I can't hand out: {names}. Move my role above them in Server Settings → Roles.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=None)
        for role in roles:
            view.add_item(RoleButton(role.id, max_roles or 0, role.name))

        await interaction.channel.send(text, view=view)
        await interaction.response.send_message("Role menu posted.", ephemeral=True)
        log.info(f"{interaction.user} posted a role menu in #{interaction.channel} with {', '.join(r.name for r in roles)}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))
