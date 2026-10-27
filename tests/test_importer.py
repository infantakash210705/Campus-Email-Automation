import io
from pathlib import Path

import pandas as pd
import pytest

from shortfundly import importer
from shortfundly.config import BASE_DIR

SAMPLE = BASE_DIR / "data" / "sample_contacts_fictional.csv"


def df_of(rows, cols=("state", "city", "college_name", "email")):
    return pd.DataFrame(rows, columns=list(cols))


def count(conn, table="contacts"):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_missing_required_columns_blocks_import(conn):
    preview = importer.build_preview(conn, pd.DataFrame({"college_name": ["X"], "email": ["x@y.example"]}))
    assert set(preview.missing_columns) == {"state", "city"}
    assert preview.rows == []
    with pytest.raises(importer.ImportFormatError):
        importer.commit_import(conn, preview)


def test_header_aliases_are_mapped(conn):
    df = pd.DataFrame([{"State Name": "S", "City/Town": "C", "Institution": "Demo", "Official Email Address": "a@demo.example",
                        "Official Website": "demo.example", "Source Page URL": "https://demo.example/c", "Junk": "x"}])
    preview = importer.build_preview(conn, df)
    assert preview.missing_columns == []
    assert preview.ignored_columns == ["Junk"]
    assert preview.rows[0].record["college_name"] == "Demo"


def test_preview_does_not_write_to_database(conn):
    df = importer.read_table(SAMPLE.read_bytes(), "sample.csv")
    importer.build_preview(conn, df, "sample.csv")
    assert count(conn) == 0 and count(conn, "import_rejections") == 0


def test_sample_file_classification(conn):
    df = importer.read_table(SAMPLE.read_bytes(), "sample.csv")
    preview = importer.build_preview(conn, df, "sample.csv")
    assert len(preview.ok_rows) == 6
    assert preview.invalid_count == 2
    assert preview.duplicate_count == 1
    assert preview.duplicate_college_count == 1


def test_invalid_rows_and_in_file_duplicates(conn):
    df = df_of([
        ["S", "C", "A", "a@demo.example"],
        ["S", "C", "B", "A@Demo.Example"],        # same email, different case
        ["S", "C", "C", "bad-email"],
        ["S", "", "D", "d@demo.example"],         # missing city
        ["", "", "", ""],                         # blank row ignored
    ])
    preview = importer.build_preview(conn, df)
    assert [r.status for r in preview.rows] == ["ok", "duplicate", "invalid", "invalid"]
    assert preview.rows[1].messages[0].startswith("Same email as row 2")


def test_duplicates_against_database(conn):
    importer.commit_import(conn, importer.build_preview(conn, df_of([["S", "C", "A", "a@demo.example"]])))
    preview = importer.build_preview(conn, df_of([["S", "C", "A2", "a@demo.example"],
                                                  ["S", "C", "A", "other@demo.example"]]))
    assert preview.rows[0].status == "duplicate"
    assert preview.rows[1].status == "ok" and preview.rows[1].duplicate_college


def test_commit_saves_only_valid_rows_and_logs_rejections(conn):
    df = importer.read_table(SAMPLE.read_bytes(), "sample.csv")
    result = importer.commit_import(conn, importer.build_preview(conn, df, "sample.csv"))
    assert (result.inserted, result.invalid, result.duplicates) == (6, 2, 1)
    assert count(conn) == 6
    assert count(conn, "import_rejections") == 3
    # imports are never auto-verified
    assert conn.execute("SELECT COUNT(*) FROM contacts WHERE verification_status != 'unverified'").fetchone()[0] == 0


def test_skip_existing_colleges_option(conn):
    df = importer.read_table(SAMPLE.read_bytes(), "sample.csv")
    result = importer.commit_import(conn, importer.build_preview(conn, df, "s.csv"), skip_existing_colleges=True)
    assert result.inserted == 5 and result.skipped_colleges == 1


def test_reimport_same_file_adds_nothing(conn):
    df = importer.read_table(SAMPLE.read_bytes(), "sample.csv")
    importer.commit_import(conn, importer.build_preview(conn, df, "s.csv"))
    result = importer.commit_import(conn, importer.build_preview(conn, df, "s.csv"))
    assert result.inserted == 0 and count(conn) == 6


def test_read_xlsx_roundtrip():
    buf = io.BytesIO()
    pd.DataFrame({"state": ["S"], "city": ["C"], "college_name": ["Demo"], "email": ["a@demo.example"]}).to_excel(buf, index=False)
    df = importer.read_table(buf.getvalue(), "contacts.xlsx")
    assert df.loc[0, "college_name"] == "Demo"


def test_read_latin1_csv():
    data = "state,city,college_name,email\nS,C,Café College,a@demo.example\n".encode("cp1252")
    assert importer.read_table(data, "x.csv").loc[0, "college_name"] == "Café College"


@pytest.mark.parametrize("name,data", [("x.txt", b"a"), ("x.xls", b"a"), ("x.xlsx", b"not a zip"), ("x.csv", b"")])
def test_unreadable_files_raise_friendly_error(name, data):
    with pytest.raises(importer.ImportFormatError):
        importer.read_table(data, name)
