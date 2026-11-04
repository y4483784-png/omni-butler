export const title = "Schedule confirm";

export const state = (v) => {
  if (v.writes > 0 && (v.retired || v.phase === "cancelled")) return "Arranged, then closed";
  if (v.retired && v.pastExpiry) return "Expired";
  if (v.retired || v.phase === "cancelled") return "Closed";
  if (v.phase === "confirmed" || v.writes > 0) return "Arranged";
  if (v.busy || v.pending > 0 || v.armed > 0 || v.unacked > 0) return "Confirming";
  if (v.mode === "choose" && v.phase === "draft") return "Choosing";
  return "Draft";
};

export const group = (v) => {
  const label = state(v);
  if (label === "Arranged" || label === "Arranged, then closed") return "done";
  if (label === "Closed" || label === "Expired") return "closed";
  if (label === "Confirming") return "busy";
  return "open";
};

export const colors = {
  open: "blue",
  busy: "orange",
  done: "teal",
  closed: "tan",
};

const stepLabel = {
  Confirm: "you confirm",
  MissingEvent: "you confirm with nothing selected",
  ChooseCandidate: "you pick one schedule",
  applySlot: "you pick an open time",
  CheckConflict: "the calendar checks for a clash",
  CreateCalendarEvent: "the calendar creates the event",
  RejectCreateConflict: "the calendar refuses a clash",
  PatchEvent: "the calendar saves the change",
  DeleteEvent: "the calendar deletes the event",
  ConfirmGoogleTitleChange: "the calendar saves the Google change",
  CalendarTitleUnmatched: "the calendar cannot find that event",
  ApplySuccess: "the card shows it is arranged",
  UpdateLastAssistant: "the answer refreshes",
  FinishRunStreamLoop: "the answer finishes",
  VoidOpenDrafts: "you send another message",
  DeclineCancel: "you choose not to delete",
  ExpireDraft: "the card expires",
};

export const step = (action) => {
  if (action === "Idle") return null;
  return stepLabel[action] ?? null;
};
