"""Same scenario + seed + code must give identical timelines (two real HA runs of one simulated day, ~20 s)."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from roommind_sim.analysis.determinism import first_divergence
from roommind_sim.paths import DEFAULT_HA_VERSION

SIM_ROOT = Path(__file__).resolve().parents[1]
REAL_HOME = Path(os.environ.get("ROOMMIND_SIM_HOME", Path.home() / ".roommind-sim")).expanduser()
VENV = REAL_HOME / "venvs" / f"ha-{DEFAULT_HA_VERSION}"


def _run(home: Path) -> Path:
    env = dict(os.environ, ROOMMIND_SIM_HOME=str(home), PYTHONPATH=str(SIM_ROOT))
    res = subprocess.run(
        [sys.executable, "-m", "roommind_sim.cli", "run", "smoke-ac-compressor", "--set", "duration=1d", "-q"],
        cwd=SIM_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    runs = sorted((home / "runs").iterdir(), key=lambda p: p.stat().st_mtime)
    return runs[-1]


@pytest.mark.skipif(not (VENV / "bin" / "python").exists(), reason="HA venv for the simulation missing")
def test_same_seed_same_timeline(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "venvs").mkdir(parents=True)
    (home / "venvs" / VENV.name).symlink_to(VENV)
    for name in ("ports.json", "ports.lock"):  # share port leases with runs of other checkouts
        (home / name).symlink_to(REAL_HOME / name)
    a, b = _run(home), _run(home)
    assert first_divergence(a, b) is None
    commands = [
        json.loads(line)
        for line in gzip.decompress((a / "timeline" / "events.jsonl.gz").read_bytes()).decode().splitlines()
        if '"command"' in line
    ]
    assert commands, "the simulated day should contain RoomMind commands, otherwise the check proves little"


def _write_run(run: Path, events: list[dict], start: float = 0.0, duration: float = 100.0) -> None:
    (run / "timeline").mkdir(parents=True)
    (run / "scenario.json").write_text(json.dumps({"start": start, "duration": duration}))
    for stream in ("samples", "observer"):
        (run / "timeline" / f"{stream}.jsonl").write_text(json.dumps({"t": 1.0}) + "\n")
    (run / "timeline" / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def test_first_divergence_reports_first_differing_record(tmp_path: Path) -> None:
    same = {"t": 5.0, "type": "command", "entity_id": "climate.x", "data": {"temperature": 21}}
    _write_run(tmp_path / "a", [same, {"t": 10.151, "type": "command"}])
    _write_run(tmp_path / "b", [same, {"t": 10.109, "type": "command"}])
    diff = first_divergence(tmp_path / "a", tmp_path / "b")
    assert diff is not None
    assert (diff["stream"], diff["index"], diff["a"]["t"], diff["b"]["t"]) == ("events", 1, 10.151, 10.109)


def test_first_divergence_ignores_shutdown_after_end(tmp_path: Path) -> None:
    _write_run(tmp_path / "a", [{"t": 5.0, "type": "x"}, {"t": 100.0, "type": "ha", "what": "stopping"}])
    _write_run(tmp_path / "b", [{"t": 5.0, "type": "x"}, {"t": 100.002, "type": "ha", "what": "stopping"}])
    assert first_divergence(tmp_path / "a", tmp_path / "b") is None


def test_first_divergence_reports_missing_records(tmp_path: Path) -> None:
    _write_run(tmp_path / "a", [{"t": 5.0, "type": "x"}, {"t": 6.0, "type": "y"}])
    _write_run(tmp_path / "b", [{"t": 5.0, "type": "x"}])
    diff = first_divergence(tmp_path / "a", tmp_path / "b")
    assert diff is not None
    assert (diff["index"], diff["a"]["type"], diff["b"]) == (1, "y", None)


def test_seed_randint_is_per_seed_and_per_module() -> None:
    from conftest import run_isolated

    code = """
        import random, sys, types
        from roommind_sim.boot import seed_randint
        seed_randint(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
        def draw(name):
            mod = types.ModuleType(name)
            exec("from random import randint\\ndef f(): return [randint(50000, 500000) for _ in range(3)]", mod.__dict__)
            return mod.f()
        a = draw("homeassistant.helpers.update_coordinator")
        b = draw("homeassistant.helpers.event")
        print(a, b)
    """
    first, second = run_isolated(code), run_isolated(code)
    assert first.returncode == 0, first.stderr
    assert first.stdout == second.stdout
    a, b = first.stdout.split("] [")
    assert a != b, "each calling module draws from its own generator"


def test_turbo_restart_resumes_after_fixed_downtime(capsys: pytest.CaptureFixture[str]) -> None:
    from roommind_sim.boot import RESTART_DOWNTIME_S, turbo_resume_mono

    assert turbo_resume_mono(86400.0, 86400.003) == 86400.0 + RESTART_DOWNTIME_S
    assert turbo_resume_mono(86400.0, 86400.0 + RESTART_DOWNTIME_S + 3) == 86400.0 + RESTART_DOWNTIME_S + 3
    assert "not reproducible" in capsys.readouterr().err
