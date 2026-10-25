"""Chat persistence: stale-read protection, retention and corrupt-store recovery."""

import time

from modules.chat.sessions import SessionManager


def test_second_manager_does_not_overwrite_newer_messages(tmp_path):
    path = str(tmp_path / "sessions.json")
    first = SessionManager(storage_file=path)
    session_id = first.create_session("shared")["id"]

    second = SessionManager(storage_file=path)
    first.add_message(session_id, "user", "from-first")
    second.add_message(session_id, "user", "from-second")

    reloaded = SessionManager(storage_file=path).get_session(session_id)
    assert [message["content"] for message in reloaded["history"]] == [
        "from-first", "from-second",
    ]


def test_message_retention_keeps_the_newest_messages(tmp_path):
    manager = SessionManager(
        storage_file=str(tmp_path / "sessions.json"), max_messages=3
    )
    session_id = manager.create_session("bounded")["id"]

    for index in range(5):
        manager.add_message(session_id, "user", f"message-{index}")

    session = manager.get_session(session_id)
    assert [message["content"] for message in session["history"]] == [
        "message-2", "message-3", "message-4",
    ]


def test_session_retention_drops_the_oldest_sessions(tmp_path):
    manager = SessionManager(
        storage_file=str(tmp_path / "sessions.json"), max_sessions=2
    )

    manager.create_session("first")
    time.sleep(0.002)
    manager.create_session("second")
    time.sleep(0.002)
    manager.create_session("third")

    names = {session["name"] for session in manager.list_sessions()}
    assert names == {"second", "third"}


def test_corrupt_session_store_is_quarantined(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("[]", encoding="utf-8")

    manager = SessionManager(storage_file=str(path))

    assert manager.sessions == {}
    assert list(tmp_path.glob("sessions.json.corrupt-*"))


def test_concurrent_write_is_seen_even_with_a_coarse_file_clock(tmp_path):
    import os

    store = str(tmp_path / "sessions.json")
    first = SessionManager(storage_file=store)
    first.create_session("from first")
    stamp = os.stat(store).st_mtime_ns
    second = SessionManager(storage_file=store)
    second.create_session("from second")
    # A 1-2 second filesystem clock can give the second write the first write's timestamp.
    os.utime(store, ns=(stamp, stamp))

    first.create_session("first again")

    names = {s["name"] for s in SessionManager(storage_file=store).sessions.values()}
    assert names == {"from first", "from second", "first again"}
