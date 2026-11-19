export const title = "这一轮的上网";

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
  if (v.zhipu && v.netField) return "两条搜索叠在一起";
  if (v.zhipu) return "又去调用了另一路搜索";
  if (v.citation && !v.hasUrl) return "没有链接却写出了引用";
  if (v.stopped && v.phase === "done") return "停止后没有再发送";
  if (v.phase === "done") return "这一轮结束";
  if (v.stopped) return "已经停止";
  if (v.phase === "card") return "确认卡已出示";
  if (v.searchCompleted && v.citation) return "有链接才展示引用";
  if (v.searchCompleted) return "搜索结束但没有链接";
  if (v.phase === "searched") return "没有另发搜索";
  if (v.phase === "built" && v.netField) return "请求附带网页搜索";
  if (v.phase === "built") return "请求没有附带搜索";
  if (v.phase === "queued") return "路由已记下";
  return "这一轮开始";
}

export function group(v) {
  if (v.sendsAfterStop || v.calendarWritten || v.zhipu || (v.citation && !v.hasUrl)) return "违规";
  if (v.stopped || v.phase === "done") return "结束";
  if (v.card || v.phase === "card") return "确认卡";
  if (v.searchCompleted || v.phase === "searched") return "搜索";
  return "请求";
}

export function step(action) {
  const labels = {
    Queue: "记下这一轮要哪些工具",
    Build: "组装回答请求",
    ClientSearch: "客户端搜索不再发出",
    NoteResult: "记下搜索是否带来链接",
    ShowCard: "出示确认卡",
    Stop: "人按下停止",
    LaterSend: "准备下一次发送",
    Done: null,
  };
  return labels[action] ?? action;
}
