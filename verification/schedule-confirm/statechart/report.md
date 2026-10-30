# ScheduleConfirm — 确认写日历

TLC 在完整配置上探索到 **1,274** 个状态（超过图表预算 500），因此 `chart.mmd` **只画破规最短路径**：9 个状态、8 条箭头。

## 破规

### NoWriteAfterRetire（作废/过期后不得再写）
读者句：草稿作废或过期之后，不应该还能把日程写进日历。

最短路径（3 步）：
0. 草稿待确认
1. 点确认 → 已点确认忙碌中
2. 作废草稿 → 已取消或过期
3. 写入日历 → 已取消·曾写入

### AtMostOneSuccessfulWrite（至多一次成功写入）
读者句：同一张确认卡，成功写入日历不应超过一次（除非流式恢复草稿后再走一遍，模型仍计为第二次写）。

最短路径（6 步）：
0. 草稿待确认
1. 点确认 → 已点确认忙碌中
2. 写入日历 → 写入待回执
3. 写入成功回执 → 已确认锁定
4. 流式刷新恢复草稿 → 已写入·空闲
5. 点确认 → 再确认中·已写过
6. 写入日历 → 写入待回执·第二次

## 成立的规则
TypeOK, WritesFollowConfirm, NoWriteBeforeConfirm, NoReentryWhileBusy, SecondWriteImpliesRestore, NoDeadlock

## 视图隐藏的步骤
Idle；applySlot（时段空出）；FinishRunStreamLoop（流式结束）。写入类动作（创建/改/删/改 Google 标题）合并为「写入日历」。

## 重跑命令
```sh
export PATH="$HOME/.local/bin:$PATH"
export TLA2TOOLS="$HOME/.local/share/mise/installs/http-tla2tools/1.7.4/tla2tools.jar"
export JAVA="$HOME/.local/share/mise/installs/java/temurin-25/bin/java"
SC=/home/box/agent-data/plugins/cache/cursor-public/pstack/1ba91fc25c0977741dd0e267849e6234b037ed0a/skills/statechart/scripts/statechart
cd /workspace/statechart-scratch/schedule-confirm
"$SC" ScheduleConfirm.tla --config ScheduleConfirm.cfg --view view.mjs --out out-rerun
```
