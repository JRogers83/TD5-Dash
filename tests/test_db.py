"""Tests for backend/db.py — update_history rollback stack."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
import db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point db.py at a fresh SQLite file per test so tests never touch the real DB."""
    monkeypatch.setattr(db, "_DB_DIR", tmp_path)
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "test.db")
    db.init_db()


def test_get_update_history_empty_by_default():
    assert db.get_update_history() == []


def test_push_then_get_returns_pushed_hash():
    db.push_update_history("a" * 40)
    assert db.get_update_history() == ["a" * 40]


def test_push_trims_to_last_five():
    hashes = [str(i) * 40 for i in range(1, 7)]  # 6 distinct hashes
    for h in hashes:
        db.push_update_history(h)
    assert db.get_update_history() == hashes[-5:]


def test_pop_returns_most_recently_pushed():
    db.push_update_history("a" * 40)
    db.push_update_history("b" * 40)
    assert db.pop_update_history() == "b" * 40
    assert db.pop_update_history() == "a" * 40


def test_pop_empty_returns_none():
    assert db.pop_update_history() is None


def test_pop_removes_entry_from_history():
    db.push_update_history("a" * 40)
    db.pop_update_history()
    assert db.get_update_history() == []
