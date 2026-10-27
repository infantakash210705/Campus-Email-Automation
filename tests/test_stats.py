import pandas as pd

from shortfundly import contacts as ct
from shortfundly import importer
from shortfundly import sending as sd
from shortfundly.config import BASE_DIR
from shortfundly.stats import get_stats
from tests.conftest import FakeMailer, make_contact


def test_empty_database_stats(conn):
    s = get_stats(conn)
    assert s["contacts_total"] == 0 and s["emails_live"]["total"] == 0 and s["emails_test"]["total"] == 0


def test_stats_reflect_actual_records(conn, live_cfg, sender):
    sample = (BASE_DIR / "data" / "sample_contacts_fictional.csv").read_bytes()
    importer.commit_import(conn, importer.build_preview(conn, importer.read_table(sample, "s.csv"), "s.csv"))
    people = ct.get_contacts(conn, [r[0] for r in conn.execute("SELECT id FROM contacts ORDER BY id")])
    ct.verify_contact(conn, people[0]["id"], "Reviewer", "", True)

    q = sd.queue_messages(conn, live_cfg, people[:4], "S {college_name}", "B {sender_name} {sender_title}", sender,
                          "C", live=True, live_confirmed=True)
    sd.process_queue(conn, FakeMailer([None, TimeoutError(), None]), live_cfg, q.created_ids[:3])

    s = get_stats(conn)
    assert s["contacts_total"] == 6 and s["contacts_verified"] == 1 and s["contacts_unverified"] == 5
    assert s["rejected_invalid_rows"] == 2 and s["rejected_duplicate_rows"] == 1
    assert s["duplicate_college_groups"] == 1 and s["duplicate_college_contacts"] == 2
    assert s["emails_live"] == {"pending": 1, "sending": 0, "sent": 2, "failed": 1, "total": 4}
    assert s["emails_test"]["total"] == 0
    assert s["contacts_by_state"] == {"Sampleland": 6}


def test_invalid_contacts_in_db_are_counted(conn):
    ct.add_contact(conn, make_contact(1))
    conn.execute("UPDATE contacts SET email='broken' WHERE id=1")    # simulate manual DB damage
    conn.commit()
    assert get_stats(conn)["contacts_invalid"] == 1
