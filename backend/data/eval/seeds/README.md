# Frozen evaluation seeds

These JSONL files are the reviewed source of truth for user-facing evaluation
questions. The public `scripts/gen_*_eval.py` commands only validate, assign
stable IDs, and serialize them; they do not call an LLM or create paraphrases.

- `rag_queries.jsonl`: 400 retrieval questions (300 single-document, 60
  multi-document, 40 hard-negative).
- `yz_queries.jsonl`: 300 non-tabular YZ questions.
- `yz_attendance.jsonl`: 100 frozen, program-calculated attendance questions.
  Recalculate it with `python scripts/gen_yz_eval.py
  --refresh-attendance-seed` only on a machine that has
  `tests/YZ测试文档/测试用例.csv`.
- `routing_utterances.jsonl`: 400 intent/tool examples, including multi-turn,
  pending-calendar, and multi-tool cases.
- `grounding_cases.jsonl`: 120 questions with fixed KB references or frozen
  web/sandbox evidence.

Rules:

1. Edit a seed, never a generated `data/eval/*.jsonl` row by hand.
2. Do not add prefix/suffix loops merely to reach a row count.
3. Run `python scripts/lint_eval_datasets.py --card` after regeneration.
4. Gold labels describe the fixture/corpus truth and must not be changed to
   accommodate a model prediction.
