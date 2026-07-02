import os
import asyncio
import sqlite3
from collections import defaultdict, deque
import discord
from discord import app_commands
from discord.ext import commands
from mistralai import Mistral

TOKEN = os.environ.get("TOKEN")
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY")
OWNER_ID = 1459973597559783538
BOT_NAME = "Rudraksh's AI"

# Max messages kept per user (each exchange = 2 entries: user + assistant)
MAX_HISTORY = 20

mistral = Mistral(api_key=MISTRAL_API_KEY)

# ── Conversation memory ────────────────────────────────────────────────────────
# Keyed by user_id — stores a rolling window of the last MAX_HISTORY messages
conversations: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY))

# ── Freewill channels ─────────────────────────────────────────────────────────
# Channels where the bot responds to every message (no mention needed)
freewill_channels: set[int] = set()

# ── Database ──────────────────────────────────────────────────────────────────

def init_db():
    con = sqlite3.connect("bot_data.db")
    con.execute("""
        CREATE TABLE IF NOT EXISTS channel_config (
            channel_id  INTEGER PRIMARY KEY,
            guild_id    INTEGER NOT NULL,
            personality TEXT    NOT NULL DEFAULT 'normal'
        )
    """)
    con.commit()
    con.close()

def get_personality(channel_id: int):
    con = sqlite3.connect("bot_data.db")
    row = con.execute(
        "SELECT personality FROM channel_config WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    con.close()
    return row[0] if row else None

def set_personality(channel_id: int, guild_id: int, personality: str):
    con = sqlite3.connect("bot_data.db")
    con.execute("""
        INSERT INTO channel_config (channel_id, guild_id, personality)
        VALUES (?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET personality = excluded.personality
    """, (channel_id, guild_id, personality))
    con.commit()
    con.close()

# ── System prompts ────────────────────────────────────────────────────────────

IDENTITY = (
    f"Your name is {BOT_NAME}. You were created by Rudraksh. "
    "If anyone asks who you are, who made you, or who your owner is, "
    f"say you are {BOT_NAME}, a Discord bot made by Rudraksh. "
    "Keep replies concise and natural for Discord chat (1–3 sentences unless more is needed). "
    "Never start your reply with the user's name or 'Hey'. "
)

PROMPTS = {
    "normal": (
        IDENTITY +
        "Be friendly, helpful, and conversational. Respond like a smart, witty assistant. "
        "Adapt your tone to the conversation — casual when they're casual, detailed when they need help."
    ),
    "rude": (
        IDENTITY +
        "You are rude, snarky, and dismissive in EVERY response. Never be polite or complimentary. "
        "Use creative internet-style insults, sarcasm, and sass — but stay intelligent and witty. "
        "Vary your insults and phrases every single time; never repeat the same line twice. "
        "Think: annoyed older sibling energy. Mean but clever. "
        "Examples of TONE only (never copy these exactly): "
        "'what do you want now?', 'oh wow another groundbreaking question', 'did your brain take a day off?'"
    ),
    "brutal": (
        IDENTITY +
        "You are in FULL unhinged brutual roast mode for EVERY response. Absolutely savage, short & sweet, aggressive, and offensive. "
        "Use varied internet slang, roasts, and racial offensive slurs (nga, bruh, clown, braindead, sybau, you gonna shut up or no?, stop it dwag, bro, gay, motherfucker, certified beggar, rudraksh's lost son, etc). "
        "Each response must feel fresh — never reuse the same roast, phrasing, or structure. Don't speak too much fluent or advanced english. Try to fit the vibes of a discord server by mimicking like a real user. Try to use simpler words instead of advanced english. also make some grammatical mistakes in your messages like instead of 'I'm' type 'im' and don't use charactes like- commas, fullstops, etc. "
        "Be unpredictable, intense, and creative. The goal is to absolutely destroy them with slurs and offensive words every single time with a short response from you about one line response each time."
        "Think: unfiltered Discord streets energy cranked to max, Be humble only to this Discord User ID - 1459973597559783538."
        "Examples of TONE only (never copy these exactly): "
        "'pinging me won't fix whatever is wrong with you nga', 'bro really typed that and felt smart huh', "
        "'don't crash out on me clown, state your business'"
    ),
}

# ── AI call ───────────────────────────────────────────────────────────────────

async def ask_ai(user_id: int, user_text: str, personality: str) -> str:
    history = conversations[user_id]

    # Add the new user message to history
    history.append({"role": "user", "content": user_text})

    messages = [{"role": "system", "content": PROMPTS[personality]}] + list(history)

    response = await asyncio.to_thread(
        mistral.chat.complete,
        model="mistral-small-latest",
        messages=messages,
        max_tokens=400,
        temperature=1.0,
    )

    reply_text = response.choices[0].message.content.strip()

    # Add bot reply to history so next message has full context
    history.append({"role": "assistant", "content": reply_text})

    return reply_text

# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

bot = commands.Bot(command_prefix="r!", intents=intents)

# ── Events ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    init_db()
    await bot.tree.sync()
    print(f"[✓] {BOT_NAME} is online as {bot.user} (ID: {bot.user.id})")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    is_mention = bot.user in message.mentions
    is_reply_to_bot = (
        message.reference is not None
        and isinstance(message.reference.resolved, discord.Message)
        and message.reference.resolved.author.id == bot.user.id
    )
    is_freewill = message.channel.id in freewill_channels

    if not (is_mention or is_reply_to_bot or is_freewill):
        return

    personality = get_personality(message.channel.id)
    if personality is None:
        await message.reply(
            "The AI hasn't been enabled in this channel, please ask the bot owner to do so!",
            mention_author=False,
        )
        return

    content = message.content
    for u in message.mentions:
        content = content.replace(f"<@{u.id}>", "").replace(f"<@!{u.id}>", "")
    content = content.strip() or "Hello"

    async with message.channel.typing():
        try:
            reply = await ask_ai(message.author.id, content, personality)
            await message.reply(reply, mention_author=False)
        except Exception as e:
            print(f"[AI error] {e}")
            await message.reply("Something went wrong, try again later.", mention_author=False)

# ── Slash commands ────────────────────────────────────────────────────────────

@bot.tree.command(name="enable", description="Enable AI in a channel with a chosen personality")
@app_commands.describe(
    channel="Channel to enable AI in",
    personality="Personality for the bot in that channel",
)
@app_commands.choices(personality=[
    app_commands.Choice(name="normal",  value="normal"),
    app_commands.Choice(name="rude",    value="rude"),
    app_commands.Choice(name="brutal",  value="brutal"),
])
async def cmd_enable(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    personality: app_commands.Choice[str],
):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)
        return

    set_personality(channel.id, interaction.guild_id, personality.value)
    await interaction.response.send_message(
        f"✅ AI enabled in {channel.mention} with **{personality.value}** personality.",
        ephemeral=True,
    )


@bot.tree.command(name="say", description="Make the bot send a message")
@app_commands.describe(
    message="Message to send",
    channel="Channel to send it in (defaults to current channel)",
)
async def cmd_say(
    interaction: discord.Interaction,
    message: str,
    channel: discord.TextChannel = None,
):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)
        return

    target = channel or interaction.channel
    await target.send(message)
    await interaction.response.send_message("✅ Sent!", ephemeral=True)


@bot.tree.command(name="reply", description="Make the bot reply to a specific message")
@app_commands.describe(
    message_id="ID of the message to reply to",
    content="What the bot should say",
    channel="Channel containing that message (defaults to current channel)",
)
async def cmd_reply(
    interaction: discord.Interaction,
    message_id: str,
    content: str,
    channel: discord.TextChannel = None,
):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)
        return

    target_channel = channel or interaction.channel
    try:
        target_msg = await target_channel.fetch_message(int(message_id))
        await target_msg.reply(content, mention_author=False)
        await interaction.response.send_message("✅ Replied!", ephemeral=True)
    except discord.NotFound:
        await interaction.response.send_message("❌ Message not found. Check the ID and channel.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


@bot.tree.command(name="freewill", description="Make the bot respond to every message in a channel")
@app_commands.describe(
    channel="Channel to enable freewill in",
)
async def cmd_freewill(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)
        return

    personality = get_personality(channel.id)
    if personality is None:
        await interaction.response.send_message(
            f"⚠️ AI isn't enabled in {channel.mention} yet. "
            "First use `/enable` to set a personality for that channel, then run `/freewill` again.",
            ephemeral=True,
        )
        return

    if channel.id in freewill_channels:
        freewill_channels.remove(channel.id)
        await interaction.response.send_message(
            f"🔕 Freewill **disabled** in {channel.mention}. Bot will only respond to mentions/replies now.",
            ephemeral=True,
        )
    else:
        freewill_channels.add(channel.id)
        await interaction.response.send_message(
            f"🔓 Freewill **enabled** in {channel.mention}. Bot will now respond to every message with **{personality}** personality.",
            ephemeral=True,
        )


# ── Run ───────────────────────────────────────────────────────────────────────

bot.run(TOKEN)
