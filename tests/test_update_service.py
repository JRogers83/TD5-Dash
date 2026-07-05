"""Tests for backend/update_service.py — OTA update/rollback orchestration.

All subprocess calls are mocked; these tests never touch a real git repo.
"""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
import update_service as svc


def _git_result(returncode=0, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ── capture_head / describe_commit ──────────────────────────────────────────

def test_capture_head_returns_stripped_output():
    with patch.object(svc.subprocess, "run", return_value=_git_result(stdout="abc123\n")):
        assert svc.capture_head() == "abc123"


def test_capture_head_raises_git_error_on_failure():
    with patch.object(svc.subprocess, "run", return_value=_git_result(returncode=1, stderr="fatal: no repo")):
        with pytest.raises(svc.GitError):
            svc.capture_head()


def test_describe_commit_falls_back_to_hash_on_git_failure():
    with patch.object(svc.subprocess, "run", return_value=_git_result(returncode=1, stderr="bad object")):
        result = svc.describe_commit("a" * 40)
    assert result == ("a" * 40)[:7]


# ── perform_update ───────────────────────────────────────────────────────────

def test_perform_update_pushes_history_when_head_moves(monkeypatch):
    monkeypatch.setattr(svc.db, "push_update_history", MagicMock())
    monkeypatch.setattr(svc, "reinstall_deps", MagicMock())
    # perform_update() calls capture_head() exactly twice: once before `pull`,
    # once after. Two items only — a third would silently mis-pair the calls.
    heads = iter(["old_hash", "new_hash"])

    def fake_run(*args, **kwargs):
        argv = args[0]
        if argv[:2] == ["git", "-C"] and "rev-parse" in argv:
            return _git_result(stdout=next(heads) + "\n")
        if "pull" in argv:
            return _git_result(stdout="Updating old_hash..new_hash\n")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    with patch.object(svc.subprocess, "run", side_effect=fake_run):
        result = svc.perform_update()

    svc.db.push_update_history.assert_called_once_with("old_hash")
    assert result == {"ok": True, "output": "Updating old_hash..new_hash", "restarting": True}


def test_perform_update_skips_history_when_pull_is_noop(monkeypatch):
    monkeypatch.setattr(svc.db, "push_update_history", MagicMock())
    monkeypatch.setattr(svc, "reinstall_deps", MagicMock())

    def fake_run(*args, **kwargs):
        argv = args[0]
        if "rev-parse" in argv:
            return _git_result(stdout="same_hash\n")
        if "pull" in argv:
            return _git_result(stdout="Already up to date.\n")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    with patch.object(svc.subprocess, "run", side_effect=fake_run):
        svc.perform_update()

    svc.db.push_update_history.assert_not_called()


def test_perform_update_raises_and_skips_history_on_pull_failure(monkeypatch):
    monkeypatch.setattr(svc.db, "push_update_history", MagicMock())
    monkeypatch.setattr(svc, "reinstall_deps", MagicMock())

    def fake_run(*args, **kwargs):
        argv = args[0]
        if "rev-parse" in argv:
            return _git_result(stdout="old_hash\n")
        if "pull" in argv:
            return _git_result(returncode=1, stderr="fatal: unable to access network")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    with patch.object(svc.subprocess, "run", side_effect=fake_run):
        with pytest.raises(svc.GitError):
            svc.perform_update()

    svc.db.push_update_history.assert_not_called()
    svc.reinstall_deps.assert_not_called()


# ── perform_rollback ─────────────────────────────────────────────────────────

def test_perform_rollback_raises_when_history_empty(monkeypatch):
    monkeypatch.setattr(svc.db, "get_update_history", MagicMock(return_value=[]))
    with pytest.raises(svc.NoPreviousVersionError):
        svc.perform_rollback()


def test_perform_rollback_resets_to_popped_hash(monkeypatch):
    monkeypatch.setattr(svc.db, "get_update_history", MagicMock(return_value=["old_hash"]))
    monkeypatch.setattr(svc.db, "pop_update_history", MagicMock())
    monkeypatch.setattr(svc, "reinstall_deps", MagicMock())

    def fake_run(*args, **kwargs):
        argv = args[0]
        if "reset" in argv:
            assert "old_hash" in argv
            return _git_result(stdout="HEAD is now at old_hash\n")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    with patch.object(svc.subprocess, "run", side_effect=fake_run):
        result = svc.perform_rollback()

    assert result == {"ok": True, "output": "HEAD is now at old_hash", "restarting": True}
    svc.db.pop_update_history.assert_called_once()
    svc.reinstall_deps.assert_called_once()


def test_perform_rollback_preserves_history_on_reset_failure(monkeypatch):
    monkeypatch.setattr(svc.db, "get_update_history", MagicMock(return_value=["old_hash"]))
    monkeypatch.setattr(svc.db, "pop_update_history", MagicMock())
    monkeypatch.setattr(svc, "reinstall_deps", MagicMock())

    def fake_run(*args, **kwargs):
        return _git_result(returncode=1, stderr="fatal: could not reset")

    with patch.object(svc.subprocess, "run", side_effect=fake_run):
        with pytest.raises(svc.GitError):
            svc.perform_rollback()

    svc.db.pop_update_history.assert_not_called()
    svc.reinstall_deps.assert_not_called()


# ── get_version_info ─────────────────────────────────────────────────────────

def test_get_version_info_no_history():
    with patch.object(svc.db, "get_update_history", return_value=[]), \
         patch.object(svc, "capture_head", return_value="head_hash"), \
         patch.object(svc, "describe_commit", return_value="head7 subject line"):
        info = svc.get_version_info()

    assert info == {
        "current_version": "head7 subject line",
        "rollback_available": 0,
        "rollback_target": None,
    }


def test_get_version_info_falls_back_when_capture_head_raises():
    with patch.object(svc.db, "get_update_history", return_value=[]), \
         patch.object(svc, "capture_head", side_effect=svc.GitError("fatal: not a git repo")):
        info = svc.get_version_info()

    assert info["current_version"] == "unknown"
    assert info["rollback_available"] == 0
    assert info["rollback_target"] is None


def test_get_version_info_with_history():
    with patch.object(svc.db, "get_update_history", return_value=["a" * 40, "b" * 40]), \
         patch.object(svc, "capture_head", return_value="head_hash"), \
         patch.object(svc, "describe_commit", side_effect=lambda h: f"described:{h}"):
        info = svc.get_version_info()

    assert info["rollback_available"] == 2
    assert info["rollback_target"] == f"described:{'b' * 40}"
