# job-agent

[![CI](https://github.com/BellaGMT/job-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/BellaGMT/job-agent/actions/workflows/ci.yml)

A personal AI agent that reads my recruiting email across several Gmail inboxes and keeps a reliable record of every opportunity. For each application it will track status, deadlines, recruiter commitments and follow-ups, and draft replies that are sent only with explicit approval.

Recruiting is the first domain of a broader personal-agent platform: a shared core for memory, permissions, approvals and notifications, with domain modules plugged in.

## Capabilities

Working today:

- Connect several Gmail inboxes with read-only OAuth (loopback flow with PKCE and `state`).
- Sync the last N months of mail into SQLite: concurrent, rate-limit aware, and safe to interrupt and re-run.
- Extract clean text from MIME email (plain/HTML selection, attachments skipped, entities decoded).
- Build an unbiased evaluation set with a labeling CLI: stratified sampling, targeted additions for rare cases, label fixes and coverage stats.
- Classify each email and extract its fields (company, role, requisition id, deadline, requested actions) with one schema-constrained model call, cached on disk.
- Evaluate the classifier against the labeled set: accuracy, per-category precision and recall, confusions, recruiting recall, and accuracy per sampling bucket.

Next: tuning the classifier prompt against the eval, then linking emails to applications, a chat agent, approval-gated drafting and a daily digest (see [Status](#status)).

## Design principles

- **Evaluated, not vibe-checked.** Every model-driven component is measured against a hand-labeled set of real emails before and after each change.
- **Pipeline where possible, agent where necessary.** Classification and extraction run as a pipeline (one structured model call per email). The agent loop is reserved for open-ended, multi-step requests.
- **No external action without approval.** Reading is automatic. Sending email or changing a calendar requires per-action confirmation, enforced in code rather than in the prompt.
- **Email is untrusted input.** Message content can inform the model but never instruct it: it is wrapped in tags, defanged, and the classifier has no tools.
- **Missing, not invented.** Extracted fields are nullable; the model reports "not stated" instead of guessing.
- **Least privilege.** OAuth scopes start read-only and widen only when a feature requires it.

## Architecture

```
 Gmail API ──► src/ (TypeScript)                    py/ (Python)
               auth.ts    OAuth, one inbox per run   label.py          labeling CLI
               ingest.ts  CLI ─► gmail/sync.ts       data.py           eval-set loader
                          retry, pacing, parsing     classify.py ─────► Claude API
                          store.ts ──┐               eval_classify.py  metrics + report
                                     ▼                     ▲
                              data/agent.db (SQLite) ──────┘
                              schema: schema.sql (shared)
```

| Layer | Language | Responsibility |
| --- | --- | --- |
| Sync | TypeScript | Gmail OAuth, resumable sync, MIME parsing, persistence |
| Storage | SQLite | Raw messages, accounts and sync cursors, labels, the eval queue |
| Data & evals | Python | Labeling, classification, evaluation |
| Agent | TypeScript | Chat agent with read tools and approval-gated actions (planned) |

Decisions worth knowing before changing code:

- **One schema, two languages.** `schema.sql` is the only definition of the database. `src/db.ts` and `py/db.py` both apply it on startup, so the two sides can't drift. Every statement is idempotent.
- **Sync correctness.** The Gmail history cursor is captured *before* listing (mail arriving mid-sync lands after it) and saved only *after* every message is stored. Messages deleted mid-sync are skipped; malformed ones are reported, not stored, and keep the cursor from advancing so the next run retries them. Inserts are idempotent, so interrupting and re-running is always safe.
- **Rate limits.** Gmail reports quota exhaustion as 429 or as a 403 with a rate-limit reason. Requests are paced; a quota error pauses every worker for that inbox and halves the pace.
- **Dependency injection at the network edge.** The sync takes a `MailSource` and the classifier takes its API call as a parameter, so tests run without network, credentials or cost.
- **Thread context.** The classifier sees the email plus the latest earlier messages of its Gmail thread (same inbox, each truncated), so a short reply or a calendar invite is read in context. The category always describes the newest email.
- **Classifier cache.** Results are keyed by model, prompt and the input exactly as the model sees it, thread context included. Changing the prompt, a new message in the thread, or re-syncing an email whose cleaned text changed re-classifies it; re-running unchanged costs nothing.

## Status

- [x] Multi-inbox Gmail OAuth with read-only scope
- [x] Resumable sync with rate-limit handling and a crash-safe cursor
- [x] MIME body extraction, unit tested
- [x] Labeling tool with stratified sampling and targeted additions
- [x] Structured-output classification and extraction with a disk cache
- [x] Classifier evaluation metrics
- [ ] Classifier prompt tuned against the eval set (in progress)
- [ ] Linking emails to the right application, deduplicated across inboxes
- [ ] Chat agent with traced tool calls and a question-level eval suite
- [ ] Drafting, calendar availability, approval-gated sending
- [ ] Daily digest and review queue for low-confidence updates

## Evaluation methodology

The eval set is about 140 emails from my own inboxes:

- **Keyword-matched** candidates, capped at 4 per sender domain so one job board can't dominate.
- **Random** emails from everything the keyword filter rejected. They measure the filter's blind spots instead of inheriting them, and keep realistic negatives in the set.
- **Targeted** hand-picked rare cases (scheduling, outreach, non-English confirmations), reported separately because they are not a random sample.

Each email gets one of 11 categories plus an application key (`Company | Role`), which distinguishes separate applications at the same company. The headline metric is **recruiting recall**: a recruiting email filed under the wrong recruiting category can be corrected later, but one filed as noise is invisible to the agent.

## Prerequisites

- Node.js 22 or newer (see `.nvmrc`)
- Python 3.11 or newer and [uv](https://docs.astral.sh/uv/)
- A Google Cloud project with the Gmail API enabled
- An Anthropic API key, only for classification

## Setup

1. In Google Cloud Console, create a project and enable the **Gmail API**.
2. Configure the OAuth consent screen as **External** and add each inbox as a test user.
3. Create an OAuth client of type **Desktop app** and save its JSON as `credentials.json` in the repo root.
4. Install dependencies:

```bash
npm ci
cd py && uv sync
```

### Configuration

| Setting | Where | Purpose |
| --- | --- | --- |
| `credentials.json` | repo root | Google OAuth client (Desktop app) |
| `tokens/<inbox>.json` | created by `npm run auth` | Per-inbox OAuth tokens, owner-only file mode |
| `ANTHROPIC_API_KEY` | repo-root `.env` | Classification calls |
| `CLASSIFY_MODEL` | environment (optional) | Classifier model, default `claude-haiku-5-5` |
| `AGENT_DB` | environment (optional) | Database path, default `data/agent.db` |

`credentials.json`, `tokens/`, `.env` and `data/` are git-ignored: no credentials or email content is ever committed.

## Usage

```bash
npm run auth                      # connect an inbox (once per inbox)
npm run ingest                    # sync the last 3 months from every inbox
npm run ingest -- --months 6 --rate 5 --account you@gmail.com
npm run ingest -- --refresh       # re-download stored messages too, e.g. after a parser fix (labels are kept)

cd py
uv run python label.py sample     # build the eval sample (once)
uv run python label.py            # label it; resumable, q to quit
uv run python label.py stats      # coverage report
uv run python label.py search offer   # add rare cases to the queue
uv run python label.py show <gmail_id>  # read one email in full (ids are in the eval report)
uv run python eval_classify.py    # classify the eval set and report metrics
```

## Development

Run everything CI runs before pushing:

```bash
npm run check                     # typecheck + Biome lint/format check + tests
npm run lint:fix                  # apply Biome formatting and safe fixes

cd py
uv run ruff format                # format
uv run ruff check                 # lint
uv run mypy                       # strict type check, tests included
uv run pytest                     # tests
```

TypeScript is linted and formatted by [Biome](https://biomejs.dev); Python by [Ruff](https://docs.astral.sh/ruff/). Both are pinned dev dependencies. Type checking is strict on both sides (`tsc` with `noUncheckedIndexedAccess`, `mypy --strict`).

### Continuous integration

`.github/workflows/ci.yml` runs on pushes and pull requests to `main`, and on demand. It has two jobs:

- **TypeScript:** `npm ci`, type check, Biome, tests.
- **Python:** `uv sync --locked`, Ruff format and lint, mypy, tests.

Every check runs even if an earlier one fails, so a single run reports all problems; Biome and Ruff findings appear as inline annotations on the diff. CI uses no secrets: Gmail and Claude are replaced by fakes, and databases are temporary. Actions are pinned to commit SHAs and the token is read-only.

### Contributing

- Read `CLAUDE.md`: it holds the engineering standards and project conventions, for people and AI assistants alike.
- Keep changes small and focused; one concern per commit, with an imperative subject line.
- New behavior and bug fixes come with tests. External services are faked; never call real APIs in tests.
- Validate external data where it enters (`unknown` plus type guards in TypeScript, Pydantic in Python).
- Run the checks above, and review `git diff --staged` before committing.

## Limitations

- Single-user, local tool: no server, no multi-tenant isolation.
- In Google's Testing mode, refresh tokens expire after 7 days; `npm run ingest` names the inbox to re-authorize.
- Only full syncs exist so far; the stored history cursor is ready for incremental sync.
- Message bodies are decoded as UTF-8 regardless of the declared charset, and attachments are ignored.
- The account-to-domain grouping used for sampling is naive (`x.co.uk` groups as `co.uk`).
- Prompt-injection defenses reduce risk; they don't eliminate it.

## Layout

```
schema.sql            the database schema, shared by both languages
src/
  auth.ts             CLI: connect one inbox
  ingest.ts           CLI: sync every connected inbox
  oauth-callback.ts   loopback server for the OAuth redirect
  google.ts           client secrets, token storage and validation
  cli-args.ts         ingest argument parsing
  db.ts, store.ts     opening the database; all sync SQL
  pool.ts             bounded-concurrency worker pool
  guards.ts           type guards for untrusted values
  gmail/
    sync.ts           full sync and the Gmail API adapter
    parse.ts          MIME message -> validated, flattened fields
    retry.ts          retry policy and shared rate-limit pause
    pacer.ts          adaptive request pacing
py/
  db.py               connection helper, applies schema.sql
  label.py            labeling CLI and sampling
  explore.py          sender-domain report
  data.py             typed eval-set loader
  classify.py         classification and extraction
  eval_classify.py    evaluation metrics and report
  prompts/            classifier system prompt
  tests/
.github/workflows/    CI
```
