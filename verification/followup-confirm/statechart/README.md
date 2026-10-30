# statechart: followup-confirm（会后确认写回）

Artifacts: `view.mjs`, `chart.mmd`, `report.md`, `FollowupConfirm.tla`, `FollowupConfirm.cfg`

Durable copies:
- bot store: `/cursor/stores/store-92b58913-5d74-4c7d-911a-0080f41eb53a/statechart/followup-confirm/`
- workspace: `/workspace/statechart-artifacts/followup-confirm/`

All invariants hold (58 states, 9 chart states / 14 arrows).

## Re-run
```sh
export PATH="$HOME/.local/bin:$PATH"
export TLA2TOOLS="$HOME/.local/share/mise/installs/http-tla2tools/1.7.4/tla2tools.jar"
export JAVA="$HOME/.local/share/mise/installs/java/temurin-25/bin/java"
SC=/home/box/agent-data/plugins/cache/cursor-public/pstack/1ba91fc25c0977741dd0e267849e6234b037ed0a/skills/statechart/scripts/statechart
cd /workspace/statechart-scratch/followup-confirm
"$SC" FollowupConfirm.tla --config FollowupConfirm.cfg --view view.mjs --out out
```
