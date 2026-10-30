# FollowupConfirm — 会后确认写回

TLC 探索到 **58** 个状态、148 步。图表画出 **9** 个状态、**14** 条箭头。

## 全部成立
TypeOK, OneRequestPerLoop, AtMostOneEvent, NoWriteBeforeConfirm, NeedsPasteWritesNothing, NoDeadlock

无破规路径。

## 视图隐藏的步骤
Idle；Untick（取消勾选，减少往返噪声）。PostFollowAction 与 ConflictCheck 合并标为「冲突检查」。确认管线中间态合并为「确认中」，写入后合并为「写入待回执」。

## 重跑命令
```sh
export PATH="$HOME/.local/bin:$PATH"
export TLA2TOOLS="$HOME/.local/share/mise/installs/http-tla2tools/1.7.4/tla2tools.jar"
export JAVA="$HOME/.local/share/mise/installs/java/temurin-25/bin/java"
SC=/home/box/agent-data/plugins/cache/cursor-public/pstack/1ba91fc25c0977741dd0e267849e6234b037ed0a/skills/statechart/scripts/statechart
cd /workspace/statechart-scratch/followup-confirm
"$SC" FollowupConfirm.tla --config FollowupConfirm.cfg --view view.mjs --out out-rerun
```
