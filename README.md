# Shortfundly Campus Email Automation (Phase 2 — Final)

A Streamlit **admin dashboard** for collecting official college contacts, editing a proposal
email template, sending personalised emails over SMTP, and tracking every message in SQLite.

**Safe by default:** the app starts in **TEST mode**. Every email is redirected to an internal
address you control, whatever recipients you select. Real sending needs `SEND_MODE=live` in
`.env` *and* a typed confirmation in the dashboard.

> See [`VERIFICATION_REPORT.md`](VERIFICATION_REPORT.md) for verification results and
> [`DEMONSTRATION.md`](DEMONSTRATION.md) for a short end-to-end walkthrough.

---

## Table of contents
1. [Project layout](#project-layout)
2. [Quick start (Windows PowerShell)](#quick-start-windows-powershell)
3. [Admin login](#admin-login)
4. [Configuration (`.env`)](#configuration-env)
5. [**Part A — Running for TESTING**](#part-a--running-for-testing) ← start here
6. [**Part B — Running for OFFICIAL / company use**](#part-b--running-for-official--company-use)
7. [Collecting & verifying Karnataka contacts](#collecting--verifying-karnataka-contacts)
8. [How sending stays safe](#how-sending-stays-safe)
9. [Running the automated tests](#running-the-automated-tests)
10. [Troubleshooting](#troubleshooting)
11. [Limitations](#limitations)

---

## Project layout

```
app.py                       Streamlit dashboard: login + Dashboard, Contacts, Templates, Send, History
logo.jpg                     Shortfundly logo shown in the dashboard banner
.streamlit/config.toml       UI theme (cosmetic only; safe to edit/delete)
.env.example                 Template for your private .env (copy it to .env)
requirements.txt             Python dependencies
pytest.ini                   Test configuration
shortfundly/
  config.py                  Environment/.env configuration (password never printed)
  database.py                SQLite schema + connection
  validation.py              Email/URL/required-field validation, domain matching
  contacts.py                CRUD, search, verification, CSV/Excel export
  importer.py                CSV/Excel import with preview, duplicate detection, rejection log
  template_service.py        Placeholder rendering, upload decoding, saved templates
  mailer.py                  SMTP client (the only module that touches the network)
  sending.py                 Queueing, sending, status tracking, duplicate prevention, retries
  stats.py                   Dashboard numbers computed from real records
templates/                   default_subject.txt, default_email_body.txt (the default proposal)
data/
  sample_contacts_fictional.csv   Fictional demo data (safe to experiment with)
  karnataka_collection_template.csv  Empty template to collect new contacts
  karnataka_contacts.csv          Collected Karnataka contacts (verification-clean)
  karnataka_contacts_leads.csv    Collected contacts that need manual confirmation
tests/                       pytest suite (no real SMTP, fictional contacts only)
VERIFICATION_REPORT.md       What was checked, what passed, what to run locally, open items
DEMONSTRATION.md             Short end-to-end workflow walkthrough (safe TEST mode)
```

---

## Quick start (Windows PowerShell)

Requires Python 3.10+ (`python --version`). From the project folder:

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
# If activation is blocked, run this once for the current window and retry:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# 3. Create your private configuration
Copy-Item .env.example .env
notepad .env          # fill in values (see Configuration below)

# 4. Start the app (opens http://localhost:8501)
streamlit run app.py
```

If `streamlit` is not recognised, use: `python -m streamlit run app.py`.

---

## Admin login

The dashboard opens on a **sign-in screen**.

- Credentials come from `.env`: `ADMIN_USERNAME` and `ADMIN_PASSWORD`.
- If those are left blank, the **demo defaults** are username `admin`, password `admin@123`.
- A **Log out** button is at the top of the sidebar.

> For official use, always set your own `ADMIN_USERNAME` / `ADMIN_PASSWORD` in `.env` and never
> rely on the demo defaults.

---

## Configuration (`.env`)

| Variable | Meaning |
|---|---|
| `SMTP_HOST`, `SMTP_PORT` | SMTP server and port (e.g. `smtp.gmail.com`, `587`) |
| `SMTP_SECURITY` | `starttls` (587), `ssl` (465) or `none` (local debug server only) |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | Credentials. Gmail/Outlook need an **app password**, not the account password |
| `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME` | From header; must be permitted by your provider |
| `SEND_MODE` | `test` (default, safe) or `live` (real recipients) |
| `TEST_RECIPIENT` | Your own address; every test-mode email is redirected here |
| `SENDER_NAME`, `SENDER_TITLE` | Used in `{sender_name}`/`{sender_title}`; also editable in the sidebar. Never invented |
| `MAX_ATTEMPTS`, `SEND_DELAY_SECONDS`, `MAX_BATCH_SIZE` | Retry cap, pause between messages, batch limit |
| `DB_PATH` | SQLite file (default `data/shortfundly.db`) |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Admin dashboard login (demo defaults: `admin` / `admin@123`) |

`.env` is git-ignored. Credentials are read only from the environment, never stored in the
database, and scrubbed from stored/displayed error messages.

---

## Part A — Running for TESTING

Use this while developing, demoing, or verifying the workflow. **No real college is ever contacted.**

### A1. Example `.env` for testing (send test mail to your own inbox)

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=your-address@gmail.com
SMTP_PASSWORD=your-16-char-gmail-app-password
SMTP_FROM_EMAIL=your-address@gmail.com
SMTP_FROM_NAME=Shortfundly Campus
SEND_MODE=test
TEST_RECIPIENT=your-own-inbox@example.com
SENDER_NAME=Your Name
SENDER_TITLE=Campus Outreach
# Admin login can stay as demo defaults for testing:
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin@123
```

### A2. Steps

1. `streamlit run app.py` → sign in with `admin` / `admin@123`.
2. **Sidebar** → enter a sender name/title → click **Test SMTP login (sends nothing)**.
   You should see *Connected and authenticated*.
3. **Contacts → Import** → upload `data/sample_contacts_fictional.csv` (or
   `data/karnataka_contacts.csv`) → review the preview → **Save valid rows**.
4. **Templates** → review/edit the default proposal → **Save**.
5. **Send** → pick a template, select recipients, check the personalised preview →
   **Queue and send**. In TEST mode every message is redirected to `TEST_RECIPIENT`
   and prefixed `[TEST]`, with the intended college noted at the top.
6. **History / Dashboard** → watch statuses update. Retry failures if any.

### A3. What makes testing safe
- `SEND_MODE=test` forces every message to `TEST_RECIPIENT` — enforced at send time, even if a
  stored row is tampered with.
- The automated tests patch `smtplib` so no real connection is ever opened.
- You can run the same campaign repeatedly; duplicates are blocked only in live mode.

---

## Part B — Running for OFFICIAL / company use

Use this only when you are ready to email **real colleges** on behalf of Shortfundly. Most of the
work here is **configuration and domain setup**, not code changes.

### B1. Use an official company mailbox (not a personal Gmail)
Point SMTP at Shortfundly's own domain mailbox via Google Workspace, Microsoft 365, or an email
service such as Amazon SES / SendGrid:

```
SMTP_HOST=<company smtp host>          # e.g. email-smtp.<region>.amazonaws.com (SES) or smtp provider
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=<company smtp username>
SMTP_PASSWORD=<company smtp credential / app password>
SMTP_FROM_EMAIL=campus@shortfundly.com   # an address on the company domain
SMTP_FROM_NAME=Shortfundly Campus
SENDER_NAME=<real representative name>
SENDER_TITLE=<real representative title>
SEND_MODE=live
TEST_RECIPIENT=                          # still set one for pre-flight tests
# Set strong, non-default admin credentials:
ADMIN_USERNAME=<your admin user>
ADMIN_PASSWORD=<a strong secret>
```

### B2. Domain deliverability (do this before sending at scale)
Ask the domain administrator to configure, on the sending domain:
- **SPF** — authorise the sending server/service
- **DKIM** — cryptographically sign outgoing mail
- **DMARC** — publish a policy for unauthenticated mail

Without these, college mail servers will mark the emails as spam or reject them.

### B3. Go-live procedure
1. Keep `SEND_MODE=test` first and send a full test batch to yourself — confirm rendering and
   personalisation look right.
2. Set `SEND_MODE=live` and restart the app (`.env` is read only at startup).
3. In **Send**, select recipients, choose a **campaign label**, then type the required
   `SEND <n>` confirmation and tick the acknowledgement before sending.
4. Respect provider daily limits; for hundreds of emails use SES/SendGrid rather than Gmail.
5. Track everything in **History**; export the CSV for records.

### B4. Compliance & good practice (official outreach)
- Email only **official institutional addresses** for a legitimate purpose (this project already
  enforces official-domain collection and rejects public mailboxes during verification).
- Provide an opt-out / unsubscribe path and an identifying signature.
- Store the SMTP credential and admin secret securely (environment/secret manager on a server,
  not a shared laptop).

---

## Collecting & verifying Karnataka contacts

The app **does not scrape or generate contacts**. The included `data/karnataka_contacts.csv`
was collected manually from official college websites; `data/karnataka_contacts_leads.csv`
holds contacts that could not be auto-verified (e.g. a public mailbox, or an email on a
parent-organisation domain) and need manual confirmation.

To collect more:
1. Find each college on its **official website** and open a contact / placement / department /
   film-club page.
2. Record into `data/karnataka_collection_template.csv` (or use *Add contact*): state, city,
   college name, official website, contact person, department, the email **shown on that page**,
   and the **URL of that page** as the source.
3. Import → records arrive **unverified**.
4. In **Contacts → Verification**, open the source page, confirm the email is shown there, and
   **Mark as verified**. The app requires the email domain and source page to match the official
   website domain and rejects public mailboxes (gmail.com etc.). Your confirmation is recorded
   (name, time, note).
5. Editing a verified record's email, website, or source URL resets it to unverified.

Never guess addresses and never use bought or scraped lists.

---

## How sending stays safe

- **Test-mode override** is enforced in the sending layer and re-applied at send time.
- **Live mode** requires `SEND_MODE=live`, a typed `SEND <n>` confirmation and an acknowledgement.
- **No duplicate sends:** a database unique index allows one live message per (campaign, recipient).
  Use a new campaign label for an intentional follow-up.
- **Atomic claim:** a message moves `pending/failed → sending` in one SQL update *before* SMTP is
  contacted; `sent` rows can never be claimed again, so retries never resend successes.
- **Statuses:** Pending, Sending, Sent (= *accepted by the SMTP server*), Failed — with attempt
  count, timestamps and the last error. **SMTP acceptance does not guarantee delivery.**
- **Failures:** auth errors stop the batch (rest stay Pending); timeouts/refusals are recorded per
  message; three consecutive connection failures stop the batch.
- **Crash recovery:** rows stuck in *Sending* become Failed with an "interrupted" warning and are
  not retried unless you explicitly include them.
- **Tests:** `smtplib` is patched to fail any real connection; the real mailer refuses to run under pytest.

---

## Running the automated tests

```powershell
python -m pytest -q
```

The suite uses in-memory/temp SQLite and a fake mailer — it never sends real email and never
opens a real SMTP connection. Expected result: all tests pass.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `streamlit : not recognized` | Dependencies not installed in the venv. Run `pip install -r requirements.txt`, or use `python -m streamlit run app.py`. |
| `gaierror: [Errno 11001] getaddrinfo failed` | `SMTP_HOST` is wrong/blank/mistyped (e.g. still the placeholder, or has a trailing space). Set it to e.g. `smtp.gmail.com` and restart. |
| `SMTP authentication failed` | Use a Gmail **App Password** (16 chars), not your login password. Remove any trailing space from `SMTP_PASSWORD`. Ensure 2-Step Verification is on. |
| Login screen rejects `admin`/`admin@123` | You set `ADMIN_USERNAME`/`ADMIN_PASSWORD` in `.env` — use those values, or clear them to restore the demo defaults. Restart after editing `.env`. |
| Changes to `.env` not taking effect | `.env` is read only at startup. Stop the app (Ctrl+C) and run `streamlit run app.py` again. |
| A contact won't verify | Email domain must match the website/source domain, and public mailboxes (gmail.com etc.) are rejected. Fix the website/source, or keep it in the leads file. |
| Logo not showing in the banner | `logo.jpg` must stay in the project root next to `app.py`. If missing, the app falls back to a built-in logo automatically. |

---

## Limitations

- Plain-text emails only (no HTML, attachments or open/click tracking); bounces are not read back.
- One SMTP connection per message; suitable for tens to a few hundred emails, not bulk mailing.
- Excel import reads the first sheet; legacy `.xls` is not supported.
- SQLite is single-user; run one app instance per database file.
