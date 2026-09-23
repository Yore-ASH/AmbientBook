from io import StringIO

from tscp_player.format import Dialogue, Directive, Script
from tscp_player.plot import Character
from tscp_player.renderer import RenderOptions, TerminalRenderer, expand_ansi


def test_renderer_aligns_dialogue_and_narrator():
    output = StringIO()
    renderer = TerminalRenderer({"f": Character("FISH", "")}, output=output,
                                options=RenderOptions(color=False, vertical_center=False),
                                music=type("Music", (), {"stop": lambda self: None})())
    renderer.render(Script([Dialogue("f", "Hi"), Dialogue(None, "Aside")]))
    lines = output.getvalue().splitlines()
    assert lines[-2] == "FISH : Hi"
    assert lines[-1] == "        Aside"


def test_renderer_expands_literal_ansi_sequences():
    assert expand_ansi(r"\033[1;91m警告\033[0m") == "\033[1;91m警告\033[0m"


class RecordingMusic:
    """Music stand-in that records which playback call was used."""

    def __init__(self):
        self.calls = []

    def ensure(self, path):
        self.calls.append(("ensure", path))
        return True

    def play(self, path):
        self.calls.append(("play", path))

    def stop(self):
        self.calls.append(("stop", None))


def test_renderer_ensures_music_so_a_track_is_not_restarted(tmp_path):
    track = tmp_path / "theme.flac"
    track.write_bytes(b"not really audio")
    music = RecordingMusic()
    renderer = TerminalRenderer(
        {"f": Character("FISH", "")},
        output=StringIO(),
        options=RenderOptions(color=False, vertical_center=False),
        music=music,
        music_files={"t": "theme.flac"},
        music_root=tmp_path,
        sleeper=lambda _: None,
    )
    renderer.render(Script([
        Directive("p", "t"),
        Dialogue("f", "A", [0.0]),
        Directive("p", "t"),
        Directive("p", "stop"),
    ]))
    # 同一曲目走 ensure（由播放器决定续播），从不调用 play 强制重启。
    assert music.calls == [
        ("ensure", str(track)),
        ("ensure", str(track)),
        ("stop", None),
    ]
