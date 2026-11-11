export const title = "只读看门狗";

export const colors = {
  进行: "blue",
  静默: "yellow",
  休眠: "teal",
  等待: "orange",
  完成: "tan",
  停止: "pink",
};

export function state(v) {
  if (v.claims > 0) return "又开了一张确认卡";
  if (v.newer === "stopped") return "新的一代也被停掉";
  if (v.other === "stopped") return "别人的任务被停掉";
  if (v.oldStatus === "stopped" && v.credit === 1) return "休眠仍被当成超时";
  if (v.oldStatus === "stopped" && v.parked) return "等人被当成卡住";
  if (v.oldStatus === "stopped" && v.finished) return "做完被改成停止";
  if (v.oldStatus === "stopped") return "静默的一代已停止";
  if (v.oldStatus === "waiting") return "正在等人";
  if (v.oldStatus === "completed") return "已经做完";
  if (v.credit === 1 && v.oldDue) return "休眠抵掉了静默";
  if (v.oldDue) return "静默已经够久";
  return "正在跑";
}

export function group(v) {
  if (v.claims > 0 || v.newer === "stopped" || v.other === "stopped") return "停止";
  if (v.oldStatus === "stopped") return "停止";
  if (v.oldStatus === "waiting") return "等待";
  if (v.oldStatus === "completed") return "完成";
  if (v.credit === 1 && v.oldDue) return "休眠";
  if (v.oldDue) return "静默";
  return "进行";
}

export function step(action, from, to) {
  const name = String(action).split("(")[0];
  if (name === "Done") return null;
  if (name === "MarkDue") return "静默已经够久";
  if (name === "Sleep") return "记下这一段休眠";
  if (name === "Wait") return "改成等人";
  if (name === "Finish") return "改成做完";
  if (name !== "Sweep") return null;
  if (to.claims > from.claims) return "又开了一张确认卡";
  if (to.newer !== from.newer) return "把新的一代也停掉";
  if (to.other !== from.other) return "把别人的任务停掉";
  if (to.oldStatus === "stopped" && from.credit === 1) return "把休眠当成超时";
  if (to.oldStatus === "stopped" && from.parked) return "把等人当成卡住";
  if (to.oldStatus === "stopped" && from.finished) return "把做完改成停止";
  if (to.oldStatus === "stopped" && from.oldStatus === "running") return "停止这一代";
  return null;
}
