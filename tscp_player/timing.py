"""Interactive compilation of source ``.tscps`` files."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional, Union

from .format import ANSI_SEQUENCE_RE, Dialogue, Script, parse_tscps, serialize_tscp


TimingMode = str


def compile_script(
    source: Script,
    input_fn=input,
    output_fn=print,
    mode: TimingMode = "sentence",
    clock: Optional[Callable[[], float]] = None,
) -> Script:
    """Record one delay per character.

    In an interactive terminal, pressing Enter advances each character.  The
    fallback prompt is intentionally deterministic and useful for redirected
    input and tests.
    """
    if mode not in {"sentence", "continuous", "per_sentence"}:
        raise ValueError("unknown timing mode: %s" % mode)
    clock = time.monotonic if clock is None else clock
    result = []
    previous_global = None
    for item in source.lines:
        if not isinstance(item, Dialogue):
            result.append(item)
            continue
        delays = []
        output_fn("\n" + (("[%s]" % item.character) if item.character else "[旁白]") + item.text)
        previous = clock() if mode != "continuous" or previous_global is None else previous_global
        plain_text = ANSI_SEQUENCE_RE.sub("", item.text)
        for character in plain_text:
            input_fn("按 Enter 播放下一个字（%s）: " % character)
            now = clock()
            delays.append(max(0.0, now - previous))
            previous = now
            previous_global = now
        result.append(Dialogue(item.character, item.text, delays))
    return Script(result)


def compile_file(
    source_path: str,
    output_path: str,
    input_fn=input,
    output_fn=print,
    mode: TimingMode = "sentence",
) -> None:
    source = parse_tscps(Path(source_path).read_text(encoding="utf-8"))
    compiled = compile_script(
        source, input_fn=input_fn, output_fn=output_fn, mode=mode
    )
    Path(output_path).write_text(serialize_tscp(compiled), encoding="utf-8")
