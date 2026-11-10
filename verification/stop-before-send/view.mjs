export const title = "停止会不会在请求发出前拦住";

export const colors = {
  进行中: "blue",
  已停止: "tan",
  已结束: "teal",
};

export function state(v) {
  if (v.calendar === 0) return "日历被删掉了";
  if (v.bubble === "late") return "迟到文字写进了气泡";
  if (v.closed && v.memory && v.stopped) return "停止后仍写了记忆";
  if (v.closed && v.stopped) return "停止后结束";
  if (v.closed) return "正常结束";
  if (v.stopped && (v.request === "flying" || v.request === "back") && !v.startedBeforeStop) {
    return "停止后仍发出了请求";
  }
  if (v.stopped && v.request === "back") return "停止后丢掉迟到文字";
  if (v.stopped && v.request === "flying") return "请求已发出后才停止";
  if (v.stopped) return "停止后不再发出";
  if (v.bubble === "kept") return "回答已写入气泡";
  if (v.request === "back") return "模型已经返回";
  if (v.request === "flying") return "请求正在发出";
  if (v.request === "queued") return "请求还在排队";
  return "这一代刚开始";
}

export function group(v) {
  if (v.closed) return "已结束";
  if (v.stopped) return "已停止";
  return "进行中";
}

export function step(action) {
  const labels = {
    Queue: "排入一个模型请求",
    Create: "发出模型请求",
    Stop: "停止这一代",
    SendWhileStopped: "停止后仍发出请求",
    Return: "模型返回",
    Keep: "写入当前气泡",
    AppendLate: "把迟到文字写入气泡",
    Remember: "提取长期记忆",
    CloseStopped: "结束且不写记忆",
    RememberStopped: "停止后仍写记忆",
    DropCalendar: "删掉已确认的日历",
    Done: null,
  };
  return Object.prototype.hasOwnProperty.call(labels, action) ? labels[action] : action;
}
