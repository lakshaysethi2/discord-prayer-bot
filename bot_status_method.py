    async def _update_all_voice_statuses(self) -> None:
        """Iterate over all enabled guilds and update their voice channel status."""
        log.info("Updating voice statuses for %d guilds", len(self.guilds))
        
        all_next_prayer_minutes = []

        for guild in self.guilds:
            guild_id = str(guild.id)
            temp_vc = None
            # Flag to suppress persistence during blip
            self._in_status_blip = True 
            try:
                cfg = get_guild_config(self.db, guild_id)
                
                if not cfg or not cfg.enabled or not cfg.voice_channel_id:
                    continue

                # Log permissions periodically
                self._log_permissions(guild, cfg.voice_channel_id)
                    
                voice_channel = guild.get_channel(int(cfg.voice_channel_id))
                if voice_channel is None:
                    try:
                        voice_channel = await guild.fetch_channel(int(cfg.voice_channel_id))
                    except Exception:
                        continue

                if not isinstance(voice_channel, (discord.VoiceChannel, discord.StageChannel)):
                    continue
                
                # Check if prayer is actually in progress (playing audio)
                player = self.players.get(guild_id)
                is_playing = player and player.is_playing() and not (guild_id in self._tts_playing)
                
                info = self._get_next_prayer_info(guild_id)
                if info:
                    all_next_prayer_minutes.append(info["minutes_left"])
                
                mins = info["minutes_left"] if info else None
                
                if is_playing:
                    status = "Now praying"
                elif mins is None:
                    status = "No prayers scheduled"
                elif mins <= 0:
                    status = "Prayer starting soon"
                else:
                    hours, minutes = divmod(mins, 60)
                    days, hours = divmod(hours, 24)
                    if days > 0:
                        status = f"next prayer starts in ~ {days}d {hours}h"
                    elif hours > 0:
                        status = f"next prayer starts in ~ {hours}h {minutes}m"
                    else:
                        status = f"next prayer starts in ~ {minutes}m"
                
                # Official Voice Status Requirement: Bot MUST be in the channel
                vc_conn = self.voice_connections.get(guild_id)
                if vc_conn and vc_conn.is_connected() and vc_conn.channel.id == voice_channel.id:
                    try:
                        # Small buffer to ensure API is ready
                        await asyncio.sleep(1)
                        await voice_channel.edit(status=status)
                        log.info("Set official VC status for guild %s: %s", guild_id, status)
                    except Exception as exc:
                        log.debug("Official VC status update failed: %s", exc)
                elif not vc_conn or not vc_conn.is_connected():
                    # Temporarily join to set status (the user explicitly requested this "blip" approach)
                    try:
                        temp_vc = await voice_channel.connect(timeout=10.0, reconnect=False)
                        # Wait 2 seconds after joining for Discord to recognize the presence
                        await asyncio.sleep(2)
                        await voice_channel.edit(status=status)
                        log.info("Set official VC status via blip for guild %s: %s", guild_id, status)
                        # Wait 2 seconds for the status to propagate before leaving
                        await asyncio.sleep(2)
                    except Exception as exc:
                        log.debug("Temporary join/status blip failed in guild %s: %s", guild_id, exc)

            except Exception as exc:
                log.warning("Unexpected error updating status for guild %s: %s", guild_id, exc)
            finally:
                self._in_status_blip = False
                # Always leave if we joined just for the status update
                if temp_vc:
                    # Check if a prayer started during the blip
                    player = self.players.get(guild_id)
                    if not player or not player.is_playing():
                        await temp_vc.disconnect()
                        log.debug("Temporary status VC disconnected for guild %s", guild_id)

        # 2. Bot Global Activity Status (Visible to everyone in member list)
        if all_next_prayer_minutes:
            earliest_mins = min(all_next_prayer_minutes)
            h, m = divmod(earliest_mins, 60)
            activity_text = f"Next prayer starts in ~{h}h {m}m" if h > 0 else f"Next prayer starts in ~{m}m"
            await self.change_presence(activity=discord.Game(name=activity_text))
            log.info("Updated bot global activity: %s", activity_text)
