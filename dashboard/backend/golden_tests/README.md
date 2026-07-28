# Golden regression set (New Vibe Test Phase 7, F.25)

Manual-only regression harness for the AI testing pipeline itself (not for
AEP_Dashboard's app). See `run_golden_set.py`'s module docstring for the
full rationale, when to run it, and what it does/doesn't do.

Quick start:

```
cd backend
python3 golden_tests/run_golden_set.py
```

Files:
- `golden_set.json` — fixture cases (currently TEMPLATE placeholders — see
  the file's `_comment` field before relying on this).
- `run_golden_set.py` — the runner. No pytest dependency (none exists in
  this repo); plain argparse script with a non-zero exit code on
  regression.
- `references/` — reference screenshots for UI cases (empty until real
  cases are added).

Not wired into CI. No scheduled trigger. Run it by hand after touching
`ai_runner.py`, `ai_eval.py`, `visual_judge.py`, or agent/judge prompt text.
