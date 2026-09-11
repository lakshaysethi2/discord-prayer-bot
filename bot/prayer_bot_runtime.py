"""PrayerBot runtime mixins."""
from bot.prayer_bot_voice import PrayerBotVoiceMixin
from bot.prayer_bot_commands import PrayerBotCommandsMixin

class PrayerBotRuntimeMixin(PrayerBotVoiceMixin, PrayerBotCommandsMixin):
    pass
