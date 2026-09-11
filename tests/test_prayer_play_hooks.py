from bot.prayer_play_hooks import _is_prayer_playing_impl


class _Player:
    def __init__(self, playing: bool):
        self._playing = playing

    def is_playing(self):
        return self._playing


class _Bot:
    def __init__(self):
        self.players = {}
        self._tts_playing = set()
        self._is_prayer_playing = lambda gid: _is_prayer_playing_impl(self, gid)


def test_prayer_playing_false_when_no_player():
    bot = _Bot()
    assert bot._is_prayer_playing("g1") is False


def test_prayer_playing_true_when_mp3_active():
    bot = _Bot()
    bot.players["g1"] = _Player(True)
    assert bot._is_prayer_playing("g1") is True


def test_prayer_playing_false_during_tts():
    bot = _Bot()
    bot.players["g1"] = _Player(True)
    bot._tts_playing.add("g1")
    assert bot._is_prayer_playing("g1") is False
