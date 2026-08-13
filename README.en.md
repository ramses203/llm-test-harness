# LLM Exam & Grader

Make your AI **take an exam before** you trust it with real work.

```
python run_test.py --provider claude-cli --model claude-haiku-4-5

✅ [A-3] normal order
🟢 [A-4] normal order
      🟢 asked for confirmation instead of confirming (answer was in candidates)

========================================================
29 cases · 27 clean (93%)
🔴 fatal 0   🟠 risky 0   🟡 missed 0   🟢 harmless 2
========================================================
```

**The bottom line is the whole verdict.** Zero fatals → ship. One fatal → don't, no matter the score.

The story behind this repo (Korean series): [I gave my AI an exam — the exam author lost 5 times](https://velog.io/@ramses203/ai-exam-author-wrong-5-times)

---

## Why grade by severity instead of counting

"5 wrong out of 29" tells you nothing about whether to ship. **Which five** is everything.

Five typos are not the same accident as turning "please cancel my order" into a new order. So the grader assigns four severities based on one question — **can a human undo this?**

| Grade | Meaning | In the example domain |
|---|---|---|
| 🔴 fatal | Irreversible | Wrong goods leave on the truck |
| 🟠 risky | Confirmed something ambiguous | Right this time, fatal next time |
| 🟡 missed | Dropped something real | A human can still catch it |
| 🟢 harmless | Over-asked for confirmation | Just slower |

One principle: **a wrong confirmation is worse than a missed one.** The model is allowed — encouraged — to answer "needs human confirmation" on ambiguous cases.

## What's inside

The example pipeline turns messy Korean order messages ("hi, 5 boxes of 250 and 2 boxes of clear tape pls") into product codes.

- `data/cases.json` — 29 exam questions with answer keys. Composition is deliberately hostile: non-orders that look like orders, changes/cancellations, extreme abbreviations, typos, post-learning situations
- `data/item_master.csv` — 50 products **with planted traps**: two tape widths, five products starting with "250", mixed units. Clean data passes exams and fails production
- `run_test.py` — runner + severity grader (Claude API / claude-cli / Gemini)
- `judge.py` — an LLM judge that re-grades the same answer sheets with the same rubric, then diffs against the code grader. In our run it disagreed twice — **both times the judge was right and the grader code was wrong**
- `gen_cases.py` — LLM exam-author experiment: 50 generated questions, zero factual errors, but it never left the composition it was told. Inventing failure modes stayed a human job
- `comment_exam.py` — the same 5-step method transplanted to a different domain (YouTube comment triage), with shipped results: regex classifier 8/15 clean · 3 fatal vs LLM 12/15 clean · 0 fatal
- `serve.py + web/` — a small browser UI for eyeballing single messages

`rule_decidable` on each case marks whether the source data alone determines the answer. When model and answer key disagree, this field is the referee — it exists because a blog commenter pointed out that fixing answer keys "toward the model" is how answer keys quietly converge to model behavior.

## Getting started

```bash
# 1. Swap in your own domain
vi data/cases.json          # questions + answer key
vi data/item_master.csv     # reference data (plant traps!)
vi prompts/intake.md        # what you ask the model to do

# 2. Run
python run_test.py --provider claude-cli --model claude-haiku-4-5 --out out_haiku

# 3. Changed grading rules? Re-score without re-calling the model
python run_test.py --rescore --out out_haiku

# 4. Compare models
python compare.py out_haiku out_gemini
```

Suggested order for building your own exam:

```
1. Write down the irreversible accidents first
2. One test case per accident, minimum
3. Plant look-alikes in the reference data
4. Run the grader; only the fatal count matters
5. When something fails, suspect the answer key before the prompt
```

Step 5 is the important one. Building this, the AI was wrong once. My answer key was wrong three times, my grader twice.

## Scoreboard honesty

Fixing the grader retroactively changed already-published scores (28/29 → 27/29, fatals unchanged). Scores are a function of the grader — publish the grader version along with the score.

## License

MIT
