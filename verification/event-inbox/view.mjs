export const title = "事件收件箱";

export const colors = {
  接收: "blue",
  直通: "teal",
  派活: "yellow",
  确认卡: "orange",
  停止: "tan",
  成功: "purple",
};

export function state(v) {
  if (v.success && !v.written) return "收到就标成外部成功";
  if (v.success) return "对方接受后记下外部成功";
  if (v.route === "stopped" && (v.cards > 0 || v.success || v.newGen > 0)) return "停止后仍去写";
  if (v.route === "passthrough" && v.newGen > 0) return "直通时又开了新任务";
  if (v.aimed === "completed" && v.route === "opened" && v.newGen === v.gen) return "完成的那一代被复用";
  if (v.cards > 1) return "同时有两张可写卡";
  if (v.seen > 1) return "同一投递有两条回执";
  if (v.settled && v.route === "queued") return "后来的事件只排队";
  if (v.route === "queued") return "事件在排队";
  if (v.route === "reissued") return "旧卡已作废且只剩一张新卡";
  if (v.route === "kept") return "指纹相同所以原卡还在";
  if (v.route === "passthrough") return "事件进入正在进行的这一代";
  if (v.route === "opened" && v.aimed === "completed") return "完成后另开了新任务";
  if (v.route === "opened") return "没有进行中的任务就新开一代";
  if (v.route === "stopped") return "停止的这一代没有写";
  if (v.route === "noise") return "噪声只被接收";
  if (v.route === "held") return "模型决定不动";
  if (v.aimed === "waiting") return "任务正在等人确认";
  if (v.aimed === "running") return "任务正在进行";
  if (v.aimed === "completed") return "任务已经完成";
  if (v.aimed === "stopped") return "任务已经停止";
  return "还没有事件";
}

export function group(v) {
  if (v.success) return "成功";
  if (v.route === "stopped" || v.aimed === "stopped") return "停止";
  if (v.route === "kept" || v.route === "reissued" || v.route === "queued" || v.aimed === "waiting") {
    return "确认卡";
  }
  if (v.route === "passthrough" || v.aimed === "running") return "直通";
  if (v.route === "opened") return "派活";
  return "接收";
}

export function step(action, _from, to) {
  const name = String(action).split("(")[0];
  if (name === "Prepare") {
    if (to.aimed === "running") return "这一代正在进行";
    if (to.aimed === "waiting") return "这一代正在等人";
    if (to.aimed === "completed") return "这一代已经做完";
    if (to.aimed === "stopped") return "这一代已经停止";
    return "先有一轮任务";
  }
  if (name === "Receive") {
    return to.route === "noise" ? "程序丢掉噪声" : "模型决定不动";
  }
  const labels = {
    OpenFresh: "没有进行中的任务就另开一代",
    Passthrough: "进入正在进行的这一代",
    Keep: "指纹相同就保留原卡",
    Reissue: "指纹不同就作废旧卡并重签一张",
    QueueFirst: "没有新卡就先排队",
    OpenAfterCompleted: "完成后另开新任务",
    RefuseStopped: "停止的这一代不写",
    Later: "后来又来一条不同的事件",
    Repeat: "同一个投递编号再来一次",
    MarkWritten: "对方接受了这次写入",
    Note: "这时才记下外部成功",
    Settle: "这一条收件结束",
    Idle: "停在这里",
  };
  const key = name.split("(")[0];
  return labels[key] ?? null;
}
