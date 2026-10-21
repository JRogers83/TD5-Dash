"""Tests for backend/provision_service.py — manifest parsing, status diff, apply."""
import os
import subprocess
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
import provision_service as ps


@pytest.fixture
def manifests(tmp_path, monkeypatch):
    sysdir = tmp_path / "system"
    sysdir.mkdir()
    monkeypatch.setattr(ps, "SYSTEM_DIR", sysdir)
    monkeypatch.setattr(ps, "PACKAGES_FILE", sysdir / "packages.txt")
    monkeypatch.setattr(ps, "REMOVE_FILE", sysdir / "remove.txt")
    monkeypatch.setattr(ps, "CLEANUP_FILE", sysdir / "cleanup.sh")
    monkeypatch.setattr(ps, "_dpkg_available", lambda: True)
    return sysdir


# ── read_manifest ────────────────────────────────────────────────────────────

def test_read_manifest_strips_comments_blanks_whitespace(tmp_path):
    f = tmp_path / "m.txt"
    f.write_text("# header\n\n  feh  \ngpsd # inline comment\n\t\npython3-gps\n")
    assert ps.read_manifest(f) == ["feh", "gpsd", "python3-gps"]


def test_read_manifest_missing_file_is_empty(tmp_path):
    assert ps.read_manifest(tmp_path / "nope.txt") == []


def test_read_manifest_dedupes_and_skips_invalid_names(tmp_path):
    f = tmp_path / "m.txt"
    f.write_text("feh\nfeh\n-oAPT::Foo=bar\nbad name\nUPPER\nlibsamplerate0\ngcc-12+b1\n")
    assert ps.read_manifest(f) == ["feh", "libsamplerate0", "gcc-12+b1"]


# ── get_status ───────────────────────────────────────────────────────────────

def test_status_pending_install_when_wanted_and_absent(manifests, monkeypatch):
    (manifests / "packages.txt").write_text("feh\ngpsd\n")
    monkeypatch.setattr(ps, "_is_installed", lambda p: p == "gpsd")
    st = ps.get_status()
    assert st["pending_install"] == ["feh"]
    assert st["pending_remove"] == []
    assert st["up_to_date"] is False


def test_status_pending_remove_when_unwanted_and_present(manifests, monkeypatch):
    (manifests / "packages.txt").write_text("feh\n")
    (manifests / "remove.txt").write_text("oldpkg\ngone\n")
    monkeypatch.setattr(ps, "_is_installed", lambda p: p in ("feh", "oldpkg"))
    st = ps.get_status()
    assert st["pending_remove"] == ["oldpkg"]
    assert st["pending_install"] == []
    assert st["up_to_date"] is False


def test_status_up_to_date_when_aligned(manifests, monkeypatch):
    (manifests / "packages.txt").write_text("feh\n")
    (manifests / "remove.txt").write_text("oldpkg\n")
    monkeypatch.setattr(ps, "_is_installed", lambda p: p == "feh")
    st = ps.get_status()
    assert st["up_to_date"] is True
    assert st["available"] is True


def test_status_cleanup_present_reflects_file(manifests, monkeypatch):
    monkeypatch.setattr(ps, "_is_installed", lambda p: True)
    assert ps.get_status()["cleanup_present"] is False
    (manifests / "cleanup.sh").write_text("#!/bin/sh\n")
    assert ps.get_status()["cleanup_present"] is True


def test_status_unavailable_without_dpkg(manifests, monkeypatch):
    (manifests / "packages.txt").write_text("feh\n")
    monkeypatch.setattr(ps, "_dpkg_available", lambda: False)
    st = ps.get_status()
    assert st["available"] is False
    assert st["pending_install"] == []


def test_is_installed_parses_dpkg_status(monkeypatch):
    def fake_run(cmd, **kw):
        assert cmd[:3] == ["dpkg-query", "-W", "-f=${Status}"]
        return MagicMock(returncode=0, stdout="install ok installed")
    monkeypatch.setattr(ps.subprocess, "run", fake_run)
    assert ps._is_installed("feh") is True
    monkeypatch.setattr(ps.subprocess, "run",
                        lambda c, **k: MagicMock(returncode=0, stdout="deinstall ok config-files"))
    assert ps._is_installed("feh") is False
    monkeypatch.setattr(ps.subprocess, "run", lambda c, **k: MagicMock(returncode=1, stdout=""))
    assert ps._is_installed("feh") is False


# ── apply ────────────────────────────────────────────────────────────────────

def test_apply_success(monkeypatch):
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout="installed feh\n", stderr="")
    monkeypatch.setattr(ps.subprocess, "run", fake_run)
    res = ps.apply()
    assert res == {"ok": True, "output": "installed feh", "needs_setup": False}
    assert calls[0][:2] == ["sudo", "-n"] and calls[0][-1] == "apply"
    assert calls[0][2] == str(ps.PROVISION_SCRIPT)


def test_apply_nonzero_does_not_raise_and_reports_output(monkeypatch):
    monkeypatch.setattr(ps.subprocess, "run",
                        lambda c, **k: MagicMock(returncode=100, stdout="", stderr="E: Unable to locate package"))
    res = ps.apply()
    assert res["ok"] is False and "Unable to locate" in res["output"]
    assert res["needs_setup"] is False


def test_apply_detects_missing_sudoers_rule(monkeypatch):
    monkeypatch.setattr(ps.subprocess, "run",
                        lambda c, **k: MagicMock(returncode=1, stdout="", stderr="sudo: a password is required"))
    assert ps.apply()["needs_setup"] is True


def test_apply_never_raises_on_oserror_or_timeout(monkeypatch):
    def boom(*a, **k):
        raise OSError("sudo not found")
    monkeypatch.setattr(ps.subprocess, "run", boom)
    assert ps.apply()["ok"] is False
    def slow(*a, **k):
        raise subprocess.TimeoutExpired("sudo", 1)
    monkeypatch.setattr(ps.subprocess, "run", slow)
    assert "Timed out" in ps.apply()["output"]


def test_apply_rejects_concurrent_run(monkeypatch):
    assert ps._apply_lock.acquire(blocking=False)
    try:
        res = ps.apply()
        assert res["ok"] is False and "already in progress" in res["output"]
    finally:
        ps._apply_lock.release()
