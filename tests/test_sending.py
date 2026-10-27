import dataclasses
import smtplib
import socket

import pytest

from shortfundly import contacts as ct
from shortfundly import mailer as mailer_mod
from shortfundly import sending as sd
from shortfundly.template_service import render_email
from tests.conftest import FakeMailer, make_contact

SUBJ = "Proposal for {college_name}"
BODY = "Dear {recipient_name},\nCommercial: ₹1,500 + GST per year.\n{sender_name}\n{sender_title} | Shortfundly"


def queue(conn, cfg, contacts, sender, campaign="Camp1", live=False, confirmed=False):
    return sd.queue_messages(conn, cfg, contacts, SUBJ, BODY, sender, campaign, live=live, live_confirmed=confirmed)


def status_of(conn, rid):
    return conn.execute("SELECT status FROM email_log WHERE id=?", (rid,)).fetchone()[0]


def row(conn, rid):
    return conn.execute("SELECT * FROM email_log WHERE id=?", (rid,)).fetchone()


# ------------------------------------------------------------------ test mode
def test_test_mode_redirects_every_message_to_internal_recipient(conn, cfg, sender, contacts, fake_mailer):
    q = queue(conn, cfg, contacts, sender)
    res = sd.process_queue(conn, fake_mailer, cfg, q.created_ids)
    assert res.accepted == 3 and res.failed == 0
    assert [m["To"] for m in fake_mailer.sent] == ["internal-test@example.invalid"] * 3
    for m in fake_mailer.sent:
        assert m["Subject"].startswith("[TEST] ")
        assert "demo-college" not in m["To"]
    stored = row(conn, q.created_ids[0])
    assert stored["recipient_email"] == contacts[0]["email"]       # intended recipient is remembered
    assert stored["delivered_to"] == "internal-test@example.invalid"
    assert stored["is_test"] == 1


def test_override_is_reapplied_at_send_time_even_if_row_was_tampered(conn, cfg, sender, contacts, fake_mailer):
    q = queue(conn, cfg, contacts[:1], sender)
    conn.execute("UPDATE email_log SET delivered_to='victim@demo-college-1.example' WHERE id=?", (q.created_ids[0],))
    conn.commit()
    sd.process_queue(conn, fake_mailer, cfg, q.created_ids)
    assert fake_mailer.sent[0]["To"] == "internal-test@example.invalid"


def test_test_mode_requires_valid_test_recipient(conn, cfg, sender, contacts):
    with pytest.raises(sd.SendingBlocked, match="TEST_RECIPIENT"):
        queue(conn, dataclasses.replace(cfg, test_recipient=""), contacts, sender)
    with pytest.raises(sd.SendingBlocked, match="TEST_RECIPIENT"):
        queue(conn, dataclasses.replace(cfg, test_recipient="not-an-email"), contacts, sender)


def test_test_sends_do_not_block_repeated_tests(conn, cfg, sender, contacts, fake_mailer):
    for _ in range(2):
        q = queue(conn, cfg, contacts, sender)
        assert len(q.created_ids) == 3
        sd.process_queue(conn, fake_mailer, cfg, q.created_ids)
    assert fake_mailer.calls == 6


# ------------------------------------------------------------------ live mode
def test_live_blocked_when_env_mode_is_test(conn, cfg, sender, contacts):
    with pytest.raises(sd.SendingBlocked, match="SEND_MODE"):
        queue(conn, cfg, contacts, sender, live=True, confirmed=True)
    assert conn.execute("SELECT COUNT(*) FROM email_log").fetchone()[0] == 0


def test_live_requires_explicit_confirmation(conn, live_cfg, sender, contacts):
    with pytest.raises(sd.SendingBlocked, match="confirmation"):
        queue(conn, live_cfg, contacts, sender, live=True, confirmed=False)


def test_live_send_goes_to_real_recipients(conn, live_cfg, sender, contacts, fake_mailer):
    q = queue(conn, live_cfg, contacts, sender, live=True, confirmed=True)
    sd.process_queue(conn, fake_mailer, live_cfg, q.created_ids)
    assert sorted(m["To"] for m in fake_mailer.sent) == sorted(c["email"] for c in contacts)
    assert all(not m["Subject"].startswith("[TEST]") for m in fake_mailer.sent)


def test_live_rows_cannot_be_sent_after_mode_is_switched_back_to_test(conn, live_cfg, cfg, sender, contacts, fake_mailer):
    q = queue(conn, live_cfg, contacts, sender, live=True, confirmed=True)
    with pytest.raises(sd.SendingBlocked):
        sd.process_queue(conn, fake_mailer, cfg, q.created_ids)       # cfg is test mode
    assert fake_mailer.calls == 0
    assert all(status_of(conn, i) == "pending" for i in q.created_ids)


# ----------------------------------------------------------- guard rails
def test_sender_details_required(conn, cfg, contacts):
    with pytest.raises(sd.SendingBlocked, match="sender"):
        queue(conn, cfg, contacts, {"sender_name": "", "sender_title": ""})


def test_smtp_must_be_configured(conn, cfg, sender, contacts):
    with pytest.raises(sd.SendingBlocked, match="SMTP is not configured"):
        queue(conn, dataclasses.replace(cfg, smtp_host=""), contacts, sender)


def test_batch_size_limit_and_empty_selection(conn, cfg, sender, contacts):
    with pytest.raises(sd.SendingBlocked, match="Batch too large"):
        queue(conn, dataclasses.replace(cfg, max_batch_size=2), contacts, sender)
    with pytest.raises(sd.SendingBlocked, match="No recipients"):
        queue(conn, cfg, [], sender)


def test_unrenderable_or_invalid_contacts_are_skipped_not_queued(conn, cfg, sender, contacts):
    bad = dict(contacts[0], email="broken")
    res = sd.queue_messages(conn, cfg, [bad, contacts[1]], "S {oops}", BODY, sender, "C")
    assert res.created_ids == [] and len(res.skipped) == 2


# ----------------------------------------------------------- duplicate sends
def test_duplicate_live_send_prevented(conn, live_cfg, sender, contacts, fake_mailer):
    q1 = queue(conn, live_cfg, contacts, sender, live=True, confirmed=True)
    sd.process_queue(conn, fake_mailer, live_cfg, q1.created_ids)
    q2 = queue(conn, live_cfg, contacts, sender, live=True, confirmed=True)
    assert q2.created_ids == [] and len(q2.skipped) == 3
    assert "Already sent" in q2.skipped[0][1]
    assert fake_mailer.calls == 3


def test_duplicate_prevented_while_pending_and_failed_but_new_campaign_allowed(conn, live_cfg, sender, contacts):
    queue(conn, live_cfg, contacts[:1], sender, live=True, confirmed=True)
    again = queue(conn, live_cfg, contacts[:1], sender, live=True, confirmed=True)
    assert again.created_ids == [] and "pending" in again.skipped[0][1]
    other = queue(conn, live_cfg, contacts[:1], sender, campaign="Follow-up", live=True, confirmed=True)
    assert len(other.created_ids) == 1


def test_database_itself_rejects_duplicate_live_rows(conn, live_cfg, sender, contacts):
    q = queue(conn, live_cfg, contacts[:1], sender, live=True, confirmed=True)
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO email_log (batch_id,campaign,recipient_email,delivered_to,subject,body,is_test,"
                     "dedupe_key,created_at,updated_at) SELECT batch_id,campaign,recipient_email,delivered_to,"
                     "subject,body,is_test,dedupe_key,created_at,updated_at FROM email_log WHERE id=?", (q.created_ids[0],))


def test_sent_message_is_never_resent_even_if_processed_again(conn, cfg, sender, contacts, fake_mailer):
    q = queue(conn, cfg, contacts[:1], sender)
    sd.process_queue(conn, fake_mailer, cfg, q.created_ids)
    again = sd.process_queue(conn, fake_mailer, cfg, q.created_ids)
    retry = sd.retry_failed(conn, fake_mailer, cfg, q.created_ids)
    assert fake_mailer.calls == 1
    assert again.accepted == 0 and retry.accepted == 0 and again.skipped


# ------------------------------------------------------------- tracking
def test_status_lifecycle_and_timestamps(conn, cfg, sender, contacts, fake_mailer):
    q = queue(conn, cfg, contacts[:1], sender)
    r = row(conn, q.created_ids[0])
    assert (r["status"], r["attempt_count"], r["last_attempt_at"], r["accepted_at"]) == ("pending", 0, None, None)
    assert r["subject"].startswith("[TEST] Proposal for FICTIONAL Demo College 1")
    sd.process_queue(conn, fake_mailer, cfg, q.created_ids)
    r = row(conn, q.created_ids[0])
    assert (r["status"], r["attempt_count"]) == ("sent", 1)
    assert r["accepted_at"] and r["last_attempt_at"] and r["last_error"] is None


def test_progress_callback_and_delay(conn, cfg, sender, contacts, fake_mailer):
    q = queue(conn, cfg, contacts, sender)
    seen, sleeps = [], []
    sd.process_queue(conn, fake_mailer, dataclasses.replace(cfg, send_delay_seconds=2), q.created_ids,
                     progress=lambda d, t, m: seen.append((d, t)), sleep=sleeps.append)
    assert seen == [(1, 3), (2, 3), (3, 3)] and sleeps == [2, 2]


def test_message_encoding_preserves_rupee_sign_and_dash(conn, cfg, sender, contacts, fake_mailer):
    q = sd.queue_messages(conn, cfg, contacts[:1], "Proposal — {college_name}", BODY, sender, "Enc")
    sd.process_queue(conn, fake_mailer, cfg, q.created_ids)
    msg = fake_mailer.sent[0]
    assert "₹1,500 + GST per year" in msg.get_content()
    assert "—" in msg["Subject"]
    assert msg.as_bytes().isascii()          # safely 7-bit on the wire


# ------------------------------------------------------------- failures
def test_partial_batch_failure_is_recorded_per_message(conn, cfg, sender, contacts):
    mailer = FakeMailer([None, TimeoutError("boom"), None])
    q = queue(conn, cfg, contacts, sender)
    res = sd.process_queue(conn, mailer, cfg, q.created_ids)
    assert (res.accepted, res.failed, res.aborted) == (2, 1, False)
    statuses = [status_of(conn, i) for i in q.created_ids]
    assert statuses == ["sent", "failed", "sent"]
    err = row(conn, q.created_ids[1])
    assert "timed out" in err["last_error"] and err["attempt_count"] == 1


def test_authentication_error_aborts_batch_and_leaves_rest_pending(conn, cfg, sender, contacts):
    auth = smtplib.SMTPAuthenticationError(535, b"5.7.8 bad credentials s3cret-pass-DO-NOT-LEAK")
    mailer = FakeMailer([auth])
    q = queue(conn, cfg, contacts, sender)
    res = sd.process_queue(conn, mailer, cfg, q.created_ids)
    assert res.aborted and "Authentication" in res.abort_reason
    assert res.failed == 1 and res.not_attempted == 2 and mailer.calls == 1
    assert [status_of(conn, i) for i in q.created_ids] == ["failed", "pending", "pending"]
    assert "authentication failed" in row(conn, q.created_ids[0])["last_error"].lower()


def test_credentials_never_stored_in_errors(conn, cfg, sender, contacts):
    mailer = FakeMailer([RuntimeError(f"login rejected for password {cfg.smtp_password}")])
    q = queue(conn, cfg, contacts[:1], sender)
    sd.process_queue(conn, mailer, cfg, q.created_ids)
    err = row(conn, q.created_ids[0])["last_error"]
    assert cfg.smtp_password not in err and "********" in err


def test_repeated_connection_failures_abort_batch(conn, cfg, sender):
    people = [ct.add_contact(conn, make_contact(n)) for n in range(1, 7)]
    mailer = FakeMailer([ConnectionRefusedError("refused")] * 6)
    q = queue(conn, cfg, ct.get_contacts(conn, people), sender)
    res = sd.process_queue(conn, mailer, cfg, q.created_ids)
    assert res.aborted and mailer.calls == 3 and res.not_attempted == 3


def test_recipient_refused_does_not_abort_batch(conn, cfg, sender, contacts):
    mailer = FakeMailer([smtplib.SMTPRecipientsRefused({"x@y.example": (550, b"no such user")}), None, None])
    q = queue(conn, cfg, contacts, sender)
    res = sd.process_queue(conn, mailer, cfg, q.created_ids)
    assert (res.failed, res.accepted, res.aborted) == (1, 2, False)


# ---------------------------------------------------------------- retries
def test_retry_only_resends_failed_messages(conn, cfg, sender, contacts):
    mailer = FakeMailer([None, TimeoutError(), None])
    q = queue(conn, cfg, contacts, sender)
    sd.process_queue(conn, mailer, cfg, q.created_ids)
    assert mailer.calls == 3
    res = sd.retry_failed(conn, mailer, cfg)
    assert mailer.calls == 4 and res.accepted == 1
    assert [status_of(conn, i) for i in q.created_ids] == ["sent"] * 3
    assert row(conn, q.created_ids[1])["attempt_count"] == 2
    assert row(conn, q.created_ids[0])["attempt_count"] == 1       # successful ones untouched


def test_retry_stops_after_max_attempts(conn, cfg, sender, contacts):
    cfg2 = dataclasses.replace(cfg, max_attempts=2)
    mailer = FakeMailer([TimeoutError()] * 5)
    q = queue(conn, cfg2, contacts[:1], sender)
    sd.process_queue(conn, mailer, cfg2, q.created_ids)
    sd.retry_failed(conn, mailer, cfg2)
    third = sd.retry_failed(conn, mailer, cfg2)
    assert mailer.calls == 2 and third.skipped and "attempts" in third.skipped[0][1]


def test_pending_rows_are_not_touched_by_retry(conn, cfg, sender, contacts, fake_mailer):
    q = queue(conn, cfg, contacts, sender)
    sd.retry_failed(conn, fake_mailer, cfg, q.created_ids)
    assert fake_mailer.calls == 0


# ---------------------------------------------------------- crash recovery
def test_stuck_sending_rows_become_failed_and_need_explicit_retry(conn, cfg, sender, contacts, fake_mailer):
    q = queue(conn, cfg, contacts[:1], sender)
    conn.execute("UPDATE email_log SET status='sending', last_attempt_at='2020-01-01T00:00:00Z' WHERE id=?",
                 (q.created_ids[0],))
    conn.commit()
    assert sd.recover_stuck_sending(conn, older_than_minutes=15) == 1
    r = row(conn, q.created_ids[0])
    assert r["status"] == "failed" and r["last_error"].startswith(sd.INTERRUPTED_PREFIX)
    skipped = sd.retry_failed(conn, fake_mailer, cfg)
    assert fake_mailer.calls == 0 and skipped.skipped
    ok = sd.retry_failed(conn, fake_mailer, cfg, include_interrupted=True)
    assert ok.accepted == 1


def test_recent_sending_rows_are_not_recovered(conn, cfg, sender, contacts):
    q = queue(conn, cfg, contacts[:1], sender)
    conn.execute("UPDATE email_log SET status='sending', last_attempt_at=? WHERE id=?",
                 (sd.utcnow(), q.created_ids[0]))
    conn.commit()
    assert sd.recover_stuck_sending(conn, older_than_minutes=15) == 0


# ------------------------------------------------------- real SMTP is blocked
def test_real_mailer_refuses_to_run_under_pytest(cfg):
    msg = mailer_mod.build_message(from_email="a@example.invalid", from_name="", to_email="b@example.invalid",
                                   subject="s", body="b")
    with pytest.raises(mailer_mod.RealSMTPBlockedError):
        mailer_mod.SMTPMailer(cfg).send(msg)
    with pytest.raises(mailer_mod.RealSMTPBlockedError):
        mailer_mod.SMTPMailer(cfg).check_connection()


def test_header_injection_rejected():
    with pytest.raises(ValueError):
        mailer_mod.build_message(from_email="a@example.invalid", from_name="", subject="s",
                                 to_email="b@example.invalid\nBcc: c@example.invalid", body="b")


def test_history_search_and_filters(conn, cfg, sender, contacts, fake_mailer):
    q = queue(conn, cfg, contacts, sender)
    sd.process_queue(conn, FakeMailer([None, TimeoutError(), None]), cfg, q.created_ids)
    assert len(sd.history_dataframe(conn)) == 3
    assert len(sd.history_dataframe(conn, status="failed")) == 1
    assert len(sd.history_dataframe(conn, text="college 2")) == 1
    assert len(sd.history_dataframe(conn, mode="live")) == 0 and len(sd.history_dataframe(conn, mode="test")) == 3


# ----------------------------------------------------------- email attachments
def test_build_message_with_attachment(tmp_path):
    attach_file = tmp_path / "proposal.pdf"
    attach_file.write_bytes(b"%PDF-1.4 dummy pdf content")

    msg = mailer_mod.build_message(
        from_email="sender@example.invalid",
        from_name="Shortfundly",
        to_email="college@example.invalid",
        subject="Proposal",
        body="Please see attached proposal.",
        attachments=[attach_file],
    )

    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    att = attachments[0]
    assert att.get_filename() == "proposal.pdf"
    assert att.get_content() == b"%PDF-1.4 dummy pdf content"


def test_build_message_missing_attachment_raises(tmp_path):
    missing_file = tmp_path / "does_not_exist.pdf"
    with pytest.raises(FileNotFoundError, match="Attachment not found"):
        mailer_mod.build_message(
            from_email="sender@example.invalid",
            from_name="Shortfundly",
            to_email="college@example.invalid",
            subject="Proposal",
            body="Hello",
            attachments=[missing_file],
        )


def test_process_queue_with_attachments(conn, cfg, sender, contacts, fake_mailer, tmp_path):
    doc = tmp_path / "brochure.txt"
    doc.write_text("Campus partnership brochure details", encoding="utf-8")

    q = queue(conn, cfg, contacts[:1], sender)
    res = sd.process_queue(conn, fake_mailer, cfg, q.created_ids, attachments=[doc])

    assert res.accepted == 1
    assert len(fake_mailer.sent) == 1
    sent_msg = fake_mailer.sent[0]
    attachments = list(sent_msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "brochure.txt"
    assert "Campus partnership brochure details" in attachments[0].get_content()
