import { useMemo, useRef } from "react";
import { Markdown } from "./Markdown";
import { extractDocToc } from "../utils/docToc";

export function DocumentArtifact({ content }: { content: string }) {
  const toc = useMemo(() => extractDocToc(content), [content]);
  const bodyRef = useRef<HTMLDivElement>(null);

  function jump(id: string) {
    const el = bodyRef.current?.querySelector(`#${CSS.escape(id)}`);
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <div className="artifacts-doc">
      {toc.length ? (
        <nav className="artifacts-toc" aria-label="目录">
          <div className="artifacts-toc-title">目录</div>
          <ul>
            {toc.map((item) => (
              <li key={item.id} className={`toc-l${item.level}`}>
                <button type="button" onClick={() => jump(item.id)}>
                  {item.text}
                </button>
              </li>
            ))}
          </ul>
        </nav>
      ) : null}
      <div className="artifacts-doc-body" ref={bodyRef}>
        <Markdown content={content} anchorHeadings />
      </div>
    </div>
  );
}
