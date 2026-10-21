"""system_service CPU load: first sample has no baseline (review finding #9)."""
import builtins
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import system_service


def _fake_proc_stat(monkeypatch, lines):
    it = iter(lines)
    real_open = builtins.open

    def fake_open(path, *a, **kw):
        if path == "/proc/stat":
            return io.StringIO(next(it))
        return real_open(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", fake_open)


def test_first_call_returns_unavailable_then_real_delta(monkeypatch):
    monkeypatch.setattr(system_service, "_cpu_prev_idle", 0)
    monkeypatch.setattr(system_service, "_cpu_prev_total", 0)
    # fields: user nice system idle iowait irq softirq ...
    _fake_proc_stat(monkeypatch, [
        "cpu  100 0 100 800 0 0 0\n",     # total 1000, idle 800
        "cpu  150 0 150 900 0 0 0\n",     # total 1200, idle 900 -> d_total 200, d_idle 100 -> 50 %
    ])
    assert system_service._read_cpu_load() == -1.0
    assert system_service._read_cpu_load() == 50.0
