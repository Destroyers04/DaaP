import asyncio
import logging
import random
import re
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import security
from bot.checks import admin_check, admin_only
from bot.db import SendBlocked
from bot.mailer import MailError
from bot.tickets import OpenTicketButton, ticket_view, tickets_enabled
from bot.ui import reply

log = logging.getLogger("bot.verify")


def menu_text(tickets: bool) -> str:
    return (
        "You need to verify your company email to get into this server.\n"
        f"Click **Verify**, enter your work email, and we'll send you a {security.CODE_LENGTH}-digit code (valid for {security.CODE_TTL // 60} minutes).\n"
        "No email? Check your spam or quarantine folder, or "
        + ("click **Need help** to talk to an admin." if tickets else "ask an admin.")
    )


def get_help(tickets: bool) -> str:
    return "open a ticket" if tickets else "ask an admin"


def pending_view(tickets: bool) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(EnterCodeButton())
    view.add_item(ResendButton())
    if tickets:
        view.add_item(OpenTicketButton())
    return view


def pending_text(pending) -> str:
    return f"📧 A code was sent to **{pending['masked_email']}**. It expires <t:{pending['expires_at']}:R>."


EXPIRED_TEXT = "⌛ This code has expired. Click **Verify** to get a new one."

# Strong references, so pending tasks aren't garbage collected
_expiry_tasks: set[asyncio.Task] = set()


def mark_expired_later(interaction: discord.Interaction, message: discord.WebhookMessage | None, expires_at: int):
    # Lost on restart; the Enter code button still says the code expired in that case

    async def run():
        await asyncio.sleep(max(0, expires_at - time.time()))
        if await interaction.client.db.get_verified(interaction.user.id):
            return
        view = ticket_view(await tickets_enabled(interaction))
        try:
            if message:
                await message.edit(content=EXPIRED_TEXT, view=view)
            else:
                await interaction.edit_original_response(content=EXPIRED_TEXT, view=view)
        except discord.HTTPException:
            # The user dismissed the message, or the interaction token ran out
            log.debug(f"Could not mark the code message of {interaction.user.id} as expired")

    task = asyncio.create_task(run())
    _expiry_tasks.add(task)
    task.add_done_callback(_expiry_tasks.discard)


async def verified_role(db, guild: discord.Guild) -> discord.Role | None:
    settings = await db.get_settings(guild.id)
    return settings and guild.get_role(settings["role_id"])


async def give_role(member: discord.Member, role: discord.Role, reason: str) -> bool:
    if role in member.roles:
        return True
    try:
        await member.add_roles(role, reason=reason)
        return True
    except discord.HTTPException:
        log.exception(f"Could not give the verified role to {member.id}, is the bot's role above it?")
        return False


NICK_MAX = 32  # Discord's nickname limit


def nickname_from_email(email: str) -> str | None:
    parts = email.split("@", 1)[0].split(".")
    if not all(w.isalpha() for p in parts for w in p.split("-")):
        return None
    names = ["-".join(w.capitalize() for w in p.split("-")) for p in parts]
    return fit_nickname(names)


def fit_nickname(names: list[str]) -> str:
    if len(" ".join(names)) <= NICK_MAX:
        return " ".join(names)
    if len(names) > 2:
        names = [names[0], *(n[0] + "." for n in names[1:-1]), names[-1]]
        if len(" ".join(names)) <= NICK_MAX:
            return " ".join(names)
        names = [names[0], names[-1]]
    if len(names) == 1:
        return names[0][:NICK_MAX]
    first, last = names
    while len(first) + 1 + len(last) > NICK_MAX:
        if len(first) >= len(last):
            first = first[:-1]
        else:
            last = last[:-1]
    return f"{first} {last}"


async def set_nickname(member: discord.Member, nickname: str):
    if member.nick == nickname:
        return
    try:
        await member.edit(nick=nickname, reason="Email verified")
    except discord.HTTPException:
        # Fails for the server owner and for members with a role above the bot's
        log.warning(f"Could not set the nickname of {member.id}, does the bot have Manage Nicknames and a high enough role?")


# The buttons use fixed custom_ids and read everything else from the DB, so they work after restarts.
# The ones on ephemeral messages are DynamicItems too, so they don't time out while someone checks their inbox.


class VerifyButton(discord.ui.DynamicItem[discord.ui.Button], template=r"verify:start"):
    def __init__(self):
        super().__init__(
            discord.ui.Button(label="Verify", style=discord.ButtonStyle.success, emoji="✅", custom_id="verify:start")
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        db = interaction.client.db
        user = interaction.user
        now = int(time.time())

        role = await verified_role(db, interaction.guild)
        if role is None:
            await reply(interaction, "Verification isn't set up, ask an admin.")
            return

        if await db.get_verified(user.id):
            # Giving the role back can take longer than 3 seconds. Only this branch defers,
            # because the modal below must be the first response
            await interaction.response.defer(ephemeral=True)
            if await give_role(user, role, "Already verified"):
                await reply(interaction, "You're already verified ✅")
            else:
                await reply(interaction, "You're already verified, but I couldn't give you the role. Ask an admin.")
            return

        pending = await db.get_pending(user.id)
        if pending and pending["expires_at"] > now:
            message = await reply(interaction, pending_text(pending), view=pending_view(await tickets_enabled(interaction)))
            mark_expired_later(interaction, message, pending["expires_at"])
            return

        # Must be the first response, no defer before it
        await interaction.response.send_modal(EmailModal())


class EnterCodeButton(discord.ui.DynamicItem[discord.ui.Button], template=r"verify:code"):
    def __init__(self):
        super().__init__(
            discord.ui.Button(label="Enter code", style=discord.ButtonStyle.primary, emoji="🔢", custom_id="verify:code")
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CodeModal())


class ResendButton(discord.ui.DynamicItem[discord.ui.Button], template=r"verify:resend"):
    def __init__(self):
        super().__init__(
            discord.ui.Button(label="Resend", style=discord.ButtonStyle.secondary, emoji="🔁", custom_id="verify:resend")
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        # The email is only stored as a hash, so the user types it again. Limits are checked on submit.
        await interaction.response.send_modal(EmailModal())


class EmailModal(discord.ui.Modal, title="Verify your work email"):
    email = discord.ui.TextInput(label="Work email", placeholder="firstname.lastname@example.com", max_length=254)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        bot = interaction.client
        db = bot.db
        user = interaction.user
        guild_id = interaction.guild.id
        now = int(time.time())

        if await verified_role(db, interaction.guild) is None:
            await reply(interaction, "Verification isn't set up, ask an admin.")
            return
        if await db.get_verified(user.id):
            await reply(interaction, "You're already verified ✅ Click **Verify** if you're missing the role.")
            return

        allowed = await db.get_domains(guild_id)
        email = security.normalize_email(self.email.value)
        if not security.EMAIL_RE.fullmatch(email) or security.email_domain(email) not in allowed:
            # The domain list isn't a secret
            await reply(interaction, f"Use your company email (allowed: {', '.join(allowed)}).")
            return
        email_hash = security.hash_email(email)

        taken = await db.email_hash_taken(email_hash, exclude_id=user.id)
        # Rate limits count both real and decoy sends, so they can't tell the two apart.
        # The send is logged here, before the email goes out, so submits at the same time can't all pass.
        try:
            send_id = await db.reserve_send(user.id, email_hash, real=not taken, now=now)
        except SendBlocked as blocked:
            if blocked.reason == "cooldown":
                await reply(interaction, f"Wait {blocked.wait} seconds before asking for a new code.")
            elif blocked.reason == "limit":
                tickets = await tickets_enabled(interaction)
                await reply(interaction, f"Too many attempts. Try again later, or {get_help(tickets)}.", ticket_view(tickets))
            else:
                log.warning("Daily email limit reached, no more codes are sent today")
                await reply(interaction, "Verification is busy, try again later.")
            return

        code = security.new_code()
        if taken:
            # DECOY: behave exactly like a real send, so nobody can find out which emails are in use.
            # The sleep hides the timing difference.
            await asyncio.sleep(random.uniform(*security.DECOY_DELAY))
            log.info(f"Decoy: {user.id} entered an email already linked to another account")
        else:
            try:
                await bot.mailer.send_code(email, code)
            except MailError:
                log.exception(f"Could not send a code to {user.id}")
                await db.unreserve_send(send_id)
                await reply(interaction, "Couldn't send the email, try again later.")
                return

        expires_at = now + security.CODE_TTL
        masked = security.mask_email(email)
        await db.ensure_user(user.id, now)
        await db.set_pending(
            user.id, email_hash, security.hash_code(user.id, code), masked, nickname_from_email(email), now, expires_at
        )
        if not taken:
            log.info(f"Sent a code to {user.id}")

        # Same text for real and decoy
        message = await reply(
            interaction,
            f"📧 Code sent to **{masked}**. It expires <t:{expires_at}:R>.",
            pending_view(await tickets_enabled(interaction)),
        )
        mark_expired_later(interaction, message, expires_at)


class CodeModal(discord.ui.Modal, title="Enter your code"):
    code = discord.ui.TextInput(
        label=f"{security.CODE_LENGTH}-digit code", min_length=security.CODE_LENGTH, max_length=security.CODE_LENGTH
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db = interaction.client.db
        member = interaction.user
        now = int(time.time())

        # Check expiry and count the attempt before anything else can succeed
        pending = await db.use_attempt(member.id, now)
        if not pending:
            if await db.get_verified(member.id):
                # The same code was submitted twice and the other one already verified them
                await reply(interaction, "✅ You're verified, welcome!")
                return
            await db.delete_pending(member.id)
            tickets = await tickets_enabled(interaction)
            await reply(interaction, "That code expired. Click **Verify** to get a new one.", ticket_view(tickets))
            return

        if not security.codes_match(pending["code_hash"], member.id, self.code.value):
            attempts = pending["attempts"]
            tickets = await tickets_enabled(interaction)
            if attempts >= security.MAX_ATTEMPTS:
                await db.delete_pending(member.id)
                await reply(
                    interaction,
                    f"Too many wrong codes. Click **Verify** to get a new one, or {get_help(tickets)}.",
                    ticket_view(tickets),
                )
            else:
                await reply(interaction, f"Wrong code, {security.MAX_ATTEMPTS - attempts} tries left.", pending_view(tickets))
            return

        try:
            await db.complete_verification(member.id, pending["email_hash"], pending["nickname"], now)
        except sqlite3.IntegrityError:
            if await db.get_verified(member.id):
                # The same code was submitted twice and the other one got here first
                await reply(interaction, "✅ You're verified, welcome!")
                return
            # Someone else verified the same email a moment ago
            await db.delete_pending(member.id)
            tickets = await tickets_enabled(interaction)
            await reply(interaction, f"Couldn't verify this email. {get_help(tickets).capitalize()}.", ticket_view(tickets))
            return
        if pending["nickname"]:
            log.info(f"{member.id} verified")
            await set_nickname(member, pending["nickname"])
        else:
            log.info(f"{member.id} verified, but their email isn't firstname.lastname, so no name was saved")

        role = await verified_role(db, interaction.guild)
        if role is None or not await give_role(member, role, "Email verified"):
            await reply(interaction, "You're verified, but I couldn't give you the role. Ask an admin.")
            return
        await reply(interaction, "✅ You're verified, welcome!")


class Verify(commands.Cog):
    domains_group = app_commands.Group(
        name="verifydomains",
        description="Manage allowed email domains",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(VerifyButton, EnterCodeButton, ResendButton)
        self.cleanup.start()

    async def cog_unload(self):
        self.cleanup.cancel()

    @tasks.loop(hours=1)
    async def cleanup(self):
        await self.bot.db.cleanup_expired(int(time.time()))

    @app_commands.command(name="verifymenu", description="Save the verification settings and post the Verify menu here")
    @admin_only
    @app_commands.describe(
        role="Role given to verified members",
        domains="Allowed email domains, comma-separated (e.g. example.com, example.org)",
        ticket_category="Category where help tickets are created",
        admin_role="Role that can see and resolve tickets",
    )
    async def verifymenu(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        domains: str,
        ticket_category: discord.CategoryChannel | None = None,
        admin_role: discord.Role | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if role.is_default() or role.managed or role >= guild.me.top_role:
            await reply(interaction, f"I can't hand out {role.name}. Move my role above it in Server Settings → Roles.")
            return

        good, bad = security.parse_domains(domains)
        if bad or not good:
            await reply(interaction, f"Invalid domains: {', '.join(bad)}" if bad else "Give at least one domain.")
            return

        if ticket_category and not ticket_category.permissions_for(guild.me).manage_channels:
            await reply(
                interaction,
                f"I can't create channels in {ticket_category.name}. "
                "Give my role View Channel and Manage Channels in that category's permissions.",
            )
            return

        await self.bot.db.save_settings(
            guild.id, role.id, ticket_category and ticket_category.id, admin_role and admin_role.id, good
        )

        tickets = ticket_category is not None
        embed = discord.Embed(
            title="🔒 Verify your company account", description=menu_text(tickets), color=discord.Color.green()
        )
        view = discord.ui.View(timeout=None)
        view.add_item(VerifyButton())
        if tickets:
            view.add_item(OpenTicketButton("Need help"))
        await interaction.channel.send(embed=embed, view=view)

        await reply(interaction, f"Menu posted. Allowed domains: {', '.join(good)}")
        log.info(f"{interaction.user} posted a verify menu in #{interaction.channel} for role {role.name}")

    async def _domains_or_reply(self, interaction: discord.Interaction) -> list[str] | None:
        if await self.bot.db.get_settings(interaction.guild.id) is None:
            await reply(interaction, "Run /verifymenu first.")
            return None
        return await self.bot.db.get_domains(interaction.guild.id)

    @domains_group.command(name="list", description="Show the allowed email domains")
    @admin_check
    async def domains_list(self, interaction: discord.Interaction):
        current = await self._domains_or_reply(interaction)
        if current is not None:
            await reply(interaction, f"Allowed domains: {', '.join(current)}")

    @domains_group.command(name="add", description="Allow more email domains")
    @admin_check
    @app_commands.describe(domains="Comma-separated, e.g. example.com, example.org")
    async def domains_add(self, interaction: discord.Interaction, domains: str):
        if await self._domains_or_reply(interaction) is None:
            return
        good, bad = security.parse_domains(domains)
        if bad or not good:
            await reply(interaction, f"Invalid domains: {', '.join(bad)}" if bad else "Give at least one domain.")
            return
        await self.bot.db.add_domains(interaction.guild.id, good)
        current = await self.bot.db.get_domains(interaction.guild.id)
        await reply(interaction, f"Allowed domains: {', '.join(current)}")
        log.info(f"{interaction.user} added verify domains {', '.join(good)}")

    @domains_group.command(name="remove", description="Stop allowing email domains")
    @admin_check
    @app_commands.describe(domains="Comma-separated, e.g. example.org")
    async def domains_remove(self, interaction: discord.Interaction, domains: str):
        current = await self._domains_or_reply(interaction)
        if current is None:
            return
        good, _ = security.parse_domains(domains)
        to_remove = [d for d in good if d in current]
        if not to_remove:
            await reply(interaction, f"None of those are allowed. Allowed domains: {', '.join(current)}")
            return
        if len(to_remove) == len(current):
            await reply(interaction, "You can't remove every domain.")
            return
        await self.bot.db.remove_domains(interaction.guild.id, to_remove)
        current = await self.bot.db.get_domains(interaction.guild.id)
        await reply(interaction, f"Allowed domains: {', '.join(current)}")
        log.info(f"{interaction.user} removed verify domains {', '.join(to_remove)}")

    @app_commands.command(name="unverify", description="Unlink a user's email and remove their verified role")
    @admin_only
    async def unverify(self, interaction: discord.Interaction, user: discord.User):
        # discord.User and not Member, so it also works for people who already left
        await interaction.response.defer(ephemeral=True)
        db = self.bot.db
        was_verified = await db.delete_verified(user.id)
        await db.delete_pending(user.id)

        member = interaction.guild.get_member(user.id)
        role = await verified_role(db, interaction.guild)
        removed_role = False
        if member and role and role in member.roles:
            try:
                await member.remove_roles(role, reason=f"Unverified by {interaction.user}")
                removed_role = True
            except discord.HTTPException:
                log.exception(f"Could not remove the verified role from {user.id}")

        if was_verified or removed_role:
            text = f"Unlinked {user.mention}. They can verify again with any allowed email."
        else:
            text = f"{user.mention} wasn't verified."
        await reply(interaction, text)
        log.info(f"{interaction.user} unverified {user.id}")

    @app_commands.command(name="forceverify", description="Verify a member with their email, without a code")
    @admin_only
    @app_commands.describe(user="Member to verify", email="Their work email, linked to their account like a normal verification")
    async def forceverify(self, interaction: discord.Interaction, user: discord.Member, email: str):
        await interaction.response.defer(ephemeral=True)
        db = self.bot.db
        guild = interaction.guild
        now = int(time.time())

        role = await verified_role(db, guild)
        if role is None:
            await reply(interaction, "Run /verifymenu first.")
            return

        allowed = await db.get_domains(guild.id)
        email = security.normalize_email(email)
        if not security.EMAIL_RE.fullmatch(email) or security.email_domain(email) not in allowed:
            await reply(interaction, f"That isn't an allowed email (allowed: {', '.join(allowed)}).")
            return
        email_hash = security.hash_email(email)

        nickname = nickname_from_email(email)
        owner = await db.force_verify(user.id, email_hash, nickname, now)
        if owner is not None:
            # Only admins see this reply, so naming the other account doesn't break the decoy
            await reply(interaction, f"That email is already linked to <@{owner}>. Run /unverify on them first.")
            return
        log.info(f"{interaction.user} force-verified {user.id}")

        if nickname:
            await set_nickname(user, nickname)
        if not await give_role(user, role, f"Force-verified by {interaction.user}"):
            await reply(interaction, f"Verified {user.mention}, but I couldn't give the role. Is my role above {role.name}?")
            return
        await reply(interaction, f"✅ Verified {user.mention} as {security.mask_email(email)}.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # Needs the members intent
        if member.guild.id != self.bot.guild_id or member.bot:
            return
        verified = await self.bot.db.get_verified(member.id)
        if not verified:
            return
        role = await verified_role(self.bot.db, member.guild)
        if role and await give_role(member, role, "Previously verified"):
            log.info(f"Re-gave verified role to {member.id}")
        # Discord drops nicknames when someone leaves
        if verified["nickname"]:
            await set_nickname(member, verified["nickname"])


async def setup(bot: commands.Bot):
    await bot.add_cog(Verify(bot))
