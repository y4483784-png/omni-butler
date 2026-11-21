export const title = "Chat image";

export const state = (v) => {
  if (v.picture === "none") return "No image yet";
  if (v.picture === "rejected") return "Image refused";
  if (v.imageRow === "staged" && v.blob === "out") return "Staged row, blob not stored";
  if (v.imageRow === "staged") return "Staged row, blob stored";
  if (v.imageRow === "tombstone" && v.blob === "gone") return "Tombstone kept after the blob is gone";
  if (v.imageRow === "tombstone") return "Terminal fence tombstoned the staged row";
  if (v.picture === "accepted") return "Image held for this chat";
  if (v.usage === "pending") return "Sent to the chat model";
  if (v.closed === false && v.usage === "missing") return "Missing counts stay null";
  if (v.closed === false && v.usage === "counted") return "Token counts stored";
  if (v.closed === false && v.usage === "failed") return "Transport failure stores no row";
  if (v.imageRow === "pending" && v.blob === "held") return "Job ended, blob still held";
  if (v.imageRow === "pending" && v.blob === "gone") return "Blob gone, row still pending";
  if (v.imageRow === "deleted") return "Image bytes removed";
  return "Other";
};

export const step = (action) => {
  const labels = {
    Accept: "the chat reserves a staged row",
    PutBlob: "the blob is stored",
    Adopt: "a stored blob becomes ready",
    FenceMissing: "a missing blob uses the terminal fence",
    TombstoneStaged: "the terminal transaction tombstones a staged row",
    DropTombstoneBlob: "cleanup deletes the blob and keeps the tombstone",
    DeleteLatePut: "a later put of a tombstone key is deleted",
    Reject: "the image is refused",
    Send: "the chat model receives the image",
    NoteMissing: "the response omits token counts",
    NoteNumbers: "the response includes token counts",
    NoteFailed: "the provider never answers",
    Finish: "the job ends and deletion stays pending",
    Purge: "the blob and the row are deleted",
    CrashAfterBlob: "the blob goes and the row remains",
    AlreadyGone: "a missing blob still drops the row",
    Done: null,
  };
  return Object.prototype.hasOwnProperty.call(labels, action) ? labels[action] : action;
};

export const group = (v) => {
  if (v.picture === "rejected") return "Stopped";
  if (v.imageRow === "deleted") return "Cleared";
  if (v.closed === true) return "Ending";
  if (v.usage === "failed") return "Stopped";
  if (v.picture === "sent" || v.picture === "accepted") return "In flight";
  return "Start";
};

export const colors = {
  Start: "tan",
  "In flight": "blue",
  Ending: "orange",
  Stopped: "yellow",
  Cleared: "teal",
};
