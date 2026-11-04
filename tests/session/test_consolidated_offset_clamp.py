"""Reset a corrupt last_consolidated offset instead of hiding history (#4066)."""

from pathlib import Path

from jenny.session.manager import Session, SessionManager


def _session(count: int, last_consolidated: object) -> Session:
    msgs = [{"role": "user", "content": f"msg{i}"} for i in range(count)]
    return Session(key="chan:chat", messages=msgs, last_consolidated=last_consolidated)


def test_out_of_range_offset_is_reset():
    assert _session(10, 999).last_consolidated == 0
    assert _session(3, -5).last_consolidated == 0


def test_non_integer_offset_is_reset():
    for offset in ("999", None, 0.5, True):
        assert _session(3, offset).last_consolidated == 0


def test_loaded_corrupt_offset_keeps_messages(tmp_path: Path):
    # Quel che una colonna INTEGER può ancora portare di storto: un numero
    # fuori misura (un "999" diventa 999) o un decimale.
    offsets = {
        "string": "999",
        "float": 0.5,
    }

    for name, offset in offsets.items():
        manager = SessionManager(tmp_path / name / "ws")
        message = {"role": "user", "content": f"survived {name}"}
        seed = manager.get_or_create("chan:chat")
        seed.messages.append(message)
        manager.save(seed)
        with manager._db.write() as conn:
            conn.execute("UPDATE sessions SET last_consolidated = ? WHERE key = 'chan:chat'",
                         (offset,))
        manager = SessionManager(tmp_path / name / "ws")

        session = manager.get_or_create("chan:chat")

        assert session.messages == [message]
        assert session.last_consolidated == 0
        assert session.get_history(max_messages=10) == [message]


def test_valid_offset_is_preserved():
    session = _session(10, 4)
    assert session.last_consolidated == 4
    assert len(session.get_history()) == 6
