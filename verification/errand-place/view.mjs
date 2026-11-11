export const title = "办事下单";

export const colors = {
  确认: "blue",
  发出: "teal",
  记下: "tan",
  停下: "orange",
};

export function state(v) {
  if (v.phase === "start") return "还没有这一单";
  if (v.phase === "offered") return "确认卡已出示";
  if (v.phase === "bare") return "没有凭据所以拒绝";
  if (v.phase === "unpaid") return "没有支付凭证";
  if (v.phase === "armed") return "编号已记下还没发出";
  if (v.phase === "placed") return "对方已接受";
  if (v.phase === "unknown") return "结果未知";
  if (v.phase === "retried" && v.places > 1) return "又下了一单";
  if (v.phase === "retried") return "再点一次不再下单";
  if (v.phase === "adopted" && v.places > 1) return "核对时又下了一单";
  if (v.phase === "adopted") return "核对到这一单";
  if (v.phase === "unnamed" && v.record) return "把别人的已下单记下了";
  if (v.phase === "unnamed") return "已下单但不是这一单";
  if (v.phase === "wall" && v.clicks > 0) return "应用墙之后还点了";
  if (v.phase === "wall") return "应用墙已停止";
  if (v.phase === "stopped") return "停止后仍然接受";
  return "其他";
}

export function group(v) {
  if (v.phase === "start" || v.phase === "offered" || v.phase === "armed") return "确认";
  if (v.phase === "placed" || v.phase === "adopted" || v.phase === "stopped") return "记下";
  if (v.phase === "unknown" || v.phase === "retried") return "发出";
  return "停下";
}

export function step(action) {
  const name = String(action).split("(")[0];
  if (name === "Done") return null;
  if (name === "Offer") return "出示确认卡";
  if (name === "Bare") return "没有凭据就提交";
  if (name === "Unpaid") return "没有支付凭证";
  if (name === "Arm") return "记下外部编号";
  if (name === "Place") return "对方接受";
  if (name === "Miss") return "这一次没有答复";
  if (name === "Again") return "再点一次";
  if (name === "Seen") return "核对到这一单";
  if (name === "BarePage") return "页面写着已下单";
  if (name === "Wall") return "遇到应用墙";
  if (name === "Stop") return "停止";
  return null;
}
