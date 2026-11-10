export const title = "一次确认会不会写第二次";

export const colors = {
  待确认: "blue",
  结果未知: "orange",
  已经写入: "teal",
  已停止: "tan",
};

export function state(v) {
  if (v.badExec) return "停止后仍执行了工具";
  if (v.blindPosts > 0) return "未知之后又发送了一次";
  if (v.stopped && v.claim === "unknown") return "已停止且结果未知";
  if (v.stopped && v.claim === "written") return "已停止且已经写入";
  if (v.stopped) return "已停止且还没写";
  if (v.claim === "written") return "已经写入";
  if (v.claim === "unknown") return "结果未知";
  return "可以确认";
}

export function group(v) {
  if (v.stopped) return "已停止";
  if (v.claim === "written") return "已经写入";
  if (v.claim === "unknown" || v.blindPosts > 0 || v.badExec) return "结果未知";
  return "待确认";
}

export function step(action) {
  const labels = {
    Send: "发送成功",
    Reject: "对方明确拒绝",
    Unknown: "没有得到答复",
    Lookup: "按编号查询",
    Blind: "未知后再次发送",
    Stop: "停止这一代",
    RunTool: null,
    BrokenRun: "停止后仍调用工具",
    Stutter: null,
  };
  return Object.prototype.hasOwnProperty.call(labels, action) ? labels[action] : action;
}
