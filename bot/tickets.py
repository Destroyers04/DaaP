import asyncio
import logging
import re
import sqlite3
import time

import discord

from bot.checks import is_moderator
from bot.ui import reply

log = logging.getLogger("bot.tickets")

DELETE_DELAY = 5  # seconds between Resolve and the channel being deleted
CHANNEL_NAME_MAX = 90  # Discord allows 100, leave some room


def ticket_category(guild: discord.Guild, settings) -> discord.CategoryChannel | None:
    category = settings and settings["ticket_category_id"] and guild.get_channel(settings["ticket_category_id"])
    return category if isinstance(category, discord.CategoryChannel) else None


async def tickets_enabled(interaction: discord.Interaction) -> bool:
    settings = await interaction.client.db.get_settings(interaction.guild.id)
    return ticket_category(interaction.guild, settings) is not None


def ticket_view(tickets: bool) -> discord.ui.View | None:
    if not tickets:
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(OpenTicketButton())
    return view


class OpenTicketButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ticket:open"):
    def __init__(self, label: str = "Open ticket"):
        super().__init__(
            discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, emoji="🎫", custom_id="ticket:open")
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(item.label or "Open ticket")

    async def callback(self, interaction: discord.Interaction):
        await open_ticket(interaction, "Verification issue", verification=True)


async def open_ticket(interaction: discord.Interaction, reason: str, verification: bool = False):
    await interaction.response.defer(ephemeral=True)
    db = interaction.client.db
    guild = interaction.guild
    user = interaction.user
    now = int(time.time())

    existing = await db.get_open_ticket(user.id)
    if existing:
        if guild.get_channel(existing["channel_id"]):
            await reply(interaction, f"You already have a ticket: <#{existing['channel_id']}>")
            return
        # The channel was deleted by hand
        await db.close_ticket(existing["channel_id"], None, now)

    settings = await db.get_settings(guild.id)
    category = ticket_category(guild, settings)
    if category is None:
        await reply(interaction, "Tickets aren't set up, ask a moderator directly.")
        return
    moderator_role = settings["moderator_role_id"] and guild.get_role(settings["moderator_role_id"])

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True, read_message_history=True
        ),
        user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True, attach_files=True
        ),
    }
    if moderator_role:
        overwrites[moderator_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )

    prefix = "verify" if verification else "ticket"
    try:
        # Discord lowercases and cleans channel names itself
        channel = await category.create_text_channel(
            name=f"{prefix}-{user.name}"[:CHANNEL_NAME_MAX], overwrites=overwrites, reason=f"Ticket for {user.id}"
        )
    except discord.HTTPException:
        log.exception(f"Could not create a ticket channel for {user.id}")
        await reply(interaction, "I couldn't open a ticket, ask a moderator directly.")
        return

    try:
        await db.open_ticket(channel.id, user.id, now)
    except sqlite3.IntegrityError:
        # Double click: another ticket was opened a moment ago
        await channel.delete(reason="Duplicate ticket")
        existing = await db.get_open_ticket(user.id)
        await reply(interaction, f"You already have a ticket: <#{existing['channel_id']}>" if existing else "Try again.")
        return

    view = discord.ui.View(timeout=None)
    view.add_item(ResolveTicketButton())
    if verification:
        view.add_item(ModeratorInfoButton())
    text = f"🏷️ **{discord.utils.escape_markdown(reason)}**\n{user.mention} needs help."
    if moderator_role:
        text += f" {moderator_role.mention}"
    # The reason is user text, so only the user and the moderator role may be pinged
    mentions = discord.AllowedMentions(everyone=False, users=[user], roles=[moderator_role] if moderator_role else False)
    await channel.send(text, view=view, allowed_mentions=mentions)

    await reply(interaction, f"Ticket opened: {channel.mention}")
    log.info(f"{user.id} opened ticket {channel.id}")


class ResolveTicketButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ticket:resolve"):
    def __init__(self):
        super().__init__(
            discord.ui.Button(label="Resolve", style=discord.ButtonStyle.success, emoji="✅", custom_id="ticket:resolve")
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        member = interaction.user
        channel = interaction.channel

        if not await is_moderator(interaction):
            await reply(interaction, "Only moderators and admins can resolve tickets.")
            return

        await db.close_ticket(channel.id, member.id, int(time.time()))
        await interaction.response.send_message(f"Resolved by {member.mention}. Deleting in {DELETE_DELAY} seconds…")
        log.info(f"{member.id} resolved ticket {channel.id}")

        await asyncio.sleep(DELETE_DELAY)
        try:
            await channel.delete(reason=f"Ticket resolved by {member}")
        except discord.HTTPException:
            log.exception(f"Could not delete ticket channel {channel.id}")


class ModeratorInfoButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ticket:admininfo"):
    def __init__(self):
        super().__init__(
            discord.ui.Button(
                # The custom_id is unchanged so buttons on tickets that are already open keep working
                label="Moderator info", style=discord.ButtonStyle.secondary, emoji="ℹ️", custom_id="ticket:admininfo"
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        if not await is_moderator(interaction):
            await reply(interaction, "Only moderators and admins can use this.")
            return
        await reply(
            interaction,
            "Confirm who they are, then run `/forceverify` with their work email.\n"
            "If the email is linked to another account, `/unverify` that account first.",
        )
