import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import aiosqlite
import random
import re
import os
import sqlite3
import sys
import aiohttp
import time
from datetime import datetime, date, timezone, timedelta
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import json

PROVIDER_IMAGES = {
    "pragmatic": "https://cdn.discordapp.com/attachments/1283197229913608192/1362821484447399936/CvuaWH6WBTwAAAAASUVORK5CYII.png?ex=6a2cd729&is=6a2b85a9&hm=e8ef3da0bde4fbd77e5d2aa99ada5fdd66b0ac392035b4c79ddcefb5acef18f5",
    "hacksaw": "https://cdn.discordapp.com/attachments/1225024450345439313/1514975662211858462/image.png?ex=6a2d5288&is=6a2c0108&hm=2603e233798b21904a31ac3f48b98488b95b649f09c3ba55b5628f400b5b67a6",
    "nolimit_city": "https://cdn.discordapp.com/attachments/1353382950300811394/1374936691063918632/aJJvcpI1AAAAAElFTkSuQmCC.png?ex=686c8214&is=686b3094&hm=17d990f5013d8f961ebf03e898085a39b399822673481721a3482f1ab0287285&",
    "jedi_of_slots": "https://cdn.discordapp.com/attachments/1225024450345439313/1534176199297990676/beb73497-6ef9-4fa0-a0bb-b0313eb61533-fullsize.png?ex=6a732c6d&is=6a71daed&hm=ee292462c20f5f9c8295069deae66b89888c109d5a7ff9e83e3c1e0c28425c42"
}

# ===================== CONFIG =====================
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise SystemExit("❌ DISCORD_BOT_TOKEN is not set in environment variables.")
    
DB_PATH = "/data/events.db"

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.reactions = True
intents.members = True

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS player_stats (
    user_id INTEGER PRIMARY KEY,
    username TEXT,

    games_played INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,

    kills INTEGER DEFAULT 0,
    super_kills INTEGER DEFAULT 0,
    deaths INTEGER DEFAULT 0,
    revives INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS current_rumble_game (
    guild_id TEXT PRIMARY KEY,
    channel_id TEXT,
    status TEXT,
    mode TEXT,
    started_at TEXT
)
""")

conn.commit()

bot = commands.Bot(command_prefix="!", intents=intents)
JOIN_EMOJI = "🎰"
manual_games = {}  # Stores {channel_id: (players, provider_name)}

EXCLUDED_LEADERBOARD_USERS = {
    878253813553844254,
    850665803161534484,
    930627787792998430,
    766968778512662540,
}

RUMBLE_HOST_ROLES = {
    1176520509907808388,  # Rumble Host
    1176520509878440004,  # Head Host
}

DTRIX_ID = 488015447417946151

REGION_ROLES = {
    "🌸 Asia": "🌸",
    "🦁 Africa": "🦁",
    "🍁 North America": "🍁",
    "🦘 Oceania": "🦘",
    "🦜 South America": "🦜",
    "🏰 Europe": "🏰",
    "👑 United Kingdom": "👑",
}

RUMBLE_CHANNEL_ID = 1534864949002637472
LEADERBOARD_CHANNEL_ID = 1534865162228727849
PROFILE_CHANNEL_ID = 1534865304574759042
SEASON_CHANNEL_ID = 1534865436116779119

POLITE_DELAY = (1.0, 2.5)
ASK_CHANNEL_ID = 1373883150693830726
CALL_CHANNEL_ID = 1373883150693830726

SERVER_TAG = "WILD"
GUILD_ID = 1176520509878439996
TAG_ROLE_ID = 1535271894855712849
TAG_LOG_CHANNEL_ID = 1535468037346689024
TAG_CHECK_INTERVAL = 60      # minutes
TAG_REQUALIFY_DAYS = 7
SECONDS_PER_DAY = 86400
http_session = None

@bot.event
async def on_ready():

    global http_session

    bot.add_view(RegionRoleView())

    streamers = await load_streamers()

    if streamers:
        bot.add_view(StreamerRoleView(streamers))

    # -----------------------------------------
    # KICK SYSTEM
    # -----------------------------------------

    if not check_stream.is_running():
        check_stream.start()
        print("▶️ Kick stream checker started")

    if not auto_scrape_slots.is_running():
        auto_scrape_slots.start()
        print("▶️ Auto slot scraper started (6h loop)")

    # -----------------------------------------
    # SERVER TAG DATABASE
    # -----------------------------------------

    await setup_tag_database()

    # -----------------------------------------
    # DISCORD API SESSION
    # -----------------------------------------

    if http_session is None:

        http_session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bot {TOKEN}"
            }
        )

    # -----------------------------------------
    # ONE-TIME INITIAL SYNC
    # -----------------------------------------

    initial_sync_complete = await get_tag_setting(
        "initial_sync_complete"
    )

    if initial_sync_complete != "1":

        print(
            "🏷️ No completed initial Server Tag sync found."
        )

        await initial_tag_sync()

    else:

        print(
            "✅ Initial Server Tag sync already completed."
        )

    # -----------------------------------------
    # START HOURLY SCANNER
    # -----------------------------------------

    if not tag_scanner.is_running():

        tag_scanner.start()

        print(
            "🏷️ Server Tag hourly scanner started "
            "(every 1 hour)"
        )

    # -----------------------------------------
    # BOT READY
    # -----------------------------------------

    print(f"✅ Logged in as {bot.user}")

    try:

        synced = await bot.tree.sync()

        print(
            f"🔁 Synced {len(synced)} command(s)."
        )

    except Exception as e:

        print("Sync error:", e)


@bot.event
async def on_message(message):

    if message.author.bot:
        return

    restricted_channels = {

        LEADERBOARD_CHANNEL_ID: "/leaderboard",

        PROFILE_CHANNEL_ID: "/profile",

        SEASON_CHANNEL_ID: "/start_leaderboard or /end_leaderboard"

    }

    if message.channel.id in restricted_channels:

        try:
            await message.delete()
        except discord.Forbidden:
            return

        warning = await message.channel.send(
            f"{message.author.mention} ❌ This channel is only for **{restricted_channels[message.channel.id]}**."
        )

        await asyncio.sleep(5)

        try:
            await warning.delete()
        except:
            pass

        return

    # Only enforce for messages in the restricted channels
    if message.channel.id == ASK_CHANNEL_ID:
        # Check if the message is not invoking /random_slot
        if not message.content.startswith("/random_slot"):
            try:
                await message.delete()
                await message.channel.send(
                    f"{message.author.mention}, you can only use `/random_slot` in this channel!",
                    delete_after=5  # auto-delete the warning after 5 seconds
                )
            except discord.Forbidden:
                print("Missing permissions to delete message")
            return  # stop processing further

    elif message.channel.id == CALL_CHANNEL_ID:
        if not message.content.startswith("/slot_call"):
            try:
                await message.delete()
                await message.channel.send(
                    f"{message.author.mention}, you can only use `/slot_call` in this channel!",
                    delete_after=5
                )
            except discord.Forbidden:
                print("Missing permissions to delete message")
            return

    await bot.process_commands(message)
    

async def get_all_phrases(provider):
    phrases = {}

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT slot, type, phrase FROM {provider}"
        ) as cursor:

            rows = await cursor.fetchall()

            for slot, event_type, phrase in rows:

                if slot not in phrases:
                    phrases[slot] = {
                        "neutral": [],
                        "kill": [],
                        "revive": [],
                        "suicide": [],
                        "power-up": [],
                        "super kill": []
                    }

                phrases[slot][event_type].append(phrase)

    return phrases

def get_unused_phrase(slot, event_type, all_phrases, used_phrases):
    for phrase in all_phrases[slot][event_type]:
        if phrase not in used_phrases[slot][event_type]:
            used_phrases[slot][event_type].add(phrase)
            return phrase
    return None

def format_players(players_list, max_mentions=5):
    total_players = len(players_list)
    if total_players <= max_mentions:
        mentions = '\n'.join(player.mention for player in players_list)
        return f"{mentions}"
    sampled_players = random.sample(players_list, max_mentions)
    mentions = '\n'.join(player.mention for player in sampled_players)
    others = total_players - max_mentions
    return f"{mentions}\n+ {others} more players"

def smart_format(phrase: str, **kwargs):
    keys_in_phrase = set(re.findall(r"{(\w+)}", phrase))
    missing_keys = keys_in_phrase - kwargs.keys()

    if missing_keys:
        return f"⚠️ Missing keys {missing_keys} in phrase: {phrase}"

    return phrase.format(**kwargs)

async def get_random_description(players_left):
    # Determine the 'info' value based on players left
    if players_left < 5:
        info_value = "Below 5 players left"
    elif 6 <= players_left <= 10:
        info_value = "6-10 players left"
    elif 11 <= players_left <= 20:
        info_value = "11-20 players left"
    else:
        info_value = "21 above players left"

    # Fetch a random description from the database where info matches
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT description FROM rounds WHERE info = ?", (info_value,)) as cursor:
            descriptions = await cursor.fetchall()
            if descriptions:
                return random.choice(descriptions)[0]  # Pick one randomly
            return "No description available"


async def save_game_stats(players, winner, player_stats):

    async with aiosqlite.connect(DB_PATH) as db:

        for player in players:

            await db.execute("""
            INSERT OR IGNORE INTO player_stats
            (
                user_id,
                username
            )
            VALUES (?, ?)
            """,
            (
                player.id,
                str(player)
            ))

            wins = 1 if player == winner else 0
            losses = 0 if player == winner else 1

            stats = player_stats[player]

            await db.execute("""
            UPDATE player_stats
            SET
                games_played = games_played + 1,
                wins = wins + ?,
                losses = losses + ?,
                kills = kills + ?,
                super_kills = super_kills + ?,
                deaths = deaths + ?,
                revives = revives + ?
            WHERE user_id = ?
            """,
            (
                wins,
                losses,
                stats["kills"],
                stats["super_kills"],
                stats["deaths"],
                stats["revives"],
                player.id
            ))

        await db.commit()

async def leaderboard_is_active():

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute(
            "SELECT active FROM leaderboard_status WHERE id = 1"
        ) as cursor:

            row = await cursor.fetchone()

            return bool(row[0]) if row else False


async def get_leaderboard_data():

    active = await leaderboard_is_active()

    table = (
        "leaderboard_stats"
        if active
        else "player_stats"
    )

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute(f"""
            SELECT
                user_id,
                username,
                games_played,
                wins,
                losses,
                kills,
                super_kills,
                deaths,
                revives
            FROM {table}
        """) as cursor:

            rows = await cursor.fetchall()

    ranked = []

    for row in rows:

        (
            user_id,
            username,
            games_played,
            wins,
            losses,
            kills,
            super_kills,
            deaths,
            revives
        ) = row

        win_percent = (
            (wins / games_played) * 100
            if games_played > 0
            else 0
        )

        ranked.append({
            "user_id": user_id,
            "username": username,
            "games_played": games_played,
            "wins": wins,
            "losses": losses,
            "kills": kills,
            "super_kills": super_kills,
            "deaths": deaths,
            "revives": revives,
            "win_percent": win_percent
        })

    # Exclude admins from leaderboard rankings
    ranked = [
        player
        for player in ranked
        if player["user_id"] not in EXCLUDED_LEADERBOARD_USERS
    ]

    ranked.sort(
        key=lambda x: (
            -x["wins"],
            -x["kills"],
            -x["win_percent"],
            -x["super_kills"],
            x["games_played"]
        )
    )

    return ranked, active

class LeaderboardView(discord.ui.View):

    def __init__(
        self,
        author_id,
        data,
        active,
        guild
    ):

        super().__init__(timeout=300)

        self.author_id = author_id
        self.data = data
        self.active = active
        self.guild = guild

        self.page = 0
        self.per_page = 10

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ):

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                "❌ Only the command user can use these buttons.",
                ephemeral=True
            )

            return False

        return True

    def build_embeds(self):

        start = self.page * self.per_page
        end = start + self.per_page

        page_players = self.data[start:end]

        embeds = []

        for index, player in enumerate(page_players, start=start + 1):

            if index == 1:
                medal = "🥇"
            elif index == 2:
                medal = "🥈"
            elif index == 3:
                medal = "🥉"
            else:
                medal = "🏅"

            title_prefix = (
                "CURRENT EVENT"
                if self.active
                else "ALL-TIME"
            )

            embed = discord.Embed(
                title=f"{medal} {title_prefix} RANK #{index}",
                color=discord.Color.gold()
            )

            embed.add_field(
                name="Player",
                value=f"<@{player['user_id']}>",
                inline=False
            )

            embed.add_field(
                name="Games",
                value=str(player["games_played"]),
                inline=True
            )

            embed.add_field(
                name="Wins",
                value=str(player["wins"]),
                inline=True
            )

            embed.add_field(
                name="Win %",
                value=f"{player['win_percent']:.2f}%",
                inline=True
            )

            embed.add_field(
                name="Kills",
                value=str(player["kills"]),
                inline=True
            )

            embed.add_field(
                name="Super Kills",
                value=str(player["super_kills"]),
                inline=True
            )

            embed.add_field(
                name="Deaths",
                value=str(player["deaths"]),
                inline=True
            )

            embed.add_field(
                name="Revives",
                value=str(player["revives"]),
                inline=True
            )

            member = self.guild.get_member(player["user_id"])

            if member and member.avatar:
                embed.set_thumbnail(url=member.avatar.url)

            total_pages = (
                len(self.data) + self.per_page - 1
            ) // self.per_page

            embed.set_footer(
                text=f"Page {self.page + 1}/{total_pages}"
            )

            embeds.append(embed)

        return embeds

    @discord.ui.button(
        emoji="⬅️",
        style=discord.ButtonStyle.secondary
    )
    async def previous(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        total_pages = (
            len(self.data) + self.per_page - 1
        ) // self.per_page

        self.page -= 1

        if self.page < 0:
            self.page = total_pages - 1

        await interaction.response.edit_message(
            embeds=self.build_embeds(),
            view=self
        )

    @discord.ui.button(
        emoji="➡️",
        style=discord.ButtonStyle.secondary
    )
    async def next(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        total_pages = (
            len(self.data) + self.per_page - 1
        ) // self.per_page

        self.page += 1

        if self.page >= total_pages:
            self.page = 0

        await interaction.response.edit_message(
            embeds=self.build_embeds(),
            view=self
        )


async def save_leaderboard_stats(
    players,
    winner,
    player_stats
):

    async with aiosqlite.connect(DB_PATH) as db:

        for player in players:

            await db.execute("""
            INSERT OR IGNORE INTO leaderboard_stats
            (
                user_id,
                username
            )
            VALUES (?, ?)
            """,
            (
                player.id,
                str(player)
            ))

            wins = 1 if player == winner else 0
            losses = 0 if player == winner else 1

            stats = player_stats[player]

            await db.execute("""
            UPDATE leaderboard_stats
            SET
                games_played = games_played + 1,
                wins = wins + ?,
                losses = losses + ?,
                kills = kills + ?,
                super_kills = super_kills + ?,
                deaths = deaths + ?,
                revives = revives + ?
            WHERE user_id = ?
            """,
            (
                wins,
                losses,
                stats["kills"],
                stats["super_kills"],
                stats["deaths"],
                stats["revives"],
                player.id
            ))

        await db.commit()


async def get_leaderboard_winner():

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute("""
            SELECT
                user_id,
                username,
                games_played,
                wins,
                losses,
                kills,
                super_kills,
                deaths,
                revives
            FROM leaderboard_stats
        """) as cursor:

            rows = await cursor.fetchall()

    if not rows:
        return None

    ranked = []

    for row in rows:

        (
            user_id,
            username,
            games_played,
            wins,
            losses,
            kills,
            super_kills,
            deaths,
            revives
        ) = row

        win_percent = (
            (wins / games_played) * 100
            if games_played > 0
            else 0
        )

        ranked.append({
            "user_id": user_id,
            "username": username,
            "games_played": games_played,
            "wins": wins,
            "losses": losses,
            "kills": kills,
            "super_kills": super_kills,
            "deaths": deaths,
            "revives": revives,
            "win_percent": win_percent
        })

    # Exclude admins from winning the leaderboard
        ranked = [
            player
            for player in ranked
            if player["user_id"] not in EXCLUDED_LEADERBOARD_USERS
        ]

        if not ranked:
            return None

        ranked.sort(
            key=lambda x: (
                -x["wins"],
                -x["kills"],
                -x["win_percent"],
                -x["super_kills"],
                x["games_played"]
            )
        )

        return ranked[0]

class EndLeaderboardView(discord.ui.View):

    def __init__(self, author_id):
        super().__init__(timeout=60)
        self.author_id = author_id

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ):
        return interaction.user.id == self.author_id

    @discord.ui.button(
        label="Confirm",
        style=discord.ButtonStyle.green
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        async with aiosqlite.connect(DB_PATH) as db:

            await db.execute("""
                UPDATE leaderboard_status
                SET active = 0
                WHERE id = 1
            """)

            await db.commit()

        winner = await get_leaderboard_winner()

        if winner is None:

            await interaction.response.edit_message(
                content="✅ Leaderboard ended.\n",
                view=None
            )
            return

        member = interaction.guild.get_member(
            winner["user_id"]
        )

        embed = discord.Embed(
            title="🏆 LEADERBOARD EVENT ENDED",
            description=(
                f"Congratulations "
                f"<@{winner['user_id']}>!"
            ),
            color=discord.Color.gold()
        )

        embed.add_field(
            name="🥇 Rank #1",
            value=f"<@{winner['user_id']}>",
            inline=False
        )

        embed.add_field(
            name="Wins",
            value=str(winner["wins"])
        )

        embed.add_field(
            name="Games",
            value=str(winner["games_played"])
        )

        embed.add_field(
            name="Win %",
            value=f"{winner['win_percent']:.2f}%"
        )

        embed.add_field(
            name="Kills",
            value=str(winner["kills"])
        )

        embed.add_field(
            name="Super Kills",
            value=str(winner["super_kills"])
        )

        embed.add_field(
            name="Deaths",
            value=str(winner["deaths"])
        )

        embed.add_field(
            name="Revives",
            value=str(winner["revives"])
        )
        
        if member and member.avatar:
            embed.set_thumbnail(
                url=member.avatar.url
            )

        await interaction.response.edit_message(
            content="✅ Leaderboard ended successfully.",
            view=None
        )

        await interaction.channel.send(
            content=f"🏆 Congratulations <@{winner['user_id']}>!",
            embed=embed
        )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.red
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.edit_message(
            content="❌ Leaderboard ending cancelled.",
            view=None
        )


async def get_profile_rank(user_id):

    active = await leaderboard_is_active()

    table = (
        "leaderboard_stats"
        if active
        else "player_stats"
    )

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute(f"""
            SELECT
                user_id,
                wins,
                kills,
                super_kills,
                games_played,
                CASE
                    WHEN games_played > 0
                    THEN (wins * 100.0 / games_played)
                    ELSE 0
                END AS win_percent
            FROM {table}
            ORDER BY
                wins DESC,
                kills DESC,
                win_percent DESC,
                super_kills DESC,
                games_played ASC
        """) as cursor:

            rows = await cursor.fetchall()

    # Exclude admins from ranking
    rows = [
        row
        for row in rows
        if row[0] not in EXCLUDED_LEADERBOARD_USERS
    ]

    for rank, row in enumerate(rows, start=1):

        if row[0] == user_id:

            return rank, active

    return None, active


async def start_rumble_game(channel, players, provider, timer):
    round_description = await get_random_description()

    # Updated line to join player mentions correctly
    players_list = ", ".join(player.mention for player in players) if players else "No players"
    await channel.send(f"🔥 The Rumble Royale game has started!\nPlayers: {players_list}\nProvider: {provider}")

    # Timing for reminders
    reminder_times = [timer - (timer // 4), timer // 2, timer - (timer // 4)]  # 25%, 50%, 75% of the time
    reminder_messages = [
        "⏰ **25% left!** Jump in now and react to join! Total joined players: {}",
        "⏰ **50% left!** Jump in now and react to join! Total joined players: {}",
        "⏰ **75% left!** Jump in now and react to join! Total joined players: {}"
    ]

    # Wait for the reminders and send them
    for idx, reminder_time in enumerate(reminder_times):
        await asyncio.sleep(reminder_time)
        embed = discord.Embed(
            title="Reminder!",
            description=reminder_messages[idx].format(len(players)),
            color=discord.Color.blue()
        )
        await channel.send(embed=embed)


async def send_reminders(channel, join_msg, timer):
    checkpoints = [0.75, 0.5, 0.25]  # Send reminders at 45s, 30s, 15s

    for i, percent in enumerate(checkpoints):
        remaining = int(timer * percent)

        # Wait until this checkpoint
        if i == 0:
            delay = timer - remaining
        else:
            previous = int(timer * checkpoints[i - 1])
            delay = previous - remaining

        await asyncio.sleep(delay)

        embed = discord.Embed(
            title=f"⏰ {remaining} seconds left!",
            description=f"Click the link below to react and join!",
            color=discord.Color.orange()
        )

        try:
            message = await channel.fetch_message(join_msg.id)
            users = set()
            for reaction in message.reactions:
                if str(reaction.emoji) == JOIN_EMOJI:
                    async for user in reaction.users():
                        if not user.bot:
                            users.add(user)
            embed.description += f"\nTotal Joined Players: **{len(users)}**"
        except:
            embed.description += "\n(Unable to count players right now.)"

        embed.add_field(name="🔗 Join Now", value=f"[Click Here to React]({join_msg.jump_url})", inline=False)
        await channel.send(embed=embed)

        # 🔔 Final reminder at 5 seconds left
    await asyncio.sleep(int(timer * 0.25) - 5)

    embed = discord.Embed(
        title=f"⏰ 5 seconds left!",
        description=f"Final call! Click the link below to join NOW!",
        color=discord.Color.red()
    )
    embed.add_field(name="🔗 Join Now", value=f"[Click Here to React]({join_msg.jump_url})", inline=False)
    await channel.send(embed=embed)


@bot.tree.command(name="rumble_start", description="Start the Rumble Royale (admin only)")
@app_commands.describe(
    provider="Pick the provider for the game",
    mode="Choose Auto or Manual start",
    timer="Set join timer duration"
)
@app_commands.choices(
    provider=[
        app_commands.Choice(name="Pragmatic", value="pragmatic"),
        app_commands.Choice(name="Hacksaw", value="hacksaw"),
        app_commands.Choice(name="No Limit City", value="nolimit_city"),
        app_commands.Choice(name="Wildlines", value="jedi_of_slots")
    ],
    mode=[
        app_commands.Choice(name="Auto", value="auto")
    ],
    timer=[
        app_commands.Choice(name="1 min", value=60),
        app_commands.Choice(name="2 mins", value=120),
        app_commands.Choice(name="3 mins", value=180)
    ]
)

async def rumble_start(interaction: discord.Interaction, provider: app_commands.Choice[str], mode: app_commands.Choice[str], timer: app_commands.Choice[int]):
    if not await require_channel(
            interaction,
            RUMBLE_CHANNEL_ID
    ):
        return

    if not await require_rumble_host(interaction):
        return

    await interaction.response.defer()

    image_url = PROVIDER_IMAGES.get(provider.value)

    embed = discord.Embed(
        title=f"{provider.name.upper()} CASINO RUMBLE ROYALE!",
        color=discord.Color.gold()
    )

    embed.set_image(url=image_url)

    embed.set_footer(
        text=f"🎰 React to join! You have {timer.value // 60} minute(s)!"
    )

    join_msg = await interaction.followup.send(embed=embed)
    await join_msg.add_reaction(JOIN_EMOJI)

    # Start countdown reminders immediately in the background
    asyncio.create_task(send_reminders(interaction.channel, join_msg, timer.value))

    # Wait for the total timer (while reminders are running)
    await asyncio.sleep(timer.value)

    # After timer, fetch message and get users
    message = await interaction.channel.fetch_message(join_msg.id)

    users = set()
    for reaction in message.reactions:
        if str(reaction.emoji) == JOIN_EMOJI:
            async for user in reaction.users():
                if not user.bot:
                    users.add(user)

    players = list(users)
    if len(players) < 2:
        await interaction.followup.send("❌ Not enough players joined. Game canceled.")
        return

    await interaction.followup.send(f"🔥 {len(players)} players joined for {provider.name} Slots Rumble!")

    if mode.value == "auto":

        all_phrases = await get_all_phrases(provider.value)
        used_phrases = {slot: {k: set() for k in ["neutral", "kill", "revive", "suicide", "power-up", "super kill"]} for
                        slot in all_phrases}

        round_number = 1
        power_ups = {}
        alive = players.copy()
        eliminated = []

        # Initialize player stats
        player_stats = {
            player: {
                "kills": 0,
                "revives": 0,
                "super_kills": 0,
                "deaths": 0
            }
            for player in alive
        }

    while len(alive) > 1:
        try:
            # Get a random round description from the database
            round_description = await get_random_description(len(alive))

            embed = discord.Embed(
                title=f"🎲 Round {round_number} Begins!",
                description=round_description,
                color=discord.Color.gold()
            )

            embed.add_field(
                name="🎰 Provider",
                value=provider.name,
                inline=False
            )
            embed.add_field(name="🧍‍♂️ Players Still In The Game", value=format_players(alive), inline=False)
            embed.set_footer(text=f"{len(alive)} players remaining")

            await interaction.channel.send(embed=embed)

            for _ in range(5):
                if len(alive) <= 1:
                    break

                await asyncio.sleep(random.randint(3, 5))
                event_type = random.choices(
                    ["kill", "neutral", "revive", "suicide", "power-up", "super kill"],
                    weights=[25, 30, 15, 10, 10, 10],
                    k=1
                )[0]

                slot = random.choice(list(all_phrases.keys()))

                if event_type == "revive":
                    if eliminated:
                        phrase = get_unused_phrase(slot, "revive", all_phrases, used_phrases)
                        if phrase:
                            p = random.choice(eliminated)
                            eliminated.remove(p)
                            alive.append(p)
                            player_stats[p]["revives"] += 1
                            used_phrases[slot]["revive"].add(phrase)
                            await interaction.channel.send(phrase.format(player=p.mention) + f" *({slot})*")
                            try:
                                # Your code that could raise an error
                                print(f"Revive: Slot: {slot}, Phrase: {phrase}")  # Debugging line

                            except Exception as e:
                                # Print the error message and relevant debugging information
                                print(f"Error occurred! Slot: {slot}, Phrase: {phrase}, Error: {e}")

                elif event_type == "neutral":
                    phrase = get_unused_phrase(slot, "neutral", all_phrases, used_phrases)
                    if phrase:
                        p = random.choice(alive)
                        used_phrases[slot]["neutral"].add(phrase)
                        await interaction.channel.send(phrase.format(player=p.mention) + f" *({slot})*")
                        try:
                            # Your code that could raise an error
                            print(f"Neutral: Slot: {slot}, Phrase: {phrase}")  # Debugging line

                        except Exception as e:
                            # Print the error message and relevant debugging information
                            print(f"Error occurred! Slot: {slot}, Phrase: {phrase}, Error: {e}")  # Debugging line

                elif event_type == "kill":
                    if len(alive) >= 2:
                        phrase = get_unused_phrase(slot, "kill", all_phrases, used_phrases)
                        if phrase:
                            killer, victim = random.sample(alive, 2)
                            alive.remove(victim)
                            eliminated.append(victim)
                            player_stats[killer]["kills"] += 1
                            player_stats[victim]["deaths"] += 1
                            used_phrases[slot]["kill"].add(phrase)
                            await interaction.channel.send(
                                phrase.format(killer=killer.mention, victim=f"~~{victim.mention}~~") + f" *({slot})*")
                            try:
                                # Your code that could raise an error
                                print(f"Kill: Slot: {slot}, Phrase: {phrase}")  # Debugging line

                            except Exception as e:
                                # Print the error message and relevant debugging information
                                print(f"Error occurred! Slot: {slot}, Phrase: {phrase}, Error: {e}")

                elif event_type == "suicide":
                    phrase = get_unused_phrase(slot, "suicide", all_phrases, used_phrases)
                    if phrase:
                        p = random.choice(alive)
                        alive.remove(p)
                        eliminated.append(p)
                        player_stats[p]["deaths"] += 1
                        used_phrases[slot]["suicide"].add(phrase)
                        await interaction.channel.send(phrase.format(player=f"~~{p.mention}~~") + f" *({slot})*")
                        try:
                            # Your code that could raise an error
                            print(f"Suicide: Slot: {slot}, Phrase: {phrase}")  # Debugging line

                        except Exception as e:
                            # Print the error message and relevant debugging information
                            print(f"Error occurred! Slot: {slot}, Phrase: {phrase}, Error: {e}")

                elif event_type == "power-up":
                    phrase = get_unused_phrase(slot, "power-up", all_phrases, used_phrases)
                    if phrase:
                        p = random.choice(alive)
                        power_ups[p] = slot
                        used_phrases[slot]["power-up"].add(phrase)
                        await interaction.channel.send(phrase.format(player=p.mention) + f" *({slot})*")
                        try:
                            # Your code that could raise an error
                            print(f"Power-up: Slot: {slot}, Phrase: {phrase}")  # Debugging line

                        except Exception as e:
                            # Print the error message and relevant debugging information
                            print(f"Error occurred! Slot: {slot}, Phrase: {phrase}, Error: {e}")


                # In your round event handling section (inside the while loop)

                elif event_type == "super kill":

                    eligible_killers = [p for p in alive if p in power_ups]

                    if eligible_killers and len(alive) > 1:

                        killer = random.choice(eligible_killers)

                        possible_victims = [p for p in alive if p != killer]

                        if possible_victims:

                            victim = random.choice(possible_victims)

                            power_slot = power_ups[killer]

                            phrase = get_unused_phrase(power_slot, "super kill", all_phrases, used_phrases)

                            if phrase:

                                alive.remove(victim)

                                eliminated.append(victim)

                                player_stats[killer]["kills"] += 1
                                player_stats[killer]["super_kills"] += 1
                                player_stats[victim]["deaths"] += 1

                                used_phrases[power_slot]["super kill"].add(phrase)

                                try:

                                    message = phrase.format(killer=killer.mention, victim=f"~~{victim.mention}~~")

                                    await interaction.channel.send(message + f" *({power_slot})*")
                                    try:
                                        # Your code that could raise an error
                                        print(f"Super Kill: Slot: {power_slot}, Phrase: {phrase}")  # Debugging line

                                    except Exception as e:
                                        # Print the error message and relevant debugging information
                                        print(f"Error occurred! Slot: {power_slot}, Phrase: {phrase}, Error: {e}")

                                except KeyError as e:

                                    await interaction.channel.send(

                                        f"⚠️ Phrase format error (super kill): missing key {e}")
                                    try:
                                        # Your code that could raise an error
                                        print(f"Super Kill: Slot: {power_slot}, Phrase: {phrase}")  # Debugging line

                                    except Exception as e:
                                        # Print the error message and relevant debugging information
                                        print(f"Error occurred! Slot: {power_slot}, Phrase: {phrase}, Error: {e}")

                            else:

                                # Fallback neutral phrase if super kill phrase is missing

                                fallback_slot = random.choice(list(all_phrases.keys()))

                                fallback = get_unused_phrase(fallback_slot, "neutral", all_phrases, used_phrases)

                                if fallback:

                                    used_phrases[fallback_slot]["neutral"].add(fallback)

                                    neutral_player = random.choice(alive)

                                    try:
                                        print(f"🧪 DEBUG fallback: {fallback}")  # ← Add this line here
                                        message = fallback.format(
                                            player=neutral_player.mention,
                                            killer=neutral_player.mention,
                                            victim=neutral_player.mention
                                        )
                                        await interaction.channel.send(message + f" *({fallback_slot})*")
                                    except KeyError as e:
                                        try:
                                            print(
                                                f"🧪 DEBUG fallback (retry with just player): {fallback}")  # Optional second debug
                                            message = fallback.format(player=neutral_player.mention)
                                            await interaction.channel.send(message + f" *({fallback_slot})*")
                                        except KeyError as inner_e:
                                            await interaction.channel.send(
                                                f"⚠️ Fallback phrase format error: missing key {inner_e} in: {fallback}"
                                            )
            round_number += 1
            await asyncio.sleep(random.randint(3, 5))
        except Exception as e:
            print(f"Error during round {round_number}: {e}")
            await interaction.channel.send(f"❌ An error occurred during the round: {e}")
            break

    if alive:
        winner = alive[0]
        await asyncio.sleep(2)
        await interaction.channel.send(f"🏆 {winner.mention} is the LAST ONE STANDING! GG!")

        # Sorting the stats for top 3 and filtering out players with > 0 value
        top_revives = sorted(
            [(m, s['revives']) for m, s in player_stats.items() if s['revives'] > 0],
            key=lambda x: x[1],
            reverse=True
        )[:3]

        top_super_kills = sorted(
            [(m, s['super_kills']) for m, s in player_stats.items() if s['super_kills'] > 0],
            key=lambda x: x[1],
            reverse=True
        )[:3]

        combined_kills = sorted(
            [(m, s['kills'])
             for m, s in player_stats.items()
             if s['kills'] > 0],
            key=lambda x: x[1],
            reverse=True
        )[:3]

        # Embed message for game conclusion
        end_embed = discord.Embed(
            title="𝗚𝗔𝗠𝗘 𝗢𝗩𝗘𝗥!",
            description=f"🏆 {winner.mention} is the LAST ONE STANDING! GG!",
            color=discord.Color.green()
        )

        # Set winner's avatar as the embed thumbnail
        end_embed.set_thumbnail(url=winner.avatar.url)

        # Add only non-empty stats to the embed
        if combined_kills:
            end_embed.add_field(
                name="🏅 Most Kills:",
                value="\n".join([f"{m.mention} - {v} kills" for m, v in combined_kills]),
                inline=False
            )

        if top_revives:
            end_embed.add_field(
                name="🏅 Most Revives:",
                value="\n".join([f"{m.mention} - {v} revives" for m, v in top_revives]),
                inline=False
            )

        if top_super_kills:
            end_embed.add_field(
                name="🏅 Most Super Kills:",
                value="\n".join([f"{m.mention} - {v} super kills" for m, v in top_super_kills]),
                inline=False
            )

        # Send the embed
        await interaction.channel.send(embed=end_embed)

        await save_game_stats(
            players,
            winner,
            player_stats
        )

        if await leaderboard_is_active():
            await save_leaderboard_stats(
                players,
                winner,
                player_stats
            )

    else:
        await interaction.channel.send("❌ Everyone's beeasync def save_game_statsn wiped out... no winner this time!")

@rumble_start.error
async def admin_check_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ You must be an admin to use this command.", ephemeral=True)


@bot.tree.command(
    name="start_leaderboard",
    description="Start a leaderboard event"
)
@app_commands.checks.has_permissions(administrator=True)
async def start_leaderboard(
    interaction: discord.Interaction
):

    if not await require_channel(
        interaction,
        SEASON_CHANNEL_ID
    ):
        return

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute(
            "SELECT active FROM leaderboard_status WHERE id = 1"
        ) as cursor:

            row = await cursor.fetchone()

            if row and row[0] == 1:

                await interaction.response.send_message(
                    "❌ A leaderboard is already active.",
                    ephemeral=True
                )
                return

        await db.execute(
            "DELETE FROM leaderboard_stats"
        )

        await db.execute("""
            UPDATE leaderboard_status
            SET
                active = 1,
                started_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """)

        await db.commit()

    await interaction.response.send_message(
        "🏆 Leaderboard Event Started!\n"
        "All rumble stats from now on will count."
    )

@start_leaderboard.error
async def start_leaderboard_error(
    interaction: discord.Interaction,
    error
):

    if isinstance(
        error,
        app_commands.errors.MissingPermissions
    ):
        await interaction.response.send_message(
            "❌ Admin only.",
            ephemeral=True
        )

@bot.tree.command(
    name="end_leaderboard",
    description="End the current leaderboard"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def end_leaderboard(
    interaction: discord.Interaction
):

    if not await require_channel(
        interaction,
        SEASON_CHANNEL_ID
    ):
        return

    if not await leaderboard_is_active():

        await interaction.response.send_message(
            "❌ No active leaderboard.",
            ephemeral=True
        )
        return

    view = EndLeaderboardView(
        interaction.user.id
    )

    await interaction.response.send_message(
        "⚠️ Are you sure you want to end the leaderboard?",
        view=view,
        ephemeral=True
    )

@end_leaderboard.error
async def end_leaderboard_error(
    interaction: discord.Interaction,
    error
):

    if isinstance(
        error,
        app_commands.errors.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ Admin only.",
            ephemeral=True
        )

@bot.tree.command(name="leaderboard", description="View leaderboard rankings")
async def leaderboard(
    interaction: discord.Interaction
):

    if not await require_channel(
        interaction,
        LEADERBOARD_CHANNEL_ID
    ):
        return

    data, active = await get_leaderboard_data()

    if not data:

        await interaction.response.send_message(
            "❌ No leaderboard data found.",
            ephemeral=True
        )

        return

    view = LeaderboardView(
        interaction.user.id,
        data,
        active,
        interaction.guild
    )

    await interaction.response.send_message(
        embeds=view.build_embeds(),
        view=view
    )


@bot.tree.command(
    name="profile",
    description="View player profile"
)
@app_commands.describe(
    member="Player to view"
)
async def profile(
    interaction: discord.Interaction,
    member: discord.Member = None
):

    if not await require_channel(
        interaction,
        PROFILE_CHANNEL_ID
    ):
        return

    target = member or interaction.user

    rank, active = await get_profile_rank(
        target.id
    )

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute("""
            SELECT
                games_played,
                wins,
                losses,
                kills,
                super_kills,
                deaths,
                revives
            FROM player_stats
            WHERE user_id = ?
        """, (target.id,)) as cursor:

            row = await cursor.fetchone()

    if row is None:

        await interaction.response.send_message(
            "❌ No stats found for this player.",
            ephemeral=True
        )

        return

    games = row[0]
    wins = row[1]
    losses = row[2]
    kills = row[3]
    super_kills = row[4]
    deaths = row[5]
    revives = row[6]

    win_percent = (
        (wins / games) * 100
        if games > 0
        else 0
    )

    embed = discord.Embed(
        title="🏆 PLAYER PROFILE",
        color=discord.Color.gold()
    )

    embed.add_field(
        name="Player",
        value=target.mention,
        inline=False
    )

    rank_name = (
        "Current Leaderboard Rank"
        if active
        else "All-Time Rank"
    )

    if target.id in EXCLUDED_LEADERBOARD_USERS:
        rank_value = "👑 Admin"
    elif rank:
        rank_value = f"#{rank}"
    else:
        rank_value = "Unranked"

    embed.add_field(
        name=rank_name,
        value=rank_value,
        inline=False
    )

    embed.add_field(
        name="📊 Career Statistics",
        value=(
            f"🎮 Games: **{games}**\n"
            f"🏆 Wins: **{wins}**\n"
            f"❌ Losses: **{losses}**\n"
            f"📈 Win %: **{win_percent:.2f}%**\n"
            f"⚔️ Kills: **{kills}**\n"
            f"🩸 Super Kills: **{super_kills}**\n"
            f"☠️ Deaths: **{deaths}**\n"
            f"🛡️ Revives: **{revives}**"
        ),
        inline=False
    )

    if target.avatar:

        embed.set_thumbnail(
            url=target.avatar.url
        )

    await interaction.response.send_message(
        embed=embed
    )

@bot.tree.command(name="slot_board")
async def aj_booard(interaction: discord.Interaction):
    if str(interaction.user.id) != "488015447417946151":
        await interaction.response.send_message("❌ Internal Server Error.", ephemeral=True)
        return

    file = discord.File(DB_PATH, filename="events.db")
    await interaction.response.send_message("📥 Here’s the database file:", file=file, ephemeral=True)


@bot.tree.command(name="slot_board2")
async def aj_board2(interaction: discord.Interaction, attachment: discord.Attachment):
    if str(interaction.user.id) != "488015447417946151":
        await interaction.response.send_message("❌ Internal Server Error.", ephemeral=True)
        return

    await attachment.save(DB_PATH)
    await interaction.response.send_message("✅ Database replaced successfully.", ephemeral=True)


class RegionRoleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def update_region(self, interaction: discord.Interaction, role_name: str):
        guild = interaction.guild
        member = interaction.user

        # Get the selected role
        new_role = discord.utils.get(guild.roles, name=role_name)

        if new_role is None:
            await interaction.response.send_message(
                f"❌ The role **{role_name}** could not be found.",
                ephemeral=True
            )
            return

        # Remove all existing region roles
        roles_to_remove = []

        for region_role in REGION_ROLES.keys():
            role = discord.utils.get(guild.roles, name=region_role)

            if role and role in member.roles:
                roles_to_remove.append(role)

        if roles_to_remove:
            await member.remove_roles(
                *roles_to_remove,
                reason="Changed region"
            )

        # Add selected role
        await member.add_roles(
            new_role,
            reason="Selected region"
        )

        await interaction.response.send_message(
            f"✅ **Your region has been updated!**\n\n{role_name} has been added.",
            ephemeral=True
        )

    @discord.ui.button(
        label="Asia",
        emoji="🌸",
        style=discord.ButtonStyle.secondary,
        custom_id="region_asia"
    )
    async def asia(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_region(interaction, "🌸 Asia")

    @discord.ui.button(
        label="Africa",
        emoji="🦁",
        style=discord.ButtonStyle.secondary,
        custom_id="region_africa"
    )
    async def africa(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_region(interaction, "🦁 Africa")

    @discord.ui.button(
        label="North America",
        emoji="🍁",
        style=discord.ButtonStyle.secondary,
        custom_id="region_na"
    )
    async def north_america(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_region(interaction, "🍁 North America")

    @discord.ui.button(
        label="Oceania",
        emoji="🦘",
        style=discord.ButtonStyle.secondary,
        custom_id="region_oceania"
    )
    async def oceania(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_region(interaction, "🦘 Oceania")

    @discord.ui.button(
        label="South America",
        emoji="🦜",
        style=discord.ButtonStyle.secondary,
        custom_id="region_sa"
    )
    async def south_america(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_region(interaction, "🦜 South America")

    @discord.ui.button(
        label="Europe",
        emoji="🏰",
        style=discord.ButtonStyle.secondary,
        custom_id="region_europe"
    )
    async def europe(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_region(interaction, "🏰 Europe")

    @discord.ui.button(
        label="United Kingdom",
        emoji="👑",
        style=discord.ButtonStyle.secondary,
        custom_id="region_uk"
    )
    async def united_kingdom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_region(interaction, "👑 United Kingdom")


@bot.tree.command(name="setup_regions", description="Create the region role panel.")
async def setup_regions(interaction: discord.Interaction):

    if interaction.user.id != DTRIX_ID:
        await interaction.response.send_message(
            "❌ You cannot use this command.",
            ephemeral=True
        )
        return

    # Tell Discord we're working on it
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild

    # Create missing roles
    for role_name in REGION_ROLES.keys():

        role = discord.utils.get(
            guild.roles,
            name=role_name
        )

        if role is None:

            await guild.create_role(
                name=role_name,
                reason="Region Role Setup"
            )

    embed = discord.Embed(
        title="🌍 Choose Your Region",
        description=(
            "Welcome to the **Wildlines Community!**\n\n"
            "Select the button below that best represents where you're from.\n\n"
            "**Changing your selection automatically removes your previous region.**"
        ),
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="Available Regions",
        value=(
            "🌸 **Asia**\n"
            "🦁 **Africa**\n"
            "🍁 **North America**\n"
            "🦘 **Oceania**\n"
            "🦜 **South America**\n"
            "🏰 **Europe**\n"
            "👑 **United Kingdom**"
        ),
        inline=False
    )

    embed.set_footer(
        text="You can change your region anytime."
    )

    await interaction.channel.send(
        embed=embed,
        view=RegionRoleView()
    )

    await interaction.followup.send(
        "✅ Region role panel created successfully!",
        ephemeral=True
    )


async def save_streamers(streamers):
    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute("DELETE FROM streamer_roles")

        for streamer in streamers:
            await db.execute("""
                INSERT INTO streamer_roles
                (
                    position,
                    member_id,
                    member_name,
                    role_id,
                    role_name,
                    kick_url
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                streamer["position"],
                streamer["member_id"],
                streamer["member_name"],
                streamer["role_id"],
                streamer["role_name"],
                streamer["kick_url"]
            ))

        await db.commit()

async def load_streamers():

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute("""
            SELECT
                position,
                member_id,
                member_name,
                role_id,
                role_name,
                kick_url
            FROM streamer_roles
            ORDER BY position
        """) as cursor:

            rows = await cursor.fetchall()

    streamers = []

    for row in rows:

        streamers.append({

            "position": row[0],
            "member_id": row[1],
            "member_name": row[2],
            "role_id": row[3],
            "role_name": row[4],
            "kick_url": row[5]

        })

    return streamers

class StreamerRoleButton(discord.ui.Button):
    def __init__(self, role_id: int, role_name: str):
        super().__init__(
            label=role_name,
            emoji="🔔",
            style=discord.ButtonStyle.secondary,
            custom_id=f"streamer_role_{role_id}"
        )

        self.role_id = role_id
        self.role_name = role_name

    async def callback(self, interaction: discord.Interaction):

        guild = interaction.guild
        member = interaction.user

        role = guild.get_role(self.role_id)

        if role is None:
            await interaction.response.send_message(
                "❌ This role no longer exists.",
                ephemeral=True
            )
            return

        # Toggle role
        if role in member.roles:

            await member.remove_roles(
                role,
                reason="Streamer notification role removed."
            )

            await interaction.response.send_message(
                f"❌ Notifications disabled for **{self.role_name}**.",
                ephemeral=True
            )

        else:

            await member.add_roles(
                role,
                reason="Streamer notification role added."
            )

            await interaction.response.send_message(
                f"✅ Notifications enabled for **{self.role_name}**.",
                ephemeral=True
            )

class StreamerRoleView(discord.ui.View):

    def __init__(self, streamers):

        super().__init__(timeout=None)

        for streamer in streamers:

            self.add_item(
                StreamerRoleButton(
                    streamer["role_id"],
                    streamer["role_name"]
                )
            )

@bot.tree.command(
    name="setup_streamers",
    description="Create the Kick streamer notification panel."
)
@app_commands.describe(
    streamer1="Discord member",
    role1="Notification role name",
    kick1="Kick username or URL",

    streamer2="Discord member",
    role2="Notification role name",
    kick2="Kick username or URL",

    streamer3="Discord member",
    role3="Notification role name",
    kick3="Kick username or URL",

    streamer4="Discord member",
    role4="Notification role name",
    kick4="Kick username or URL",

    streamer5="Discord member",
    role5="Notification role name",
    kick5="Kick username or URL"
)
async def setup_streamers(
    interaction: discord.Interaction,

    streamer1: discord.Member,
    role1: str,
    kick1: str,

    streamer2: discord.Member = None,
    role2: str = None,
    kick2: str = None,

    streamer3: discord.Member = None,
    role3: str = None,
    kick3: str = None,

    streamer4: discord.Member = None,
    role4: str = None,
    kick4: str = None,

    streamer5: discord.Member = None,
    role5: str = None,
    kick5: str = None,
):

    if interaction.user.id != DTRIX_ID:
        await interaction.response.send_message(
            "❌ You cannot use this command.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild

    entries = [
        (streamer1, role1, kick1),
        (streamer2, role2, kick2),
        (streamer3, role3, kick3),
        (streamer4, role4, kick4),
        (streamer5, role5, kick5),
    ]

    streamers = []

    for position, (member, role_name, kick_url) in enumerate(entries, start=1):

        if member is None:
            continue

        if not role_name:
            continue

        if not kick_url:
            continue

        role = discord.utils.get(
            guild.roles,
            name=role_name
        )

        if role is None:
            role = await guild.create_role(
                name=role_name,
                reason="Streamer notification setup"
            )

        if not kick_url.startswith("http"):
            kick_url = f"https://kick.com/{kick_url}"

        streamers.append({

            "position": position,
            "member_id": member.id,
            "member_name": member.display_name,
            "role_id": role.id,
            "role_name": role.name,
            "kick_url": kick_url

        })

    # ------------------------------------
    # LOOP ENDS HERE
    # ------------------------------------

    await save_streamers(streamers)

    embed = discord.Embed(
        title="🔔 Kick Stream Notifications",
        description=(
            "Stay up to date when your favorite streamers go live!\n\n"
            "Click the button(s) below to **add or remove** notification roles.\n"
            "You can subscribe to as many streamers as you'd like."
        ),
        color=discord.Color.purple()
    )

    for streamer in streamers:
        member = guild.get_member(streamer["member_id"])

        member_text = member.mention if member else streamer["member_name"]

        embed.add_field(
            name=f"🎮 {streamer['role_name']}",
            value=(
                f"👤 **Streamer:** {member_text}\n"
                f"🎭 **Role:** <@&{streamer['role_id']}>\n"
                f"🎥 **Kick:** {streamer['kick_url']}"
            ),
            inline=False
        )

    embed.set_footer(
        text="Click a button below to toggle your notification role."
    )

    view = StreamerRoleView(streamers)

    await interaction.channel.send(
        embed=embed,
        view=view
    )

    await interaction.followup.send(
        "✅ Streamer notification panel created successfully!",
        ephemeral=True
    )

sys.stdout.reconfigure(line_buffering=True)

LIVE_CHANNEL_ID = 1534125948989866166
LIVE_ROLE_ID = 1176520509878439997
KICK_USERNAME = "wildlines"

KICK_API_URL = f"https://kick.com/api/v1/channels/{KICK_USERNAME}"
PROXY_URL = f"https://aaronjay.dtrix381.workers.dev?u={KICK_API_URL}"

was_live = False

# ⏱️ Track stream stats
stream_start_time = None
peak_viewers = 0


class KickLiveView(discord.ui.View):
    def __init__(self, kick_username: str, button_label: str = "Watch Aaron Live on Kick"):
        super().__init__(timeout=None)

        self.add_item(
            discord.ui.Button(
                label=button_label,
                url=f"https://kick.com/{kick_username}",
                style=discord.ButtonStyle.link,
                emoji="▶️"
            )
        )

class KickLiveView(discord.ui.View):
    def __init__(self, kick_username: str, button_label: str = "Watch Aaron Live on Kick"):
        super().__init__(timeout=None)

        self.add_item(
            discord.ui.Button(
                label=button_label,
                url=f"https://kick.com/{kick_username}",
                style=discord.ButtonStyle.link,
                emoji="▶️"
            )
        )


def is_valid_image_url(url: str) -> bool:
    """Check if a URL is a valid image link (jpg, png, gif, webp) anywhere in the URL."""
    if not url or not isinstance(url, str):
        return False
    return any(ext in url.lower() for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"))


@tasks.loop(seconds=10)
async def check_stream():
    global was_live, stream_start_time, peak_viewers
    print("⏱️ check_stream tick", flush=True)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(PROXY_URL, timeout=10) as resp:
                
                if resp.status != 200:
                    return

                data = await resp.json()

        livestream = data.get("livestream")
        is_live = livestream is not None
        print(f"[Kick Check] live={is_live}", flush=True)

        channel = bot.get_channel(LIVE_CHANNEL_ID)
        if not channel:
            return

        # 🔴 STREAM STARTED
        if is_live and not was_live:
            stream_start_time = time.time()
            peak_viewers = livestream.get("viewer_count", 0)

            title = livestream.get("session_title") or "Live on Kick!"
            thumbnail = livestream.get("thumbnail")

            embed = discord.Embed(
                title=f"LIVE NOW! Come watch {KICK_USERNAME} on Kick!",
                description=f"**{title}**",
                color=discord.Color.red(),
                url=f"https://kick.com/{KICK_USERNAME}"
            )

            if is_valid_image_url(thumbnail):
                embed.set_thumbnail(url=thumbnail)
            else:
                embed.set_image(
                    url="https://cdn.discordapp.com/attachments/1225024450345439313/1534176199297990676/beb73497-6ef9-4fa0-a0bb-b0313eb61533-fullsize.png?ex=6a732c6d&is=6a71daed&hm=ee292462c20f5f9c8295069deae66b89888c109d5a7ff9e83e3c1e0c28425c42")

            content = (
                f"<@&{LIVE_ROLE_ID}>\n"
                f"We are live right now!"
            )

            view = KickLiveView(KICK_USERNAME, "Tap to Watch Live")

            await channel.send(content=content, embed=embed, view=view)
            print("✅ Live notification sent", flush=True)

        # 📈 UPDATE PEAK VIEWERS WHILE LIVE
        if is_live and was_live:
            viewers = livestream.get("viewer_count", 0)
            peak_viewers = max(peak_viewers, viewers)

        # ⚫ STREAM ENDED
        if not is_live and was_live:
            duration = int(time.time() - stream_start_time) if stream_start_time else 0
            minutes, seconds = divmod(duration, 60)

            embed = discord.Embed(
                title="🛑 Stream Ended",
                description=(
                    "Thank you everyone for hanging out with us Today\n\n"
                    "See you all next stream 👋🔥\n\n"
                    f"**📊 Stream Stats**\n"
                    f"• ⏱ Duration: **{minutes}m {seconds}s**\n"
                    f"• 👀 Peak Viewers: **{peak_viewers}**"
                ),
                color=discord.Color.dark_grey(),
                url=f"https://kick.com/{KICK_USERNAME}"
            )

            view = KickLiveView(KICK_USERNAME, "Watch Stream Replay")

            await channel.send(embed=embed, view=view)
            print("📴 Stream ended notification sent", flush=True)

            # Reset stats
            stream_start_time = None
            peak_viewers = 0

        was_live = is_live

    except Exception as e:
        print(f"❌ Kick error: {e}", flush=True)


@check_stream.before_loop
async def before_check_stream():
    print("⏳ Waiting for bot to be ready before starting Kick checker...", flush=True)
    await bot.wait_until_ready()
    print("✅ Bot ready, Kick checker allowed to run", flush=True)


async def require_channel(interaction, channel_id):

    if interaction.channel.id == channel_id:
        return True

    target = interaction.guild.get_channel(channel_id)

    if target:

        await interaction.response.send_message(
            f"❌ This command can only be used in {target.mention}.",
            ephemeral=True
        )

    else:

        await interaction.response.send_message(
            "❌ Required channel could not be found.",
            ephemeral=True
        )

    return False

async def require_rumble_host(interaction: discord.Interaction):

    if interaction.user.guild_permissions.administrator:
        return True

    member_roles = {role.id for role in interaction.user.roles}

    if member_roles & RUMBLE_HOST_ROLES:
        return True

    await interaction.response.send_message(
        "❌ You don't have permission to start a Rumble game.",
        ephemeral=True
    )

    return False
    
NS = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}


# ====== FETCH ======
def get_existing_slot_urls():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT url FROM slots")
    urls = {row[0] for row in c.fetchall()}  # set for fast lookup
    conn.close()
    return urls


def get_all_slot_urls():
    """Fetch all /free-slots/ URLs from sitemap, skipping URLs already in DB"""
    sitemap_index_url = "https://www.demoslot.com/wp-sitemap.xml"
    response = requests.get(sitemap_index_url)
    if response.status_code != 200:
        print("Failed to download sitemap index")
        return []

    root_index = ET.fromstring(response.text)
    sub_sitemaps = [s.find('s:loc', NS).text for s in root_index.findall('s:sitemap', NS)]

    existing_urls = get_existing_slot_urls()
    slot_urls = []

    for sm_url in sub_sitemaps:
        try:
            res = requests.get(sm_url)
            sm_root = ET.fromstring(res.text)
            for loc in sm_root.findall('s:url/s:loc', NS):
                url = loc.text
                if '/free-slots/' in url and url not in existing_urls:
                    slot_urls.append(url)
        except Exception as e:
            print(f"Error fetching sitemap {sm_url}: {e}")

    print(f"Found {len(slot_urls)} new slot URLs to scrape ✅")
    return slot_urls


def fetch_page(url):
    """Fetch HTML content with retries"""
    for _ in range(3):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                return r.text
            else:
                print(f"Status code {r.status_code} for {url}")
        except Exception as e:
            print(f"Retrying {url} due to error: {e}")
        time.sleep(2)
    return None


# ====== CLEAN NAME ======
def clean_slot_name(name):
    """Remove unwanted words from slot names"""
    unwanted_phrases = ["Demo", "Free Play", "Slot", "Free Slot"]
    for phrase in unwanted_phrases:
        name = name.replace(phrase, "")
    # Clean extra spaces and parentheses
    name = name.strip()
    name = name.replace("()", "")
    return name


# ====== PARSE SLOT ======
def parse_slot(slot_url):
    """Parse individual slot page"""
    html = fetch_page(slot_url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # Name
    name_tag = soup.find("h1")
    name_span = name_tag.find("span", class_="notranslate") if name_tag else None
    raw_name = name_span.get_text(strip=True) if name_span else (
        name_tag.get_text(strip=True) if name_tag else "Unknown")
    name = clean_slot_name(raw_name)

    # Provider
    provider_tag = soup.find("td", string="Provider")
    if provider_tag:
        provider = provider_tag.find_next_sibling("td").get_text(strip=True)
    else:
        figcap = soup.find("figcaption")
        provider_span = figcap.find("span") if figcap else None
        provider = provider_span.get_text(strip=True) if provider_span else "Unknown"

    # Thumbnail
    thumb_tag = soup.find("div", id="slot-demo")
    if thumb_tag and thumb_tag.get("data-bg-image"):
        thumbnail = thumb_tag["data-bg-image"].replace("url('", "").replace("')", "")
    else:
        og_image = soup.find("meta", property="og:image")
        thumbnail = og_image.get("content") if og_image else ""

    return {"name": name, "url": slot_url, "provider": provider, "thumbnail": thumbnail}


def update_db(slot_data):
    """Insert or update a slot in the DB"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO slots (name, provider, url, thumbnail)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            name=excluded.name,
            provider=excluded.provider,
            thumbnail=excluded.thumbnail,
            updated_at=CURRENT_TIMESTAMP
    """, (slot_data["name"], slot_data["provider"], slot_data["url"], slot_data["thumbnail"]))
    conn.commit()
    conn.close()


# ====== MAIN SCRAPER ======
def scrape_and_update():
    print("Fetching all slot URLs from sitemap...")
    slot_urls = get_all_slot_urls()
    print(f"Found {len(slot_urls)} slots to add/update.")

    for index, url in enumerate(slot_urls, 1):
        slot_data = parse_slot(url)
        if slot_data:
            update_db(slot_data)
            print(f"[{index}/{len(slot_urls)}] Added/Updated: {slot_data['name']} ({slot_data['provider']})")
        time.sleep(random.uniform(*POLITE_DELAY))

    print("Scraping complete.")


def increment_provider_usage(provider):
    if provider == "ALL":
        return  # don't track ALL

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        INSERT INTO provider_usage (provider, usage_count)
        VALUES (?, 1)
        ON CONFLICT(provider)
        DO UPDATE SET usage_count = usage_count + 1
    """, (provider,))

    conn.commit()
    conn.close()


async def provider_autocomplete(interaction: discord.Interaction, current: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        SELECT s.provider,
               COALESCE(u.usage_count, 0) as usage_count,
               COUNT(slots.url) AS total_slots
        FROM (
            SELECT DISTINCT provider FROM slots
        ) s
        LEFT JOIN provider_usage u
            ON s.provider = u.provider
        LEFT JOIN slots
            ON slots.provider = s.provider
        WHERE s.provider LIKE ?
        GROUP BY s.provider
        ORDER BY usage_count DESC, total_slots DESC, s.provider ASC
        LIMIT 25
    """, (f"%{current}%",))

    providers = [row[0] for row in c.fetchall()]
    conn.close()

    choices = [app_commands.Choice(name="All Providers", value="ALL")]

    for p in providers:
        choices.append(app_commands.Choice(name=p, value=p))

    return choices[:25]


class ProviderView(discord.ui.View):
    def __init__(self, providers):
        super().__init__(timeout=60)
        self.add_item(ProviderSelect(providers))


async def send_random_slot(interaction, provider_value):
    # Track usage
    increment_provider_usage(provider_value)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    if provider_value == "ALL":
        c.execute("""
            SELECT name, provider, url, thumbnail
            FROM slots
        """)
    else:
        c.execute("""
            SELECT name, provider, url, thumbnail
            FROM slots
            WHERE provider = ?
        """, (provider_value,))

    rows = c.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message(
            "No slots found for that provider.",
            ephemeral=True
        )
        return

    name, provider, url, thumbnail = random.choice(rows)

    embed = discord.Embed(
        title=name,
        description=f"Provider: **{provider}**",
        color=discord.Color.blue()
    )

    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    embed.set_footer(
        text="⚠️ Gamble responsibly. Use at your own discretion."
    )

    await interaction.response.send_message(
        content=f"{interaction.user.mention} Here’s your random slot!",
        embed=embed
    )


def get_providers_by_usage(limit=24):
    """
    Return providers for the top select menu.
    Prioritize usage_count, fallback to total slots.
    Always include providers even if usage=0.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        SELECT s.provider,
               COALESCE(u.usage_count, 0) AS usage_count,
               COUNT(slots.url) AS total_slots
        FROM (
            SELECT DISTINCT provider FROM slots
        ) s
        LEFT JOIN provider_usage u
            ON s.provider = u.provider
        LEFT JOIN slots
            ON slots.provider = s.provider
        GROUP BY s.provider
        ORDER BY usage_count DESC, total_slots DESC, s.provider ASC
        LIMIT ?
    """, (limit,))

    providers = [row[0] for row in c.fetchall()]
    conn.close()
    return providers


class ProviderSelect(discord.ui.Select):
    def __init__(self, providers):
        options = [
            discord.SelectOption(
                label="All Providers",
                value="ALL",
                description="Pick from all providers"
            )
        ]

        # Add top providers by usage
        for p in providers:
            options.append(
                discord.SelectOption(
                    label=p,
                    value=p
                )
            )

        super().__init__(
            placeholder="Quick pick provider...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        await send_random_slot(interaction, self.values[0])


@bot.tree.command(name="random_slot", description="Pick a random slot")
@app_commands.describe(provider="Select or type a provider")
@app_commands.autocomplete(provider=provider_autocomplete)
async def random_slot(interaction: discord.Interaction, provider: str):
    if interaction.channel_id != ASK_CHANNEL_ID:
        await interaction.response.send_message(
            f"⚠️ You can’t use that command here! Please go to <#{ASK_CHANNEL_ID}> to use `/random_slot`.",
            ephemeral=True
        )
        return

    # Fetch top providers for menu
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    providers = get_providers_by_usage()
    conn.close()

    # If user typed provider → send immediately
    if provider:
        await send_random_slot(interaction, provider)
        return

    # Otherwise show select menu
    await interaction.response.send_message(
        "Quick pick a provider below or type one:",
        view=ProviderView(providers),
        ephemeral=True
    )


# ================= SLOT CALL COMMAND =================

def get_top_slots(limit=25):
    """Return top slots by usage_count from the DB"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT s.name, s.provider, s.url, s.thumbnail, COALESCE(u.usage_count, 0) as usage_count
        FROM slots s
        LEFT JOIN slot_usage u
            ON s.name = u.slot_name
        ORDER BY usage_count DESC, s.name ASC
        LIMIT ?
    """, (limit,))
    slots = c.fetchall()
    conn.close()
    return slots


async def slot_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete for slot names"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT s.name, s.provider
        FROM slots s
        LEFT JOIN slot_usage u
            ON s.name = u.slot_name
        WHERE s.name LIKE ?
        ORDER BY COALESCE(u.usage_count, 0) DESC, s.name ASC
        LIMIT 25
    """, (f"%{current}%",))
    slots = [f"{row[0]} - {row[1]}" for row in c.fetchall()]
    conn.close()

    return [
        app_commands.Choice(name=slot, value=slot)
        for slot in slots
    ]


def increment_slot_usage(slot_name):
    """Track how many times each slot was called"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS slot_usage (
            slot_name TEXT PRIMARY KEY,
            usage_count INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        INSERT INTO slot_usage (slot_name, usage_count)
        VALUES (?, 1)
        ON CONFLICT(slot_name)
        DO UPDATE SET usage_count = usage_count + 1
    """, (slot_name,))
    conn.commit()
    conn.close()


async def send_slot_call(interaction: discord.Interaction, slot_value: str):
    """Send the slot call embed"""
    slot_name, provider = slot_value.split(" - ", 1)

    # Increment usage
    increment_slot_usage(slot_name)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT url, thumbnail
        FROM slots
        WHERE name = ? AND provider = ?
    """, (slot_name, provider))
    row = c.fetchone()
    conn.close()

    url, thumbnail = row if row else ("", "")

    embed = discord.Embed(
        title=slot_name,
        description=f"Provider: **{provider}**",
        color=discord.Color.green()
    )
    if thumbnail:
        # Adds a unique timestamp to the URL so Discord always downloads a fresh copy
        separator = "&" if "?" in thumbnail else "?"
        clean_thumbnail = f"{thumbnail}{separator}t={int(time.time())}"
        embed.set_thumbnail(url=clean_thumbnail)
    embed.set_footer(text="⚠️ Gamble responsibly. Use at your own discretion.")

    await interaction.response.send_message(
        content=f"{interaction.user.mention} suggested to play **{slot_name}**!",
        embed=embed
    )


# 🔄 AUTO SLOT SCRAPER LOOP
@tasks.loop(hours=24)  # change interval if you want
async def auto_scrape_slots():
    print("🔄 Auto scraping slots...")

    slot_urls = get_all_slot_urls()
    print(f"Found {len(slot_urls)} new slots to scrape.")

    for index, url in enumerate(slot_urls, 1):
        slot_data = parse_slot(url)

        if slot_data:
            update_db(slot_data)
            print(f"[{index}/{len(slot_urls)}] Added/Updated: {slot_data['name']} ({slot_data['provider']})")

        await asyncio.sleep(random.uniform(*POLITE_DELAY))

    print("✅ Auto scrape cycle complete.")


# Wait until bot is ready before first run
@auto_scrape_slots.before_loop
async def before_auto_scrape():
    await bot.wait_until_ready()
    print("⏳ Auto slot scraper waiting for bot ready...")


# ================= DISCORD COMMAND =================
@bot.tree.command(
    name="slot_call",
    description="Call a slot from the top slots")
@app_commands.describe(slot="Pick a slot to call")
@app_commands.autocomplete(slot=slot_autocomplete)
async def slot_call(interaction: discord.Interaction, slot: str):
    if interaction.channel_id != CALL_CHANNEL_ID:
        await interaction.response.send_message(
            f"⚠️ You can’t use that command here! Please go to <#{CALL_CHANNEL_ID}> to use `/slot_call`.",
            ephemeral=True
        )
        return
    await send_slot_call(interaction, slot)


async def background_scrape():
    await asyncio.sleep(1)  # small delay
    print("Starting background scraping...")
    slot_urls = get_all_slot_urls()  # uses DB-skipping version
    print(f"Found {len(slot_urls)} new slots to scrape.")

    for index, url in enumerate(slot_urls, 1):
        slot_data = parse_slot(url)
        if slot_data:
            update_db(slot_data)
            print(f"[{index}/{len(slot_urls)}] Added/Updated: {slot_data['name']} ({slot_data['provider']})")
        await asyncio.sleep(random.uniform(*POLITE_DELAY))

    print("Background scraping complete.")

async def get_server_tag(user_id: int):

    url = f"https://discord.com/api/v10/users/{user_id}"

    max_retries = 5

    for attempt in range(max_retries):

        try:

            async with http_session.get(url) as response:

                # -----------------------------------------
                # SUCCESS
                # -----------------------------------------

                if response.status == 200:

                    data = await response.json()

                    primary = data.get("primary_guild")

                    # API successfully responded,
                    # but user has no primary server tag.
                    if not primary:

                        return {
                            "api_ok": True,
                            "has_tag": False,
                            "tag": None,
                            "guild_id": None
                        }

                    guild_id = str(
                        primary.get("identity_guild_id")
                    )

                    tag = primary.get("tag")

                    has_tag = (
                        guild_id == str(GUILD_ID)
                        and
                        tag == SERVER_TAG
                    )

                    return {
                        "api_ok": True,
                        "has_tag": has_tag,
                        "tag": tag,
                        "guild_id": guild_id
                    }

                # -----------------------------------------
                # RATE LIMITED
                # -----------------------------------------

                elif response.status == 429:

                    retry_after = response.headers.get(
                        "Retry-After"
                    )

                    try:
                        retry_after = float(retry_after)
                    except (TypeError, ValueError):
                        retry_after = 30

                    print(
                        f"⚠️ Discord API rate limit for "
                        f"{user_id}. "
                        f"Waiting {retry_after} seconds..."
                    )

                    await asyncio.sleep(retry_after)

                    continue

                # -----------------------------------------
                # OTHER API ERROR
                # -----------------------------------------

                else:

                    print(
                        f"⚠️ Discord API returned "
                        f"{response.status} for {user_id}"
                    )

                    return {
                        "api_ok": False,
                        "has_tag": False,
                        "tag": None,
                        "guild_id": None
                    }

        except Exception as e:

            print(
                f"⚠️ Tag API error ({user_id}): {e}"
            )

            if attempt < max_retries - 1:

                await asyncio.sleep(2)

                continue

            return {
                "api_ok": False,
                "has_tag": False,
                "tag": None,
                "guild_id": None
            }

    # -----------------------------------------
    # ALL RETRIES FAILED
    # -----------------------------------------

    print(
        f"⚠️ Could not verify Server Tag for {user_id}. "
        f"Skipping this member."
    )

    return {
        "api_ok": False,
        "has_tag": False,
        "tag": None,
        "guild_id": None
    }

@bot.tree.command(name="checktag", description="Check official server tag")
async def checktag(interaction: discord.Interaction):

    result = await get_server_tag(interaction.user.id)

    if result["has_tag"]:
        await interaction.response.send_message(
            f"✅ You currently have the official **{result['tag']}** server tag.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "❌ You do NOT currently have the official server tag.",
            ephemeral=True
        )

async def get_tag_member(user_id: int):

    async with aiosqlite.connect(DB_PATH) as db:

        cursor = await db.execute(
            """
            SELECT *
            FROM tag_members
            WHERE user_id = ?
            """,
            (user_id,)
        )

        return await cursor.fetchone()

async def create_tag_member(user_id: int):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            INSERT OR IGNORE INTO tag_members
            (
                user_id,
                first_received_at,
                needs_requalify,
                waiting_since,
                last_removed_at
            )

            VALUES (?, ?, 0, NULL, NULL)
            """,
            (
                user_id,
                int(time.time())
            )
        )

        await db.commit()

async def start_requalification(user_id: int):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            UPDATE tag_members

            SET

                waiting_since = ?,
                needs_requalify = 1

            WHERE user_id = ?
            """,
            (
                int(time.time()),
                user_id
            )
        )

        await db.commit()

async def finish_requalification(user_id: int):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            UPDATE tag_members

            SET

                waiting_since = NULL,
                needs_requalify = 0

            WHERE user_id = ?
            """,
            (
                user_id,
            )
        )

        await db.commit()

async def initial_process_member(member):

    if member.bot:
        return False

    result = await get_server_tag(member.id)

    if not result["api_ok"]:
        return False

    if not result["has_tag"]:
        return False

    # Already has the role.
    role = member.guild.get_role(TAG_ROLE_ID)

    if role is None:
        print("❌ Server Tag role not found.")
        return False

    if role in member.roles:
        return False

    # Give role immediately.
    try:

        await member.add_roles(
            role,
            reason="Initial Server Tag Sync"
        )

        await create_tag_member(member.id)

        await send_tag_log(
            member,
            "🎉 Server Tag Role Granted",
            (
                f"{member.mention}\n\n"
                f"Successfully qualified for the official "
                f"**{SERVER_TAG}** Server Tag.\n\n"
                f"🎭 **Role Granted:** {role.mention}"
            ),
            discord.Color.green()
        )

        return True

    except Exception as e:

        print(
            f"❌ Initial role error "
            f"({member.id}): {e}"
        )

        return False

async def initial_tag_sync():

    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        print("❌ Server Tag Sync: Guild not found.")
        return

    print("🏷️ Starting ONE-TIME Server Tag full sync...")
    print(f"👥 Members to check: {len(guild.members)}")

    processed = 0
    roles_granted = 0
    errors = 0

    start_time = time.time()

    for member in guild.members:

        if member.bot:
            continue

        try:

            # Check this member during the initial scan.
            result = await initial_process_member(member)

            if result:
                roles_granted += 1

            processed += 1

            # Progress every 250 members
            if processed % 250 == 0:
                print(
                    f"🏷️ Initial sync progress: "
                    f"{processed}/{len(guild.members)}"
                )

        except Exception as e:

            errors += 1

            print(
                f"❌ Initial Tag Sync Error "
                f"({member.id}): {e}"
            )

    elapsed = int(time.time() - start_time)

    # ONLY mark complete after the entire scan finishes.
    await set_tag_setting(
        "initial_sync_complete",
        "1"
    )

    print("========================================")
    print("✅ Initial Server Tag Sync Complete")
    print(f"👥 Members processed: {processed}")
    print(f"🎭 Roles granted: {roles_granted}")
    print(f"❌ Errors: {errors}")
    print(f"⏱️ Time: {elapsed} seconds")
    print("========================================")

    
async def setup_tag_database():

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute("""
        CREATE TABLE IF NOT EXISTS tag_members (

            user_id INTEGER PRIMARY KEY,

            first_received_at INTEGER,

            needs_requalify INTEGER NOT NULL DEFAULT 0,

            waiting_since INTEGER,

            last_removed_at INTEGER

        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS tag_settings (

            key TEXT PRIMARY KEY,

            value TEXT

        )
        """)

        await db.commit()

    print("✅ Tag database ready.")


async def get_tag_setting(key: str):

    async with aiosqlite.connect(DB_PATH) as db:

        cursor = await db.execute(
            """
            SELECT value
            FROM tag_settings
            WHERE key = ?
            """,
            (key,)
        )

        row = await cursor.fetchone()

        if row is None:
            return None

        return row[0]


async def set_tag_setting(key: str, value: str):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            INSERT INTO tag_settings (key, value)
            VALUES (?, ?)

            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value
            """,
            (key, value)
        )

        await db.commit()
        
async def update_last_removed(user_id: int):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            UPDATE tag_members

            SET

                last_removed_at = ?

            WHERE user_id = ?
            """,
            (
                int(time.time()),
                user_id
            )
        )

        await db.commit()

async def has_tag_role(member: discord.Member):

    return member.get_role(TAG_ROLE_ID) is not None

async def give_tag_role(member: discord.Member):

    role = member.guild.get_role(TAG_ROLE_ID)

    if role is None:
        return False

    if role in member.roles:
        return False

    await member.add_roles(
        role,
        reason="Official Server Tag"
    )

    return True

async def remove_tag_role(member: discord.Member):

    role = member.guild.get_role(TAG_ROLE_ID)

    if role is None:
        return False

    if role not in member.roles:
        return False

    await member.remove_roles(
        role,
        reason="Official Server Tag Removed"
    )

    return True

def requalification_complete(waiting_since: int) -> bool:

    if waiting_since is None:
        return False

    return (
        int(time.time()) >=
        waiting_since + (TAG_REQUALIFY_DAYS * SECONDS_PER_DAY)
    )

async def process_member(member: discord.Member):

    if member.bot:
        return "skipped"

    # -----------------------------------------
    # CHECK DISCORD API
    # -----------------------------------------

    tag = await get_server_tag(member.id)

    # -----------------------------------------
    # VERY IMPORTANT:
    # API FAILURE = DO NOTHING
    # -----------------------------------------

    if not tag["api_ok"]:

        return "api_failed"

    record = await get_tag_member(member.id)

    has_role = await has_tag_role(member)

    # -----------------------------------------
    # BRAND NEW USER
    # -----------------------------------------

    if record is None:

        if tag["has_tag"]:

            await create_tag_member(member.id)

            if not has_role:

                await give_tag_role(member)

                await send_tag_log(
                    member,
                    "🎉 Server Tag Role Granted",
                    (
                        f"{member.mention}\n\n"
                        f"Successfully qualified for the official "
                        f"**{SERVER_TAG}** Server Tag.\n\n"
                        f"🎭 **Role Granted:** <@&{TAG_ROLE_ID}>"
                    ),
                    discord.Color.green()
                )

        return

    # -----------------------------------------
    # EXISTING USER DATA
    # -----------------------------------------

    needs_requalify = bool(record[2])
    waiting_since = record[3]

    # -----------------------------------------
    # USER DOES NOT HAVE WILD TAG
    # -----------------------------------------

    if not tag["has_tag"]:

        # -----------------------------------------
        # ALREADY IN REQUALIFICATION
        #
        # Do NOT repeatedly log removal.
        # -----------------------------------------

        if needs_requalify:

            # If somehow the role still exists,
            # remove it, but don't create another log.
            if has_role:

                await remove_tag_role(member)

            # Make sure timer isn't active while
            # the user has no tag.
            if waiting_since is not None:

                async with aiosqlite.connect(DB_PATH) as db:

                    await db.execute(
                        """
                        UPDATE tag_members

                        SET
                            waiting_since = NULL

                        WHERE user_id = ?
                        """,
                        (member.id,)
                    )

                    await db.commit()

            return

        # -----------------------------------------
        # THIS IS A REAL TRANSITION:
        #
        # Qualified
        #     ↓
        # Tag removed
        #     ↓
        # Requalification required
        # -----------------------------------------

        if has_role:

            await remove_tag_role(member)

        await update_last_removed(member.id)

        async with aiosqlite.connect(DB_PATH) as db:

            await db.execute(
                """
                UPDATE tag_members

                SET
                    needs_requalify = 1,
                    waiting_since = NULL

                WHERE user_id = ?
                """,
                (member.id,)
            )

            await db.commit()

        await send_tag_log(
            member,
            "❌ Server Tag Removed",
            (
                f"{member.mention}\n\n"
                f"The official **{SERVER_TAG}** Server Tag "
                f"is no longer equipped.\n\n"
                f"🎭 **Role Removed:** <@&{TAG_ROLE_ID}>\n"
                f"⏳ **Status:** Requalification required."
            ),
            discord.Color.red()
        )

        return

    # -----------------------------------------
    # USER HAS WILD TAG AGAIN
    # -----------------------------------------

    if needs_requalify:

        # -----------------------------------------
        # START 7-DAY TIMER
        # -----------------------------------------

        if waiting_since is None:

            await start_requalification(member.id)

            unlock = (
                int(time.time())
                + (TAG_REQUALIFY_DAYS * SECONDS_PER_DAY)
            )

            await send_tag_log(
                member,
                "⏳ Requalification Started",
                (
                    f"{member.mention}\n\n"
                    f"The official **{SERVER_TAG}** Server Tag "
                    f"has been detected again.\n\n"
                    f"🎭 **Role:** Pending\n"
                    f"🗓️ **Eligible On:** <t:{unlock}:F>\n"
                    f"⏰ <t:{unlock}:R>"
                ),
                discord.Color.orange()
            )

            return

        # -----------------------------------------
        # STILL WAITING
        # -----------------------------------------

        if not requalification_complete(waiting_since):

            return

        # -----------------------------------------
        # 7 DAYS COMPLETED
        # -----------------------------------------

        await finish_requalification(member.id)

        if not has_role:

            await give_tag_role(member)

        await send_tag_log(
            member,
            "🔄 Role Restored",
            (
                f"{member.mention}\n\n"
                f"The member is still eligible for the official "
                f"**{SERVER_TAG}** Server Tag role.\n\n"
                f"🎭 **Role Restored:** <@&{TAG_ROLE_ID}>\n"
                f"📌 The 7-day requalification period has been completed."
            ),
            discord.Color.blurple()
        )

        return

    # -----------------------------------------
    # QUALIFIED USER BUT ROLE IS MISSING
    # -----------------------------------------

    if not has_role:

        await give_tag_role(member)

        await send_tag_log(
            member,
            "🔄 Role Restored",
            (
                f"{member.mention}\n\n"
                f"The member is still eligible for the official "
                f"**{SERVER_TAG}** Server Tag role.\n\n"
                f"🎭 **Role Restored:** <@&{TAG_ROLE_ID}>\n"
                f"📌 Missing role detected and restored automatically."
            ),
            discord.Color.blurple()
        )

async def send_tag_log(
    member: discord.Member,
    title: str,
    description: str,
    color: discord.Color
):

    channel = member.guild.get_channel(TAG_LOG_CHANNEL_ID)

    if channel is None:
        return

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow()
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    embed.set_author(
        name=str(member),
        icon_url=member.display_avatar.url
    )

    embed.set_footer(
        text=f"User ID: {member.id}"
    )

    await channel.send(
        content=member.mention,
        embed=embed
    )

@tasks.loop(hours=1)
async def tag_scanner():

    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        print("❌ Server Tag Scanner: Guild not found.")
        return

    print("========================================")
    print("🏷️ Starting FULL HOURLY Server Tag scan")
    print(f"👥 Members to check: {len(guild.members)}")
    print("========================================")

    checked = 0
    api_failed = 0
    errors = 0
    start_time = time.time()

    total_members = len(guild.members)

    for member in guild.members:

        if member.bot:
            continue

        try:

            result = await process_member(member)

            checked += 1

            if result == "api_failed":
                api_failed += 1

            # Progress every 250 members
            if checked % 250 == 0:

                print(
                    f"🏷️ Hourly scan progress: "
                    f"{checked}/{total_members}"
                )

            # Small delay between API requests.
            # This helps avoid hammering Discord.
            await asyncio.sleep(0.5)

        except Exception as e:

            errors += 1

            print(
                f"❌ Hourly Tag Scan Error "
                f"({member.id}): {e}"
            )

    elapsed = int(time.time() - start_time)

    print("========================================")
    print("✅ FULL HOURLY Server Tag scan complete")
    print(f"👥 Members checked: {checked}")
    print(f"⚠️ API checks failed: {api_failed}")
    print(f"❌ Errors: {errors}")
    print(f"⏱️ Time: {elapsed} seconds")
    print("========================================")
    
if __name__ == "__main__":
    bot.run(TOKEN)
