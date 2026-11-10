export const title = "Task turn";

const words = {
  absent: "No turn",
  running: "Running",
  waiting_person: "Waiting on the person",
  waiting_resource: "Waiting on a resource",
  completed: "Completed",
  stopped: "Stopped",
};

const label = (status) => words[status] ?? status;

export const state = (v) => {
  if (v.s2 === "absent") return label(v.s1);
  return `${label(v.s1)}, then ${label(v.s2)}`;
};

export const group = (v) => {
  const latest = v.s2 === "absent" ? v.s1 : v.s2;
  if (latest === "running") return "open";
  if (latest === "waiting_person" || latest === "waiting_resource") return "waiting";
  if (latest === "completed") return "finished";
  if (latest === "stopped") return "stopped";
  return "none";
};

export const colors = {
  none: "yellow",
  open: "blue",
  waiting: "orange",
  finished: "teal",
  stopped: "tan",
};

const stepLabel = {
  Begin: "a new message arrives",
  Finish1: "the answer is stored",
  Finish2: "the answer is stored",
  Stop1: "stop is called",
  Stop2: "stop is called",
  WaitPerson1: "a confirm card is about to show",
  WaitPerson2: "a confirm card is about to show",
  WaitResource1: "the turn waits on a resource",
  WaitResource2: "the turn waits on a resource",
};

export const step = (action) => stepLabel[action] ?? null;
