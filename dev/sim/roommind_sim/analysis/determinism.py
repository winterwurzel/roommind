"""Compare two runs record by record: same scenario + seed + code should give identical timelines."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .load import _read_jsonl

STREAMS = ("events", "samples", "observer")


def first_divergence(run_a: Path, run_b: Path) -> dict[str, Any] | None:
    """First differing timeline record up to the scenario end, or None if identical.

    Records at or after the end are ignored: the shutdown after the last tick runs in
    real time (see ``boot._THEN["stop"]``), so its timestamps are not reproducible.
    """
    end = _end(run_a)
    for stream in STREAMS:
        a = _records(run_a, stream, end)
        b = _records(run_b, stream, end)
        for index, (ra, rb) in enumerate(zip(a, b, strict=False)):
            if ra != rb:
                return {"stream": stream, "index": index, "a": ra, "b": rb}
        if len(a) != len(b):
            index = min(len(a), len(b))
            return {
                "stream": stream,
                "index": index,
                "a": a[index] if index < len(a) else None,
                "b": b[index] if index < len(b) else None,
            }
    return None


def _end(run: Path) -> float:
    scenario = json.loads((run / "scenario.json").read_text())
    return float(scenario["start"]) + float(scenario["duration"])


def _records(run: Path, stream: str, end: float) -> list[dict[str, Any]]:
    return [r for r in _read_jsonl(run / "timeline" / f"{stream}.jsonl") if float(r.get("t", 0)) < end]
