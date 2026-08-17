import os
import json
import urllib.parse
import threading
from flask import Flask
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# --- Web Server for Render Health Checks ---
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return "Bot is alive!", 200

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

# Start web server in a background thread so Render detects an open port
threading.Thread(target=run_web_server, daemon=True).start()

# --- Bot Initialization & Configuration ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

ALLOWED_ROLE_ID = 123456789012345678      # Replace with your actual Moderator/GD Role ID
LEADERBOARD_CHANNEL_ID = 123456789012345678  # Replace with your actual Leaderboard Channel ID
DATA_FILE = "gd_leaderboard.json"

gd_leaderboard_data = {}

# EVW / Community Tier Ratings for Main RobTop Levels
ROBTOP_LEVELS = {
    "stereo madness": 0.10, "back on track": 0.20, "polargeist": 0.30,
    "dry out": 0.40, "base after base": 0.50, "cant let go": 0.60,
    "jumper": 0.70, "time machine": 0.80, "cycles": 0.90,
    "xstep": 1.00, "clutterfunk": 1.20, "theory of everything": 1.10,
    "electroman adventures": 1.15, "electrodynamix": 1.60, "hexagon force": 1.40,
    "blast processing": 0.85, "geometrical dominator": 1.05, "fingerdash": 1.30,
    "dash": 1.25, "clubstep": 2.10, "theory of everything 2": 2.40,
    "deadlocked": 2.80
}


# --- Persistence Helpers ---
def load_data():
    global gd_leaderboard_data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            try:
                raw_data = json.load(f)
                gd_leaderboard_data = {int(k): v for k, v in raw_data.items()}
                print(f"[DATA] Loaded {len(gd_leaderboard_data)} leaderboard record(s).")
            except Exception as e:
                print(f"[ERROR] Failed to load data file: {e}")
                gd_leaderboard_data = {}
    else:
        gd_leaderboard_data = {}


def save_data():
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(gd_leaderboard_data, f, indent=4)
        print("[DATA] Saved updated leaderboard data.")
    except Exception as e:
        print(f"[ERROR] Failed to save data: {e}")


def has_gd_role():
    async def predicate(interaction: discord.Interaction) -> bool:
        if any(role.id == ALLOWED_ROLE_ID for role in interaction.user.roles):
            return True
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return False

    return app_commands.check(predicate)


# --- API Fetcher Engine ---
async def fetch_gddl_info(level_input: str):
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession(headers=headers) as session:
        # Strategy A: Level ID Lookup
        if level_input.isdigit():
            level_id = level_input
            gddl_url = f"https://gdladder.com/api/level/{level_id}"
            name, rating = None, 0.0

            try:
                async with session.get(gddl_url, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, dict):
                            name = data.get("Name") or data.get("name")
                            raw_tier = data.get("Rating") or data.get("Tier") or data.get("rating")
                            if raw_tier is not None:
                                rating = float(raw_tier)
            except Exception as e:
                print(f"[API WARN] GDDL ID Lookup failed: {e}")

            if not name:
                try:
                    gd_url = f"https://gdbrowser.com/api/level/{level_id}"
                    async with session.get(gd_url, timeout=5) as resp:
                        if resp.status == 200:
                            gd_data = await resp.json()
                            name = gd_data.get("name", f"Level {level_id}")
                except Exception as e:
                    print(f"[API WARN] GDBrowser ID Lookup failed: {e}")
                    name = f"Level {level_id}"

            return name, round(rating, 2)

        # Strategy B: Search Level by Name
        search_url = f"https://gdladder.com/api/level/search?name={urllib.parse.quote(level_input)}"
        try:
            async with session.get(search_url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data if isinstance(data, list) else data.get("levels", [])
                    if results:
                        first = results[0]
                        name = first.get("Name") or first.get("name") or level_input.title()
                        raw_tier = first.get("Rating") or first.get("Tier") or first.get("rating")
                        rating = float(raw_tier) if raw_tier is not None else 0.0
                        return name, round(rating, 2)
        except Exception as e:
            print(f"[API WARN] GDDL Search failed: {e}")

        # Strategy C: GDBrowser Fallback Search
        try:
            gd_search_url = f"https://gdbrowser.com/api/search/{urllib.parse.quote(level_input)}"
            async with session.get(gd_search_url, timeout=5) as resp:
                if resp.status == 200:
                    gd_results = await resp.json()
                    if isinstance(gd_results, list) and len(gd_results) > 0:
                        return gd_results[0].get("name", level_input.title()), 0.0
        except Exception as e:
            print(f"[API WARN] GDBrowser Search failed: {e}")

    return level_input.title(), 0.0


# --- Leaderboard Rendering ---
def build_leaderboard_content():
    # Header instructions matching exact format
    header = (
        "**Geometry Dash Leaderboard**\n"
        "Send your *top two* completions with the level name, and the **precise rating according to GDDL**.\n"
        "GDDL can be found here: https://gdladder.com/\n"
        "Non Demons will be **RobTop levels only** with difficulties that are estimated by EVW.\n"
        "Example: Acu (20.26), Supersonic (16.86)\n\n"
    )

    sorted_users = sorted(
        gd_leaderboard_data.items(),
        key=lambda item: (
            item[1]["hardest"]["rating"]
            if item[1].get("hardest") and item[1]["hardest"].get("rating") is not None
            else 0.0
        ),
        reverse=True,
    )

    if not sorted_users:
        return header + "*No entries yet! Use `/gdhardest` to add your scores.*"

    lines = []
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}

    for rank, (user_id, data) in enumerate(sorted_users, start=1):
        hardest = data.get("hardest")
        second = data.get("second")

        # Format individual level strings
        level_strs = []
        if hardest:
            level_strs.append(f"{hardest['name']} ({hardest['rating']:.2f})")
        if second:
            level_strs.append(f"{second['name']} ({second['rating']:.2f})")

        levels_formatted = ", ".join(level_strs) if level_strs else "None"

        # Determine rank badge icon
        badge = medals.get(rank, f"{rank}")

        # Construct concise single-line format
        lines.append(f"{badge} <@{user_id}> {levels_formatted}")

    return header + "\n".join(lines)


async def sync_or_create_leaderboard_message(ctx_or_interaction):
    channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
    if not channel:
        print(f"[ERROR] Leaderboard channel {LEADERBOARD_CHANNEL_ID} not found.")
        return

    content = build_leaderboard_content()
    save_data()

    async for message in channel.history(limit=20):
        if message.author == bot.user:
            await message.edit(content=content, embed=None)
            return

    await channel.send(content=content)


# --- Core Logic Implementations ---
async def gdhardest_logic(ctx, target_user: discord.Member, level_type: str, level_input: str, tier_rating: float = None):
    user_id = target_user.id
    l_type = level_type.lower()
    fetched_name, fetched_rating = await fetch_gddl_info(level_input)

    name = fetched_name if fetched_name else level_input.title()
    rating = float(tier_rating) if tier_rating is not None and tier_rating > 0.0 else fetched_rating

    if l_type in ["non-demon", "nondemon"] and rating == 0.0:
        clean_input = level_input.lower().strip()
        if clean_input in ROBTOP_LEVELS:
            rating = ROBTOP_LEVELS[clean_input]

    if user_id not in gd_leaderboard_data:
        gd_leaderboard_data[user_id] = {"hardest": None, "second": None}

    old_hardest = gd_leaderboard_data[user_id]["hardest"]
    if old_hardest:
        gd_leaderboard_data[user_id]["second"] = old_hardest

    gd_leaderboard_data[user_id]["hardest"] = {"name": name, "rating": round(rating, 2)}

    await sync_or_create_leaderboard_message(ctx)
    if hasattr(ctx, "send"):
        await ctx.send(f"Updated **#1 Hardest** for **{target_user.display_name}** to **{name} ({rating:.2f})**!", delete_after=5)


async def gd2hardest_logic(ctx, target_user: discord.Member, level_type: str, level_input: str, tier_rating: float = None):
    user_id = target_user.id
    l_type = level_type.lower()
    fetched_name, fetched_rating = await fetch_gddl_info(level_input)

    name = fetched_name if fetched_name else level_input.title()
    rating = float(tier_rating) if tier_rating is not None and tier_rating > 0.0 else fetched_rating

    if l_type in ["non-demon", "nondemon"] and rating == 0.0:
        clean_input = level_input.lower().strip()
        if clean_input in ROBTOP_LEVELS:
            rating = ROBTOP_LEVELS[clean_input]

    if user_id not in gd_leaderboard_data:
        gd_leaderboard_data[user_id] = {"hardest": None, "second": None}

    gd_leaderboard_data[user_id]["second"] = {"name": name, "rating": round(rating, 2)}

    await sync_or_create_leaderboard_message(ctx)
    if hasattr(ctx, "send"):
        await ctx.send(f"Updated **#2 Hardest** for **{target_user.display_name}** to **{name} ({rating:.2f})**!", delete_after=5)


# --- Slash Commands ---
@bot.hybrid_command(name="gdhardest", description="Updates a user's #1 hardest GD level completion.")
@has_gd_role()
@app_commands.choices(level_type=[
    app_commands.Choice(name="Demon", value="demon"),
    app_commands.Choice(name="Non-Demon", value="non-demon")
])
async def gdhardest(ctx: commands.Context, target_user: discord.Member, level_type: str, level_input: str, tier_rating: float = None):
    await gdhardest_logic(ctx, target_user, level_type, level_input, tier_rating)


@bot.hybrid_command(name="gd2hardest", description="Updates a user's #2 hardest GD level completion.")
@has_gd_role()
@app_commands.choices(level_type=[
    app_commands.Choice(name="Demon", value="demon"),
    app_commands.Choice(name="Non-Demon", value="non-demon")
])
async def gd2hardest(ctx: commands.Context, target_user: discord.Member, level_type: str, level_input: str, tier_rating: float = None):
    await gd2hardest_logic(ctx, target_user, level_type, level_input, tier_rating)


@bot.hybrid_command(name="gdremove", description="Removes a user from the GD leaderboard.")
@has_gd_role()
async def gdremove(ctx: commands.Context, target_user: discord.Member):
    user_id = target_user.id
    if user_id in gd_leaderboard_data:
        del gd_leaderboard_data[user_id]
        await sync_or_create_leaderboard_message(ctx)
        await ctx.send(f"Removed **{target_user.display_name}** from the leaderboard.", delete_after=5)
    else:
        await ctx.send(f"**{target_user.display_name}** is not currently on the leaderboard.", delete_after=5)


@bot.hybrid_command(name="gdprofile", description="Shows the GD hardest levels for a specific user.")
async def gdprofile(ctx: commands.Context, target_user: discord.Member = None):
    user = target_user or ctx.author
    data = gd_leaderboard_data.get(user.id)

    if not data or (not data.get("hardest") and not data.get("second")):
        await ctx.send(f"No records found for **{user.display_name}**.", delete_after=10)
        return

    embed = discord.Embed(title=f"🎮 GD Profile: {user.display_name}", color=discord.Color.blue())
    hardest = data.get("hardest")
    second = data.get("second")

    embed.add_field(name="🥇 #1 Hardest", value=f"**{hardest['name']}** (Tier {hardest['rating']:.2f})" if hardest else "None", inline=False)
    embed.add_field(name="🥈 #2 Hardest", value=f"**{second['name']}** (Tier {second['rating']:.2f})" if second else "None", inline=False)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="gdrefresh", description="Force updates and re-posts the leaderboard embed.")
@has_gd_role()
async def gdrefresh(ctx: commands.Context):
    await sync_or_create_leaderboard_message(ctx)
    await ctx.send("Leaderboard refreshed!", delete_after=5)


@bot.hybrid_command(name="gdinfo", description="Fetches GDDL information for a specific level.")
async def gdinfo(ctx: commands.Context, level_input: str):
    name, rating = await fetch_gddl_info(level_input)
    embed = discord.Embed(title=f"ℹ️ Level Info: {name}", color=discord.Color.teal())
    embed.add_field(name="Level Name / Input", value=name, inline=True)
    embed.add_field(name="GDDL Tier / Rating", value=f"{rating:.2f}", inline=True)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="leaderboard", description="Displays the full GD leaderboard.")
async def leaderboard(ctx: commands.Context):
    content = build_leaderboard_content()
    await ctx.send(content=content)


@bot.hybrid_command(name="ping", description="Checks the bot's latency.")
async def ping(ctx: commands.Context):
    latency = round(bot.latency * 1000)
    await ctx.send(f"🏓 Pong! Latency: `{latency}ms`")


@bot.hybrid_command(name="gdhelp", description="Lists all available GD bot commands.")
async def gdhelp(ctx: commands.Context):
    embed = discord.Embed(title="📜 GD Leaderboard Bot Help", color=discord.Color.purple())
    embed.add_field(name="/gdhardest @User <type> <level> [tier]", value="Update a user's #1 hardest completion.", inline=False)
    embed.add_field(name="/gd2hardest @User <type> <level> [tier]", value="Update a user's #2 hardest completion.", inline=False)
    embed.add_field(name="/gdremove @User", value="Remove a user from the leaderboard.", inline=False)
    embed.add_field(name="/gdprofile [@User]", value="View your or another member's profile.", inline=False)
    embed.add_field(name="/gdrefresh", value="Force refresh and post the leaderboard.", inline=False)
    embed.add_field(name="/gdinfo <level>", value="Lookup GDDL details for a level.", inline=False)
    embed.add_field(name="/leaderboard", value="Show the current leaderboard.", inline=False)
    embed.add_field(name="/ping", value="Check latency.", inline=False)
    await ctx.send(embed=embed)


# --- Global Error Handler ---
@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CheckFailure):
        if hasattr(ctx, "send"):
            await ctx.send("You do not have permission to use this command.", delete_after=5)
    else:
        print(f"[COMMAND ERROR] {error}")


# --- Prefix Handler ---
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content = message.content

    if content.startswith("!gdhardest"):
        ctx = await bot.get_context(message)
        if not any(role.id == ALLOWED_ROLE_ID for role in message.author.roles):
            await ctx.send("You do not have permission to use this command.")
            return
        parts = content.split(maxsplit=4)
        if len(parts) >= 4 and message.mentions:
            tier = float(parts[4]) if len(parts) >= 5 else None
            await gdhardest_logic(ctx, message.mentions[0], parts[2], parts[3], tier)
        else:
            await ctx.send("Usage: `!gdhardest @User <demon/non-demon> <level_id_or_name> [tier]`")
        return

    elif content.startswith("!gd2hardest"):
        ctx = await bot.get_context(message)
        if not any(role.id == ALLOWED_ROLE_ID for role in message.author.roles):
            await ctx.send("You do not have permission to use this command.")
            return
        parts = content.split(maxsplit=4)
        if len(parts) >= 4 and message.mentions:
            tier = float(parts[4]) if len(parts) >= 5 else None
            await gd2hardest_logic(ctx, message.mentions[0], parts[2], parts[3], tier)
        else:
            await ctx.send("Usage: `!gd2hardest @User <demon/non-demon> <level_id_or_name> [tier]`")
        return

    elif content.startswith("!gdremove"):
        ctx = await bot.get_context(message)
        if not any(role.id == ALLOWED_ROLE_ID for role in message.author.roles):
            await ctx.send("You do not have permission to use this command.")
            return
        if message.mentions:
            await gdremove(ctx, message.mentions[0])
        else:
            await ctx.send("Usage: `!gdremove @User`")
        return

    elif content.startswith("!gdprofile"):
        ctx = await bot.get_context(message)
        target = message.mentions[0] if message.mentions else ctx.author
        await gdprofile(ctx, target)
        return

    elif content.startswith("!gdrefresh"):
        ctx = await bot.get_context(message)
        if not any(role.id == ALLOWED_ROLE_ID for role in message.author.roles):
            await ctx.send("You do not have permission to use this command.")
            return
        await gdrefresh(ctx)
        return

    elif content.startswith("!gdinfo"):
        ctx = await bot.get_context(message)
        parts = content.split(maxsplit=1)
        if len(parts) > 1:
            await gdinfo(ctx, parts[1])
        else:
            await ctx.send("Usage: `!gdinfo <level_id_or_name>`")
        return

    elif content.startswith("!leaderboard"):
        ctx = await bot.get_context(message)
        await leaderboard(ctx)
        return

    elif content.startswith("!ping"):
        ctx = await bot.get_context(message)
        await ping(ctx)
        return

    elif content.startswith("!gdhelp"):
        ctx = await bot.get_context(message)
        await gdhelp(ctx)
        return

    await bot.process_commands(message)


# --- Startup & Execution Engine ---
@bot.event
async def on_ready():
    load_data()
    try:
        synced = await bot.tree.sync()
        print(f"[BOOT] Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"[BOOT ERROR] Failed to sync slash commands: {e}")
    print(f"[ONLINE] Logged in as {bot.user}")


if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")

    if not TOKEN:
        print("[CRITICAL ERROR] No DISCORD_TOKEN found in environment variables.")
    else:
        try:
            bot.run(TOKEN)
        except Exception as e:
            print(f"[CRITICAL ERROR] Bot failed to start: {e}")
