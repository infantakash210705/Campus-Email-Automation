# Demonstration Guide — Shortfundly Campus Email Automation

A short, end-to-end walkthrough of the complete workflow:

> Assigned State → Collect Official College Emails → Organize Contact List →
> Upload Content → Personalize Emails → Automatically Send → Track Status

Everything below runs **safely in TEST mode** — every email is redirected to an internal
address you control. No real college is contacted at any point in this demo.

---

## 0. Start the app (Windows PowerShell)

```powershell
cd "path\to\shortfundly_email_automation"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

Copy-Item .env.example .env
notepad .env     # see step 1 below for what to fill
```

Minimum `.env` for a safe self-test (send a test email to your own inbox):

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=your-address@gmail.com
SMTP_PASSWORD=your-16-char-gmail-app-password
SMTP_FROM_EMAIL=your-address@gmail.com
SEND_MODE=test
TEST_RECIPIENT=your-own-inbox@example.com
```

> Gmail needs an **App Password** (not your normal password), and watch for trailing
> spaces when pasting it — a stray space is a common cause of silent login failures.

Then launch:

```powershell
streamlit run app.py
```

The dashboard opens at **http://localhost:8501**.

---

## 1. Sender details + SMTP check (sidebar)

1. In the left sidebar, confirm the banner says **TEST mode** and shows your
   `TEST_RECIPIENT`.
2. Enter a **Sender name** and **Sender title**, click **Save sender details**.
   (The app never invents these; `{sender_name}` / `{sender_title}` come from here.)
3. Click **Test SMTP login (sends nothing)** — it should say *Connected and
   authenticated.* If not, fix `.env` and retry.

## 2. Organize the contact list — import the sample (Contacts → Import)

1. Go to **Contacts → Import**, upload `data/sample_contacts_fictional.csv`.
2. The preview shows: **6 ready**, **2 invalid**, **1 duplicate email**,
   **1 college already present**. Nothing is saved yet.
3. Click **Save valid rows to database** → 6 fictional contacts are imported as
   **unverified**. (Invalid/duplicate rows are logged, never saved.)

> For the real assignment you instead fill `data/karnataka_collection_template.csv`
> from official college websites (state, city, college, official website, contact
> person/department, the email **shown on the page**, and that page's **URL** as the
> source), then import it the same way. See README → "Collecting and verifying".

## 3. (Optional) Verify a contact (Contacts → Verification)

1. Open **Contacts → Verification**, pick a contact.
2. Open its source page, confirm the email is shown there, tick the confirmation box,
   enter your name, click **Mark as verified**. The app records who verified it and when,
   and blocks verification for public mailboxes (gmail.com etc.) or mismatched domains.

## 4. Upload / edit the content (Templates)

1. Go to **Templates**. The default Shortfundly proposal is pre-loaded.
2. Edit the **Subject** and **Body**, or **Upload a .txt file** to replace the body.
   Placeholders available: `{college_name}`, `{recipient_name}`, `{department}`,
   `{city}`, `{state}`, `{sender_name}`, `{sender_title}`.
3. Use the **Preview** at the bottom to see a fully personalized example.
4. Give it a name and click **Save template**.

## 5. Personalize + automatically send (Send) — TEST mode

1. Go to **Send**. Choose your saved template and a **Campaign label**
   (e.g. `Campus Proposal Demo`).
2. Filter/select recipients (tick **Select all filtered contacts** to grab them all).
   Each selected college gets its **own** personalized message.
3. Scroll to **3. Send**. In TEST mode it confirms every message goes to your
   `TEST_RECIPIENT`. Click **Queue and send**.
4. Watch the progress bar. You'll see e.g. *"6 accepted by the SMTP server, 0 failed"*.
5. Check your `TEST_RECIPIENT` inbox — each email is prefixed `[TEST]` and names the
   intended college at the top.

## 6. Track status (History + Dashboard)

1. **History** tab: every message with status (**sent = accepted by SMTP**),
   attempt count, timestamps, and any error. Download the history as CSV.
2. Retry any **failed** message from here; **sent** messages are never re-sent.
3. **Dashboard** tab: live counts of Pending / Sending / Accepted / Failed, plus contact
   stats and a contacts-by-state chart.

## 7. Prove the safety guarantees (optional but recommended for the demo)

- **Duplicate prevention:** run the same campaign label again — every recipient is
  skipped with *"Already sent in campaign …"*. Change the campaign label to send an
  intentional follow-up.
- **Test isolation:** even if you edited the stored recipient, test messages still go
  only to your internal address (the override is re-applied at send time).
- **Live mode is gated:** real sending requires `SEND_MODE=live` in `.env` **and** typing
  `SEND <n>` plus a ticked acknowledgement in the dashboard. Leave it in TEST mode for
  the demo.

---

### One-line narration for your submission video

> "Import official contacts → verify against the source page → edit the proposal
> template → select colleges → send in TEST mode (every mail lands in my own inbox) →
> watch statuses update in History, with duplicate-send protection and safe retries."
