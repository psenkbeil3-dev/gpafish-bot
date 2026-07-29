import os
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

    towerstats_url = f"https://www.towerstats.com/etoh?username={roblox_username}"
    api_url = f"https://www.towerstats.com/api/v1/user/{roblox_username}"
    
    headers = {
        "Authorization": f"Bearer {TOWERSTATS_KEY}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(api_url, headers=headers, timeout=5) as response:
                print(f"[DEBUG] TowerStats API HTTP Status: {response.status}")
                
                if response.status == 200:
                    data = await response.json()
                    hardest = data.get("hardest_tower", "Unknown")
                    completed_count = data.get("completed_towers", 0)
                else:
                    hardest = "Check TowerStats Page"
                    completed_count = "N/A"

        except Exception as err:
            print(f"[DEBUG] API fetch error: {err}")
            hardest = "Check TowerStats Page"
            completed_count = "N/A"

    embed = discord.Embed(
        title=f"{roblox_username}'s EToH Towerstats",
        url=towerstats_url,
        color=discord.Color.blue()
    )
    embed.add_field(name="Hardest Completed", value=hardest, inline=False)
    embed.add_field(name="Towers Completed", value=f"{completed_count}/410", inline=False)
    embed.set_footer(text="Game: Eternal Towers of Hell | Click title to view full progress")

    await ctx.send(embed=embed)

# --- SIKKY COMMAND ---

SIKKY_IMAGES = [
    "https://i.ytimg.com/vi/aL3pPzX_G6w/hqdefault.jpg",
    "https://i.ytimg.com/vi/3N2S4A1Xb_0/hqdefault.jpg",
    "https://i.ytimg.com/vi/8a92mS-q_hI/hqdefault.jpg",
    "https://i.ytimg.com/vi/q_xJ1W9e_90/hqdefault.jpg",
    "https://i.ytimg.com/vi/zaRxbC3m7HM/hqdefault.jpg",
    "https://i.ytimg.com/vi/gISWceDeGxc/hqdefault.jpg",
    "https://i.ytimg.com/vi/WPhv8wPuwMo/hqdefault.jpg",
    "https://i.ytimg.com/vi/ardXWyk_jO8/hqdefault.jpg"
]

@bot.hybrid_command(name="sikky", description="Pulls up a random hilarious picture of Sikky!")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def sikky(ctx: commands.Context):
    selected_image = random.choice(SIKKY_IMAGES)
    
    embed = discord.Embed(
        title="🤪 Sikky Moment",
        color=discord.Color.gold()
    )
    embed.set_image(url=selected_image)
    embed.set_footer(text="Geometry Dash Memes")

    await ctx.send(embed=embed)

# --- RUN BOT ---

if TOKEN:
    print("4. Connecting to Discord...")
    bot.run(TOKEN)
else:
    print("❌ Token missing! Check your .env file or Render Environment Variables.")
