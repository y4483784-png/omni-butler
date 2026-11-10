export const title = "干活循环会不会再写一次";

export const colors = {
  还没调用: "blue",
  确认卡: "teal",
  未知: "orange",
  已停止: "tan",
  违规: "pink",
};

export function state(v) {
  if (v.invokedAfterStop) return "停止后仍调用了工具";
  if (v.insertKind === "second") return "未知之后又插入了一次";
  if (v.insertKind === "draft") return "确认之前就写入了";
  if (v.lookups === 1 && v.choice === "send") return "不该核对时去查找了";
  if (v.dispatchInvoked || v.dispatchClaimed || v.dispatchWrote) return "派活自己做了事";
  if (v.stopped && v.phase === "halted") return "已停止且没有调用";
  if (v.phase === "unknown") return "结果未知还没核对";
  if (v.card) return "确认卡已出还没写入";
  if (v.reply === "work" && v.choice === "reconcile") return "核对过了没有再写入";
  if (v.reply === "work" && v.choice === "send") return "拒绝重发没有再写入";
  if (v.reply === "work") return "干活路径已经回答";
  if (v.phase === "drafted") return "日历步骤还没出卡";
  return "还没调用工具";
}

export function group(v) {
  if (v.invokedAfterStop || v.insertKind !== "none" || (v.lookups === 1 && v.choice === "send") || v.dispatchInvoked) {
    return "违规";
  }
  if (v.stopped) return "已停止";
  if (v.phase === "unknown" || v.choice === "reconcile" || v.choice === "send") return "未知";
  if (v.card) return "确认卡";
  return "还没调用";
}

export function step(action) {
  const labels = {
    Assign: "派活记下工具",
    Stop: "停止这一代",
    Enter: "进入干活",
    Halt: "停止后结束循环",
    AnswerNone: "没有工具就回答",
    Call: "调用这一个工具",
    ReadAnswer: "只读问题直接回答",
    ShowCard: "先出确认卡",
    WriteFromLoop: "确认之前就写入",
    ReportUnknown: "结果变成未知",
    SettleReconcile: "按核对查找不再插入",
    SettleSend: "不重发也不查找",
    SecondInsert: "未知后再插入一次",
    LookupOnSend: "不该核对时去查找",
    Done: null,
  };
  return Object.prototype.hasOwnProperty.call(labels, action) ? labels[action] : action;
}
