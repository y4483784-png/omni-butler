export const title = "确认凭证";

export const state = (v) => {
  if (v.writes > 0 || v.claim === "written") return "已经写入";
  if (v.claim === "cancelled") {
    const stopped = v.g1 === "stopped" || v.g2 === "stopped";
    const completed = v.g1 === "completed" || v.g2 === "completed";
    if (stopped && completed) return "停止并且做完后失效";
    if (completed) return "做完后失效";
    if (stopped) return "停止后失效";
    return "确认已失效";
  }
  if (v.claim === "open" && !v.match) return "参数已改";
  const live1 = Boolean(v.link1) && v.g1 === "live";
  const live2 = Boolean(v.link2) && v.g2 === "live";
  if (v.claim === "open" && v.link1 && v.link2 && live1 !== live2) return "还剩一代";
  if (v.claim === "open") return "待确认";
  if (v.g1 === "live" || v.g2 === "live") return "任务进行中";
  return "还没有确认";
};

export const group = (v) => {
  const label = state(v);
  if (label === "已经写入") return "已写入";
  if (label.endsWith("失效")) return "已失效";
  if (label === "待确认" || label === "还剩一代" || label === "参数已改") return "待确认";
  return "未签发";
};

export const colors = {
  未签发: "blue",
  待确认: "orange",
  已写入: "teal",
  已失效: "tan",
};

const stepLabel = {
  Start1: "前一代开始",
  Start2: "后一代开始",
  Stop1: "停止前一代",
  Stop2: "停止后一代",
  Complete1: "前一代做完",
  Complete2: "后一代做完",
  Edit: "改了时间",
  Restore: "改回原来的时间",
  Consume: "写入日程",
  Foreign: "别人来用这张确认",
};

function endStep(_action, to) {
  const next = state(to);
  if (next === "还剩一代") return "结束一代后另一代还能写";
  if (next === "做完后失效") return "做完后确认失效";
  if (next === "停止后失效") return "停止后确认失效";
  if (next === "停止并且做完后失效") return "结束后确认失效";
  if (next === "还没有确认") return "这一代结束";
  return "这一代结束";
}

export const step = (action, from, to) => {
  if (action === "Idle") return null;
  if (action === "Issue") {
    if (from.claim === "open") return "另一代挂上同一张确认";
    if (from.claim === "cancelled") return "失效后重新签发";
    return "签发确认";
  }
  if (action === "Restore") {
    return state(to) === "还剩一代" ? "改回原来的时间后仍只剩一代" : "改回原来的时间";
  }
  if (/^(Stop|Complete)[12]$/.test(action)) return endStep(action, to);
  return stepLabel[action] ?? null;
};
