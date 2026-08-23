/** Heading outline for long-document Artifacts (Claude Canvas / VS Code outline). */

export type TocItem = { id: string; text: string; level: number };

const HEADING = /^(#{1,3})\s+(.+?)\s*$/;

export function slugifyHeading(text: string, used: Set<string>): string {
  const base =
    text
      .trim()
      .toLowerCase()
      .replace(/\s+/g, "-")
      .replace(/[^\w\u4e00-\u9fff-]/g, "")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "") || "section";
  let id = base;
  let n = 2;
  while (used.has(id)) {
    id = `${base}-${n++}`;
  }
  used.add(id);
  return id;
}

export function extractDocToc(markdown: string): TocItem[] {
  const used = new Set<string>();
  const items: TocItem[] = [];
  for (const line of (markdown || "").split(/\r?\n/)) {
    const m = HEADING.exec(line);
    if (!m) continue;
    const text = m[2].replace(/[`*_]+/g, "").trim();
    if (!text) continue;
    items.push({ id: slugifyHeading(text, used), text, level: m[1].length });
  }
  return items;
}
