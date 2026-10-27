import sqlite3

from shortfundly import contacts as ct
from shortfundly import database as db
from shortfundly import sending
from tests.conftest import FakeMailer, make_contact


def test_data_persists_across_connections(tmp_path, cfg, sender):
    path = str(tmp_path / "persist.db")
    conn = db.connect(path)
    cid = ct.add_contact(conn, make_contact(1))
    db.set_setting(conn, "sender_name", "Persisted Name")
    q = sending.queue_messages(conn, cfg, ct.get_contacts(conn, [cid]), "S {college_name}", "B {sender_name}",
                               sender, "Camp")
    sending.process_queue(conn, FakeMailer(), cfg, q.created_ids)
    conn.close()

    conn2 = db.connect(path)
    assert ct.get_contact(conn2, cid)["college_name"] == "FICTIONAL Demo College 1"
    assert db.get_setting(conn2, "sender_name") == "Persisted Name"
    row = conn2.execute("SELECT * FROM email_log").fetchone()
    assert row["status"] == "sent" and row["attempt_count"] == 1 and row["accepted_at"]
    conn2.close()


def test_init_is_idempotent(tmp_path):
    path = str(tmp_path / "x.db")
    db.connect(path).close()
    conn = db.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"contacts", "email_log", "email_templates", "settings", "import_rejections"} <= tables


def test_foreign_keys_enforced_and_delete_keeps_history(conn, cfg, sender, contacts):
    q = sending.queue_messages(conn, cfg, contacts[:1], "S", "B {sender_name}", sender, "Camp")
    ct.delete_contacts(conn, [contacts[0]["id"]])
    row = conn.execute("SELECT * FROM email_log WHERE id=?", (q.created_ids[0],)).fetchone()
    assert row["contact_id"] is None and row["recipient_email"] == contacts[0]["email"]
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_config_hides_password_and_normalises():
    from shortfundly.config import Config
    c = Config.from_env({"SMTP_PASSWORD": "topsecret", "SEND_MODE": "LIVE", "SMTP_SECURITY": "weird",
                         "SMTP_PORT": "abc", "DB_PATH": ":memory:"})
    assert "topsecret" not in repr(c)
    assert c.redact("login with topsecret failed") == "login with ******** failed"
    assert c.send_mode == "live" and c.smtp_security == "starttls" and c.smtp_port == 587
    assert Config.from_env({}).send_mode == "test"
    assert Config.from_env({"SEND_MODE": "yes please"}).send_mode == "test"
