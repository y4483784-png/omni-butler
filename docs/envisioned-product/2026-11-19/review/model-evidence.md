# Model evidence

The operator changed the planning seats during this run. This file records the calls that actually returned. It does not claim a cross-model review.

## Calls that produced files

| When | Slug requested | Agent | Output |
| --- | --- | --- | --- |
| First candidate | `grok-4.7-high` | `bc-4dbf3bd0-8a57-5172-a1df-71fe955d3f5d` | `review/candidate-grok-high/` |
| Second candidate | `grok-4.7-xhigh-fast` | `bc-fcff8fd3-6be0-51e4-b222-39309d29db46` | `review/candidate-grok-xhigh/` and the base of `../plan.md` |
| Review A | `grok-4.7-xhigh-fast` | `bc-ed9a78ef-7047-580b-86a6-11d2f9bd7590` | `review/review-grok-a/review.md` |
| Review B | `grok-4.7-xhigh-fast` | `bc-2153726a-b02a-5c33-84fd-3dd7d75c21a9` | `review/review-grok-b/review.md` |

The Task tool accepted those slugs. The first line of each rationale or review file is the slug that agent was told to write. That is the invocation record this run can show. It is not a vendor attestation beyond the tool accepting the slug.

## Calls that did not produce a plan

| When | Slug requested | Agent | What happened |
| --- | --- | --- | --- |
| Gemini candidate | `gemini-4-neon-high` | `bc-3ceb508b-ae2d-55a7-a35e-8b144cdfeeb1` | `resource_exhausted` after mapping, citations, rationale, and open questions. No `plan.md`. Notes are in `review/historical-gemini/`. |
| Gemini retry | `gemini-4-neon-high` | `bc-89428bea-b017-5ad6-8355-db2428877b5f` | Was `RUNNING` when this planning run tried to stop it. That stop was rejected because the child was running. No kill tool was available to this run. Its output directory was made read-only. On 2026-11-19 a later status read showed `IDLE`. The parent reported cancelling it and seeing it finished around 20:00 SGT. This run's status index did not show `isKilled`, so the cancel action is the parent's report. Late files from that child are not the plan. |
| Fable candidate | `claude-fable-5-2-high` | `bc-b16e5dcf-af1a-5cf9-b6c3-52b83c1d91a7` | Stopped with "You've hit your usage limit." `/tmp/arena-plan/candidate-fable/` was empty. Not retried on another model. |

After the Fable failure the operator said to finish with Grok, and set every pstack role to Grok 4.7 extra high with fast on. The supported Task slug is `grok-4.7-xhigh-fast`. `.cursor/rules/pstack-models.mdc` records that for later roles. This review did not use a second vendor after that instruction.

## Parent checks

The parent opened the four DeepSeek guides listed in `synthesis.md` and the release files cited in the review findings that the plan now depends on, including `task_run.py` lines 15-73, `event_inbox.py` line 35, and `verification/errand-place/check.py` lines 81 and 108. The parent did not re-run the product.
