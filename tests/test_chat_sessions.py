import pytest
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.chat.sessions import SessionManager


@pytest.fixture
def temp_session_file():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def session_manager(temp_session_file):
    return SessionManager(storage_file=temp_session_file)


def test_create_session(session_manager):
    session = session_manager.create_session()
    
    assert "id" in session
    assert session["name"].startswith("Session ")
    assert session["history"] == []
    assert "created_at" in session
    assert "updated_at" in session


def test_create_session_with_name(session_manager):
    session = session_manager.create_session(name="My Chat")
    
    assert session["name"] == "My Chat"


def test_get_session(session_manager):
    created = session_manager.create_session()
    session_id = created["id"]
    
    retrieved = session_manager.get_session(session_id)
    
    assert retrieved is not None
    assert retrieved["id"] == session_id


def test_get_session_not_found(session_manager):
    result = session_manager.get_session("nonexistent-id")
    
    assert result is None


def test_delete_session(session_manager):
    session = session_manager.create_session()
    session_id = session["id"]
    
    result = session_manager.delete_session(session_id)
    
    assert result is True
    assert session_manager.get_session(session_id) is None


def test_delete_session_not_found(session_manager):
    result = session_manager.delete_session("nonexistent-id")
    
    assert result is False


def test_rename_session(session_manager):
    session = session_manager.create_session()
    session_id = session["id"]
    
    result = session_manager.rename_session(session_id, "New Name")
    
    assert result is True
    assert session_manager.get_session(session_id)["name"] == "New Name"


def test_rename_session_not_found(session_manager):
    result = session_manager.rename_session("nonexistent-id", "New Name")
    
    assert result is False


def test_list_sessions(session_manager):
    session1 = session_manager.create_session(name="First")
    session2 = session_manager.create_session(name="Second")
    session3 = session_manager.create_session(name="Third")
    
    sessions = session_manager.list_sessions()
    
    assert len(sessions) == 3
    names = [s["name"] for s in sessions]
    assert "First" in names
    assert "Second" in names
    assert "Third" in names


def test_add_message(session_manager):
    session = session_manager.create_session()
    session_id = session["id"]
    
    result = session_manager.add_message(session_id, "user", "Hello")
    
    assert result is True
    session = session_manager.get_session(session_id)
    assert len(session["history"]) == 1
    assert session["history"][0]["role"] == "user"
    assert session["history"][0]["content"] == "Hello"


def test_add_message_to_nonexistent_session(session_manager):
    result = session_manager.add_message("nonexistent-id", "user", "Hello")
    
    assert result is False
