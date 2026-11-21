export const title = "Mail send";

export const colors = {
  Open: "blue",
  Unknown: "orange",
  Sealed: "teal",
};

export function state(v) {
  if (v.claim === "open") return "Claim is open";
  if (v.claim === "unknown" && v.remote === "attempt" && !v.refused) return "Attempt id is stored";
  if (v.claim === "unknown" && v.remote === "attempt" && v.refused) return "Attempt confirm changes nothing";
  if (v.claim === "unknown" && v.remote === "none" && !v.refused) return "No provider id is stored";
  if (v.claim === "unknown" && v.remote === "none" && v.refused) return "Empty id confirm changes nothing";
  if (v.claim === "unknown" && v.remote === "provider" && v.row === "legacy") return "Acceptance is stored";
  if (v.claim === "unknown" && v.remote === "provider" && v.lookup === "other") return "Lookup did not accept";
  if (v.claim === "unknown" && v.remote === "provider" && v.row === "none") return "Provider id is stored";
  if (v.receipt === "sealed" && !v.resealed) return "Receipt is sealed";
  if (v.receipt === "sealed" && v.resealed) return "Sealed receipt confirmed again";
  return "Other";
}

export function group(v) {
  if (v.claim === "open") return "Open";
  if (v.receipt === "sealed") return "Sealed";
  return "Unknown";
}

export function step(action, from) {
  const name = String(action).split("(")[0];
  if (name === "Done") return null;
  if (name === "Send") return "send once";
  if (name === "Accept") return "the provider returns an id";
  if (name === "Reject") return "the provider rejects";
  if (name === "RepeatUnstored" && from.remote === "attempt") return "confirm again [the id is an attempt]";
  if (name === "RepeatUnstored") return "confirm again [the id is empty]";
  if (name === "RepairNoTransport") return "repair [no transport]";
  if (name === "LookupAccepted") return "lookup accepts";
  if (name === "LookupOther") return "lookup does not accept";
  if (name === "RepairLegacy") return "seal the stored acceptance";
  if (name === "Reseal") return "confirm the sealed receipt again";
  return null;
}
