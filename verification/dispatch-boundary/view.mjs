export const title = "派活会不会自己动手";

export const colors = {
  派活: "blue",
  干活: "teal",
  已停止: "tan",
  已结束: "yellow",
};

export function state(v) {
  if (v.dispatchInvoked) return "派活调用了工具";
  if (v.dispatchClaimed) return "派活发布了凭据";
  if (v.dispatchWrote || (v.calendar === 1 && !v.claim)) return "派活写入了日历";
  if (v.invokedAfterStop) return "停止后仍调用了网关";
  if (v.phase === "replied" && v.calendar === 1) return "确认后写入了日历";
  if (v.phase === "replied" && v.claim) return "凭据已发出，回复已写出";
  if (v.phase === "replied") return "干活路径写出了回复";
  if (v.phase === "card") return "确认卡已给出";
  if (v.phase === "halted") return "这一代已停下";
  if (v.phase === "working" && v.stopped) return "干活途中已停止";
  if (v.phase === "working") return "干活进行中";
  if (v.phase === "assigned" && v.stopped) return "分配已记下但还没干活";
  if (v.phase === "assigned") return "这一回合已分配";
  if (v.stopped) return "停止时还没有分配";
  return "这一代刚开始";
}

export function group(v) {
  if (v.phase === "halted" || v.phase === "replied" || v.phase === "card") return "已结束";
  if (v.stopped) return "已停止";
  if (v.phase === "working") return "干活";
  return "派活";
}

export function step(action) {
  const labels = {
    Assign: "写下这一回合的分配",
    Stop: "停止这一代",
    EnterWork: "干活图从分配开始",
    WorkCutoff: "停止后不再调用网关",
    WorkCall: "干活路径调用工具",
    WorkAnswer: "干活路径写出回答",
    Publish: "干活返回后发布凭据",
    Confirm: "点击后写入日历",
    Done: null,
  };
  return Object.prototype.hasOwnProperty.call(labels, action) ? labels[action] : action;
}
