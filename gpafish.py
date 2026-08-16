import os
import re
import random
import datetime
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from aiohttp import web

print("1. Starting script execution...")

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
TOWERSTATS_KEY = os.getenv('TOWERSTATS_KEY')

# Role ID restricted to running GD leaderboard commands
ALLOWED_ROLE_ID = 1538631317456035850

print(f"2. Token loaded: {'Yes' if TOKEN else 'NO - TOKEN IS MISSING!'}")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- IN-MEMORY DATA STORES ---
user_warnings = {}
# Format: {user_id: {"hardest": {"name": str, "rating": float}, "second": {"name": str, "rating": float}}}
gd_leaderboard_data = {}
# References to track the active leaderboard message for live updates
gd_leaderboard_msg_id = None
gd_leaderboard_channel_id = None

# Hardcoded EVW estimations for standard RobTop main levels
ROBTOP_LEVELS = {
    "stereo madness": 0.1, "back on track": 0.2, "polargeist": 0.4,
    "dry out": 0.6, "base after base": 0.8, "cant let go": 1.2,
    "jumper": 1.5, "time machine": 2.1, "cycles": 2.5,
    "xstep": 2.8, "clutterfunk": 3.5, "theory of everything": 3.8,
    "electroman adventures": 4.0, "clubstep": 10.2, "electrodynamix": 5.5,
    "hexagon force": 4.8, "blast processing": 3.0, "theory of everything 2": 10.8,
    "geometrical dominator": 4.2, "deadlocked": 11.5, "fingerdash": 4.5,
    "dash": 5.0
}

# --- DUMMY WEB SERVER FOR RENDER WEB SERVICE ---
async def handle_ping(request):
    return web.Response(text="Bot service is operational.")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server running on port {port}")

@bot.event
async def on_ready():
    bot.loop.create_task(start_web_server())
    
    print("3. Syncing application commands...")
    try:
        synced = await bot.tree.sync()
        print(f"Successfully synced {len(synced)} command(s) globally.")
    except Exception as e:
        print(f"Error syncing commands: {e}")
    print(f'Logged in as {bot.user}')

# Custom check to verify the required Role ID
def has_gd_role():
    async def predicate(ctx: commands.Context):
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            return False
        if any(role.id == ALLOWED_ROLE_ID for role in ctx.author.roles):
            return True
        await ctx.send("You do not have permission to use this command.", ephemeral=True)
        return False
    return commands.check(predicate)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Support for legacy !gdhardest, !gd2hardest, and !gddeleteboard text prefixes
    if message.content.startswith("!gdhardest"):
        ctx = await bot.get_context(message)
        if not any(role.id == ALLOWED_ROLE_ID for role in message.author.roles):
            await ctx.send("You do not have permission to use this command.")
            return

        parts = message.content.split(maxsplit=3)
        if len(parts) >= 4 and message.mentions:
            target_user = message.mentions[0]
            await gdhardest_logic(ctx, target_user, parts[2], parts[3])
        else:
            await ctx.send("Usage: `!gdhardest @User <demon/non-demon> <level_id_or_name>`")
        return

    elif message.content.startswith("!gd2hardest"):
        ctx = await bot.get_context(message)
        if not any(role.id == ALLOWED_ROLE_ID for role in message.author.roles):
            await ctx.send("You do not have permission to use this command.")
            return

        parts = message.content.split(maxsplit=3)
        if len(parts) >= 4 and message.mentions:
            target_user = message.mentions[0]
            await gd2hardest_logic(ctx, target_user, parts[2], parts[3])
        else:
            await ctx.send("Usage: `!gd2hardest @User <demon/non-demon> <level_id_or_name>`")
        return

    elif message.content.startswith("!gddeleteboard"):
        ctx = await bot.get_context(message)
        if not any(role.id == ALLOWED_ROLE_ID for role in message.author.roles):
            await ctx.send("You do not have permission to use this command.")
            return
        await gddeleteboard_logic(ctx)
        return

    await bot.process_commands(message)


# ==========================================
# --- GEOMETRY DASH LEADERBOARD LOGIC ---
# ==========================================

async def fetch_gddl_info(level_id: str):
    """Fetches level details from the GDDL (Geometry Dash Demon Ladder) API."""
    url = f"https://gdladder.com/api/level/{level_id}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    name = data.get("name", "Unknown Level")
                    rating = data.get("rating", 0.0)
                    return name, round(float(rating), 2)
        except Exception as e:
            print(f"[DEBUG] GDDL Fetch Error: {e}")
    return None, None

def render_gd_leaderboard_embed():
    embed = discord.Embed(
        title="Geometry Dash Leaderboard",
        description=(
            "Send your **top two** completions with the level name, and the "
            "**precise rating according to GDDL**.\n"
            "GDDL can be found here: https://gdladder.com/\n"
            "Non Demons will be **RobTop levels only** with difficulties that are estimated by EVW.\n"
            "*Example: Acu (20.26), Supersonic (16.86)*"
        ),
        color=discord.Color.dark_theme()
    )

    if not gd_leaderboard_data:
        embed.add_field(name="Leaderboard Empty", value="No scores submitted yet!", inline=False)
        return embed

    # Sort users by their highest rating, then second highest rating
    sorted_users = sorted(
        gd_leaderboard_data.items(),
        key=lambda x: (
            x[1].get("hardest", {}).get("rating", 0.0) if x[1].get("hardest") else 0.0,
            x[1].get("second", {}).get("rating", 0.0) if x[1].get("second") else 0.0
        ),
        reverse=True
    )

    lines = []
    medals = ["🥇", "🥈", "🥉"]

    for idx, (uid, data) in enumerate(sorted_users, start=1):
        h = data.get("hardest")
        s = data.get("second")

        h_str = f"{h['name']} ({h['rating']:.2f})" if h else "None"
        s_str = f"{s['name']} ({s['rating']:.2f})" if s else "None"

        rank_prefix = medals[idx - 1] if idx <= 3 else f"{idx}"
        lines.append(f"{rank_prefix} <@{uid}> {h_str}, {s_str}")

    embed.add_field(name="\u200b", value="\n".join(lines), inline=False)
    return embed

async def sync_or_create_leaderboard_message(ctx: commands.Context):
    """Sends or edits the persistent leaderboard message in the channel."""
    global gd_leaderboard_msg_id, gd_leaderboard_channel_id
    
    embed = render_gd_leaderboard_embed()

    # Try editing the existing leaderboard message if recorded
    if gd_leaderboard_msg_id and gd_leaderboard_channel_id:
        try:
            channel = ctx.guild.get_channel(gd_leaderboard_channel_id) or await bot.fetch_channel(gd_leaderboard_channel_id)
            msg = await channel.fetch_message(gd_leaderboard_msg_id)
            await msg.edit(embed=embed)
            return
        except Exception as e:
            print(f"[DEBUG] Could not edit existing leaderboard message: {e}")

    # If message does not exist or fetch failed, create a new one
    msg = await ctx.send(embed=embed)
    gd_leaderboard_msg_id = msg.id
    gd_leaderboard_channel_id = msg.channel.id

async def gdhardest_logic(ctx: commands.Context, target_user: discord.Member, level_type: str, level_input: str):
    user_id = target_user.id
    l_type = level_type.lower()
    name, rating = None, None

    if l_type == "demon":
        name, rating = await fetch_gddl_info(level_input)
        if not name or rating is None:
            await ctx.send(f"Could not find a valid GDDL demon entry for ID `{level_input}`.", delete_after=5)
            return
    elif l_type in ["non-demon", "nondemon"]:
        clean_input = level_input.lower().strip()
        if clean_input in ROBTOP_LEVELS:
            name = level_input.title()
            rating = ROBTOP_LEVELS[clean_input]
        else:
            name = level_input.title()
            rating = 0.0
    else:
        await ctx.send("Type must be either `demon` or `non-demon`.", delete_after=5)
        return

    if user_id not in gd_leaderboard_data:
        gd_leaderboard_data[user_id] = {"hardest": None, "second": None}

    old_hardest = gd_leaderboard_data[user_id]["hardest"]
    
    # Push original hardest to second hardest if setting a new record
    if old_hardest:
        gd_leaderboard_data[user_id]["second"] = old_hardest

    gd_leaderboard_data[user_id]["hardest"] = {"name": name, "rating": rating}

    await sync_or_create_leaderboard_message(ctx)
    await ctx.send(f"Updated **#1 Hardest** for {target_user.mention} to **{name} ({rating:.2f})**!", delete_after=5)

async def gd2hardest_logic(ctx: commands.Context, target_user: discord.Member, level_type: str, level_input: str):
    user_id = target_user.id
    l_type = level_type.lower()
    name, rating = None, None

    if l_type == "demon":
        name, rating = await fetch_gddl_info(level_input)
        if not name or rating is None:
            await ctx.send(f"Could not find a valid GDDL demon entry for ID `{level_input}`.", delete_after=5)
            return
    elif l_type in ["non-demon", "nondemon"]:
        clean_input = level_input.lower().strip()
        if clean_input in ROBTOP_LEVELS:
            name = level_input.title()
            rating = ROBTOP_LEVELS[clean_input]
        else:
            name = level_input.title()
            rating = 0.0
    else:
        await ctx.send("Type must be either `demon` or `non-demon`.", delete_after=5)
        return

    if user_id not in gd_leaderboard_data:
        gd_leaderboard_data[user_id] = {"hardest": None, "second": None}

    gd_leaderboard_data[user_id]["second"] = {"name": name, "rating": rating}

    await sync_or_create_leaderboard_message(ctx)
    await ctx.send(f"Updated **#2 Hardest** for {target_user.mention} to **{name} ({rating:.2f})**!", delete_after=5)

async def gddeleteboard_logic(ctx: commands.Context):
    global gd_leaderboard_msg_id, gd_leaderboard_channel_id, gd_leaderboard_data
    
    # Delete the leaderboard message from channel if it exists
    if gd_leaderboard_msg_id and gd_leaderboard_channel_id:
        try:
            channel = ctx.guild.get_channel(gd_leaderboard_channel_id) or await bot.fetch_channel(gd_leaderboard_channel_id)
            msg = await channel.fetch_message(gd_leaderboard_msg_id)
            await msg.delete()
        except Exception as e:
            print(f"[DEBUG] Error deleting leaderboard message: {e}")

    gd_leaderboard_data.clear()
    gd_leaderboard_msg_id = None
    gd_leaderboard_channel_id = None

    await ctx.send("Leaderboard data and message have been deleted.")


@bot.hybrid_command(name="gdhardest", description="Updates a user's #1 hardest GD level completion.")
@has_gd_role()
@app_commands.choices(level_type=[
    app_commands.Choice(name="Demon", value="demon"),
    app_commands.Choice(name="Non-Demon", value="non-demon")
])
async def gdhardest(ctx: commands.Context, target_user: discord.Member, level_type: str, level_id: str):
    await gdhardest_logic(ctx, target_user, level_type, level_id)

@bot.hybrid_command(name="gd2hardest", description="Updates a user's #2 hardest GD level completion.")
@has_gd_role()
@app_commands.choices(level_type=[
    app_commands.Choice(name="Demon", value="demon"),
    app_commands.Choice(name="Non-Demon", value="non-demon")
])
async def gd2hardest(ctx: commands.Context, target_user: discord.Member, level_type: str, level_id: str):
    await gd2hardest_logic(ctx, target_user, level_type, level_id)

@bot.hybrid_command(name="gddeleteboard", description="Deletes the leaderboard data and message.")
@has_gd_role()
async def gddeleteboard(ctx: commands.Context):
    await gddeleteboard_logic(ctx)


# ==========================================
# --- MODERATION COMMANDS ---
# ==========================================

@bot.hybrid_command(name="kick", description="Kicks a member from the server.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def kick(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
    if member == ctx.author:
        await ctx.send("Action failed: You cannot kick yourself.")
        return
    await member.kick(reason=reason)
    await ctx.send(f"**{member.display_name}** has been kicked from the server. | Reason: {reason}")

@bot.hybrid_command(name="ban", description="Bans a member from the server.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def ban(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
    if member == ctx.author:
        await ctx.send("Action failed: You cannot ban yourself.")
        return
    await member.ban(reason=reason)
    await ctx.send(f"**{member.display_name}** has been permanently banned. | Reason: {reason}")

@bot.hybrid_command(name="unban", description="Unbans a user by their User ID.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def unban(ctx: commands.Context, user_id: str, *, reason: str = "No reason provided."):
    try:
        user = await bot.fetch_user(int(user_id))
        await ctx.guild.unban(user, reason=reason)
        await ctx.send(f"Successfully unbanned **{user.name}**.")
    except Exception:
        await ctx.send("Failed to unban user. Check the ID and try again.")

@bot.hybrid_command(name="softban", description="Bans and unbans a user to clear their recent messages.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def softban(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
    if member == ctx.author:
        await ctx.send("Action failed: You cannot softban yourself.")
        return
    await member.ban(reason=reason, delete_message_days=7)
    await ctx.guild.unban(member, reason="Softban completion.")
    await ctx.send(f"**{member.display_name}** has been softbanned (messages purged). | Reason: {reason}")

@bot.hybrid_command(name="massban", description="Bans multiple user IDs at once.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def massban(ctx: commands.Context, user_ids: str, *, reason: str = "Massban initiated."):
    ids = user_ids.split()
    banned_count = 0
    for uid in ids:
        try:
            user = await bot.fetch_user(int(uid))
            await ctx.guild.ban(user, reason=reason)
            banned_count += 1
        except Exception:
            continue
    await ctx.send(f"Massban action complete. Banned **{banned_count}** user(s).")

@bot.hybrid_command(name="warn", description="Issues a formal warning to a member.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def warn(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
    key = (ctx.guild.id, member.id)
    if key not in user_warnings:
        user_warnings[key] = []
    user_warnings[key].append(reason)
    try:
        await member.send(f"You were warned in **{ctx.guild.name}** | Reason: {reason}")
    except discord.Forbidden:
        pass
    await ctx.send(f"{member.mention} was warned.")

@bot.hybrid_command(name="warnings", aliases=["warns"], description="Displays warning logs for a member.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def warnings(ctx: commands.Context, member: discord.Member = None):
    target = member or ctx.author
    key = (ctx.guild.id, target.id)
    warns = user_warnings.get(key, [])
    if not warns:
        await ctx.send(f"**{target.display_name}** has no active warnings.")
        return
    warn_list = "\n".join([f"{i+1}. {r}" for i, r in enumerate(warns)])
    await ctx.send(f"Warning records for **{target.display_name}** ({len(warns)} total):\n{warn_list}")

@bot.hybrid_command(name="warnremove", description="Removes a specific warning by its index number.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def warnremove(ctx: commands.Context, member: discord.Member, index: int):
    key = (ctx.guild.id, member.id)
    warns = user_warnings.get(key, [])
    if not warns or index < 1 or index > len(warns):
        await ctx.send(f"Invalid warning index for **{member.display_name}**.")
        return
    removed_reason = user_warnings[key].pop(index - 1)
    await ctx.send(f"Removed warning #{index} (`{removed_reason}`) from **{member.display_name}**.")

@bot.hybrid_command(name="clearwarns", description="Clears all recorded warnings for a member.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def clearwarns(ctx: commands.Context, member: discord.Member):
    key = (ctx.guild.id, member.id)
    if key in user_warnings:
        del user_warnings[key]
        await ctx.send(f"Cleared all warnings for **{member.display_name}**.")
    else:
        await ctx.send(f"**{member.display_name}** has no warnings to clear.")

@bot.hybrid_command(name="timeout", description="Applies a timeout to a member for a specified duration.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def timeout(ctx: commands.Context, member: discord.Member, minutes: int, *, reason: str = "No reason provided."):
    if member == ctx.author:
        await ctx.send("Action failed: You cannot timeout yourself.")
        return
    duration = datetime.timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    await ctx.send(f"**{member.display_name}** has been timed out for {minutes} minute(s). | Reason: {reason}")

@bot.hybrid_command(name="untimeout", description="Removes a timeout from a member.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def untimeout(ctx: commands.Context, member: discord.Member):
    await member.timeout(None)
    await ctx.send(f"Removed timeout for **{member.display_name}**.")

@bot.hybrid_command(name="mute", description="Mutes a member for a specified duration in minutes.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def mute(ctx: commands.Context, member: discord.Member, minutes: int = 10, *, reason: str = "No reason provided."):
    duration = datetime.timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    await ctx.send(f"**{member.display_name}** has been muted for {minutes} minutes. | Reason: {reason}")

@bot.hybrid_command(name="unmute", description="Unmutes a member.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def unmute(ctx: commands.Context, member: discord.Member):
    await member.timeout(None)
    await ctx.send(f"**{member.display_name}** has been unmuted.")

@bot.hybrid_command(name="deafen", description="Server deafens a member in voice chat.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def deafen(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
    if not member.voice or not member.voice.channel:
        await ctx.send(f"**{member.display_name}** is not currently in a voice channel.")
        return
    await member.edit(deafen=True, reason=reason)
    await ctx.send(f"**{member.display_name}** has been deafened in voice channels.")

@bot.hybrid_command(name="undeafen", description="Undeafens a member in voice chat.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def undeafen(ctx: commands.Context, member: discord.Member):
    if not member.voice or not member.voice.channel:
        await ctx.send(f"**{member.display_name}** is not currently in a voice channel.")
        return
    await member.edit(deafen=False)
    await ctx.send(f"**{member.display_name}** is no longer deafened.")

@bot.hybrid_command(name="purge", description="Deletes a specified number of recent messages.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def purge(ctx: commands.Context, amount: int):
    if amount < 1 or amount > 100:
        await ctx.send("Please specify a range between 1 and 100.")
        return
    deleted = await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"Successfully purged **{len(deleted)-1}** message(s).", delete_after=4)

@bot.hybrid_command(name="slowmode", description="Sets slowmode delay for the current channel in seconds.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def slowmode(ctx: commands.Context, seconds: int):
    if seconds < 0 or seconds > 21600:
        await ctx.send("Slowmode must be set between 0 and 21600 seconds (6 hours).")
        return
    await ctx.channel.edit(slowmode_delay=seconds)
    if seconds == 0:
        await ctx.send("Slowmode has been disabled for this channel.")
    else:
        await ctx.send(f"Channel slowmode set to **{seconds}** second(s).")

@bot.hybrid_command(name="slowmodeoff", description="Disables slowmode in the current channel.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def slowmodeoff(ctx: commands.Context):
    await ctx.channel.edit(slowmode_delay=0)
    await ctx.send("Slowmode disabled for this channel.")

@bot.hybrid_command(name="lock", description="Locks down the current text channel.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def lock(ctx: commands.Context):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send("Channel has been locked.")

@bot.hybrid_command(name="unlock", description="Unlocks the current text channel.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def unlock(ctx: commands.Context):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send("Channel has been unlocked.")

@bot.hybrid_command(name="lockall", description="Locks all text channels in the server.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def lockall(ctx: commands.Context):
    for channel in ctx.guild.text_channels:
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        try:
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        except Exception:
            continue
    await ctx.send("Locked all accessible text channels.")

@bot.hybrid_command(name="unlockall", description="Unlocks all text channels in the server.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def unlockall(ctx: commands.Context):
    for channel in ctx.guild.text_channels:
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        try:
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        except Exception:
            continue
    await ctx.send("Unlocked all accessible text channels.")

@bot.hybrid_command(name="nickname", aliases=["nick"], description="Changes a member's server nickname.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def nickname(ctx: commands.Context, member: discord.Member, *, nickname: str = None):
    await member.edit(nick=nickname)
    if nickname:
        await ctx.send(f"Updated **{member.name}**'s nickname to **{nickname}**.")
    else:
        await ctx.send(f"Reset **{member.name}**'s nickname.")

@bot.hybrid_command(name="nickreset", description="Resets a member's nickname to default.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def nickreset(ctx: commands.Context, member: discord.Member):
    await member.edit(nick=None)
    await ctx.send(f"Reset nickname for **{member.name}**.")

@bot.hybrid_command(name="roleadd", description="Gives a role to a member.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def roleadd(ctx: commands.Context, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await ctx.send(f"Added role **{role.name}** to **{member.display_name}**.")

@bot.hybrid_command(name="roleremove", description="Removes a role from a member.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def roleremove(ctx: commands.Context, member: discord.Member, role: discord.Role):
    await member.remove_roles(role)
    await ctx.send(f"Removed role **{role.name}** from **{member.display_name}**.")

@bot.hybrid_command(name="pin", description="Pins a specific message by Message ID.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def pin(ctx: commands.Context, message_id: str):
    try:
        msg = await ctx.channel.fetch_message(int(message_id))
        await msg.pin()
        await ctx.send("Message successfully pinned.")
    except Exception:
        await ctx.send("Could not locate or pin that message ID in this channel.")

@bot.hybrid_command(name="vckick", description="Disconnects a member from voice chat.")
@commands.guild_only()
@commands.has_any_role("Moderator", "Admin")
async def vckick(ctx: commands.Context, member: discord.Member):
    if not member.voice or not member.voice.channel:
        await ctx.send(f"**{member.display_name}** is not in a voice channel.")
        return
    await member.move_to(None)
    await ctx.send(f"Disconnected **{member.display_name}** from voice chat.")


# ==========================================
# --- FUN & UTILITY COMMANDS ---
# ==========================================

@bot.hybrid_command(name="ping", description="Checks the latency of the bot.")
async def ping(ctx: commands.Context):
    latency = round(bot.latency * 1000)
    await ctx.send(f"Pong! Response latency: **{latency}ms**")

@bot.hybrid_command(name="botinfo", description="Displays details about the bot runtime.")
async def botinfo(ctx: commands.Context):
    embed = discord.Embed(title="Bot Statistics", color=discord.Color.green())
    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="Servers Joined", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Library", value=f"discord.py v{discord.__version__}", inline=True)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="userinfo", description="Displays detailed profile information about a member.")
@commands.guild_only()
async def userinfo(ctx: commands.Context, member: discord.Member = None):
    target = member or ctx.author
    roles = [role.mention for role in target.roles if role.name != "@everyone"]
    embed = discord.Embed(
        title=f"User Information - {target.display_name}",
        color=target.color
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Username", value=str(target), inline=True)
    embed.add_field(name="User ID", value=target.id, inline=True)
    embed.add_field(name="Joined Server", value=target.joined_at.strftime("%b %d, %Y"), inline=False)
    embed.add_field(name="Account Created", value=target.created_at.strftime("%b %d, %Y"), inline=False)
    embed.add_field(name=f"Roles [{len(roles)}]", value=", ".join(roles) if roles else "None", inline=False)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="serverinfo", description="Displays information about the server.")
@commands.guild_only()
async def serverinfo(ctx: commands.Context):
    guild = ctx.guild
    embed = discord.Embed(
        title=f"Server Overview - {guild.name}",
        color=discord.Color.blue()
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Server ID", value=guild.id, inline=True)
    embed.add_field(name="Owner", value=str(guild.owner), inline=True)
    embed.add_field(name="Members", value=guild.member_count, inline=True)
    embed.add_field(name="Text Channels", value=len(guild.text_channels), inline=True)
    embed.add_field(name="Voice Channels", value=len(guild.voice_channels), inline=True)
    embed.add_field(name="Roles", value=len(guild.roles), inline=True)
    embed.add_field(name="Created On", value=guild.created_at.strftime("%b %d, %Y"), inline=False)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="roleinfo", description="Shows information about a specific server role.")
@commands.guild_only()
async def roleinfo(ctx: commands.Context, role: discord.Role):
    embed = discord.Embed(title=f"Role Info - {role.name}", color=role.color)
    embed.add_field(name="Role ID", value=role.id, inline=True)
    embed.add_field(name="Members Count", value=len(role.members), inline=True)
    embed.add_field(name="Hoisted", value=role.hoist, inline=True)
    embed.add_field(name="Created At", value=role.created_at.strftime("%b %d, %Y"), inline=False)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="channelinfo", description="Shows information about the current text channel.")
@commands.guild_only()
async def channelinfo(ctx: commands.Context):
    ch = ctx.channel
    embed = discord.Embed(title=f"Channel Info - #{ch.name}", color=discord.Color.blue())
    embed.add_field(name="Channel ID", value=ch.id, inline=True)
    embed.add_field(name="Category", value=ch.category.name if ch.category else "None", inline=True)
    embed.add_field(name="Slowmode", value=f"{ch.slowmode_delay}s", inline=True)
    embed.add_field(name="Created On", value=ch.created_at.strftime("%b %d, %Y"), inline=False)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="emojis", description="Lists custom emojis in this server.")
@commands.guild_only()
async def emojis(ctx: commands.Context):
    emojis_list = [str(e) for e in ctx.guild.emojis]
    if not emojis_list:
        await ctx.send("This server has no custom emojis.")
        return
    output = " ".join(emojis_list[:50])
    await ctx.send(f"Custom Server Emojis ({len(ctx.guild.emojis)} total):\n{output}")

@bot.hybrid_command(name="avatar", aliases=["av"], description="Fetches a high-resolution display avatar.")
async def avatar(ctx: commands.Context, member: discord.Member = None):
    target = member or ctx.author
    embed = discord.Embed(
        title=f"Avatar - {target.display_name}",
        color=target.color
    )
    embed.set_image(url=target.display_avatar.url)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="avataruser", description="Fetches user avatar by User ID.")
async def avataruser(ctx: commands.Context, user_id: str):
    try:
        user = await bot.fetch_user(int(user_id))
        embed = discord.Embed(title=f"Avatar - {user.name}")
        embed.set_image(url=user.display_avatar.url)
        await ctx.send(embed=embed)
    except Exception:
        await ctx.send("Could not fetch user by ID.")

@bot.hybrid_command(name="8ball", description="Ask a question and receive a magic 8-ball answer.")
async def eightball(ctx: commands.Context, *, question: str):
    responses = [
        "It is certain.", "Without a doubt.", "You may rely on it.",
        "Most likely.", "Outlook good.", "Signs point to yes.",
        "Reply hazy, try again.", "Ask again later.", "Better not tell you now.",
        "Don't count on it.", "My reply is no.", "Outlook not so good."
    ]
    answer = random.choice(responses)
    embed = discord.Embed(title="Magic 8-Ball", color=discord.Color.dark_purple())
    embed.add_field(name="Question", value=question, inline=False)
    embed.add_field(name="Answer", value=answer, inline=False)
    await ctx.send(embed=embed)

@bot.hybrid_command(name="coinflip", aliases=["flip"], description="Flips a coin.")
async def coinflip(ctx: commands.Context):
    outcome = random.choice(["Heads", "Tails"])
    await ctx.send(f"Coin flip result: **{outcome}**")

@bot.hybrid_command(name="roll", description="Rolls a standard 6-sided die or specify custom sides.")
async def roll(ctx: commands.Context, sides: int = 6):
    if sides < 2:
        await ctx.send("Number of sides must be at least 2.")
        return
    result = random.randint(1, sides)
    await ctx.send(f"Rolled a d{sides}: **{result}**")

@bot.hybrid_command(name="dice", description="Roll multiple dice using standard notation (e.g. 2d20).")
async def dice(ctx: commands.Context, notation: str):
    match = re.match(r'^(\d+)d(\d+)$', notation.lower().strip())
    if not match:
        await ctx.send("Format must be in dice notation like `!dice 2d6` or `!dice 1d20`.")
        return
    count, sides = int(match.group(1)), int(match.group(2))
    if count > 20 or sides > 100 or count < 1 or sides < 2:
        await ctx.send("Limit: 1-20 dice, 2-100 sides.")
        return
    rolls = [random.randint(1, sides) for _ in range(count)]
    await ctx.send(f"Rolled `{notation}`: **{rolls}** (Total: **{sum(rolls)}**)")

@bot.hybrid_command(name="rps", description="Play Rock, Paper, Scissors against the bot.")
async def rps(ctx: commands.Context, choice: str):
    user_choice = choice.lower().strip()
    valid = ["rock", "paper", "scissors"]
    if user_choice not in valid:
        await ctx.send("Please choose: `rock`, `paper`, or `scissors`.")
        return
    bot_choice = random.choice(valid)
    if user_choice == bot_choice:
        res = "It's a tie!"
    elif (user_choice == "rock" and bot_choice == "scissors") or \
         (user_choice == "paper" and bot_choice == "rock") or \
         (user_choice == "scissors" and bot_choice == "paper"):
        res = "You win!"
    else:
        res = "I win!"
    await ctx.send(f"You chose **{user_choice}**, I chose **{bot_choice}**. {res}")

@bot.hybrid_command(name="poll", description="Creates a quick reaction poll.")
@commands.guild_only()
async def poll(ctx: commands.Context, *, question: str):
    embed = discord.Embed(title="Poll", description=question, color=discord.Color.blue())
    embed.set_footer(text=f"Initiated by {ctx.author.display_name}")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("👍")
    await msg.add_reaction("👎")

@bot.hybrid_command(name="rate", description="Rates anything on a scale from 0 to 10.")
async def rate(ctx: commands.Context, *, subject: str):
    rating = random.randint(0, 10)
    await ctx.send(f"I would rate **{subject}** a **{rating}/10**.")

@bot.hybrid_command(name="choose", description="Picks a random item from choices separated by commas.")
async def choose(ctx: commands.Context, *, choices: str):
    options = [opt.strip() for opt in choices.split(",") if opt.strip()]
    if len(options) < 2:
        await ctx.send("Please provide at least two choices separated by commas. (e.g. `!choose Pizza, Burgers`)")
        return
    selection = random.choice(options)
    await ctx.send(f"I choose: **{selection}**")

@bot.hybrid_command(name="ship", description="Calculates compatibility between two users.")
async def ship(ctx: commands.Context, user1: discord.Member, user2: discord.Member = None):
    target2 = user2 or ctx.author
    score = random.randint(0, 100)
    await ctx.send(f"Compatibility rating between **{user1.display_name}** and **{target2.display_name}**: **{score}%**")

@bot.hybrid_command(name="roast", description="Delivers a friendly roast.")
async def roast(ctx: commands.Context, member: discord.Member = None):
    target = member or ctx.author
    roasts = [
        "Is your brain on low power mode?",
        "I'd agree with you, but then we'd both be wrong.",
        "You're like a cloud. When you disappear, it's a beautiful day.",
        "You're proof that even mistakes can make it far."
    ]
    await ctx.send(f"{target.mention}, {random.choice(roasts)}")

@bot.hybrid_command(name="calc", description="Performs basic calculation (+, -, *, /).")
async def calc(ctx: commands.Context, expression: str):
    try:
        clean_expr = re.sub(r'[^0-9\+\-\*\/\.\(\)\s]', '', expression)
        result = eval(clean_expr, {"__builtins__": None}, {})
        await ctx.send(f"Result: `{result}`")
    except Exception:
        await ctx.send("Invalid mathematical expression.")

@bot.hybrid_command(name="reverse", description="Reverses text.")
async def reverse(ctx: commands.Context, *, text: str):
    await ctx.send(text[::-1])

@bot.hybrid_command(name="say", description="Makes the bot repeat text.")
async def say(ctx: commands.Context, *, message: str):
    await ctx.send(message)


# ==========================================
# --- TOWERSTATS COMMAND ---
# ==========================================

@bot.hybrid_command(name="towerstats", description="Retrieves Roblox tower statistics.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def towerstats(ctx: commands.Context, game_acronym: str, roblox_username: str):
    acronym = game_acronym.lower()
    if acronym != "etoh":
        await ctx.send(f"Game `{game_acronym}` is not configured yet. Currently supported: `etoh`")
        return

    await ctx.defer()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    }

    async with aiohttp.ClientSession() as session:
        roblox_api_url = "https://users.roblox.com/v1/usernames/users"
        roblox_payload = {"usernames": [roblox_username], "excludeBannedUsers": False}
        real_username = roblox_username
        user_id = None

        try:
            async with session.post(roblox_api_url, json=roblox_payload, timeout=5) as r_resp:
                if r_resp.status == 200:
                    r_data = await r_resp.json()
                    if r_data.get("data"):
                        user_info = r_data["data"][0]
                        real_username = user_info.get("name", roblox_username)
                        user_id = user_info.get("id")
        except Exception as e:
            print(f"[DEBUG] Roblox lookup error: {e}")

        if not user_id:
            await ctx.send(f"Could not find a Roblox account named `{roblox_username}`.")
            return

        towerstats_url = f"https://www.towerstats.com/etoh?username={real_username}"
        hardest = None
        completed_count = None

        try:
            async with session.get(towerstats_url, headers=headers, timeout=8) as page_resp:
                if page_resp.status == 200:
                    html_text = await page_resp.text()
                    count_match = re.search(r'(\d+)\s*/\s*(\d+)', html_text)
                    if count_match:
                        completed_count = count_match.group(1)

                    hardest_match = re.search(r'Hardest:?\s*<[^>]+>([^<]+)<', html_text, re.IGNORECASE)
                    if not hardest_match:
                        hardest_match = re.search(r'Hardest Tower:?\s*([A-Za-z0-9_ ]+)', html_text, re.IGNORECASE)

                    if hardest_match:
                        hardest = hardest_match.group(1).strip()
        except Exception as page_err:
            print(f"[DEBUG] TowerStats HTML scrape error: {page_err}")

    display_completed = f"{completed_count}/410" if completed_count is not None else "N/A"
    display_hardest = hardest if hardest else "None"

    embed = discord.Embed(
        title=f"{real_username}'s EToH Towerstats",
        url=towerstats_url,
        color=discord.Color.blue()
    )
    embed.add_field(name="Hardest Completed", value=f"**{display_hardest}**", inline=False)
    embed.add_field(name="Towers Completed", value=f"**{display_completed}**", inline=False)
    embed.set_footer(text="Game: Eternal Towers of Hell")

    await ctx.send(embed=embed)


# ==========================================
# --- MEME DATA STORES & COMMANDS ---
# ==========================================

GD_MEMES = [
    {
        "title": "FIRE IN THE HOLE!",
        "desc": "POV: You opened the 2.2 level editor for 5 seconds.",
        "image": "https://i.ytimg.com/vi/7xQ23zshA5I/hqdefault.jpg",
        "footer": "Normal Face approved this message."
    },
    {
        "title": "Is this section sightreadable enough?",
        "desc": "GD players when a level has 0.001 seconds of unsightreadable gameplay.",
        "image": "https://i.ytimg.com/vi/3N2S4A1Xb_0/hqdefault.jpg",
        "footer": "Dash spider jumpscare incoming..."
    },
    {
        "title": "CONGREGATION JUMPSCARE",
        "desc": "You think you're watching a normal layout... and then the drop hits.",
        "image": "https://i.ytimg.com/vi/aL3pPzX_G6w/hqdefault.jpg",
        "footer": "0% -> 100% in pitch darkness."
    },
    {
        "title": "DEVIL VORTEX SAWS",
        "desc": "Legend has it there are still 54 extra saws floating above the level.",
        "image": "https://i.ytimg.com/vi/q_xJ1W9e_90/hqdefault.jpg",
        "footer": "100% legit, totally auto-verified."
    },
    {
        "title": "Tidal Wave at 99%",
        "desc": "The longest 2 seconds of your entire life.",
        "image": "https://i.ytimg.com/vi/zaRxbC3m7HM/hqdefault.jpg",
        "footer": "It's blue. Very blue."
    },
    {
        "title": "WATER ON THE HILL!",
        "desc": "Logistics in 2.2 geometry dash levels be like:",
        "image": "https://i.ytimg.com/vi/gISWceDeGxc/hqdefault.jpg",
        "footer": "Lobotomy Dash strikes again."
    },
    {
        "title": "ROCK IN THE GROUND!",
        "desc": "When the sound effects kick in and your headphones explode.",
        "image": "https://i.ytimg.com/vi/WPhv8wPuwMo/hqdefault.jpg",
        "footer": "Ear damage guaranteed."
    },
    {
        "title": "Practice Mode 99% (1,482 Attempts)",
        "desc": "'Just gotta fix my consistency bro, I swear I can beat it in normal mode.'",
        "image": "https://i.ytimg.com/vi/ardXWyk_jO8/hqdefault.jpg",
        "footer": "Narrator: He could not beat it in normal mode."
    }
]

ETOH_MEMES = [
    {
        "title": "Falling on Floor 10 of a Remorseless Tower",
        "desc": "POV: You spent 45 minutes climbing and missed a 0.5 stud jump.",
        "image": "https://i.ytimg.com/vi/ATAyZGDoyRk/hqdefault.jpg",
        "footer": "Welcome back to Floor 1!"
    },
    {
        "title": "Tower of Screen Punching (ToSP)",
        "desc": "The tower isn't hard, your mental health just drops by 90% per floor.",
        "image": "https://static.wikia.nocookie.net/jtoh/images/b/b5/Tower_of_Hecc_thumbnail.png",
        "footer": "Keyboard warranty sold separately."
    },
    {
        "title": "Average Ring 1 Lobby Enjoyer",
        "desc": "Standing near Tower of Hecc pretending you're going to beat it today.",
        "image": "https://static.wikia.nocookie.net/jtoh/images/2/22/Ring_1_Revamp.png",
        "footer": "Ring 1 never changes."
    },
    {
        "title": "Tower of Getting Gnomed",
        "desc": "When you think you hit the win pad but it was a trap button.",
        "image": "https://static.wikia.nocookie.net/jtoh/images/0/07/Tower_of_Getting_Gnomed_game_icon.png",
        "footer": "You've been gnomed!"
    },
    {
        "title": "'Just One Quick Tower Before Bed'",
        "desc": "3 hours later: You're stuck on Floor 7 of a Steep difficulty tower questioning life.",
        "image": "https://static.wikia.nocookie.net/jtoh/images/2/23/R6_lobby_revamp_3.png",
        "footer": "Sleep is for the weak."
    },
    {
        "title": "Frame-Precise Truss Flick",
        "desc": "Roblox physics engine deciding whether to fling you into outer space or work properly.",
        "image": "https://i.ytimg.com/vi/pr3P0ZxVJ8I/hqdefault.jpg",
        "footer": "Shift lock active."
    }
]

@bot.hybrid_command(name="gdmemes", description="Pulls up a Geometry Dash meme.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def gdmemes(ctx: commands.Context):
    meme = random.choice(GD_MEMES)
    embed = discord.Embed(
        title=meme["title"],
        description=meme["desc"],
        color=discord.Color.from_rgb(0, 255, 127)
    )
    embed.set_image(url=meme["image"])
    embed.set_footer(text=meme["footer"])
    await ctx.send(embed=embed)

@bot.hybrid_command(name="etohmemes", description="Pulls up an Eternal Towers of Hell meme.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def etohmemes(ctx: commands.Context):
    meme = random.choice(ETOH_MEMES)
    embed = discord.Embed(
        title=meme["title"],
        description=meme["desc"],
        color=discord.Color.from_rgb(186, 85, 211)
    )
    embed.set_image(url=meme["image"])
    embed.set_footer(text=meme["footer"])
    await ctx.send(embed=embed)


# ==========================================
# --- ERROR HANDLING ---
# ==========================================

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingRole) or isinstance(error, commands.MissingAnyRole) or isinstance(error, commands.CheckFailure):
        await ctx.send("You do not have permission to use this command.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have the required permissions to execute this command.")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send("I do not have the required server permissions to complete this action.")

# ==========================================
# --- RUN BOT ---
# ==========================================

if TOKEN:
    print("4. Connecting to Discord...")
    bot.run(TOKEN)
else:
    print("TOKEN missing! Check your .env file or environment variables.")
