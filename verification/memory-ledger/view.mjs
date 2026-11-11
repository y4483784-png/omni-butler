export const title = "记忆账本和脉搏";

export const colors = {
  进行: "blue",
  账本: "teal",
  待确认: "yellow",
  提醒: "orange",
  停止: "tan",
};

export function state(v) {
  if (v.memoryClaims > 0) return "低把握却开了确认卡";
  if (v.pulseClaims > 1) return "同一指纹开了第二张卡";
  if (v.personRows === 1 && v.called && v.live === "turn" && !v.pending) return "人写的值被盖掉";
  if (v.pending && v.live === "person") return "两边都留着";
  if (v.pending) return "只记了待确认账本";
  if (v.pulses > 1 && v.pulseClaims === 1) return "第二次提醒仍是一张卡";
  if (v.live === "person" && v.pulseClaims === 1) return "聊天里有一张确认卡";
  if (v.live === "turn") return "新事实写进了活档案";
  if (v.live === "person") return "人写下了活档案";
  if (v.status === "stopped") return "这一代已经停止";
  if (v.status === "waiting") return "这一代正在等人";
  if (v.status === "completed") return "这一代已经完成";
  return "这一代还在进行";
}

export function group(v) {
  if (v.status === "stopped" || v.status === "waiting") return "停止";
  if (v.pulseClaims > 0 || v.pulses > 0) return "提醒";
  if (v.pending) return "待确认";
  if (v.called || v.live !== "empty") return "账本";
  return "进行";
}

export function step(action, from, to) {
  const name = String(action).split("(")[0];
  if (name === "Done") return null;
  if (name === "HighRemember") {
    return to.pending && to.live === "person" ? "两边都留下" : "把这条写进活档案";
  }
  if (name === "Pulse" && from.pulses > 0 && to.pulseClaims === from.pulseClaims) {
    return "同一指纹不再开卡";
  }
  const labels = {
    PersonWrites: "人写下一条记忆",
    Complete: "这一代做完了",
    Stop: "这一代被停掉",
    Wait: "这一代还在等人",
    LowRemember: "把握不够只记账本",
    Pulse: "到点推一张确认卡",
    Close: "这一轮收起",
  };
  return labels[name] ?? null;
}
