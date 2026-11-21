export const title = "Task sweep";

const rowHeld = (v) =>
  v.stamp === v.snap[1] &&
  v.observed === v.snap[2] &&
  v.told === v.snap[3] &&
  v.notices === v.snap[4] &&
  v.stable === v.snap[5] &&
  v.hits1 === v.snap[6] &&
  v.hits2 === v.snap[7];

const label = (v) => {
  if (v.status === "waiting_person") {
    return rowHeld(v) ? "Waiting on the person" : "Waiting on the person, row changed";
  }
  if (v.status === "waiting_resource") {
    return rowHeld(v) ? "Waiting on a resource" : "Waiting on a resource, row changed";
  }
  if (v.status === "stopped") return "Stopped";
  if (v.notices > 0) return "Running, notice sent";
  if (v.stamp === 0) return "Running, no sample";
  return "Running, sample saved";
};

export const state = (v) => label(v);

export const group = (v) => {
  if (v.status === "stopped") return "stopped";
  if (v.status === "waiting_person" || v.status === "waiting_resource") return "waiting";
  if (v.notices > 0) return "noticed";
  if (v.stamp === 0) return "quiet";
  return "working";
};

export const colors = {
  quiet: "yellow",
  working: "blue",
  noticed: "teal",
  waiting: "orange",
  stopped: "tan",
};

export const step = (action, from, to) => {
  if (label(from) === label(to)) return null;
  if (action === "Progress" || action === "RecordedEarlier") return "a progress sample is saved";
  if (action === "Sweep") return "the sweep runs";
  if (action === "WaitPerson") return "the job waits on the person";
  if (action === "WaitResource") return "the job waits on a resource";
  return null;
};
