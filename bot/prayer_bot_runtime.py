"""PrayerBot runtime mixins."""
from bot.prayer_bot_commands import PrayerBotCommandsMixin
from bot.prayer_bot_playback import PrayerBotPlaybackMixin
from bot.prayer_bot_status import PrayerBotStatusMixin
from bot.prayer_bot_tts import PrayerBotTtsMixin
from bot.prayer_bot_voice import PrayerBotVoiceMixin


class PrayerBotRuntimeMixin(
    PrayerBotVoiceMixin,
    PrayerBotTtsMixin,
    PrayerBotPlaybackMixin,
    PrayerBotCommandsMixin,
    PrayerBotStatusMixin,
):
    pass
