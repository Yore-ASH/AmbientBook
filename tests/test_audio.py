from tscp_player.audio import MusicPlayer


class FakeMusic:
    def __init__(self):
        self.loads = []
        self.plays = 0
        self.stops = 0
        self.pauses = 0
        self.unpauses = 0

    def load(self, path):
        self.loads.append(path)

    def set_volume(self, value):
        pass

    def play(self, loops=0):
        self.plays += 1

    def stop(self):
        self.stops += 1

    def pause(self):
        self.pauses += 1

    def unpause(self):
        self.unpauses += 1


class FakeMixer:
    def __init__(self):
        self.music = FakeMusic()


def _track(tmp_path, name="a.flac"):
    path = tmp_path / name
    path.write_bytes(b"not really audio")
    return path


def test_ensure_does_not_restart_the_current_track(tmp_path):
    track = _track(tmp_path)
    mixer = FakeMixer()
    player = MusicPlayer(mixer=mixer)
    assert player.ensure(track) is True
    assert player.ensure(track) is False
    assert mixer.music.loads == [str(track)]
    assert mixer.music.plays == 1


def test_switching_tracks_reloads(tmp_path):
    first = _track(tmp_path, "a.flac")
    second = _track(tmp_path, "b.flac")
    mixer = FakeMixer()
    player = MusicPlayer(mixer=mixer)
    player.ensure(first)
    player.ensure(second)
    player.ensure(first)
    assert mixer.music.loads == [str(first), str(second), str(first)]
    assert mixer.music.plays == 3


def test_restart_flag_forces_a_reload(tmp_path):
    track = _track(tmp_path)
    mixer = FakeMixer()
    player = MusicPlayer(mixer=mixer)
    player.ensure(track)
    assert player.ensure(track, restart=True) is True
    assert mixer.music.plays == 2


def test_paused_track_resumes_without_reloading(tmp_path):
    track = _track(tmp_path)
    mixer = FakeMixer()
    player = MusicPlayer(mixer=mixer)
    player.ensure(track)
    player.pause()
    assert player.paused is True
    assert player.ensure(track) is False
    assert player.paused is False
    assert mixer.music.unpauses == 1
    assert mixer.music.loads == [str(track)]


def test_stop_clears_the_current_track(tmp_path):
    track = _track(tmp_path)
    mixer = FakeMixer()
    player = MusicPlayer(mixer=mixer)
    player.ensure(track)
    player.stop()
    assert player.current is None
    assert player.ensure(track) is True
    assert mixer.music.loads == [str(track), str(track)]


def test_missing_file_is_reported(tmp_path):
    mixer = FakeMixer()
    player = MusicPlayer(mixer=mixer)
    try:
        player.ensure(tmp_path / "missing.flac")
    except FileNotFoundError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a missing music file must raise FileNotFoundError")
