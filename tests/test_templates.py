import pytest

from shortfundly import database as db
from shortfundly import template_service as ts
from tests.conftest import make_contact

SENDER = {"sender_name": "Test Person", "sender_title": "Test Title"}


def test_default_subject_and_files_present():
    subject, body = ts.load_default_template()
    assert subject == "Proposal for Shortfundly Campus Collaboration — {college_name}"
    assert body.startswith("PROPOSAL FOR SHORTFUNDLY CAMPUS COLLABORATION")


def test_all_placeholders_render():
    text = "{college_name}|{recipient_name}|{department}|{city}|{state}|{sender_name}|{sender_title}"
    out = ts.render(text, ts.build_context(make_contact(1), SENDER))
    assert out.missing == [] and out.unknown == []
    assert out.text == ("FICTIONAL Demo College 1|Dr. Fictional 1|Media Studies|Exampleville|Sampleland|"
                        "Test Person|Test Title")


def test_default_proposal_terms_survive_rendering():
    subject, body = ts.load_default_template()
    r = ts.render_email(subject, body, make_contact(1), SENDER)
    assert r.problems == []
    assert r.subject == "Proposal for Shortfundly Campus Collaboration — FICTIONAL Demo College 1"
    for must_have in (
        "₹1,500 + GST per year", "+91 93612 23155", "https://campus.shortfundly.com/",
        "https://calendly.com/shortfundly-meeting/30min", "Dear Dr. Fictional 1,",
        "Warm regards,\nTest Person\nTest Title | Shortfundly", "Empowering Student Creators at FICTIONAL Demo College 1",
        "Connect, Festival, Hub, Marketing, Ticket, and Tools",
        "Institutional pricing and any additional activity charges will be confirmed through a separate quotation",
    ):
        assert must_have in r.body
    assert "{" not in r.body


def test_sender_details_are_never_invented():
    subject, body = ts.load_default_template()
    r = ts.render_email(subject, body, make_contact(1), {"sender_name": "", "sender_title": ""})
    assert set(r.missing) == {"sender_name", "sender_title"}
    assert "{sender_name}" in r.body and r.problems


def test_unknown_placeholder_is_reported_not_crashing():
    r = ts.render_email("Hi {college_name}", "Hello {nonsense} and plain braces", make_contact(1), SENDER)
    assert r.unknown == ["nonsense"]
    assert r.problems


def test_missing_contact_person_uses_generic_salutation():
    r = ts.render_email("S", "Dear {recipient_name},", make_contact(1, contact_person=""), SENDER)
    assert r.body == "Dear Sir/Madam,"


def test_subject_cannot_contain_line_breaks():
    r = ts.render_email("A\r\nBcc: x@y.example {college_name}", "b", make_contact(1, college_name="N\nM"), SENDER)
    assert "\n" not in r.subject and "\r" not in r.subject


def test_decode_upload():
    assert ts.decode_upload("Dear {recipient_name},\r\nHi\r\n".encode("utf-8-sig")) == "Dear {recipient_name},\nHi"
    assert "₹" in ts.decode_upload("₹1,500".encode("utf-8"))
    with pytest.raises(ValueError):
        ts.decode_upload(b"   \n ")
    with pytest.raises(ValueError):
        ts.decode_upload(b"x" * (ts.MAX_UPLOAD_BYTES + 1))


def test_template_save_load_and_persistence(tmp_path):
    path = str(tmp_path / "t.db")
    conn = db.connect(path)
    ts.ensure_default_template(conn)
    ts.save_template(conn, "Custom", "Subject {college_name}", "Body for {college_name}")
    ts.save_template(conn, "Custom", "Subject 2 {college_name}", "Body 2")     # overwrite
    conn.close()
    conn2 = db.connect(path)
    assert ts.list_templates(conn2) == ["Custom", "Default Proposal"]
    assert ts.load_template(conn2, "Custom")["subject"] == "Subject 2 {college_name}"
    ts.delete_template(conn2, "Custom")
    with pytest.raises(ValueError):
        ts.delete_template(conn2, ts.DEFAULT_TEMPLATE_NAME)
    ts.save_template(conn2, ts.DEFAULT_TEMPLATE_NAME, "changed", "changed")
    ts.restore_default_template(conn2)
    assert "₹1,500" in ts.load_template(conn2, ts.DEFAULT_TEMPLATE_NAME)["body"]
    with pytest.raises(ValueError):
        ts.save_template(conn2, "x", "  ", "body")
    conn2.close()
