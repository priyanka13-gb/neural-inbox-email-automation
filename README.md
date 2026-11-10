#  NeuralInbox — Email Automation with a RAG Pipeline

NeuralInbox watches an inbox over IMAP, classifies each incoming email with Claude, retrieves relevant context from your own knowledge base (RAG), and drafts a reply in the right department's tone. A human reviews, edits and approves each draft before it is sent over SMTP. High-confidence replies can be flagged for auto-send.

## Features

- **IMAP listener**: a background task polls the mailbox at a configurable interval (default 70 s).
- **AI classification**: Claude returns intent, department, sentiment, urgency, a summary and key topics for every email.
- **RAG over your docs**: upload PDFs or text, which are chunked and indexed in an in-memory vector store; the top matches ground each draft.
- **Department-aware drafts**: each department has its own tone, signature and auto-send threshold. Defaults: Support, Billing, Sales, General.
- **Confidence scoring**: a heuristic score (0.1–0.99) based on intent, sentiment, urgency, knowledge-base hits and email length.
- **Human-in-the-loop**: approve (optionally with edits), reject, or regenerate a draft with custom instructions.
- **Audit trail**: every action is recorded in an activity log.
- **Simulation endpoint**: push a test email through the whole pipeline without a live mailbox.
- **Dashboard + settings API**: for the React frontend. Covers stats, IMAP/SMTP configuration, a test-SMTP action and department editing.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, Uvicorn, `aiosqlite` (SQLite) |
| LLM | Anthropic Claude (`claude-sonnet-4-20250514`) via the `anthropic` SDK |
| Retrieval | Custom in-memory term-frequency / cosine-similarity vector store |
| Email | IMAP (inbound), SMTP (outbound) |
| Documents | PyPDF2 for PDF text extraction |
| Frontend | React (dev origins `localhost:3000` and `localhost:5173` are allowed by CORS) |

## How It Works

```
 IMAP inbox ──► IMAP listener ──► AI engine ──────────────► Review queue (pending_approval)
                                    │                              │
                                    │ 1. classify (Claude)         │  approve / edit / reject / regenerate
                                    │ 2. retrieve top-3 KB chunks  ▼
                                    │ 3. draft reply (Claude)    SMTP sender ──► Customer
                                    │ 4. score confidence
                                    ▼
                               SQLite (emails, knowledge_docs, departments, users, email_settings, activity_log)
```

**Confidence score.** It starts at 0.7 and then changes as follows:

- Routine intents (`general_inquiry`, `order_status`, `billing_question`): +0.15.
- Risky intents (`complaint`, `urgent_issue`, `refund_request`): −0.2.
- Angry or frustrated sentiment: −0.15. Positive sentiment: +0.05.
- Critical urgency: −0.3. High urgency: −0.1.
- Knowledge-base match found: +0.1.
- Very short email (under 50 characters): −0.1.

The score is compared with the department's `auto_send_threshold` (default 0.95).

**Email status flow:** `processing` → `pending_approval` → `sent` or `rejected` (`failed` if AI processing errors).

## Project Structure

```
neural-inbox-email-automation/
├── main.py              # FastAPI app, lifespan (DB init + IMAP listener), CORS, health check
├── auth.py              # /api/auth       – authentication
├── emails.py            # /api/emails     – list, view, approve, reject, regenerate, simulate
├── knowledge.py         # /api/knowledge  – upload / list / delete knowledge-base docs
├── dashboard.py         # /api/dashboard  – dashboard data
├── settings.py          # /api/settings   – IMAP/SMTP config, departments, test-SMTP
├── ai_engine.py         # Classification, RAG retrieval, drafting, confidence scoring
├── imap_listener.py     # Background inbox polling
├── smtp_sender.py       # Outgoing mail
├── database.py          # SQLite schema + seed data
└── logger.py            # Logging
```

> `main.py` imports these modules as `routes.*`, `services.*` and `utils.logger`. Depending on how you lay out the repository, you may need to arrange the files into `routes/`, `services/` and `utils/` packages.

## Getting Started

### Prerequisites

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com/)
- A mailbox with IMAP and SMTP access (for Gmail, use an [app password](https://support.google.com/accounts/answer/185833))
- Node.js 18+ (only if you run the React frontend)

### 1. Clone and install

```bash
git clone https://github.com/priyanka13-gb/neural-inbox-email-automation.git
cd neural-inbox-email-automation

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install fastapi uvicorn aiosqlite anthropic PyPDF2 python-multipart
```

Install any further packages that the auth module needs, such as a password-hashing or JWT library. Check its imports.

### 2. Configure environment

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export DB_PATH="./neuralinbox.db"     # optional, this is the default
```

IMAP/SMTP credentials, the company name, check interval and the auto-send toggle are stored in the database. Set them through the settings API or the dashboard, not environment variables.

### 3. Run the backend

```bash
python main.py
# or
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- API: http://localhost:8000
- Interactive docs (Swagger): http://localhost:8000/docs
- Health check: http://localhost:8000/api/health

### 4. Configure the system

1. Create a user through `/api/auth`. No users are seeded.
2. `PUT /api/settings` with your IMAP and SMTP details, company name and check interval.
3. `POST /api/settings/test-smtp` to verify outbound mail.
4. Upload knowledge-base documents with `POST /api/knowledge/upload`.
5. Optionally tune departments (tone, signature, threshold) with `PUT /api/settings/departments/{id}`.

### 5. Try it without a mailbox

```bash
curl -X POST http://localhost:8000/api/emails/simulate \
  -H "Content-Type: application/json" \
  -d '{
        "sender_name": "Jane Doe",
        "sender_email": "jane@example.com",
        "subject": "Where is my order?",
        "body": "Hi, I placed an order last week and it has not arrived yet."
      }'
```

If the auth module protects this route, include your auth token. The email appears in the review queue with a classification, a draft and a confidence score.

## API Overview

All routes sit under `/api`. Full details are in the Swagger UI at `/docs`.

| Area | Endpoint | Description |
|---|---|---|
| Health | `GET /api/health` | Service status and IMAP listener flag |
| Emails | `GET /api/emails` | List with filters (`status`, `department`, `urgency`, `search`) and pagination (`page`, `limit`) |
| | `GET /api/emails/{id}` | Email detail with activity log |
| | `POST /api/emails/{id}/approve` | Send the reply, using the edited `reply_body` or the AI draft |
| | `POST /api/emails/{id}/reject` | Reject the draft |
| | `POST /api/emails/{id}/regenerate` | New draft, optional `custom_instructions` |
| | `POST /api/emails/simulate` | Run a test email through the pipeline |
| Knowledge | `GET /api/knowledge` | List documents, grouped by name |
| | `POST /api/knowledge/upload` | Upload a file with an optional `department` |
| | `POST /api/knowledge/text` | Add text directly |
| | `DELETE /api/knowledge/{doc_name}` | Remove a document |
| Settings | `GET` / `PUT /api/settings` | Read or update email settings (passwords masked on read) |
| | `POST /api/settings/test-smtp` | Send a test email |
| | `GET /api/settings/departments` | List departments |
| | `PUT /api/settings/departments/{id}` | Update a department |
| Auth, Dashboard | `/api/auth/*`, `/api/dashboard/*` | See Swagger UI |

## Knowledge Base Notes

- PDFs are read with PyPDF2. Other files are decoded as UTF-8, so plain-text formats such as `.txt`, `.md` and `.csv` work best.
- Text is split into 800-word chunks with a 50-word overlap.
- Retrieval returns the top 3 matches for the subject, intent and key topics of the email.

## Known Limitations

- The vector store is in memory and uses simple term-frequency cosine similarity, not neural embeddings. Check that it is repopulated from `knowledge_docs` after a restart.
- Deleting a document removes its database rows but may leave vectors in the running store until restart.
- The Anthropic client is synchronous, so model calls block the async event loop under load.
- `approve` does not check that an email is still `pending_approval`, so a reply could be sent twice.
- Secrets such as IMAP/SMTP passwords are stored in SQLite. Restrict access to the database file and consider encrypting them before production use.

## Roadmap Ideas

- Real embeddings with a persistent vector database
- Async LLM calls and a background job queue
- Attachment handling
- Stronger approval-state checks and role-based permissions
- Docker setup and automated tests

## Contributing

Issues and pull requests are welcome. Fork the repo, create a feature branch and open a PR with a clear description.

## License

No license has been specified yet. Add a `LICENSE` file, such as MIT, to state the terms.
