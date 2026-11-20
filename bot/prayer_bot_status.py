from __future__ import annotations

import asyncio
import logging

import discord
import pytz

from db.models import PrayerType
from db.prayers import get_audio_filename, get_guild_config

log = logging.getLogger(__name__)

class PrayerBotStatusMixin:
    def _log_permissions(self, guild: discord.Guild, voice_channel_id: str | None) -> None:
        me = guild.me
        guild_perms = me.guild_permissions
        perm_list = {
            "Connect": guild_perms.connect,
            "Speak": guild_perms.speak,
            "Manage Channels": guild_perms.manage_channels,
            "Set VC Status": hasattr(guild_perms, "set_voice_channel_status") and guild_perms.set_voice_channel_status,
            "Send Messages": guild_perms.send_messages,
            "Use Slash Commands": True,
        }
        if voice_channel_id:
            channel = guild.get_channel(int(voice_channel_id))
            if channel:
                ch_perms = channel.permissions_for(me)
                perm_list["Connect (Channel)"] = ch_perms.connect
                perm_list["Speak (Channel)"] = ch_perms.speak
                if hasattr(ch_perms, "set_voice_channel_status"):
                    perm_list["Set VC Status (Channel)"] = ch_perms.set_voice_channel_status
        granted = [name for name, val in perm_list.items() if val]
        missing = [name for name, val in perm_list.items() if not val]
        log.info(
            "Permissions for guild '%s': GRANTED=%s | MISSING=%s",
            guild.name,
            ", ".join(granted),
            ", ".join(missing) if missing else "None",
        )

    async def _update_all_voice_statuses(self) -> None:
        minutes = await asyncio.gather(
            *(self._status_for_guild(guild) for guild in self.guilds)
        )
        upcoming = [m for m in minutes if m is not None]
        if not upcoming:
            return
        h, m = divmod(min(upcoming), 60)
        text = f"Next prayer starts in ~{h}h {m}m" if h > 0 else f"Next prayer starts in ~{m}m"
        await self.change_presence(activity=discord.Game(name=text))

    def _refresh_statuses_soon(self) -> None:
        task = getattr(self, "_status_refresh_task", None)
        if task is None or task.done():
            self._status_refresh_task = asyncio.create_task(self._update_all_voice_statuses())

    async def _status_for_guild(self, guild: discord.Guild) -> int | None:
        guild_id = str(guild.id)
        cfg = get_guild_config(self.db, guild_id)
        if not cfg or not cfg.enabled or not cfg.voice_channel_id:
            return None
        channel = guild.get_channel(int(cfg.voice_channel_id))
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return None
        player = self.players.get(guild_id)
        playing = bool(player and player.is_playing() and guild_id not in self._tts_playing)
        minutes_left = self._get_next_prayer_minutes(guild_id)
        status = self._status_text(playing, minutes_left)
        connected = self.voice_connections.get(guild_id) or guild.voice_client
        try:
            if connected and connected.is_connected():
                await channel.edit(status=status)
            elif cfg.status_blip_enabled:
                blip = await channel.connect(timeout=10.0, reconnect=False)
                try:
                    await channel.edit(status=status)
                finally:
                    await blip.disconnect()
        except Exception as exc:
            log.debug("Status update failed for guild %s: %s", guild_id, exc)
        return minutes_left

    @staticmethod
    def _status_text(playing: bool, minutes_left: int | None) -> str:
        if playing:
            return "Now praying"
        if minutes_left is None:
            return "No prayers scheduled"
        if minutes_left <= 0:
            return "Prayer starting soon"
        days, remainder = divmod(minutes_left, 1440)
        hours, mins = divmod(remainder, 60)
        if days > 0:
            return f"Next prayer starts in ~{days}d {hours}h"
        if hours > 0:
            return f"Next prayer starts in ~{hours}h {mins}m"
        return f"Next prayer starts in ~{mins}m"

    def _setup_slash_commands(self) -> None:
        @self.tree.command(name="start", description="Play a prayer adhoc")
        @discord.app_commands.describe(
            prayer_type="The type of prayer to play",
            opener="Play the opening prayers (Our Father + two prayers) first. Ignored for The 91st Psalm.",
        )
        @discord.app_commands.choices(prayer_type=[
            discord.app_commands.Choice(name="Buddhist", value="buddhist"),
            discord.app_commands.Choice(name="Christian", value="christian"),
            discord.app_commands.Choice(name="The 91st Psalm", value="psalm_91"),
            discord.app_commands.Choice(name="Jewish", value="jewish"),
            discord.app_commands.Choice(name="Sufi", value="sufi"),
            discord.app_commands.Choice(name="Vedantic", value="vedantic"),
            discord.app_commands.Choice(name="Three Daily", value="three_daily"),
        ])
        @discord.app_commands.guild_only()
        async def start_prayer(interaction: discord.Interaction, prayer_type: str, opener: bool = True):
            guild_id = str(interaction.guild_id)
            pt = PrayerType(prayer_type)
            filename = get_audio_filename(pt)
            await interaction.response.defer(ephemeral=True)
            success = await self._start_prayer_playback(guild_id, pt, filename, is_adhoc=True, with_opener=opener)
            if success:
                await interaction.followup.send(
                    f"🕌 Playing **{pt.value.title()}** prayer."
                )
            else:
                await interaction.followup.send(
                    "❌ Failed to start prayer. Please check if I have voice permissions."
                )

        @self.tree.command(
            name="setticketdrawchannel",
            description="Set the text channel for the daily ticket draw",
        )
        @discord.app_commands.describe(
            channel="Text channel where the daily draw is posted",
        )
        @discord.app_commands.default_permissions(manage_guild=True)
        @discord.app_commands.guild_only()
        async def setticketdrawchannel(
            interaction: discord.Interaction,
            channel: discord.TextChannel,
        ) -> None:
            from bot.daily_draw_commands import process_slash_setticketdrawchannel

            reply = await process_slash_setticketdrawchannel(self.db, interaction, channel)
            ephemeral = not reply.startswith("Daily draw channel set to")
            await interaction.response.send_message(reply, ephemeral=ephemeral)

        @self.tree.command(name="exit", description="Stop the current prayer and leave the voice channel")
        @discord.app_commands.default_permissions(manage_guild=True)
        @discord.app_commands.guild_only()
        async def exit_prayer(interaction: discord.Interaction):
            guild_id = str(interaction.guild_id)
            await interaction.response.defer(ephemeral=True)
            result = await self._handle_command("disconnect", {"guild_id": guild_id})
            if result == "ok:disconnected":
                await interaction.followup.send(
                    "👋 Disconnected and stopped any active prayer."
                )
            elif result == "ok:not_connected":
                await interaction.followup.send(
                    "⚠️ Not currently connected to a voice channel."
                )
            else:
                await interaction.followup.send(f"❌ Failed to disconnect: {result}")

        @self.tree.command(name="next", description="Find out when the next prayer is scheduled")
        @discord.app_commands.guild_only()
        async def next_prayer(interaction: discord.Interaction):
            guild_id = str(interaction.guild_id)
            info = self._get_next_prayer_info(guild_id)
            if not info:
                await interaction.response.send_message(
                    "📅 No prayers are currently scheduled for this server.",
                    ephemeral=True,
                )
                return
            sched = info["schedule"]
            dt = info["datetime"]
            mins = info["minutes_left"]
            cfg = get_guild_config(self.db, guild_id)
            tz_name = cfg.timezone_name if cfg else "UTC"
            local_dt = dt.astimezone(pytz.timezone(tz_name))
            time_str = local_dt.strftime("%I:%M %p")
            day_str = local_dt.strftime("%A")
            hours, remainder = divmod(mins, 60)
            countdown = f"{hours}h {remainder}m" if hours > 0 else f"{remainder}m"
            tradition = sched.prayer_type.value.title()
            embed = discord.Embed(
                title=f"🕌 Next Prayer: {tradition}",
                description=f"The next recitation will begin in **{countdown}**.",
                color=discord.Color.blue(),
            )
            embed.add_field(name="Time", value=f"**{time_str}** ({day_str})", inline=True)
            embed.add_field(name="Timezone", value=tz_name, inline=True)
            embed.set_footer(text="Join the voice channel early for the welcome greeting!")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @self.tree.command(name="help", description="Show all available commands for the Prayer Bot")
        async def help_command(interaction: discord.Interaction):
            embed = discord.Embed(
                title="🕌 Prayer Bot Help",
                description="I play scheduled and adhoc prayer recitations in voice channels.",
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="📖 Public Commands",
                value=(
                    "`/start [tradition]` - Trigger an immediate adhoc prayer\n"
                    "`/next` - See when the next prayer is scheduled (Ephemeral)\n"
                    "`/help` - Show this help message"
                ),
                inline=False,
            )
            embed.add_field(
                name="🛡️ Admin Commands (Manage Server required)",
                value=(
                    "`/exit` - Stop playback and make the bot leave voice\n"
                    "`/setticketdrawchannel` - Set the daily ticket-draw text channel (dropdown)"
                ),
                inline=False,
            )
            embed.add_field(
                name="🌐 Dashboard",
                value="Admins can configure schedules and settings at: https://prayer-bot-dnd.lak.nz",
                inline=False,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
