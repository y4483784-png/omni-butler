export const title = "确认写日历";

export const colors = {
  草稿: "teal",
  忙碌: "orange",
  成功: "yellow",
  结束: "pink",
};

export const group = (v) => {
  if (v.phase === "confirmed") return "成功";
  if (v.phase === "cancelled" || (v.phase === "draft" && v.pastExpiry)) return "结束";
  if (v.busy || v.pending > 0 || v.armed > 0 || v.unacked > 0) return "忙碌";
  return "草稿";
};

export const state = (v) => {
  if (v.phase === "confirmed") return "已确认锁定";
  if (v.phase === "cancelled") {
    return v.writes >= 1 ? "已取消·曾写入" : "已取消或过期";
  }
  if (v.phase === "draft" && v.pastExpiry) {
    return v.writes >= 1 ? "已过期·曾写入" : "已取消或过期";
  }
  if (v.mode === "choose") return "选事件模式";

  const inFlight = v.busy || v.pending > 0 || v.armed > 0 || v.unacked > 0;
  if (inFlight) {
    if (v.writes >= 2) return "写入待回执·第二次";
    if (v.writes >= 1 && v.unacked > 0) return "写入待回执";
    if (v.writes >= 1) return "再确认中·已写过";
    if (v.armed > 0 || v.unacked > 0) return "写入中";
    return "已点确认忙碌中";
  }

  if (v.writes >= 2) return "已写入两次·空闲";
  if (v.writes >= 1) return "已写入·空闲";
  if (v.restored) return "流式恢复草稿";
  if (v.phase === "" && v.mode === "") return "草稿待确认";
  return "草稿待确认";
};

export const step = (action, from, to) => {
  if (action === "Idle" || action === "applySlot" || action === "FinishRunStreamLoop") return null;
  if (action === "PatchEvent" || action === "DeleteEvent" || action === "ConfirmGoogleTitleChange" || action === "CreateCalendarEvent") {
    return "写入日历";
  }
  const labels = {
    ChooseCandidate: "选中候选事件",
    Confirm: "点确认",
    MissingEvent: "缺事件拒绝",
    CheckConflict: "冲突检查通过",
    RejectCreateConflict: "冲突拒绝",
    CalendarTitleUnmatched: "标题对不上拒绝",
    ApplySuccess: "写入成功回执",
    UpdateLastAssistant: "流式刷新恢复草稿",
    VoidOpenDrafts: "作废草稿",
    DeclineCancel: "用户放弃取消",
    ExpireDraft: "过期",
  };
  return labels[action] ?? action;
};
