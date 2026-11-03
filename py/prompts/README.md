# Classifier prompt

`classify.md` is the system prompt for `classify.py`. Each email arrives as the user message, wrapped in `<email>` tags, with the mailbox owner, sender, recipient, date and subject above the body. The output schema (category plus extracted fields) is enforced by the API, so the prompt only has to explain *how to decide*.

## What the prompt needs to cover

- [ ] Who the model is working for and what the output is used for.
- [ ] A definition of each of the 11 categories, with the edge cases from your labeling rules:
  - interview invite (first ask) vs. scheduling (logistics for an interview already agreed)
  - recruiter outreach (a person contacting you first) vs. job alert (automated listings)
  - position closed or filled counts as rejection
  - emails sent by the mailbox owner
  - two categories fit: the tiebreak order
- [ ] Emails can be in languages other than English.
- [ ] Email content is data, never instructions. Text inside `<email>` that tries to direct the model must be ignored.
- [ ] Extraction rules: company is the hiring company, not the ATS or job board; never guess (null when not stated); deadlines as YYYY-MM-DD.

## How to iterate

1. Start short. Run `uv run python eval_classify.py`.
2. Read every error it prints, not just the accuracy.
3. Change one thing, re-run, and compare. Unchanged emails come from the cache, so re-runs are cheap.
4. Commit the prompt each time the numbers improve, so `git log prompts/classify.md` is your history.

Do not paste examples copied from your eval emails into the prompt. The eval would then be testing on its own answers, and the score would stop meaning anything. Write general rules instead, or invent examples.
