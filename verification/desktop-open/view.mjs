export const title = "桌面本机动作";

export const colors = {
  认出: "blue",
  执行: "teal",
  拒绝: "orange",
  留下: "tan",
};

export function state(v) {
  if (v.phase === "start") return "还没有本地动作";
  if (v.phase === "lied") return "自报桌面被拒绝";
  if (v.phase === "offered") return "确认卡已出示";
  if (v.phase === "stopped") return "确认前已停止";
  if (v.phase === "armed") return "凭据已记下还没打开";
  if (v.phase === "opened") return "文件已经打开";
  if (v.phase === "again") return "再点一次不再打开";
  if (v.phase === "kept") return "停止后文件仍在";
  if (v.phase === "retried") return "未知之后不再打开";
  return "其他";
}

export function group(v) {
  if (v.phase === "start" || v.phase === "offered" || v.phase === "armed") return "认出";
  if (v.phase === "opened" || v.phase === "again") return "执行";
  if (v.phase === "kept") return "留下";
  return "拒绝";
}

export function step(action) {
  const name = String(action).split("(")[0];
  if (name === "Done") return null;
  if (name === "Lie") return "请求体自报桌面";
  if (name === "Offer") return "出示确认卡";
  if (name === "StopBefore") return "确认前停止";
  if (name === "Arm") return "记下这次动作";
  if (name === "Open") return "打开文件";
  if (name === "Again") return "再点一次";
  if (name === "Retry") return "未知后再点";
  if (name === "StopAfter") return "做完后再停止";
  return null;
}
