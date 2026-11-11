export const title = "网页执行面";

export const colors = {
  到达: "blue",
  网页: "teal",
  拒绝: "orange",
  执行: "tan",
};

export function state(v) {
  if (v.phase === "start") return "请求刚到达";
  if (v.phase === "minted") return "服务器写成网页";
  if (v.phase === "denied" && v.kind === "unknown") return "未知名称走了本机拒绝";
  if (v.phase === "denied") return "本机工具被拒绝";
  if (v.phase === "whitelist") return "名称不在白名单";
  if (v.phase === "executed" && v.kind === "local") return "本机工具被执行了";
  if (v.phase === "executed") return "服务端工具已执行";
  if (v.surface === "desktop") return "表面被写成了桌面";
  return "其他";
}

export function group(v) {
  if (v.phase === "start") return "到达";
  if (v.phase === "executed" && v.kind !== "local") return "执行";
  if (v.phase === "denied" || v.phase === "whitelist" || v.kind === "local" && v.ran > 0) return "拒绝";
  if (v.surface === "desktop") return "拒绝";
  return "网页";
}

export function step(action, from, to) {
  const name = String(action).split("(")[0];
  if (name === "Done") return null;
  if (name === "Mint") {
    if (to.surface === "desktop") return "把自报的桌面当成表面";
    return "按登录写成网页";
  }
  if (name !== "Decide") return null;
  if (to.phase === "executed" && to.kind === "local") return "放行了本机工具";
  if (to.phase === "denied" && to.kind === "unknown") return "把未知名称当成本机工具";
  if (to.phase === "denied") return "拒绝本机工具";
  if (to.phase === "whitelist") return "按白名单拒绝";
  if (to.phase === "executed") return "执行服务端工具";
  return null;
}
