"""PrayerBot runtime mixins."""
from bot.prayer_bot_voice import PrayerBotVoiceMixin
from bot.prayer_bot_commands import PrayerBotCommandsMixin
from bot.prayer_bot_status import PrayerBotStatusMixin

class PrayerBotRuntimeMixin(PrayerBotVoiceMixin, PrayerBotCommandsMixin, PrayerBotStatusMixin):
    pass
