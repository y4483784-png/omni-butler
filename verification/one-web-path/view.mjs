export const title = "这一轮的上网路径";

export const colors = {
  请求: "blue",
  搜索: "teal",
  确认卡: "orange",
  结束: "tan",
  违规: "pink",
};

export function state(v) {
  if (v.sendsAfterStop) return "停止后仍发送";
  if (v.calendarWritten) return "确认卡写出了日历";
  if (v.netField && v.zhipu) return "联网字段和搜索叠在一起";
  if (v.netField) return "请求带上了联网字段";
  if (v.phase === "done" && v.stopped) return "停止后没有再发送";
  if (v.phase === "done") return "这一轮结束";
  if (v.phase === "card" && v.stopped) return "已停止且确认卡还在";
  if (v.card) return "确认卡已出示";
  if (v.phase === "searched" && v.zhipu && v.stopped) return "已停止且搜索已经发出";
  if (v.phase === "searched" && v.zhipu) return "已经调用网页搜索";
  if (v.phase === "searched" && v.stopped) return "已停止且这一轮没有搜索";
  if (v.phase === "searched") return "这一轮没有搜索";
  if (v.phase === "built" && v.stopped) return "已停止且请求只有思考开关";
  if (v.phase === "built") return "请求只有思考开关";
  if (v.phase === "queued") return "路由已经记下要不要搜索";
  return "这一轮开始";
}

export function group(v) {
  if (v.sendsAfterStop || v.calendarWritten || (v.netField && v.zhipu) || v.netField) return "违规";
  if (v.stopped || v.phase === "done") return "结束";
  if (v.card) return "确认卡";
  if (v.zhipu || v.phase === "searched") return "搜索";
  return "请求";
}

export function step(action) {
  const labels = {
    Queue: "记下路由结果",
    Build: "组装模型请求",
    Search: "调用现有搜索",
    SkipSearch: "不调用搜索",
    ShowCard: "出示确认卡",
    Stop: "人按下停止",
    LaterSend: "准备下一次发送",
    Done: null,
  };
  return labels[action] ?? action;
}
