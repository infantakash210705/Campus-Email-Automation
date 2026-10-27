"""Shortfundly Campus Email Automation - Streamlit dashboard.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from shortfundly import contacts as ct
from shortfundly import database as db
from shortfundly import importer, sending
from shortfundly import stats as stats_mod
from shortfundly import template_service as ts
from shortfundly.config import Config, load_config
from shortfundly.mailer import SMTPMailer

st.set_page_config(page_title="Shortfundly Campus Email Automation", page_icon="🔴", layout="wide")

VERIFICATION_OPTIONS = ["All", "verified", "unverified"]

# --------------------------------------------------------------- UI / styling
import base64 as _b64


def _app_dir() -> str:
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


def _logo_html(size_px: int = 54) -> str:
    """Return an <img> tag with the embedded logo.jpg, or an SVG fallback."""
    path = os.path.join(_app_dir(), "logo.jpg")
    try:
        with open(path, "rb") as fh:
            data = _b64.b64encode(fh.read()).decode("ascii")
        return (f'<img class="qw-logo" src="data:image/jpeg;base64,{data}" '
                f'style="width:{size_px}px;height:{size_px}px;border-radius:50%;" alt="Shortfundly"/>')
    except Exception:
        return (
            '<svg class="qw-logo" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">'
            '<circle cx="50" cy="50" r="40" fill="#E50914"/>'
            '<path d="M40 32 L70 50 L40 68 Z" fill="#ffffff"/></svg>'
        )

_GLOBAL_CSS = """
<style>
/* ---- entrance animations ---- */
@keyframes qwFadeUp { from {opacity:0; transform:translateY(14px);} to {opacity:1; transform:translateY(0);} }
@keyframes qwGradient { 0%{background-position:0% 50%;} 50%{background-position:100% 50%;} 100%{background-position:0% 50%;} }
@keyframes qwGlow { 0%,100%{box-shadow:0 0 12px rgba(229,9,20,.35);} 50%{box-shadow:0 0 26px rgba(229,9,20,.7);} }

/* ---- animated hero banner ---- */
.qw-hero {
  border-radius:16px; padding:26px 30px; margin-bottom:18px; color:#fff;
  background:linear-gradient(120deg,#E50914,#8b1a1a,#1A1D24,#E50914);
  background-size:300% 300%; animation:qwGradient 12s ease infinite, qwFadeUp .6s ease both;
  box-shadow:0 8px 30px rgba(0,0,0,.45);
}
.qw-hero h1 { margin:0; font-size:2.1rem; font-weight:800; letter-spacing:.3px; }
.qw-hero p  { margin:.4rem 0 0; opacity:.92; font-size:1.02rem; }

/* ---- login card ---- */
.qw-login-wrap { animation:qwFadeUp .7s ease both; }
.qw-badge {
  display:inline-block; padding:4px 12px; border-radius:999px; font-size:.78rem; font-weight:700;
  background:rgba(229,9,20,.15); color:#ff5a63; border:1px solid rgba(229,9,20,.4); margin-bottom:8px;
}

/* ---- metric cards: fade in + hover lift ---- */
div[data-testid="stMetric"] {
  background:#1A1D24; border:1px solid #262a33; border-radius:12px; padding:14px 16px;
  animation:qwFadeUp .5s ease both; transition:transform .15s ease, box-shadow .15s ease;
}
div[data-testid="stMetric"]:hover { transform:translateY(-3px); box-shadow:0 6px 18px rgba(0,0,0,.4); }

/* ---- buttons: hover glow ---- */
.stButton > button {
  border-radius:10px; font-weight:600; transition:transform .12s ease, box-shadow .2s ease, background .2s ease;
}
.stButton > button:hover { transform:translateY(-1px); animation:qwGlow 1.6s ease infinite; }

/* ---- tabs: smoother active underline ---- */
.stTabs [data-baseweb="tab-list"] { gap:6px; }
.stTabs [aria-selected="true"] { color:#E50914 !important; }

/* ---- bordered containers fade in ---- */
div[data-testid="stVerticalBlockBorderWrapper"] { animation:qwFadeUp .5s ease both; }

.qw-hero-row { display:flex; align-items:center; gap:16px; }
.qw-logo { flex:0 0 auto; width:54px; height:54px; filter:drop-shadow(0 2px 6px rgba(0,0,0,.4)); }
.qw-hero-text h1 { margin:0; font-size:2.1rem; font-weight:800; letter-spacing:.3px; }
.qw-hero-text p  { margin:.35rem 0 0; opacity:.92; font-size:1.02rem; }
</style>
"""


def inject_css() -> None:
    """Inject global animated styling (purely cosmetic)."""
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


def hero(title: str, subtitle: str) -> None:
    """Render an animated gradient hero banner with the Shortfundly logo."""
    logo = _logo_html()
    st.markdown(
        f'<div class="qw-hero"><div class="qw-hero-row">{logo}'
        f'<div class="qw-hero-text"><h1>{title}</h1><p>{subtitle}</p></div></div></div>',
        unsafe_allow_html=True,
    )




# ------------------------------------------------------------------ helpers
def flash(kind: str, message: str) -> None:
    """Remember a message across the next rerun."""
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    kind, message = st.session_state.pop("_flash", (None, None))
    if kind:
        getattr(st, kind)(message)


def get_sender(conn, cfg: Config) -> dict:
    return {
        "sender_name": db.get_setting(conn, "sender_name", cfg.sender_name) or "",
        "sender_title": db.get_setting(conn, "sender_title", cfg.sender_title) or "",
    }


def contact_label(c) -> str:
    return f"#{c['id']} {c['college_name']} <{c['email']}>"


def csv_download(df: pd.DataFrame, label: str, filename: str, key: str) -> None:
    st.download_button(label, df.to_csv(index=False).encode("utf-8-sig"), filename, "text/csv", key=key)


# ------------------------------------------------------------------ sidebar
def render_sidebar(conn, cfg: Config) -> dict:
    with st.sidebar:
        if st.button("Log out", use_container_width=True):
            st.session_state.pop("_authenticated", None)
            st.rerun()
        st.divider()
        st.header("Sending mode")
        if cfg.send_mode == "live":
            st.warning("LIVE mode is enabled in .env. Real recipients are allowed after explicit confirmation.")
        else:
            st.success(
                "TEST mode (safe default). Every email is redirected to "
                f"{cfg.test_recipient or 'TEST_RECIPIENT (not set yet!)'}."
            )
        st.caption("SMTP configured: " + ("yes" if cfg.smtp_configured else "no - edit your .env file"))
        if st.button("Test SMTP login (sends nothing)", disabled=not cfg.smtp_configured):
            try:
                SMTPMailer(cfg).check_connection()
                st.success("Connected and authenticated.")
            except Exception as exc:  # noqa: BLE001
                st.error(cfg.redact(f"{type(exc).__name__}: {exc}"))

        st.header("Sender details")
        st.caption("Used for {sender_name} and {sender_title}. Never invented by the app.")
        current = get_sender(conn, cfg)
        with st.form("sender_form"):
            name = st.text_input("Sender name", value=current["sender_name"])
            title = st.text_input("Sender title", value=current["sender_title"])
            if st.form_submit_button("Save sender details"):
                db.set_setting(conn, "sender_name", name.strip())
                db.set_setting(conn, "sender_title", title.strip())
                st.success("Saved.")
    return get_sender(conn, cfg)


# ---------------------------------------------------------------- dashboard
def render_dashboard(conn) -> None:
    s = stats_mod.get_stats(conn)
    with st.container(border=True):
        st.subheader("📇 Contacts")
        c = st.columns(4)
        c[0].metric("Total contacts", s["contacts_total"])
        c[1].metric("Verified", s["contacts_verified"])
        c[2].metric("Unverified", s["contacts_unverified"])
        c[3].metric("Invalid records in database", s["contacts_invalid"])
        total = s["contacts_total"] or 0
        if total:
            pct = s["contacts_verified"] / total
            st.progress(pct, text=f"Verified {s['contacts_verified']} of {total} contacts ({pct*100:.0f}%)")
        c = st.columns(4)
        c[0].metric("Colleges with several contacts", s["duplicate_college_groups"])
        c[1].metric("Import rows rejected: invalid", s["rejected_invalid_rows"])
        c[2].metric("Import rows rejected: duplicate", s["rejected_duplicate_rows"])
        c[3].metric("Contacts in multi-contact colleges", s["duplicate_college_contacts"])

    with st.container(border=True):
        st.subheader("📧 Emails (live)")
        e = s["emails_live"]
        c = st.columns(5)
        c[0].metric("Pending", e["pending"])
        c[1].metric("Sending", e["sending"])
        c[2].metric("Accepted by SMTP", e["sent"])
        c[3].metric("Failed", e["failed"])
        c[4].metric("Total", e["total"])
        t = s["emails_test"]
        st.caption(
            f"Test-mode messages (redirected to your internal address): {t['total']} total, "
            f"{t['sent']} accepted, {t['failed']} failed, {t['pending']} pending."
        )
        st.info(sending.SMTP_ACCEPTANCE_NOTE)
    if s["contacts_by_state"]:
        with st.container(border=True):
            st.subheader("📊 Contacts by state")
            st.bar_chart(pd.Series(s["contacts_by_state"], name="contacts"))


# ----------------------------------------------------------------- contacts
def filter_row(conn, key: str):
    c1, c2, c3, c4 = st.columns(4)
    text = c1.text_input("Search", key=f"{key}_text")
    state = c2.selectbox("State", ["All"] + ct.list_states(conn), key=f"{key}_state")
    city = c3.selectbox("City", ["All"] + ct.list_cities(conn, "" if state == "All" else state), key=f"{key}_city")
    ver = c4.selectbox("Verification", VERIFICATION_OPTIONS, key=f"{key}_ver")
    return text, "" if state == "All" else state, "" if city == "All" else city, "" if ver == "All" else ver


def render_browse(conn) -> None:
    text, state, city, ver = filter_row(conn, "browse")
    df = ct.search_contacts(conn, text, state, city, ver)
    st.caption(f"{len(df)} contact(s)")
    shown = ["id", "state", "city", "college_name", "contact_person", "department", "email",
             "website", "source_url", "verification_status"]
    st.dataframe(df[shown], hide_index=True)
    if df.empty:
        return
    rows = {int(r["id"]): r for r in df.to_dict("records")}

    st.markdown("#### Edit a contact")
    cid = st.selectbox("Contact", list(rows), index=None, format_func=lambda i: contact_label(rows[i]),
                       placeholder="Choose a contact to edit", key="edit_pick")
    if cid is not None:
        cur = rows[cid]
        with st.form(f"edit_form_{cid}"):
            cols = st.columns(2)
            vals = {
                "college_name": cols[0].text_input("College name", cur["college_name"]),
                "email": cols[1].text_input("Official email", cur["email"]),
                "state": cols[0].text_input("State", cur["state"]),
                "city": cols[1].text_input("City", cur["city"]),
                "website": cols[0].text_input("Official website", cur["website"]),
                "source_url": cols[1].text_input("Source page URL", cur["source_url"]),
                "contact_person": cols[0].text_input("Contact person", cur["contact_person"]),
                "department": cols[1].text_input("Department", cur["department"]),
            }
            if st.form_submit_button("Save changes"):
                try:
                    ct.update_contact(conn, cid, vals)
                    flash("success", "Contact updated (verification resets if email/website/source changed).")
                    st.rerun()
                except ct.ContactError as exc:
                    for err in exc.errors:
                        st.error(err)

    st.markdown("#### Delete contacts")
    doomed = st.multiselect("Contacts to delete", list(rows), format_func=lambda i: contact_label(rows[i]))
    confirm = st.checkbox("I understand this permanently deletes the selected contacts", key="del_confirm")
    if st.button("Delete selected", disabled=not (doomed and confirm)):
        n = ct.delete_contacts(conn, doomed)
        flash("success", f"Deleted {n} contact(s). Email history is kept.")
        st.rerun()


def render_import(conn) -> None:
    st.write("Upload a CSV or Excel file. Nothing is saved until you review the preview and confirm.")
    st.caption("Required columns: state, city, college_name, email. Optional: website, contact_person, "
               "department, source_url. Common header variants (e.g. 'Official Email') are recognised. "
               "Excel files: the first sheet is read.")
    upload = st.file_uploader("Contacts file", type=["csv", "xlsx", "xlsm"], key="import_file")
    if st.button("Analyse file", disabled=upload is None):
        try:
            df = importer.read_table(upload.getvalue(), upload.name)
            st.session_state["import_preview"] = importer.build_preview(conn, df, upload.name)
        except importer.ImportFormatError as exc:
            st.session_state.pop("import_preview", None)
            st.error(str(exc))

    preview = st.session_state.get("import_preview")
    if not preview:
        return
    st.markdown(f"#### Preview: {preview.filename}")
    if preview.missing_columns:
        st.error("Missing required column(s): " + ", ".join(preview.missing_columns))
        return
    if preview.ignored_columns:
        st.caption("Ignored columns: " + ", ".join(preview.ignored_columns))
    c = st.columns(4)
    c[0].metric("Ready to import", len(preview.ok_rows))
    c[1].metric("Invalid", preview.invalid_count)
    c[2].metric("Duplicate emails", preview.duplicate_count)
    c[3].metric("College already present", preview.duplicate_college_count)
    st.dataframe(importer.preview_dataframe(preview), hide_index=True)
    skip = st.checkbox("Skip extra contacts for colleges that already exist", value=False)
    st.caption("Imported contacts are always saved as UNVERIFIED. Verify them in the Verification tab.")
    if st.button("Save valid rows to database", disabled=not preview.ok_rows, type="primary"):
        result = importer.commit_import(conn, preview, skip_existing_colleges=skip)
        st.session_state.pop("import_preview", None)
        flash("success", f"Imported {result.inserted} contact(s). Rejected: {result.invalid} invalid, "
                         f"{result.duplicates} duplicate.")
        st.rerun()


def render_add(conn) -> None:
    with st.form("add_form", clear_on_submit=False):
        cols = st.columns(2)
        vals = {
            "college_name": cols[0].text_input("College name *"),
            "email": cols[1].text_input("Official email *"),
            "state": cols[0].text_input("State *", value="Karnataka"),
            "city": cols[1].text_input("City *"),
            "website": cols[0].text_input("Official website"),
            "source_url": cols[1].text_input("Source page URL"),
            "contact_person": cols[0].text_input("Contact person"),
            "department": cols[1].text_input("Department"),
        }
        if st.form_submit_button("Add contact"):
            try:
                ct.add_contact(conn, vals)
                flash("success", "Contact added as UNVERIFIED.")
                st.rerun()
            except ct.ContactError as exc:
                for err in exc.errors:
                    st.error(err)


def render_verification(conn) -> None:
    st.markdown(
        "Mark a record **verified** only after you have opened the college's **official website** and seen "
        "the email address on the page you recorded as the source. The app also checks that the email and "
        "source page belong to the official website's domain. It cannot browse for you, and it never "
        "invents or auto-verifies contacts. Use the Karnataka filter below to work through Karnataka colleges."
    )
    c1, c2 = st.columns(2)
    state = c1.selectbox("State", ["All"] + ct.list_states(conn), key="ver_state")
    status = c2.selectbox("Status", ["unverified", "verified"], key="ver_status")
    df = ct.search_contacts(conn, state="" if state == "All" else state, verification=status)
    if df.empty:
        st.info("No contacts match.")
        return
    rows = {int(r["id"]): r for r in df.to_dict("records")}
    cid = st.selectbox("Contact", list(rows), format_func=lambda i: contact_label(rows[i]), key="ver_pick")
    cur = rows[cid]
    st.write({k: cur[k] for k in ("college_name", "city", "website", "email", "source_url", "verification_status")})
    if cur["website"]:
        st.markdown(f"[Open official website]({cur['website']})")
    if cur["source_url"]:
        st.markdown(f"[Open source page]({cur['source_url']})")
    if cur["verification_status"] == "verified":
        st.success(f"Verified by {cur['verified_by']} on {cur['verified_at']}. Note: {cur['verification_note'] or '-'}")
        if st.button("Mark as unverified"):
            ct.unverify_contact(conn, cid)
            st.rerun()
        return
    problems = ct.verification_problems(cur)
    for p in problems:
        st.warning(p)
    who = st.text_input("Verified by (your name)", key="ver_by")
    note = st.text_input("Note (optional)", key="ver_note")
    seen = st.checkbox("I opened the source page on the official website and saw this email address there",
                       key="ver_seen")
    if st.button("Mark as verified", disabled=bool(problems)):
        try:
            ct.verify_contact(conn, cid, who, note, seen)
            flash("success", "Marked as verified.")
            st.rerun()
        except ct.ContactError as exc:
            for err in exc.errors:
                st.error(err)


def render_export(conn) -> None:
    text, state, city, ver = filter_row(conn, "export")
    df = ct.search_contacts(conn, text, state, city, ver)
    st.caption(f"{len(df)} contact(s) will be exported")
    c1, c2 = st.columns(2)
    c1.download_button("Download CSV", ct.export_csv(df), "shortfundly_contacts.csv", "text/csv", key="dl_csv")
    c2.download_button("Download Excel", ct.export_xlsx(df), "shortfundly_contacts.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_xlsx")
    dups = ct.find_duplicate_colleges(conn)
    if not dups.empty:
        st.markdown("#### Colleges with more than one contact")
        st.dataframe(dups.drop(columns=["college_key"]), hide_index=True)


# ---------------------------------------------------------------- templates
def _tpl_load_selected() -> None:
    row = ts.load_template(st.session_state["_conn"], st.session_state["tpl_select"])
    if row:
        st.session_state.update(tpl_subject=row["subject"], tpl_body=row["body"], tpl_name=row["name"])
        flash("info", f"Loaded template '{row['name']}'.")


def _tpl_on_upload() -> None:
    f = st.session_state.get("tpl_upload")
    if f is None:
        return
    try:
        st.session_state["tpl_body"] = ts.decode_upload(f.getvalue())
        flash("success", "Body loaded from the file. Review it, then click Save template.")
    except ValueError as exc:
        flash("error", str(exc))


def _tpl_restore_default() -> None:
    conn = st.session_state["_conn"]
    ts.restore_default_template(conn)
    row = ts.load_template(conn, ts.DEFAULT_TEMPLATE_NAME)
    st.session_state.update(tpl_subject=row["subject"], tpl_body=row["body"], tpl_name=row["name"])
    flash("success", "Default proposal restored.")


def render_templates(conn, sender: dict) -> None:
    if "tpl_subject" not in st.session_state:
        row = ts.load_template(conn, ts.DEFAULT_TEMPLATE_NAME)
        st.session_state.update(tpl_subject=row["subject"], tpl_body=row["body"], tpl_name=row["name"])
    st.session_state["_conn"] = conn

    c1, c2 = st.columns([3, 1])
    c1.selectbox("Saved templates", ts.list_templates(conn), key="tpl_select")
    c2.write("")
    c2.button("Load", on_click=_tpl_load_selected)
    st.file_uploader("Upload a .txt file to replace the body", type=["txt"], key="tpl_upload", on_change=_tpl_on_upload)

    st.text_input("Subject", key="tpl_subject")
    st.text_area("Body", key="tpl_body", height=420)
    st.caption("Placeholders: " + ", ".join("{" + p + "}" for p in ts.ALLOWED_PLACEHOLDERS))
    st.text_input("Template name", key="tpl_name")
    b1, b2, b3 = st.columns(3)
    if b1.button("Save template"):
        try:
            ts.save_template(conn, st.session_state["tpl_name"], st.session_state["tpl_subject"], st.session_state["tpl_body"])
            flash("success", f"Saved '{st.session_state['tpl_name']}'.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    b2.button("Restore default proposal", on_click=_tpl_restore_default)
    if b3.button("Delete selected template"):
        try:
            ts.delete_template(conn, st.session_state["tpl_select"])
            flash("success", "Template deleted.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    st.markdown("#### Preview")
    df = ct.search_contacts(conn)
    options = {int(r["id"]): r for r in df.to_dict("records")}
    pick = st.selectbox("Preview with contact", [0] + list(options),
                        format_func=lambda i: "Sample (fictional) contact" if i == 0 else contact_label(options[i]))
    contact = ({"college_name": "Sample College of Arts", "contact_person": "Dr. Sample Name", "department": "Media Studies",
                "city": "Sample City", "state": "Sample State"} if pick == 0 else options[pick])
    rendered = ts.render_email(st.session_state["tpl_subject"], st.session_state["tpl_body"], contact, sender)
    for p in rendered.problems:
        st.warning(p + (" - set sender details in the sidebar." if "sender_" in p else ""))
    st.text_input("Rendered subject", rendered.subject, disabled=True, key=f"prev_subj_{pick}_{hash(rendered.subject)}")
    st.text_area("Rendered body", rendered.body, height=360, disabled=True, key=f"prev_body_{pick}_{hash(rendered.body)}")


# --------------------------------------------------------------------- send
def render_send(conn, cfg: Config, sender: dict) -> None:
    names = ts.list_templates(conn)
    c1, c2 = st.columns(2)
    tpl_name = c1.selectbox("Template (saved in the Templates tab)", names, key="send_tpl")
    campaign = c2.text_input("Campaign label", value=db.get_setting(conn, "campaign", "Campus Proposal"), key="send_campaign")
    st.caption("Duplicate protection: each recipient can receive a live email only once per campaign label. "
               "Use a new label for an intentional follow-up.")
    tpl = ts.load_template(conn, tpl_name)

    st.markdown("#### 1. Choose recipients")
    text, state, city, ver = filter_row(conn, "send")
    only_verified = st.checkbox("Only verified contacts", value=False)
    df = ct.search_contacts(conn, text, state, city, "verified" if only_verified else ver)
    if df.empty:
        st.info("No contacts match the filters.")
        return
    select_all = st.checkbox("Select all filtered contacts", value=False)
    already = sending.emails_in_campaign(conn, campaign)
    view = df[["id", "college_name", "city", "state", "contact_person", "email", "verification_status"]].copy()
    view.insert(0, "Select", select_all)
    view["already_in_campaign"] = view["email"].isin(already)
    sig = hash((text, state, city, ver, only_verified, select_all, campaign))
    edited = st.data_editor(
        view, hide_index=True, key=f"send_editor_{sig}",
        disabled=[c for c in view.columns if c != "Select"],
        column_config={"Select": st.column_config.CheckboxColumn("Select")},
    )
    ids = [int(i) for i in edited.loc[edited["Select"], "id"]]
    selected = ct.get_contacts(conn, ids)
    st.write(f"**{len(selected)}** recipient(s) selected.")
    if not selected:
        return
    if any(c["verification_status"] != "verified" for c in selected):
        st.warning("Some selected contacts are NOT verified against an official college website.")

    st.markdown("#### 2. Preview")
    by_id = {c["id"]: c for c in selected}
    pid = st.selectbox("Preview recipient", list(by_id), format_func=lambda i: contact_label(by_id[i]))
    rendered = ts.render_email(tpl["subject"], tpl["body"], by_id[pid], sender)
    for p in rendered.problems:
        st.error(p)
    st.text_input("Subject", rendered.subject, disabled=True, key=f"s_subj_{pid}_{hash(rendered.subject)}")
    st.text_area("Body", rendered.body, height=300, disabled=True, key=f"s_body_{pid}_{hash(rendered.body)}")

    st.markdown("#### 3. Attachments (optional)")
    attach_dir = Path(__file__).parent / "data" / "attachments"
    attach_dir.mkdir(parents=True, exist_ok=True)
    uploaded_files = st.file_uploader(
        "Upload files to attach to every email in this batch",
        accept_multiple_files=True,
        type=["pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "png", "jpg", "jpeg", "gif", "txt", "zip"],
        help="Accepted: PDF, Word, PowerPoint, Excel, images, text, zip. Max 25 MB per file recommended.",
    )
    saved_attachment_paths = []
    if uploaded_files:
        for uf in uploaded_files:
            dest = attach_dir / uf.name
            dest.write_bytes(uf.read())
            saved_attachment_paths.append(str(dest))
        st.success(f"{len(saved_attachment_paths)} file(s) will be attached: "
                   + ", ".join(Path(p).name for p in saved_attachment_paths))

    st.markdown("#### 4. Send")
    live_possible = cfg.send_mode == "live"
    mode = st.radio("Mode", ["Test"] + (["Live"] if live_possible else []), horizontal=True,
                    help="Test mode redirects every message to your internal TEST_RECIPIENT.")
    live = mode == "Live"
    if not live_possible:
        st.caption("Live sending is disabled. To enable it, set SEND_MODE=live in .env and restart the app.")
    if live:
        st.error(f"LIVE: {len(selected)} real email(s) will be sent to the selected college contacts.")
        phrase = f"SEND {len(selected)}"
        typed = st.text_input(f"Type {phrase} to confirm")
        ack = st.checkbox("I understand SMTP acceptance does not guarantee delivery and that I cannot unsend.")
        confirmed = typed.strip() == phrase and ack
    else:
        st.info(f"TEST: all {len(selected)} message(s) go to {cfg.test_recipient or '(TEST_RECIPIENT not set)'}; "
                "the intended recipient is noted at the top of each message.")
        confirmed = True

    if st.button("Queue and send", type="primary", disabled=not confirmed or bool(rendered.problems)):
        db.set_setting(conn, "campaign", campaign)
        try:
            q = sending.queue_messages(conn, cfg, selected, tpl["subject"], tpl["body"], sender, campaign,
                                       live=live, live_confirmed=live and confirmed)
            for email, why in q.skipped:
                st.warning(f"Skipped {email}: {why}")
            if q.created_ids:
                bar = st.progress(0.0, text="Sending...")
                res = sending.process_queue(
                    conn, SMTPMailer(cfg), cfg, q.created_ids,
                    progress=lambda done, total, msg: bar.progress(done / total, text=msg),
                    attachments=saved_attachment_paths or None,
                )
                bar.empty()
                (st.success if not res.failed and not res.aborted else st.warning)(
                    f"{res.accepted} accepted by the SMTP server, {res.failed} failed, {res.not_attempted} not attempted.")
                if res.aborted:
                    st.error(res.abort_reason)
                st.caption(sending.SMTP_ACCEPTANCE_NOTE + " Open the History tab for details and retries.")
        except sending.SendingError as exc:
            st.error(str(exc))


# ------------------------------------------------------------------ history
def render_history(conn, cfg: Config) -> None:
    c1, c2, c3 = st.columns(3)
    text = c1.text_input("Search history", key="hist_text")
    status = c2.selectbox("Status", ["All", "pending", "sending", "sent", "failed"], key="hist_status")
    mode = c3.selectbox("Mode", ["All", "live", "test"], key="hist_mode")
    df = sending.history_dataframe(conn, text, "" if status == "All" else status, "" if mode == "All" else mode)
    st.caption(sending.SMTP_ACCEPTANCE_NOTE)
    cols = ["id", "created_at", "campaign", "college_name", "recipient_email", "delivered_to", "subject", "status",
            "attempt_count", "last_attempt_at", "accepted_at", "last_error", "is_test"]
    # Quick status summary for the current view
    if not df.empty:
        vc = df["status"].value_counts()
        m = st.columns(4)
        m[0].metric("Accepted (sent)", int(vc.get("sent", 0)))
        m[1].metric("Failed", int(vc.get("failed", 0)))
        m[2].metric("Pending", int(vc.get("pending", 0)))
        m[3].metric("Sending", int(vc.get("sending", 0)))
    # Colour-code the status column (purely visual; the underlying df is unchanged)
    _status_colors = {"sent": "#1e7f4f", "failed": "#8b1a1a", "pending": "#8a6d1a", "sending": "#1a5a8a"}
    def _style_status(val):
        return f"background-color: {_status_colors.get(val, '')}; color: #ffffff" if val in _status_colors else ""
    _shown = df[cols].rename(columns={"status": "status (sent = accepted)"})
    # pandas >=2.1 uses Styler.map; older 2.0.x uses applymap. Support both.
    _apply = getattr(_shown.style, "map", None) or _shown.style.applymap
    _styled = _apply(_style_status, subset=["status (sent = accepted)"])
    st.dataframe(_styled, hide_index=True)
    csv_download(df[cols], "Download history (CSV)", "email_history.csv", "hist_dl")

    st.markdown("#### Retry failed messages")
    failed = df[df["status"] == "failed"]
    if failed.empty:
        st.write("No failed messages in the current view.")
    else:
        opts = {int(r["id"]): r for r in failed.to_dict("records")}
        picks = st.multiselect("Failed messages", list(opts),
                               format_func=lambda i: f"#{i} {opts[i]['recipient_email']} (attempts: {opts[i]['attempt_count']})")
        interrupted = st.checkbox("Include messages interrupted by a crash (they may already have been accepted)")
        needs_live = any(opts[i]["is_test"] == 0 for i in picks)
        live_ok = st.checkbox("I confirm retrying LIVE messages to real recipients") if needs_live else True
        if st.button("Retry selected", disabled=not picks or not live_ok):
            try:
                res = sending.retry_failed(conn, SMTPMailer(cfg), cfg, picks, include_interrupted=interrupted)
                st.success(f"{res.accepted} accepted, {res.failed} failed again.")
                for rid, why in res.skipped:
                    st.warning(f"#{rid} skipped: {why}")
                if res.aborted:
                    st.error(res.abort_reason)
            except sending.SendingError as exc:
                st.error(str(exc))

    st.markdown("#### Messages stuck in 'sending'")
    minutes = st.number_input("Treat 'sending' rows older than (minutes)", min_value=0, value=15, step=5)
    if st.button("Mark stuck messages as failed (interrupted)"):
        n = sending.recover_stuck_sending(conn, int(minutes))
        st.success(f"{n} message(s) marked as interrupted. Check your mailbox's Sent folder before retrying them.")


# --------------------------------------------------------------------- login
def _admin_credentials() -> tuple:
    """Admin login credentials. Read from the environment; demo defaults otherwise.

    Set ADMIN_USERNAME / ADMIN_PASSWORD in your .env to override. Never hard-code
    real credentials in source — keep them in .env (which is git-ignored).
    """
    user = (os.environ.get("ADMIN_USERNAME") or "admin").strip()
    secret = (os.environ.get("ADMIN_PASSWORD") or "admin@123").strip()
    return user, secret


def require_login() -> bool:
    """Render a simple admin login gate. Returns True once authenticated."""
    if st.session_state.get("_authenticated"):
        return True
    inject_css()
    hero("Shortfundly Campus Email Automation",
         "Campus outreach admin panel \u2014 secure sign-in")
    st.markdown('<div class="qw-login-wrap"><span class="qw-badge">ADMIN ACCESS</span></div>',
                unsafe_allow_html=True)
    with st.form("login_form"):
        in_user = st.text_input("Username")
        in_secret = st.text_input("password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        exp_user, exp_secret = _admin_credentials()
        if in_user.strip() == exp_user and in_secret.strip() == exp_secret:
            st.session_state["_authenticated"] = True
            st.rerun()
        else:
            st.error("Invalid username or password.")
    st.caption("Internal admin panel. Credentials are configured in .env "
               "(ADMIN_USERNAME / ADMIN_PASSWORD).")
    return False


# ---------------------------------------------------------------- automation
def render_automation(conn, cfg: Config) -> None:
    """Automation tab: pipeline status, seed CSV upload, run-now, bulk approve."""
    from shortfundly import monitoring_db as mdb

    mdb.init_monitoring(conn)
    mstats = mdb.monitoring_stats(conn)

    # ---- Status overview ----
    with st.container(border=True):
        st.subheader("🤖 Automation Pipeline Status")
        c = st.columns(4)
        c[0].metric("Colleges tracked", mstats["colleges_tracked"])
        c[1].metric("Scraped contacts", mstats["contacts_total"])
        c[2].metric("Auto-approved", mstats["contacts_approved"])
        c[3].metric("Pending review", mstats["pending_approvals"])

        last_run = mstats.get("last_run_at") or "Never"
        st.caption(f"Last monitoring run: **{last_run}**")

        col_a, col_b, col_c = st.columns(3)
        col_a.info(
            f"**Auto-approve:** {'✅ Enabled' if cfg.auto_approve_enabled else '❌ Disabled'}  \n"
            f"Threshold: {cfg.auto_approve_threshold}/100"
        )
        col_b.info(
            f"**Auto-send:** {'✅ Enabled' if cfg.auto_send_enabled else '❌ Disabled'}  \n"
            f"Mode: `{cfg.send_mode}`  |  Campaign: `{cfg.auto_send_campaign}`"
        )
        col_c.info(
            f"**Monitor interval:** `{cfg.monitor_interval}`  \n"
            f"Seed CSV: `{cfg.seed_csv_path}`"
        )

    # ---- Run pipeline now ----
    with st.container(border=True):
        st.subheader("▶ Run Pipeline Now")
        st.write(
            "Trigger a full pipeline pass immediately from the dashboard: "
            "seed sync → monitoring → auto-approve → contact sync.  "
            "*(Email auto-send still requires AUTO_SEND_ENABLED=true in .env)*"
        )
        dry = st.checkbox("Dry run (simulate only, no DB writes or emails)", value=True, key="auto_dry")
        if st.button("🚀 Run pipeline now", type="primary"):
            import threading
            result_holder = {}

            def _run():
                try:
                    import importlib
                    import sys
                    # Import worker as a module (not __main__)
                    spec = importlib.util.spec_from_file_location(
                        "worker_mod",
                        str(Path(__file__).resolve().parent / "worker.py")
                    )
                    w = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(w)
                    result_holder["result"] = w.run_once(verbose=False, dry_run=dry)
                except Exception as exc:
                    result_holder["error"] = str(exc)

            with st.spinner("Running pipeline…"):
                t = threading.Thread(target=_run)
                t.start()
                t.join(timeout=300)

            if "error" in result_holder:
                st.error(f"Pipeline error: {result_holder['error']}")
            elif "result" in result_holder:
                res = result_holder["result"]
                st.success("Pipeline run complete!")
                st.json(res)
            else:
                st.warning("Pipeline timed out or returned no result.")

    # ---- Seed CSV management ----
    with st.container(border=True):
        st.subheader("📋 Seed College List")
        seed_path_raw = cfg.seed_csv_path or "data/seed_colleges.csv"
        if not Path(seed_path_raw).is_absolute():
            seed_path = str(Path(__file__).resolve().parent / seed_path_raw)
        else:
            seed_path = seed_path_raw

        if Path(seed_path).exists():
            try:
                seed_df = pd.read_csv(seed_path)
                st.caption(f"{len(seed_df)} colleges in seed list (`{seed_path_raw}`)")
                st.dataframe(seed_df, hide_index=True, use_container_width=True)
                st.download_button(
                    "⬇ Download seed CSV",
                    seed_df.to_csv(index=False).encode("utf-8-sig"),
                    "seed_colleges.csv",
                    "text/csv",
                    key="dl_seed",
                )
            except Exception as exc:
                st.warning(f"Could not read seed CSV: {exc}")
        else:
            st.info(f"Seed CSV not found at `{seed_path_raw}`. It will be created on first run.")

        st.markdown("**Upload a new seed CSV** (columns: `name`, `website`, `state`, `city`, `institution_type`):")
        uploaded = st.file_uploader("Choose seed CSV", type=["csv"], key="seed_upload")
        if uploaded is not None:
            try:
                new_df = pd.read_csv(uploaded)
                st.dataframe(new_df.head(10), hide_index=True)
                if st.button("💾 Save uploaded seed CSV", key="save_seed"):
                    Path(seed_path).parent.mkdir(parents=True, exist_ok=True)
                    new_df.to_csv(seed_path, index=False)
                    st.success(f"Saved {len(new_df)} colleges to `{seed_path_raw}`.")
                    st.rerun()
            except Exception as exc:
                st.error(f"Could not parse CSV: {exc}")

    # ---- Monitored colleges ----
    with st.container(border=True):
        st.subheader("🏫 Monitored Colleges")
        try:
            colleges_df = pd.read_sql_query(
                "SELECT id, name, state, city, website, status, last_check_status, "
                "last_checked_at, monitoring_enabled FROM colleges ORDER BY name",
                conn,
            )
            if colleges_df.empty:
                st.info("No colleges tracked yet. Add colleges to your seed CSV and run the pipeline.")
            else:
                st.caption(f"{len(colleges_df)} college(s) in database")
                st.dataframe(colleges_df, hide_index=True, use_container_width=True)
        except Exception:
            st.info("Monitoring tables not initialised yet. Run the pipeline once to initialise.")

    # ---- Bulk approve pending contacts ----
    with st.container(border=True):
        st.subheader("✅ Bulk Approve Pending Contacts")
        pending_count = mstats.get("pending_approvals", 0)
        st.write(
            f"There are **{pending_count}** contact change(s) pending review.  \n"
            "Approve all contacts that meet the confidence threshold, or review individually below."
        )
        thr = st.slider(
            "Confidence threshold (contacts scoring below this need manual review)",
            min_value=0, max_value=100, value=cfg.auto_approve_threshold, step=5,
            key="bulk_thr",
        )
        dry2 = st.checkbox("Dry run (preview only)", value=True, key="bulk_dry")
        if st.button("⚡ Run auto-approve now", key="bulk_approve_btn"):
            from shortfundly.auto_approve import auto_approve_pending, sync_approved_to_contacts
            with st.spinner("Scoring and approving contacts…"):
                appr = auto_approve_pending(conn, threshold=thr, dry_run=dry2)
                sync = sync_approved_to_contacts(conn, dry_run=dry2)
            label = "(DRY RUN) " if dry2 else ""
            st.success(
                f"{label}Approved **{appr['approved']}** contact(s), "
                f"skipped **{appr['skipped_low_score']}** (low score), "
                f"synced **{sync['synced']}** to main contacts table."
            )
            st.rerun()

        # Show pending contact_records
        try:
            pending_df = pd.read_sql_query(
                "SELECT cr.id, c.name AS college, cr.email, cr.source_url, cr.status, "
                "       c.website "
                "FROM contact_records cr "
                "JOIN colleges c ON cr.college_id = c.id "
                "WHERE cr.status IN ('discovered', 'pending_review') "
                "ORDER BY cr.collected_at DESC LIMIT 100",
                conn,
            )
            if not pending_df.empty:
                from shortfundly.auto_approve import confidence_score
                pending_df["score"] = pending_df.apply(
                    lambda r: confidence_score(r["email"], r["source_url"], r["website"]),
                    axis=1,
                )
                st.caption(f"{len(pending_df)} pending contact(s) shown (max 100)")
                st.dataframe(
                    pending_df[["id", "college", "email", "source_url", "status", "score"]],
                    hide_index=True,
                    use_container_width=True,
                )
        except Exception:
            pass

    # ---- Worker log tail ----
    with st.container(border=True):
        st.subheader("📄 Worker Log (last 50 lines)")
        log_path = Path(__file__).resolve().parent / "data" / "worker.log"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            st.code("\n".join(lines[-50:]), language="")
        else:
            st.info("No worker log found yet. Run the worker (`python -m worker`) to generate it.")


# --------------------------------------------------------------------- main
def main() -> None:
    if not require_login():
        return
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    try:
        ts.ensure_default_template(conn)
        sending.recover_stuck_sending(conn, older_than_minutes=60)
        inject_css()
        hero("Shortfundly Campus Email Automation",
             "Collect official college contacts · personalise · send safely · track — starts in TEST mode")
        sender = render_sidebar(conn, cfg)
        show_flash()
        tabs = st.tabs(["Dashboard", "Contacts", "Templates", "Send", "History", "⚡ Automation"])
        with tabs[0]:
            st.caption("Live counts for your contacts and email delivery, computed from the database.")
            render_dashboard(conn)
        with tabs[1]:
            st.caption("Manage your college contact list: browse, import, add, verify against sources, and export.")
            sub = st.tabs(["Browse & edit", "Import", "Add contact", "Verification", "Export"])
            with sub[0]:
                render_browse(conn)
            with sub[1]:
                render_import(conn)
            with sub[2]:
                render_add(conn)
            with sub[3]:
                render_verification(conn)
            with sub[4]:
                render_export(conn)
        with tabs[2]:
            st.caption("Edit, upload, and save the email subject & body. Use {placeholders} to personalise.")
            render_templates(conn, sender)
        with tabs[3]:
            st.caption("Pick a template and recipients, preview the personalised email, then queue and send.")
            render_send(conn, cfg, sender)
        with tabs[4]:
            st.caption("Every message with its status, attempts, and errors. Retry failures here.")
            render_history(conn, cfg)
        with tabs[5]:
            st.caption("Full automation pipeline: college discovery, monitoring, contact approval and email sending.")
            render_automation(conn, cfg)
    finally:
        conn.close()


main()
