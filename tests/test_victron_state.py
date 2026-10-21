"""VictronState staleness: only configured devices count (review finding #4)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from victron.scanner import VictronScanner, VictronState


def _fresh_now():
    return time.monotonic()


def test_unconfigured_orion_does_not_make_state_stale():
    st = VictronState()
    VictronScanner(st, "AA:AA:AA:AA:AA:01", "k" * 32, "AA:AA:AA:AA:AA:02", "k" * 32)  # no Orion
    st.shunt_updated = st.mppt_updated = _fresh_now()
    assert st.configured == frozenset({"shunt", "mppt"})
    assert st.stale_devices() == []
    assert st.is_fresh()


def test_configured_device_not_reporting_is_stale():
    st = VictronState()
    VictronScanner(st, "AA:AA:AA:AA:AA:01", "k" * 32, "AA:AA:AA:AA:AA:02", "k" * 32,
                   "AA:AA:AA:AA:AA:03", "k" * 32)
    st.shunt_updated = st.mppt_updated = _fresh_now()   # orion never reported
    assert st.stale_devices() == ["orion"]
    assert not st.is_fresh()


def test_old_report_is_stale():
    st = VictronState()
    st.configured = frozenset({"shunt"})
    st.shunt_updated = time.monotonic() - 60
    assert st.stale_devices() == ["shunt"]
