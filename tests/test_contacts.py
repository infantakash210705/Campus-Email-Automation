import pandas as pd
import pytest

from shortfundly import contacts as ct
from tests.conftest import make_contact


def test_add_get_update_delete(conn):
    cid = ct.add_contact(conn, make_contact(1))
    assert ct.get_contact(conn, cid)["verification_status"] == "unverified"
    ct.update_contact(conn, cid, {"department": "Film Club"})
    assert ct.get_contact(conn, cid)["department"] == "Film Club"
    assert ct.delete_contacts(conn, [cid]) == 1
    assert ct.get_contact(conn, cid) is None


def test_add_rejects_invalid_and_duplicate(conn):
    ct.add_contact(conn, make_contact(1))
    with pytest.raises(ct.ContactError) as e:
        ct.add_contact(conn, make_contact(1, email="CONTACT1@demo-college-1.example"))
    assert "already exists" in str(e.value)
    with pytest.raises(ct.ContactError):
        ct.add_contact(conn, make_contact(2, email="broken"))
    with pytest.raises(ct.ContactError):
        ct.add_contact(conn, make_contact(2, city=""))


def test_update_cannot_steal_another_contacts_email(conn):
    ct.add_contact(conn, make_contact(1))
    b = ct.add_contact(conn, make_contact(2))
    with pytest.raises(ct.ContactError):
        ct.update_contact(conn, b, {"email": "contact1@demo-college-1.example"})


def test_search_and_filters(conn, contacts):
    ct.add_contact(conn, make_contact(9, state="Otherland", city="Elsewhere", college_name="Quirky 100%_Name"))
    assert len(ct.search_contacts(conn)) == 4
    assert len(ct.search_contacts(conn, state="Otherland")) == 1
    assert len(ct.search_contacts(conn, text="demo college 2")) == 1
    assert len(ct.search_contacts(conn, text="100%_")) == 1       # wildcards are escaped
    assert len(ct.search_contacts(conn, text="%")) == 1
    assert ct.search_contacts(conn, ids=[]).empty
    assert ct.list_states(conn) == ["Otherland", "Sampleland"]


def test_duplicate_colleges_detected(conn):
    ct.add_contact(conn, make_contact(1))
    ct.add_contact(conn, make_contact(1, email="second@demo-college-1.example", department="Film Club"))
    dups = ct.find_duplicate_colleges(conn)
    assert len(dups) == 2


def test_verification_requires_evidence_and_confirmation(conn):
    cid = ct.add_contact(conn, make_contact(1))
    with pytest.raises(ct.ContactError):
        ct.verify_contact(conn, cid, "Reviewer", "", confirmed_on_source=False)
    with pytest.raises(ct.ContactError):
        ct.verify_contact(conn, cid, "", "", confirmed_on_source=True)
    ct.verify_contact(conn, cid, "Reviewer", "seen on contact page", confirmed_on_source=True)
    row = ct.get_contact(conn, cid)
    assert row["verification_status"] == "verified" and row["verified_by"] == "Reviewer" and row["verified_at"]


def test_verification_blocked_for_free_mailbox_and_foreign_source(conn):
    free = ct.add_contact(conn, make_contact(1, email="principal@gmail.com"))
    foreign = ct.add_contact(conn, make_contact(2, source_url="https://some-directory.example/list"))
    nosource = ct.add_contact(conn, make_contact(3, source_url=""))
    for cid in (free, foreign, nosource):
        with pytest.raises(ct.ContactError):
            ct.verify_contact(conn, cid, "Reviewer", "", confirmed_on_source=True)
        assert ct.get_contact(conn, cid)["verification_status"] == "unverified"


def test_editing_evidence_resets_verification(conn):
    cid = ct.add_contact(conn, make_contact(1))
    ct.verify_contact(conn, cid, "Reviewer", "", True)
    ct.update_contact(conn, cid, {"department": "Other dept"})       # not evidence
    assert ct.get_contact(conn, cid)["verification_status"] == "verified"
    ct.update_contact(conn, cid, {"email": "new@demo-college-1.example"})
    assert ct.get_contact(conn, cid)["verification_status"] == "unverified"


def test_export_csv_and_xlsx(conn):
    ct.add_contact(conn, make_contact(1, college_name="=HYPERLINK(\"http://evil.example\")"))
    df = ct.search_contacts(conn)
    csv_text = ct.export_csv(df).decode("utf-8-sig")
    assert "contact1@demo-college-1.example" in csv_text
    assert "'=HYPERLINK" in csv_text                                  # formula injection neutralised
    assert ct.export_xlsx(df)[:2] == b"PK"
    import io
    back = pd.read_excel(io.BytesIO(ct.export_xlsx(df)))
    assert list(back.columns)[:3] == ["state", "city", "college_name"]
