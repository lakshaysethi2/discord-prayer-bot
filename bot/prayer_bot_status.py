"""VC status updates and slash commands for PrayerBot."""
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
        log.info("Updating voice statuses for %d guilds", len(self.guilds))
        all_next_prayer_minutes = []
        for guild in self.guilds:
            guild_id = str(guild.id)
            temp_vc = None
            try:
                cfg = get_guild_config(self.db, guild_id)
                if not cfg or not cfg.enabled or not cfg.voice_channel_id:
                    continue
                self._log_permissions(guild, cfg.voice_channel_id)
                voice_channel = guild.get_channel(int(cfg.voice_channel_id))
                if voice_channel is None:
                    try:
                        voice_channel = await guild.fetch_channel(int(cfg.voice_channel_id))
                    except Exception:
                        continue
                if not isinstance(voice_channel, (discord.VoiceChannel, discord.StageChannel)):
                    continue
                player = self.players.get(guild_id)
                is_playing = player and player.is_playing() and not (guild_id in self._tts_playing)
                minutes_left = self._get_next_prayer_minutes(guild_id)
                if minutes_left is not None:
                    all_next_prayer_minutes.append(minutes_left)
                if is_playing:
                    status = "Now praying"
                elif minutes_left is None:
                    status = "No prayers scheduled"
                elif minutes_left <= 0:
                    status = "Prayer starting soon"
                else:
                    days, remainder = divmod(minutes_left, 1440)
                    hours, mins = divmod(remainder, 60)
                    if days > 0:
                        status = f"Next prayer starts in ~{days}d {hours}h"
                    elif hours > 0:
                        status = f"Next prayer starts in ~{hours}h {mins}m"
                    else:
                        status = f"Next prayer starts in ~{mins}m"
                vc_conn = self.voice_connections.get(guild_id) or guild.voice_client
                if vc_conn and vc_conn.is_connected() and vc_conn.channel and str(vc_conn.channel.id) == str(voice_channel.id):
                    try:
                        await asyncio.sleep(1)
                        await voice_channel.edit(status=status)
                        log.info("Set official VC status for guild %s: %s", guild_id, status)
                    except Exception as exc:
                        log.debug("Official VC status update failed: %s", exc)
                elif not vc_conn or not vc_conn.is_connected():
                    is_connected = (guild.voice_client and guild.voice_client.is_connected()) or (vc_conn and vc_conn.is_connected())
                    if cfg.status_blip_enabled and not is_connected:
                        try:
                            temp_vc = await voice_channel.connect(timeout=10.0, reconnect=False)
                            await asyncio.sleep(2)
                            await voice_channel.edit(status=status)
                            log.info("Set official VC status via blip for guild %s: %s", guild_id, status)
                            await asyncio.sleep(2)
                        except Exception as exc:
                            log.debug("Temporary join/status blip failed in guild %s: %s", guild_id, exc)
                    else:
                        log.debug("Skipping status blip for guild %s (feature disabled or bot connected)", guild_id)
            except Exception as exc:
                log.warning("Unexpected error updating status for guild %s: %s", guild_id, exc)
            finally:
                if temp_vc:
                    player = self.players.get(guild_id)
                    if not player or not player.is_playing():
                        await temp_vc.disconnect()
                        log.debug("Temporary status VC disconnected for guild %s", guild_id)
        if all_next_prayer_minutes:
            earliest_mins = min(all_next_prayer_minutes)
            h, m = divmod(earliest_mins, 60)
            activity_text = f"Next prayer starts in ~{h}h {m}m" if h > 0 else f"Next prayer starts in ~{m}m"
            await self.change_presence(activity=discord.Game(name=activity_text))
            log.info("Updated bot global activity: %s", activity_text)

    def _setup_slash_commands(self) -> None:
        @self.tree.command(name="start", description="Play a prayer adhoc")
        @discord.app_commands.describe(prayer_type="The type of prayer to play")
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
        async def start_prayer(interaction: discord.Interaction, prayer_type: str):
            guild_id = str(interaction.guild_id)
            pt = PrayerType(prayer_type)
            filename = get_audio_filename(pt)
            await interaction.response.defer(ephemeral=True)
            success = await self._start_prayer_playback(guild_id, pt, filename, is_adhoc=True)
            if success:
                await interaction.followup.send(f"\U0001f54c Playing **{pt.value.title()}** prayer.")
            else:
                await interaction.followup.send("\u274c Failed to start prayer. Please check if I have voice permissions.")

        @self.tree.command(name="exit", description="Stop the current prayer and leave the voice channel")
        @discord.app_commands.default_permissions(manage_guild=True)
        @discord.app_commands.guild_only()
        async def exit_prayer(interaction: discord.Interaction):
            guild_id = str(interaction.guild_id)
            await interaction.response.defer(ephemeral=True)
            result = await self._handle_command("disconnect", {"guild_id": guild_id})
            if result == "ok:disconnected":
                await interaction.followup.send("\U0001f44b Disconnected and stopped any active prayer.")
            elif result == "ok:not_connected":
                await interaction.followup.send("\u26a0\ufe0f Not currently connected to a voice channel.")
            else:
                await interaction.followup.send(f"\u274c Failed to disconnect: {result}")

        @self.tree.command(name="next", description="Find out when the next prayer is scheduled")
        @discord.app_commands.guild_only()
        async def next_prayer(interaction: discord.Interaction):
            guild_id = str(interaction.guild_id)
            info = self._get_next_prayer_info(guild_id)
            if not info:
                await interaction.response.send_message("\U0001f4c5 No prayers are currently scheduled for this server.", ephemeral=True)
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
                title=f"\U0001f54c Next Prayer: {tradition}",
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
                title="\U0001f54c Prayer Bot Help",
                description="I play scheduled and adhoc prayer recitations in voice channels.",
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="\U0001f4d6 Public Commands",
                value=(
                    "`/start [tradition]` - Trigger an immediate adhoc prayer\n"
                    "`/next` - See when the next prayer is scheduled (Ephemeral)\n"
                    "`/help` - Show this help message"
                ),
                inline=False,
            )
            embed.add_field(
                name="\U0001f6e1\ufe0f Admin Commands (Manage Server required)",
                value="`/exit` - Stop playback and make the bot leave voice",
                inline=False,
            )
            embed.add_field(
                name="\U0001f310 Dashboard",
                value="Admins can configure schedules and settings at: https://prayer-bot-dnd.lak.nz",
                inline=False,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
