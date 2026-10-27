"""Shared fixtures. All contacts are FICTIONAL and no test may touch the network."""
from __future__ import annotations

import dataclasses
import smtplib

import pytest

from shortfundly import contacts as ct
from shortfundly import database as db
from shortfundly.config import Config
from shortfundly.template_service import DEFAULT_TEMPLATE_NAME, ensure_default_template, load_template


@pytest.fixture(autouse=True)
def block_real_smtp(monkeypatch):
    """Any attempt to open a real SMTP connection fails the test immediately."""
    def boom(*args, **kwargs):
        raise AssertionError("A real SMTP connection was attempted during tests")
    monkeypatch.setattr(smtplib, "SMTP", boom)
    monkeypatch.setattr(smtplib, "SMTP_SSL", boom)


class FakeMailer:
    """Records messages. `script` is a list of None (success) or Exception (raise), consumed in order."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.sent = []
        self.calls = 0

    def send(self, msg):
        self.calls += 1
        action = self.script.pop(0) if self.script else None
        if isinstance(action, BaseException):
            raise action
        self.sent.append(msg)


@pytest.fixture
def fake_mailer():
    return FakeMailer()


@pytest.fixture
def cfg():
    return Config(
        db_path=":memory:", smtp_host="smtp.example.invalid", smtp_port=587,
        smtp_username="user@example.invalid", smtp_password="s3cret-pass-DO-NOT-LEAK",
        from_email="campus@example.invalid", from_name="Test Sender",
        send_mode="test", test_recipient="internal-test@example.invalid",
        max_attempts=3, send_delay_seconds=0, max_batch_size=20,
    )


@pytest.fixture
def live_cfg(cfg):
    return dataclasses.replace(cfg, send_mode="live")


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def sender():
    return {"sender_name": "Test Person", "sender_title": "Test Title"}


@pytest.fixture
def template(conn):
    ensure_default_template(conn)
    return load_template(conn, DEFAULT_TEMPLATE_NAME)


def make_contact(n: int, **over) -> dict:
    rec = {
        "state": "Sampleland", "city": "Exampleville",
        "college_name": f"FICTIONAL Demo College {n}",
        "website": f"https://www.demo-college-{n}.example/",
        "contact_person": f"Dr. Fictional {n}", "department": "Media Studies",
        "email": f"contact{n}@demo-college-{n}.example",
        "source_url": f"https://www.demo-college-{n}.example/contact",
    }
    rec.update(over)
    return rec


@pytest.fixture
def contacts(conn):
    """Three saved fictional contacts, returned as DB rows (dicts with ids)."""
    ids = [ct.add_contact(conn, make_contact(n)) for n in (1, 2, 3)]
    return ct.get_contacts(conn, ids)
