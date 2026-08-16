import os
import json
import urllib.parse
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Bot Initialization & Configuration ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

ALLOWED_ROLE_ID = 123456789012345678      # Replace with your moderator/GD role ID
LEADERBOARD_CHANNEL_ID = 123456789012345678  # Replace with your target leaderboard channel ID
DATA_FILE = "gd_leaderboard.json"

gd_leaderboard_data = {}

# EVW / Community Tier Ratings for Main RobTop Levels
ROBTOP_LEVELS = {
    "stereo madness": 0.10,
    "back on track": 0.20,
    "polargeist": 0.30,
    "dry out": 0.40,
    "base after base": 0.50,
    "cant let go": 0.60,
    "jumper": 0.70,
    "time machine": 0.80,
    "cycles": 0.90,
    "xstep": 1.00,
    "clutterfunk": 1.20,
    "theory of everything": 1.10,
    "electroman adventures": 1.15,
    "electrodynamix": 1.60,
    "hexagon force": 1.40,
    "blast processing": 0.85,
    "geometrical dominator": 1.05,
    "fingerdash": 1.30,
    "dash": 1.25,
    "clubstep": 2.10,
    "theory of everything 2": 2.40,
    "deadlocked": 2.80,
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
        await interaction.response.send_message(
            "You do not have permission to use this command.", ephemeral=True
        )
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
                            raw_tier = (
                                data.get("Rating")
                                or data.get("Tier")
                                or data.get("rating")
                            )
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
                        name = (
                            first.get("Name")
                            or first.get("name")
                            or level_input.title()
                        )
                        raw_tier = (
                            first.get("Rating")
                            or first.get("Tier")
                            or first.get("rating")
                        )
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
async def build_leaderboard_embed():
    embed = discord.Embed(
        title="🏆 GD Hardest Levels Leaderboard 🏆", color=discord.Color.gold()
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
        embed.description = "No entries yet! Use `/gdhardest` to add your scores."
        return embed

    lines = []
    for rank, (user_id, data) in enumerate(sorted_users, start=1):
        hardest = data.get("hardest")
        second = data.get("second")

        h_str = (
            f"**{hardest['name']}** (Tier {hardest['rating']:.2f})"
            if hardest
            else "None"
        )
        s_str = (
            f"**{second['name']}** (Tier {second['rating']:.2f})"
            if second
            else "None"
        )

        lines.append(
            f"**#{rank}** <@{user_id}>\n"
            f"> 🥇 **#1:** {h_str}\n"
            f"> 🥈 **#2:** {s_str}\n"
        )

    embed.description = "\n".join(lines)
    return embed


async def sync_or_create_leaderboard_message(ctx_or_interaction):
    channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
    if not channel:
        print(f"[ERROR] Leaderboard channel {LEADERBOARD_CHANNEL_ID} not found.")
        return

    embed = await build_leaderboard_embed()
    save_data()

    async for message in channel.history(limit=20):
        if message.author == bot.user and message.embeds:
            await message.edit(embed=embed)
            return

    await channel.send(embed=embed)


# --- Core Command Implementations ---
async def gdhardest_logic(
    ctx,
    target_user: discord.Member,
    level_type: str,
    level_input: str,
    tier_rating: float = None,
):
    user_id = target_user.id
    l_type = level_type.lower()
    fetched_name, fetched_rating = await fetch_gddl_info(level_input)

    name = fetched_name if fetched_name else level_input.title()

    if tier_rating is not None and tier_rating > 0.0:
        rating = float(tier_rating)
    else:
        rating = fetched_rating

    if l_type in ["non-demon", "nondemon"] and rating == 0.0:
        clean_input = level_input.lower().strip()
        if clean_input in ROBTOP_LEVELS:
            rating = ROBTOP_LEVELS[clean_input]

    if user_id not in gd_leaderboard_data:
        gd_leaderboard_data[user_id] = {"hardest": None, "second": None}

    old_hardest = gd_leaderboard_data[user_id]["hardest"]
    if old_hardest:
        gd_leaderboard_data[user_id]["second"] = old_hardest

    gd_leaderboard_data[user_id]["hardest"] = {
        "name": name,
        "rating": round(rating, 2),
    }

    await sync_or_create_leaderboard_message(ctx)
    if hasattr(ctx, "send"):
        await ctx.send(
            f"Updated **#1 Hardest** for {target_user.mention} to **{name} ({rating:.2f})**!",
            delete_after=5,
        )


async def gd2hardest_logic(
    ctx,
    target_user: discord.Member,
    level_type: str,
    level_input: str,
    tier_rating: float = None,
):
    user_id = target_user.id
    l_type = level_type.lower()
    fetched_name, fetched_rating = await fetch_gddl_info(level_input)

    name = fetched_name if fetched_name else level_input.title()

    if tier_rating is not None and tier_rating > 0.0:
        rating = float(tier_rating)
    else:
        rating = fetched_rating

    if l_type in ["non-demon", "nondemon"] and rating == 0.0:
        clean_input = level_input.lower().strip()
        if clean_input in ROBTOP_LEVELS:
            rating = ROBTOP_LEVELS[clean_input]

    if user_id not in gd_leaderboard_data:
        gd_leaderboard_data[user_id] = {"hardest": None, "second": None}

    gd_leaderboard_data[user_id]["second"] = {
        "name": name,
        "rating": round(rating, 2),
    }

    await sync_or_create_leaderboard_message(ctx)
    if hasattr(ctx, "send"):
        await ctx.send(
            f"Updated **#2 Hardest** for {target_user.mention} to **{name} ({rating:.2f})**!",
            delete_after=5,
        )


# --- Slash Commands ---
@bot.hybrid_command(
    name="gdhardest",
    description="Updates a user's #1 hardest GD level completion.",
)
@has_gd_role()
@app_commands.choices(
    level_type=[
        app_commands.Choice(name="Demon", value="demon"),
        app_commands.Choice(name="Non-Demon", value="non-demon"),
    ]
)
async def gdhardest(
    ctx: commands.Context,
    target_user: discord.Member,
    level_type: str,
    level_input: str,
    tier_rating: float = None,
):
    await gdhardest_logic(ctx, target_user, level_type, level_input, tier_rating)


@bot.hybrid_command(
    name="gd2hardest",
    description="Updates a user's #2 hardest GD level completion.",
)
@has_gd_role()
@app_commands.choices(
    level_type=[
        app_commands.Choice(name="Demon", value="demon"),
        app_commands.Choice(name="Non-Demon", value="non-demon"),
    ]
)
async def gd2hardest(
    ctx: commands.Context,
    target_user: discord.Member,
    level_type: str,
    level_input: str,
    tier_rating: float = None,
):
    await gd2hardest_logic(ctx, target_user, level_type, level_input, tier_rating)


@bot.hybrid_command(
    name="gdremove", description="Removes a user from the GD leaderboard."
)
@has_gd_role()
async def gdremove(ctx: commands.Context, target_user: discord.Member):
    user_id = target_user.id
    if user_id in gd_leaderboard_data:
        del gd_leaderboard_data[user_id]
        await sync_or_create_leaderboard_message(ctx)
        await ctx.send(
            f"Removed {target_user.mention} from the leaderboard.", delete_after=5
        )
    else:
        await ctx.send(
            f"{target_user.mention} is not currently on the leaderboard.", delete_after=5
        )


@bot.hybrid_command(
    name="gdprofile", description="Shows the GD hardest levels for a specific user."
)
async def gdprofile(ctx: commands.Context, target_user: discord.Member = None):
    user = target_user or ctx.author
    data = gd_leaderboard_data.get(user.id)

    if not data or (not data.get("hardest") and not data.get("second")):
        await ctx.send(f"No records found for {user.mention}.", delete_after=10)
        return

    embed = discord.Embed(
        title=f"🎮 GD Profile: {user.display_name}", color=discord.Color.blue()
    )
    hardest = data.get("hardest")
    second = data.get("second")

    embed.add_field(
        name="🥇 #1 Hardest",
        value=(
            f"**{hardest['name']}** (Tier {hardest['rating']:.2f})"
            if hardest
            else "None"
        ),
        inline=False,
    )
    embed.add_field(
        name="🥈 #2 Hardest",
        value=(
            f"**{second['name']}** (Tier {second['rating']:.2f})"
            if second
            else "None"
        ),
        inline=False,
    )
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="gdrefresh", description="Force updates and re-posts the leaderboard embed."
)
@has_gd_role()
async def gdrefresh(ctx: commands.Context):
    await sync_or_create_leaderboard_message(ctx)
    await ctx.send("Leaderboard refreshed!", delete_after=5)


# --- Legacy Prefix Commands Handler ---
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
            target_user = message.mentions[0]
            level_type = parts[2]
            level_input = parts[3]
            tier = float(parts[4]) if len(parts) >= 5 else None
            await gdhardest_logic(ctx, target_user, level_type, level_input, tier)
        else:
            await ctx.send(
                "Usage: `!gdhardest @User <demon/non-demon> <level_id_or_name> [tier]`"
            )
        return

    elif content.startswith("!gd2hardest"):
        ctx = await bot.get_context(message)
        if not any(role.id == ALLOWED_ROLE_ID for role in message.author.roles):
            await ctx.send("You do not have permission to use this command.")
            return

        parts = content.split(maxsplit=4)
        if len(parts) >= 4 and message.mentions:
            target_user = message.mentions[0]
            level_type = parts[2]
            level_input = parts[3]
            tier = float(parts[4]) if len(parts) >= 5 else None
            await gd2hardest_logic(ctx, target_user, level_type, level_input, tier)
        else:
            await ctx.send(
                "Usage: `!gd2hardest @User <demon/non-demon> <level_id_or_name> [tier]`"
            )
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
    print(f"[ONLINE] Bot logged in as {bot.user} (ID: {bot.user.id})")


if __name__ == "__main__":
    # Tries retrieving 'DISCORD_TOKEN' first, then 'BOT_TOKEN' as a fallback
    TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")

    if not TOKEN:
        print(
            "[CRITICAL] No bot token found! Ensure DISCORD_TOKEN is set in your environment variables."
        )
    else:
        try:
            bot.run(TOKEN)
        except Exception as e:
            print(f"[CRITICAL ERROR] Failed to initialize bot session: {e}")
