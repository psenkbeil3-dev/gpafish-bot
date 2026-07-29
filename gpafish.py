import os
import re
import json
import random
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

print(f"2. Token loaded: {'Yes' if TOKEN else 'NO - TOKEN IS MISSING!'}")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="-", intents=intents)

# --- DUMMY WEB SERVER FOR RENDER WEB SERVICE ---
async def handle_ping(request):
    return web.Response(text="Bot is running and alive 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 Web server running on port {port}")

@bot.event
async def on_ready():
    bot.loop.create_task(start_web_server())
    
    print("3. Bot is attempting to sync commands...")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Error syncing commands: {e}")
    print(f'✅ Logged in as {bot.user}')

# --- MODERATION COMMANDS ---

@bot.hybrid_command(name="ban", description="Bans a member from the server.")
@commands.guild_only()
@commands.has_permissions(ban_members=True)
async def ban(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    if member == ctx.author:
        await ctx.send("❌ You cannot ban yourself!")
        return
    
    await member.ban(reason=reason)
    await ctx.send(f"🔨 **{member.display_name}** has been banned. | Reason: {reason}")

@ban.error
async def ban_error(ctx: commands.Context, error):
    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send("❌ This command can only be used inside a server!")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to ban members.")

# --- TOWERSTATS COMMAND ---

@bot.hybrid_command(name="towerstats", description="Get a player's tower stats.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def towerstats(ctx: commands.Context, game_acronym: str, roblox_username: str):
    acronym = game_acronym.lower()

    if acronym != "etoh":
        await ctx.send(f"❌ Game `{game_acronym}` is not configured yet! Currently supported: `etoh`")
        return

    await ctx.defer()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    async with aiohttp.ClientSession() as session:
        # 1. Resolve Roblox Username to User ID
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
            print(f"[DEBUG] Roblox API check failed: {e}")

        # 2. Query TowerStats Web Page & API
        towerstats_url = f"https://www.towerstats.com/etoh?username={real_username}"
        api_url = f"https://www.towerstats.com/api/v1/user/{real_username}"
        
        if TOWERSTATS_KEY:
            headers["Authorization"] = f"Bearer {TOWERSTATS_KEY}"

        hardest = "Unknown"
        completed_count = None

        # Try direct API fetch first
        try:
            async with session.get(api_url, headers=headers, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    hardest = data.get("hardest_tower") or data.get("hardestTower") or data.get("hardest", "Unknown")
                    completed_count = data.get("completed_towers") or data.get("completedTowers") or data.get("completed")
        except Exception as err:
            print(f"[DEBUG] API fetch error: {err}")

        # If API returned incomplete data, fetch and parse TowerStats web page
        if completed_count is None or hardest == "Unknown":
            try:
                async with session.get(towerstats_url, headers=headers, timeout=5) as page_resp:
                    if page_resp.status == 200:
                        html = await page_resp.text()
                        
                        # Extract Next.js embedded data state
                        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
                        if match:
                            page_data = json.loads(match.group(1))
                            props = page_data.get("props", {}).get("pageProps", {})
                            user_stats = props.get("user") or props.get("userData") or props.get("stats", {})
                            
                            if isinstance(user_stats, dict):
                                hardest = user_stats.get("hardestTower") or user_stats.get("hardest_tower") or user_stats.get("hardest", hardest)
                                completed_count = user_stats.get("completedTowers") or user_stats.get("completed_towers") or user_stats.get("completed", completed_count)

                        # Regex Fallback on HTML text if JSON parsing missed keys
                        if completed_count is None:
                            count_match = re.search(r'Completed:\s*<b>(\d+)</b>|(\d+)\s*/\s*410', html, re.IGNORECASE)
                            if count_match:
                                completed_count = count_match.group(1) or count_match.group(2)

                        if hardest == "Unknown":
                            hardest_match = re.search(r'Hardest:\s*<b>([^<]+)</b>', html, re.IGNORECASE)
                            if hardest_match:
                                hardest = hardest_match.group(1).strip()
            except Exception as page_err:
                print(f"[DEBUG] HTML Parse error: {page_err}")

    # Fallback default display format
    display_completed = f"{completed_count}/410" if completed_count is not None else "0/410 (Check Page)"
    display_hardest = hardest if hardest != "Unknown" else "None / Not Found"

    embed = discord.Embed(
        title=f"🗼 {real_username}'s EToH Towerstats",
        url=towerstats_url,
        color=discord.Color.blue()
    )
    embed.add_field(name="Hardest Completed", value=f"**{display_hardest}**", inline=False)
    embed.add_field(name="Towers Completed", value=f"**{display_completed}**", inline=False)
    embed.set_footer(text="Game: Eternal Towers of Hell | Click title to view full TowerStats profile")

    await ctx.send(embed=embed)

# --- MEME DATA STORES ---

GD_MEMES = [
    {
        "title": "🟢 FIRE IN THE HOLE! 🕳️",
        "desc": "POV: You opened the 2.2 level editor for 5 seconds.",
        "image": "https://i.ytimg.com/vi/7xQ23zshA5I/hqdefault.jpg",
        "footer": "Normal Face approved this message."
    },
    {
        "title": "🕷️ Is this section sightreadable enough?",
        "desc": "GD players when a level has 0.001 seconds of unsightreadable gameplay.",
        "image": "https://i.ytimg.com/vi/3N2S4A1Xb_0/hqdefault.jpg",
        "footer": "Dash spider jumpscare incoming..."
    },
    {
        "title": "👁️ CONGREGATION JUMPSCARE",
        "desc": "You think you're watching a normal layout... and then the drop hits.",
        "image": "https://i.ytimg.com/vi/aL3pPzX_G6w/hqdefault.jpg",
        "footer": "0% -> 100% in pitch darkness."
    },
    {
        "title": "🪚 DEVIL VORTEX SAWS",
        "desc": "Legend has it there are still 54 extra saws floating above the level.",
        "image": "https://i.ytimg.com/vi/q_xJ1W9e_90/hqdefault.jpg",
        "footer": "100% legit, totally auto-verified."
    },
    {
        "title": "🌊 Tidal Wave at 99%",
        "desc": "The longest 2 seconds of your entire life.",
        "image": "https://i.ytimg.com/vi/zaRxbC3m7HM/hqdefault.jpg",
        "footer": "It's blue. Very blue."
    },
    {
        "title": "🗣️ WATER ON THE HILL! 🏔️💧",
        "desc": "Logistics in 2.2 geometry dash levels be like:",
        "image": "https://i.ytimg.com/vi/gISWceDeGxc/hqdefault.jpg",
        "footer": "Lobotomy Dash strikes again."
    },
    {
        "title": "🔊 ROCK IN THE GROUND! 🗿",
        "desc": "When the sound effects kick in and your headphones explode.",
        "image": "https://i.ytimg.com/vi/WPhv8wPuwMo/hqdefault.jpg",
        "footer": "Ear damage guaranteed."
    },
    {
        "title": "💀 Practice Mode 99% (1,482 Attempts)",
        "desc": "'Just gotta fix my consistency bro, I swear I can beat it in normal mode.'",
        "image": "https://i.ytimg.com/vi/ardXWyk_jO8/hqdefault.jpg",
        "footer": "Narrator: He could not beat it in normal mode."
    }
]

ETOH_MEMES = [
    {
        "title": "🗼 Falling on Floor 10 of a Remorseless Tower",
        "desc": "POV: You spent 45 minutes climbing and missed a 0.5 stud jump.",
        "image": "https://i.ytimg.com/vi/ATAyZGDoyRk/hqdefault.jpg",
        "footer": "Welcome back to Floor 1!"
    },
    {
        "title": "🥊 Tower of Screen Punching (ToSP)",
        "desc": "The tower isn't hard, your mental health just drops by 90% per floor.",
        "image": "https://static.wikia.nocookie.net/jtoh/images/b/b5/Tower_of_Hecc_thumbnail.png",
        "footer": "Keyboard warranty sold separately."
    },
    {
        "title": "👑 Average Ring 1 Lobby Enjoyer",
        "desc": "Standing near Tower of Hecc pretending you're going to beat it today.",
        "image": "https://static.wikia.nocookie.net/jtoh/images/2/22/Ring_1_Revamp.png",
        "footer": "Ring 1 never changes."
    },
    {
        "title": "🦎 Tower of Getting Gnomed",
        "desc": "When you think you hit the win pad but it was a trap button.",
        "image": "https://static.wikia.nocookie.net/jtoh/images/0/07/Tower_of_Getting_Gnomed_game_icon.png",
        "footer": "HOOHOO! You've been gnomed!"
    },
    {
        "title": "⏱️ 'Just One Quick Tower Before Bed'",
        "desc": "3 hours later: You're stuck on Floor 7 of a Steep difficulty tower questioning life.",
        "image": "https://static.wikia.nocookie.net/jtoh/images/2/23/R6_lobby_revamp_3.png",
        "footer": "Sleep is for the weak."
    },
    {
        "title": "🧱 Frame-Precise Truss Flick",
        "desc": "Roblox physics engine deciding whether to fling you into outer space or work properly.",
        "image": "https://i.ytimg.com/vi/pr3P0ZxVJ8I/hqdefault.jpg",
        "footer": "Shift lock active."
    }
]

# --- GD MEMES COMMAND ---

@bot.hybrid_command(name="gdmemes", description="Pulls up an insanely funny Geometry Dash meme!")
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

# --- ETOH MEMES COMMAND ---

@bot.hybrid_command(name="etohmemes", description="Pulls up a hilarious Eternal Towers of Hell meme!")
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

# --- RUN BOT ---

if TOKEN:
    print("4. Connecting to Discord...")
    bot.run(TOKEN)
else:
    print("❌ Token missing! Check your .env file or Render Environment Variables.")
