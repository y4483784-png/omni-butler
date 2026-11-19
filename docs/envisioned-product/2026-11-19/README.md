# 2026-11-19 target revision

This directory is the next design after the fourteen knives that already landed on `release` `d7710d687d147aeeda502d412454ab8a3829af66`. It does not replace `../envisioned-product-plan.md`, the reviewed flowcharts, or the earlier OSS maps. Those files stay as the record of the work that merged through pull request 92.

Read `plan.md` for the units an implementer follows. Read `mapping.md` for the target, the current code, the gap, and the unit. Read `review/synthesis.md` for the same-model review, the grafts, and the dissent. The candidate files and the Gemini notes are evidence. They are not a second plan.

This directory is not permission to implement, merge, or deploy.

The first spine unit is `talk`. `picture`, `mail`, `calendar`, `errand`, and `devices` may branch from `release` beside it. `loop` waits for `talk` and `picture`. `search` waits for `loop`. `watch` waits for `talk`. `resume` waits for `talk`, `loop`, and `watch`. `desktop` waits for `talk`, `loop`, and `devices`.
