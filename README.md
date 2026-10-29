# job-agent

A personal AI agent that reads my recruiting email across several inboxes and keeps a reliable record of every opportunity. For each application it tracks status, deadlines, recruiter commitments and follow-ups, and it drafts replies that are sent only with explicit approval.

Recruiting is the first domain of a broader personal-agent platform: a shared core for memory, permissions, approvals and notifications, with domain modules plugged in.

## Design principles

- **Evaluated, not vibe-checked.** Every model-driven component is measured against a hand-labeled set of real emails before and after each change.
- **Pipeline where possible, agent where necessary.** Classification and extraction run as a deterministic pipeline (one structured model call per email). The agent loop is reserved for open-ended, multi-step requests.
- **No external action without approval.** Reading is automatic. Sending email or changing a calendar requires per-action confirmation, enforced in code rather than in the prompt.
- **Email is untrusted input.** Message content can inform the agent but never instruct it. Prompt-injection cases are part of the eval set.
- **Provenance on every fact.** Each record field is marked as stated in a message, inferred, or missing, and the agent never fills gaps with guesses.
- **Least privilege.** OAuth scopes start read-only and widen only when a feature requires it.

## Architecture

| Layer | Language | Responsibility |
| --- | --- | --- |
| Sync | TypeScript | Gmail OAuth (multiple inboxes, PKCE), resumable message sync, MIME parsing |
| Storage | SQLite | Raw messages, labels, and (next) opportunities, applications, events, commitments |
| Data & evals | Python | Labeling tool, eval harness, prompt experiments |
| Agent | TypeScript | Chat agent on the Claude Agent SDK with read tools and approval-gated actions (planned) |

## Status

- [x] Multi-inbox Gmail OAuth with read-only scope
- [x] Resumable 3-month sync with rate-limit backoff and incremental-sync cursor
- [x] MIME body extraction (plain/HTML fallback, attachment skipping), unit tested
- [x] Labeling tool with stratified sampling for an unbiased eval set
- [ ] Email classification and field extraction, with per-category accuracy
- [ ] Entity resolution: linking emails to the right application, deduplicated across inboxes
- [ ] Chat agent with traced tool calls and a question-level eval suite
- [ ] Drafting, calendar availability, approval-gated sending
- [ ] Daily digest and review queue for low-confidence updates

## Evaluation methodology

The eval set is about 100 emails sampled from my own inboxes:

- **70% keyword-matched** candidates, capped at 4 per sender domain, so a single job board can't dominate.
- **30% random** from everything the keyword filter rejected. This measures the filter's blind spots instead of inheriting them, for example a recruiter writing "would love to chat about an opening."

Each email gets one of 11 categories plus an application key (`Company | Role`). The key is what distinguishes, say, three separate applications at the same company. Labels are about the email's purpose, not the application's overall state. "Needs follow-up" is derived from state over time, never labeled per email.

## Setup

Requires Node 22+ and Python 3.12+.

1. In Google Cloud Console, create a project and enable the **Gmail API**.
2. Configure the OAuth consent screen as **External**, and add each inbox as a test user.
3. Create an OAuth client of type **Desktop app**, and save its JSON as `credentials.json` in the repo root.

```bash
npm install
npm run auth                  # once per inbox
npm run ingest                # last 3 months from all connected inboxes
python3 py/label.py sample    # build the eval sample
python3 py/label.py           # label it
python3 py/label.py stats     # coverage report
```

In Testing mode, Google revokes refresh tokens after 7 days. The sync detects this per inbox and prompts a re-auth.

`credentials.json`, `tokens/` and `data/` are git-ignored, so no credentials or email content is committed.

## Development

```bash
npm test           # unit tests
npm run typecheck
```

## Layout

```
src/
  auth.ts           OAuth loopback flow with PKCE, one inbox per run
  google.ts         client secrets, token storage, refresh handling
  ingest.ts         concurrent, resumable message sync
  db.ts             SQLite schema
  gmail/parse.ts    MIME tree -> clean text (+ tests)
py/
  label.py          eval-set sampling, labeling CLI, coverage stats
```
