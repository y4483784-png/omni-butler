export type ThinkingFold = "hidden" | "streaming" | "collapsed" | "opened";

export type FoldEvent =
  | { type: "reasoning" }
  | { type: "answer" }
  | { type: "toggle" }
  | { type: "hydrate"; reasoning: string; content: string };

export function foldThinking(phase: ThinkingFold, event: FoldEvent): ThinkingFold {
  if (event.type === "hydrate") {
    if (!event.reasoning.trim()) return "hidden";
    if (phase === "opened" || phase === "collapsed") return phase;
    return event.content.trim() ? "collapsed" : "streaming";
  }
  if (event.type === "reasoning") {
    return phase === "hidden" ? "streaming" : phase;
  }
  if (event.type === "answer") {
    return phase === "streaming" ? "collapsed" : phase;
  }
  if (phase === "collapsed") return "opened";
  if (phase === "opened" || phase === "streaming") return "collapsed";
  return "hidden";
}
