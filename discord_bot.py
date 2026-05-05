"""
Companion Discord bot (Addendum 3, C4).
Separate script — NOT bundled in the poker bot binary.
Run independently on any machine with access to the stats/ directory.

Usage:
    python discord_bot.py

Commands:
    ?stats                   — All bots combined all-time
    ?stats BOT-01            — Specific bot all-time
    ?stats recent            — Most recent session, all bots
    ?stats BOT-01 recent     — Most recent session for BOT-01
"""
from __future__ import annotations

import glob
import json
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────
# Set via environment variables or edit directly
BOT_TOKEN  = os.environ.get("DISCORD_BOT_TOKEN", "")
STATS_DIR  = os.environ.get("STATS_DIR", "./")
COMMAND_PREFIX = "?"

# ── Bot setup ─────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)


def _load_all_stats() -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for path in glob.glob(os.path.join(STATS_DIR, "stats_*.json")):
        try:
            with open(path) as f:
                data = json.load(f)
            bid = data.get("bot_id", os.path.basename(path))
            stats[bid] = data
        except Exception as exc:
            log.warning("Failed to load %s: %s", path, exc)
    return stats


def _is_online(bot_data: dict) -> bool:
    """Bot is online if last_updated was <5 minutes ago."""
    try:
        last = bot_data.get("last_updated", "")
        if not last:
            return False
        dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() < 300
    except Exception:
        return False


def _format_runtime(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


def _build_fleet_embed(all_stats: dict) -> discord.Embed:
    """Build the ?stats embed for all bots combined."""
    embed = discord.Embed(
        title="📊 VANTAGE BOT FLEET — ALL TIME",
        color=0x89b4fa,
        timestamp=datetime.now(timezone.utc),
    )

    total_runtime = 0
    total_tournaments = 0
    total_hands = 0
    total_s1 = 0
    total_s2 = 0
    total_final = 0
    online_count = 0

    per_bot_lines = []
    for bid, data in sorted(all_stats.items()):
        at = data.get("all_time", {})
        runtime = at.get("total_runtime_seconds", 0)
        tickets = at.get("tickets", {})
        s1 = tickets.get("stage1", 0)
        s2 = tickets.get("stage2", 0)
        final = tickets.get("final", 0)
        online = _is_online(data)

        total_runtime += runtime
        total_tournaments += at.get("tournaments_entered", 0)
        total_hands += at.get("hands_played", 0)
        total_s1 += s1
        total_s2 += s2
        total_final += final
        if online:
            online_count += 1

        status = "🟢" if online else "⚫"
        per_bot_lines.append(f"{status} **{bid}**  S1:{s1}  S2:{s2}  F:{final}")

    embed.add_field(name="Bots Active",    value=str(online_count),              inline=True)
    embed.add_field(name="Total Runtime",  value=_format_runtime(total_runtime), inline=True)
    embed.add_field(name="Tournaments",    value=f"{total_tournaments:,}",        inline=True)
    embed.add_field(name="Hands Played",   value=f"{total_hands:,}",              inline=True)
    embed.add_field(name="\u200b",         value="\u200b",                        inline=False)

    tickets_text = f"Stage 1: **{total_s1}**  Stage 2: **{total_s2}**  Final: **{total_final}**"
    embed.add_field(name="TICKETS WON", value=tickets_text, inline=False)

    if per_bot_lines:
        embed.add_field(name="BOT STATUS", value="\n".join(per_bot_lines), inline=False)

    embed.set_footer(text=f"Last updated: {datetime.now().strftime('%H:%M:%S')}")
    return embed


def _build_bot_embed(data: dict) -> discord.Embed:
    """Build per-bot all-time stats embed."""
    bid = data.get("bot_id", "?")
    at = data.get("all_time", {})
    tickets = at.get("tickets", {})
    online = _is_online(data)
    status = "🟢 Online" if online else "⚫ Offline"

    embed = discord.Embed(
        title=f"📊 {bid} — ALL TIME  ({status})",
        color=0xa6e3a1 if online else 0x6c7086,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Sessions",     value=str(at.get("sessions", 0)),          inline=True)
    embed.add_field(name="Runtime",      value=_format_runtime(at.get("total_runtime_seconds", 0)), inline=True)
    embed.add_field(name="Tournaments",  value=f"{at.get('tournaments_entered', 0):,}", inline=True)
    embed.add_field(name="Hands",        value=f"{at.get('hands_played', 0):,}",    inline=True)
    embed.add_field(name="Stage 1 🎫",   value=str(tickets.get("stage1", 0)),       inline=True)
    embed.add_field(name="Stage 2 🎫",   value=str(tickets.get("stage2", 0)),       inline=True)
    embed.add_field(name="Final 🎫",     value=str(tickets.get("final", 0)),        inline=True)
    return embed


def _build_recent_session_embed(bid: str, session: dict) -> discord.Embed:
    """Build embed for a single recent session."""
    start = session.get("start", "")
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        date_str = dt.strftime("%b %d, %Y")
    except Exception:
        date_str = "?"

    duration = _format_runtime(session.get("duration_seconds", 0))
    tickets = session.get("tickets_won", {})
    wins = session.get("wins", [])

    embed = discord.Embed(
        title=f"📊 {bid} — MOST RECENT SESSION",
        color=0x89b4fa,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Date",      value=date_str,                    inline=True)
    embed.add_field(name="Duration",  value=duration,                    inline=True)
    embed.add_field(name="Entered",   value=str(session.get("tournaments_entered", 0)), inline=True)
    embed.add_field(name="Hands",     value=str(session.get("hands_played", 0)),        inline=True)
    embed.add_field(name="Busts",     value=str(session.get("busts", 0)),               inline=True)
    embed.add_field(name="\u200b",    value="\u200b",                                   inline=True)

    if wins:
        win_lines = []
        for w in wins[:8]:  # cap at 8 lines
            t_type = w.get("ticket_type", "?").capitalize()
            tourn = w.get("tournament", "?")
            rank = w.get("rank", "?")
            win_lines.append(f"🎫 {tourn} → {t_type}  ({rank})")
        embed.add_field(name="WINS THIS SESSION", value="\n".join(win_lines), inline=False)

    t_str = f"S1:{tickets.get('stage1',0)}  S2:{tickets.get('stage2',0)}  F:{tickets.get('final',0)}"
    embed.add_field(name="Tickets", value=t_str, inline=False)
    return embed


# ── Commands ──────────────────────────────────────────────────────────────

@bot.command(name="stats")
async def stats_command(ctx: commands.Context, *args):
    """?stats [BOT-ID] [recent]"""
    all_stats = _load_all_stats()

    if not all_stats:
        await ctx.send("❌ No stats files found in configured directory.")
        return

    # Parse args
    target_bot: Optional[str] = None
    show_recent = False

    for arg in args:
        if arg.lower() == "recent":
            show_recent = True
        elif arg.upper() in all_stats:
            target_bot = arg.upper()
        elif arg.upper().startswith("BOT"):
            target_bot = arg.upper()

    if target_bot:
        data = all_stats.get(target_bot)
        if not data:
            await ctx.send(f"❌ Bot **{target_bot}** not found. Known bots: {', '.join(all_stats.keys())}")
            return

        if show_recent:
            sessions = data.get("sessions", [])
            if not sessions:
                await ctx.send(f"❌ No sessions recorded for {target_bot}")
                return
            recent = sessions[-1]
            embed = _build_recent_session_embed(target_bot, recent)
        else:
            embed = _build_bot_embed(data)

        await ctx.send(embed=embed)

    elif show_recent:
        # Most recent session for all bots
        for bid, data in sorted(all_stats.items()):
            sessions = data.get("sessions", [])
            if sessions:
                embed = _build_recent_session_embed(bid, sessions[-1])
                await ctx.send(embed=embed)
        if not all_stats:
            await ctx.send("No sessions found.")

    else:
        # Fleet aggregate
        embed = _build_fleet_embed(all_stats)
        await ctx.send(embed=embed)


@bot.command(name="help")
async def help_command(ctx: commands.Context):
    embed = discord.Embed(title="ClubGG Bot — Commands", color=0x89b4fa)
    embed.add_field(name="?stats",              value="All bots combined stats",           inline=False)
    embed.add_field(name="?stats BOT-01",       value="Stats for a specific bot",          inline=False)
    embed.add_field(name="?stats recent",       value="Most recent session, all bots",     inline=False)
    embed.add_field(name="?stats BOT-01 recent",value="Most recent session for one bot",   inline=False)
    await ctx.send(embed=embed)


@bot.event
async def on_ready():
    log.info("Discord bot connected as %s", bot.user)


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ERROR: Set DISCORD_BOT_TOKEN environment variable")
        raise SystemExit(1)
    bot.run(BOT_TOKEN)
