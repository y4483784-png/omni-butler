export const title = "邮件入口";

export const colors = {
  收下: "blue",
  确认: "teal",
  成功: "tan",
  拒绝: "orange",
};

export function state(v) {
  if (v.phase === "start") return "还没有这封信";
  if (v.phase === "filed") return "已收下";
  if (v.phase === "repeated" && v.receipts > 1) return "同一编号开了第二封";
  if (v.phase === "repeated") return "同一编号仍是一封";
  if (v.phase === "offered") return "确认卡已出示";
  if (v.phase === "refused") return "没有凭据所以拒绝";
  if (v.phase === "armed") return "凭据已取出还没接受";
  if (v.phase === "sealed") return "对方已接受";
  if (v.phase === "stopped") return "停止后仍然接受";
  if (v.phase === "linked") return "打开链接不算发出";
  return "其他";
}

export function group(v) {
  if (v.phase === "start" || v.phase === "filed" || v.phase === "repeated") return "收下";
  if (v.phase === "offered" || v.phase === "armed") return "确认";
  if (v.phase === "sealed" || v.phase === "stopped") return "成功";
  return "拒绝";
}

export function step(action, from, to) {
  const name = String(action).split("(")[0];
  if (name === "Done") return null;
  if (name === "File") return "收下新邮件";
  if (name === "Again") return "同一编号再来";
  if (name === "Offer") return "按下发送";
  if (name === "Bare") return "没有凭据就提交";
  if (name === "Arm") return "取出凭据";
  if (name === "Seal") return "对方接受";
  if (name === "Stop") return "停止";
  if (name === "Link") return "打开写信链接";
  return null;
}
