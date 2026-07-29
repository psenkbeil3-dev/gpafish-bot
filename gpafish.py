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

print(f"2. Token loaded: {'Yes' if TOKEN else 'NO - TOKEN IS MISSING!'}")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="-", intents=intents)

# --- IN-MEMORY WARNING STORAGE ---
# Structure: {(guild_id, user_id): ["Reason 1", "Reason 2"]}
user_warnings = {}

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
    print(f"Web server running on port {port}")

@bot.event
async def on_ready():
    bot.loop.create_task(start_web_server())
    
    print("3. Bot is attempting to sync commands...")
    try:
        synced = await bot.tree.sync()
        print(f"Successfully synced {len(synced)} slash command(s) globally.")
    except Exception as e:
        print(f"Error syncing commands: {e}")
    print(f'Logged in as {bot.user}')

# --- DYNO-STYLE MODERATION COMMANDS ---

# 1. KICK
@bot.hybrid_command(name="kick", description="Kicks a member from the server.")
@commands.guild_only()
@commands.has_permissions(kick_members=True)
async def kick(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    if member == ctx.author:
        await ctx.send("You cannot kick yourself!")
        return
    await member.kick(reason=reason)
    await ctx.send(f"**{member.display_name}** has been kicked. | Reason: {reason}")

# 2. BAN
@bot.hybrid_command(name="ban", description="Bans a member from the server.")
@commands.guild_only()
@commands.has_permissions(ban_members=True)
async def ban(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    if member == ctx.author:
        await ctx.send("You cannot ban yourself!")
        return
    await member.ban(reason=reason)
    await ctx.send(f"**{member.display_name}** has been banned. | Reason: {reason}")

# 3. WARN
@bot.hybrid_command(name="warn", description="Issues a warning to a member.")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def warn(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    key = (ctx.guild.id, member.id)
    if key not in user_warnings:
        user_warnings[key] = []
    
    user_warnings[key].append(reason)

    try:
        await member.send(f"You have been warned in **{ctx.guild.name}** | Reason: {reason}")
    except discord.Forbidden:
        pass

    await ctx.send(f"{member.mention} was warned.")

# 4. SEE WARNINGS
@bot.hybrid_command(name="warnings", aliases=["warns"], description="Shows warnings for a member.")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def warnings(ctx: commands.Context, member: discord.Member = None):
    target = member or ctx.author
    key = (ctx.guild.id, target.id)
    warns = user_warnings.get(key, [])

    if not warns:
        await ctx.send(f"**{target.display_name}** has no warnings.")
        return

    warn_list = "\n".join([f"{i+1}. {r}" for i, r in enumerate(warns)])
    await ctx.send(f"Warnings for **{target.display_name}** ({len(warns)} total):\n{warn_list}")

# 5. CLEAR WARNINGS
@bot.hybrid_command(name="clearwarns", description="Clears all warnings for a member.")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def clearwarns(ctx: commands.Context, member: discord.Member):
    key = (ctx.guild.id, member.id)
    if key in user_warnings:
        del user_warnings[key]
        await ctx.send(f"Cleared warnings for **{member.display_name}**.")
    else:
        await ctx.send(f"**{member.display_name}** has no warnings to clear.")

# 6. TIMEOUT
@bot.hybrid_command(name="timeout", description="Times out a member for a set number of minutes.")
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
async def timeout(ctx: commands.Context, member: discord.Member, minutes: int, *, reason: str = "No reason provided"):
    if member == ctx.author:
        await ctx.send("You cannot timeout yourself!")
        return
    duration = datetime.timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    await ctx.send(f"**{member.display_name}** has been timed out for {minutes} minute(s). | Reason: {reason}")

# 7. UNTIMEOUT
@bot.hybrid_command(name="untimeout", description="Removes a timeout from a member.")
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
async def untimeout(ctx: commands.Context, member: discord.Member):
    await member.timeout(None)
    await ctx.send(f"Removed timeout for **{member.display_name}**.")

# 8. MUTE
@bot.hybrid_command(name="mute", description="Mutes a member for a specified duration in minutes.")
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
async def mute(ctx: commands.Context, member: discord.Member, minutes: int = 10, *, reason: str = "No reason provided"):
    duration = datetime.timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    await ctx.send(f"**{member.display_name}** has been muted for {minutes} minutes. | Reason: {reason}")

# 9. UNMUTE
@bot.hybrid_command(name="unmute", description="Unmutes a member.")
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
async def unmute(ctx: commands.Context, member: discord.Member):
    await member.timeout(None)
    await ctx.send(f"**{member.display_name}** has been unmuted.")

# 10. DEAFEN
@bot.hybrid_command(name="deafen", description="Server deafens a member in voice chat.")
@commands.guild_only()
@commands.has_permissions(deafen_members=True)
async def deafen(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    if not member.voice or not member.voice.channel:
        await ctx.send(f"**{member.display_name}** is not in a voice channel.")
        return
    await member.edit(deafen=True, reason=reason)
    await ctx.send(f"**{member.display_name}** has been server deafened.")

# 11. UNDEAFEN
@bot.hybrid_command(name="undeafen", description="Undeafens a member in voice chat.")
@commands.guild_only()
@commands.has_permissions(deafen_members=True)
async def undeafen(ctx: commands.Context, member: discord.Member):
    if not member.voice or not member.voice.channel:
        await ctx.send(f"**{member.display_name}** is not in a voice channel.")
        return
    await member.edit(deafen=False)
    await ctx.send(f"**{member.display_name}** is no longer deafened.")

# 12. PURGE
@bot.hybrid_command(name="purge", description="Deletes a specified number of recent messages.")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def purge(ctx: commands.Context, amount: int):
    if amount < 1 or amount > 100:
        await ctx.send("Please specify a number between 1 and 100.")
        return
    deleted = await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"Deleted **{len(deleted)-1}** messages.", delete_after=5)

# 13. USERINFO
@bot.hybrid_command(name="userinfo", description="Displays information about a server member.")
@commands.guild_only()
async def userinfo(ctx: commands.Context, member: discord.Member = None):
    target = member or ctx.author
    roles = [role.mention for role in target.roles if role.name != "@everyone"]
    
    embed = discord.Embed(
        title=f"User Info - {target.display_name}",
        color=target.color
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Username", value=str(target), inline=True)
    embed.add_field(name="User ID", value=target.id, inline=True)
    embed.add_field(name="Joined Server", value=target.joined_at.strftime("%b %d, %Y"), inline=False)
    embed.add_field(name="Account Created", value=target.created_at.strftime("%b %d, %Y"), inline=False)
    embed.add_field(name=f"Roles [{len(roles)}]", value=", ".join(roles) if roles else "None", inline=False)

    await ctx.send(embed=embed)

# --- GLOBAL ERROR HANDLER FOR PERMISSIONS ---

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have the required permissions to use this command.")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send("I don't have the necessary permissions to execute this action.")

# --- TOWERSTATS COMMAND ---

@bot.hybrid_command(name="towerstats", description="Get a player's tower stats.")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def towerstats(ctx: commands.Context, game_acronym: str, roblox_username: str):
    acronym = game_acronym.lower()

    if acronym != "etoh":
        await ctx.send(f"Game `{game_acronym}` is not configured yet! Currently supported: `etoh`")
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

                    count_match = re.search(r'(\d+)\s*/\s*410', html_text)
                    if count_match:
                        completed_count = count_match.group(1)

                    hardest_match = re.search(r'Hardest:?\s*<[^>]+>([^<]+)<', html_text, re.IGNORECASE)
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

# --- MEME DATA STORES ---

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

# --- GD MEMES COMMAND ---

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

# --- ETOH MEMES COMMAND ---

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

# --- RUN BOT ---

if TOKEN:
    print("4. Connecting to Discord...")
    bot.run(TOKEN)
else:
    print("TOKEN missing! Check your .env file or Render Environment Variables.")
