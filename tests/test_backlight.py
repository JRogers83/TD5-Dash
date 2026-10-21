"""Tests for backend/backlight.py — sysfs apply, scaling, and boot-time saved level."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
import backlight
import db


@pytest.fixture
def fake_backlight(tmp_path, monkeypatch):
    dev = tmp_path / "bl" / "panel0"
    dev.mkdir(parents=True)
    (dev / "brightness").write_text("0")
    (dev / "max_brightness").write_text("255")
    monkeypatch.setattr(backlight, "_BACKLIGHT_DIR_GLOB", str(tmp_path / "bl" / "*"))
    return dev


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_DIR", tmp_path)
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "test.db")
    db.init_db()


def test_apply_writes_value(fake_backlight):
    res = backlight.apply(120)
    assert res["ok"] and res["value"] == 120
    assert (fake_backlight / "brightness").read_text() == "120"


def test_apply_scales_to_max_brightness(fake_backlight):
    (fake_backlight / "max_brightness").write_text("100")
    backlight.apply(255)
    assert (fake_backlight / "brightness").read_text() == "100"
    backlight.apply(0)
    assert (fake_backlight / "brightness").read_text() == "0"


def test_apply_clamps(fake_backlight):
    assert backlight.apply(999)["value"] == 255
    assert backlight.apply(-5)["value"] == 0


def test_apply_without_device_reports_not_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(backlight, "_BACKLIGHT_DIR_GLOB", str(tmp_path / "none" / "*"))
    res = backlight.apply(100)
    assert res["ok"] is False and res["detail"] == "no backlight device"


def test_apply_write_failure_reports_not_ok(fake_backlight, monkeypatch):
    real_open = open

    def deny(path, mode="r", *a, **kw):
        if "w" in mode:
            raise PermissionError(13, "Permission denied")
        return real_open(path, mode, *a, **kw)

    monkeypatch.setattr("builtins.open", deny)
    res = backlight.apply(100)
    assert res["ok"] is False and "denied" in res["detail"].lower()


def test_saved_level_defaults_to_day(isolated_db):
    assert backlight.saved_level() == 180


def test_saved_level_follows_mode(isolated_db):
    db.set_settings({"brightness_night": "60", "brightness_mode": "night"})
    assert backlight.saved_level() == 60
    db.set_settings({"brightness_mode": "day", "brightness_day": "200"})
    assert backlight.saved_level() == 200


def test_apply_on_startup_applies_saved_level(fake_backlight, isolated_db):
    db.set_settings({"brightness_day": "150"})
    asyncio.run(backlight.apply_on_startup())
    assert (fake_backlight / "brightness").read_text() == "150"


def test_apply_on_startup_gives_up_when_write_denied(fake_backlight, isolated_db, monkeypatch):
    calls = []
    monkeypatch.setattr(backlight, "apply", lambda v: calls.append(v) or
                        {"ok": False, "value": v, "detail": "Permission denied"})
    asyncio.run(backlight.apply_on_startup())
    assert len(calls) == 1   # no retry loop on a permission failure
