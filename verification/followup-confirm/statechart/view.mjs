export const title = "会后确认写回";

export const colors = {
  空闲: "blue",
  草稿: "teal",
  忙碌: "orange",
  成功: "yellow",
  结束: "pink",
};

export const group = (v) => {
  if (v.done) return "成功";
  if (v.phase === "cancelled" || (v.phase === "draft" && v.expired)) return "结束";
  if (v.inflight || v.toSend > 0 || v.checking > 0 || v.checked > 0 || v.unacked > 0) return "忙碌";
  if (v.phase === "draft" || v.phase === "info") return "草稿";
  return "空闲";
};

export const state = (v) => {
  if (v.done) return "已写入成功";
  if (v.phase === "cancelled" || (v.phase === "draft" && v.expired)) return "已取消或过期";
  if (v.phase === "info") return "需粘贴只读";
  if (v.phase === "none") return "无卡片";

  const inFlight = v.inflight || v.toSend > 0 || v.checking > 0 || v.checked > 0 || v.unacked > 0;
  if (inFlight) {
    if (v.unacked > 0 || v.events >= 1) return "写入待回执";
    return "确认中";
  }

  if (v.events >= 1) return "已写入·回执丢失后";
  if (v.ticked) return "草稿已勾选";
  return "草稿未勾选";
};

export const step = (action, from, to) => {
  if (action === "Idle" || action === "Untick") return null;
  if (action === "PostFollowAction" || action === "ConflictCheck") return "冲突检查";
  const labels = {
    FollowUpDraft: "出草稿卡片",
    FollowUpNeedsPaste: "needs_paste",
    Tick: "勾选待办",
    Confirm: "确认",
    RejectOverlap: "冲突拒绝",
    InsertEvent: "写入",
    ObserveSuccess: "成功回执",
    ObserveLostResponse: "回执丢失",
    ExpireDraft: "过期",
    VoidDraft: "作废",
  };
  return labels[action] ?? action;
};
