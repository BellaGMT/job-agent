# job-agent

Personal agent, recruiting domain first. TypeScript for the product (sync, database, chat agent, UI), Python for data work (labeling, evals). Both share `data/agent.db`.

## Day 1: get your mail in, label 100 emails

### 1. Google Cloud setup (once, ~10 min)

1. In [Google Cloud Console](https://console.cloud.google.com), create a project.
2. APIs & Services → Library → **Gmail API** → Enable.
3. OAuth consent screen (Google Auth Platform): user type **External**, then add **every inbox you'll connect** as a test user.
4. Clients → Create client → **Desktop app** → download the JSON and save it as `credentials.json` in this folder.

The app stays in Testing mode, and Google revokes Testing-mode refresh tokens after 7 days. When the sync says an inbox's access expired, run `npm run auth` again for that inbox.

### 2. Connect inboxes and sync

```bash
npm install
npm run auth        # once per inbox; pick a different account each time
npm run ingest      # last 3 months from every connected inbox; safe to re-run
```

### 3. Label

```bash
python3 py/label.py sample   # picks ~100: 70% keyword matches, 30% random
python3 py/label.py          # label; resumable, q to quit
python3 py/label.py stats    # check coverage
```

**Labeling rules.** Consistent labels matter more than labeling a lot.

- Label the purpose of **this email**, not the application's overall status.
- `interview_invite` is the first ask to interview. `interview_scheduling` is logistics for an interview already agreed.
- `recruiter_outreach` is first contact that isn't about something you applied to.
- A LinkedIn or Indeed digest is `job_alert`, even if it names a company you applied to.
- Application key: the same role always gets exactly the same key. If the role is unknown, use `Company | ?`. For an agency recruiter, use the client company if it's named, otherwise `Agency | ?`.
- Unsure? Pick your best answer and add `!` for a note. Those notes are the hardest cases for the classifier.

Done when `stats` shows ~100 labeled and every category has at least a few examples. Rare ones like `offer` may need real hunting: search your mail and add them with `--force` re-sampling, or wait for Day 2's tooling.

### Concepts to understand today

- **Labels before prompts.** Without ground truth, "the classifier works" is a feeling. From Day 2 on, every change gets a number.
- **The random bucket.** The keyword pre-filter has blind spots: a recruiter writing "would love to chat about an opening" matches nothing. Drawing 30% at random lets you measure what it misses, so the classifier doesn't inherit its blind spots.
- **History id captured before listing** (`ingest.ts`). Mail that arrives during a sync lands in the next incremental sync instead of falling into a gap.
- **`References` header.** Gmail thread ids are per inbox. The RFC headers chain one conversation across inboxes, and Day 3's dedup relies on them.

**Exercise (Python + SQL):** write `py/explore.py` that prints your 20 most frequent sender domains and how many of their emails match `KEYWORDS`. Then decide whether any domain should be added to the pre-filter or capped harder.
