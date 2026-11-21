export const title = "Saved tool call";

const label = (v) => {
  if (v.crashed) return "Crashed after the model";
  if (v.nextModel === 1 && v.readResult === 0) return "Model asked before the result";
  if (v.preCrashed && !v.resumed && v.callPhase === "pending") return "Process died before the read";
  if (v.resumed && v.callPhase === "pending") return "Same pending read resumed";
  if (v.callPhase === "running" && v.invoked > 1) return "Read ran twice";
  if (v.callPhase === "running") return "Read in flight";
  if (v.nextModel === 1) return "Model requested";
  if (v.opened && v.confirms === 0) return "Confirm card open";
  if (v.confirms === 1) return "Write confirmed";
  if (v.confirms > 1) return "Second confirm stored";
  if (v.readResult === 1 || v.callPhase === "result") return "Read result stored";
  if (v.callPhase === "pending") return "Calls saved";
  if (v.stopped) return "Stopped before any call";
  return "No calls yet";
};

export const state = (v) => label(v);

export const group = (v) => {
  const name = label(v);
  if (name === "Crashed after the model") return "after";
  if (name === "Process died before the read" || name === "Same pending read resumed") return "before";
  if (name === "Model asked before the result" || name === "Read ran twice") return "broken";
  if (name === "Stopped before any call") return "stopped";
  return "main";
};

export const colors = {
  main: "blue",
  before: "orange",
  after: "tan",
  broken: "pink",
  stopped: "yellow",
};

export const step = (action, from, to) => {
  if (label(from) === label(to)) return null;
  if (action === "Save" && to.stopped) return "the job stops before any call";
  if (action === "Save") return "the turn is saved";
  if (action === "CrashBeforeInvoke") return "the process dies before the read";
  if (action === "Resume") return "the same pending call resumes";
  if (action === "Invoke" && to.invoked > 1) return "the saved read runs again";
  if (action === "Invoke") return "the saved read runs";
  if (action === "Persist") return "the read result is stored";
  if (action === "Open") return "the confirm card opens";
  if (action === "Confirm") return "the write is confirmed";
  if (action === "ConfirmAgain") return "the write is confirmed again";
  if (action === "RequestBeforeSavedResult") return "the model is asked before the result";
  if (action === "RequestAfterTools") return "the model is requested";
  if (action === "Crash") return "the process dies after the model";
  return null;
};
