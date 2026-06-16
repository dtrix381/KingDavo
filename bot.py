import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import aiosqlite
import random
import re
import os

PROVIDER_IMAGES = {
    "pragmatic": "https://cdn.discordapp.com/attachments/1283197229913608192/1362821484447399936/CvuaWH6WBTwAAAAASUVORK5CYII.png?ex=6a2cd729&is=6a2b85a9&hm=e8ef3da0bde4fbd77e5d2aa99ada5fdd66b0ac392035b4c79ddcefb5acef18f5",
    "hacksaw": "https://cdn.discordapp.com/attachments/1225024450345439313/1514975662211858462/image.png?ex=6a2d5288&is=6a2c0108&hm=2603e233798b21904a31ac3f48b98488b95b649f09c3ba55b5628f400b5b67a6",
    "nolimit_city": "https://cdn.discordapp.com/attachments/1353382950300811394/1374936691063918632/aJJvcpI1AAAAAElFTkSuQmCC.png?ex=686c8214&is=686b3094&hm=17d990f5013d8f961ebf03e898085a39b399822673481721a3482f1ab0287285&",
    "jedi_of_slots": "https://cdn.discordapp.com/attachments/1355803389262303362/1514923068982558751/DF5842AC-C6DF-4BF9-A751-1C52BC07A166.png?ex=6a2d218d&is=6a2bd00d&hm=ab41ffde88b21c9b0aecfc1185286624afbfecd65a68a78f466e07fa4119003c"
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

bot = commands.Bot(command_prefix="!", intents=intents)
JOIN_EMOJI = "🎰"
manual_games = {}  # Stores {channel_id: (players, provider_name)}

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔁 Synced {len(synced)} command(s).")
    except Exception as e:
        print("Sync error:", e)

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
        app_commands.Choice(name="Jedi of Slots", value="jedi_of_slots")
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
@app_commands.checks.has_permissions(administrator=True)
async def rumble_start(interaction: discord.Interaction, provider: app_commands.Choice[str], mode: app_commands.Choice[str], timer: app_commands.Choice[int]):
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

@bot.tree.command(
    name="leaderboard",
    description="View leaderboard rankings"
)
async def leaderboard(
    interaction: discord.Interaction
):

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

    embed.add_field(
        name=(
            "Current Leaderboard Rank"
            if active
            else "All-Time Rank"
        ),
        value=(
            f"#{rank}"
            if rank
            else "Unranked"
        ),
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


if __name__ == "__main__":
    bot.run(TOKEN)
