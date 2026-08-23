import { Fragment, type ReactNode, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import hljs from "highlight.js";
import type { Citation } from "../api/client";
import { useStore } from "../store/useStore";
import { extractDocToc } from "../utils/docToc";

const CITE_RE = /\[(\d+)\]/g;

function withCitations(text: string, citations?: Citation[]): ReactNode[] {
  if (!citations?.length) return [text];
  const map = new Map(citations.map((c) => [Number(c.index), c]));
  const nodes: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  const re = new RegExp(CITE_RE.source, "g");
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const idx = Number(m[1]);
    const c = map.get(idx);
    if (c) {
      const label = c.title || c.filename;
      nodes.push(
        <span key={`${m.index}-${idx}`} className="cite-wrap" tabIndex={0}>
          {c.url ? (
            <a
              className="cite-link"
              href={c.url}
              target="_blank"
              rel="noreferrer"
              title={label}
            >
              [{idx}]
            </a>
          ) : (
            <span className="cite-link">[{idx}]</span>
          )}
          <span className="cite-tip">
            <strong>{label}</strong>
            <br />
            {c.snippet}
            {c.url ? (
              <>
                <br />
                <a className="cite-url" href={c.url} target="_blank" rel="noreferrer">
                  打开链接
                </a>
              </>
            ) : null}
          </span>
        </span>
      );
    } else {
      nodes.push(m[0]);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function TextWithCites({ children, citations }: { children?: ReactNode; citations?: Citation[] }) {
  if (!citations?.length) return <>{children}</>;
  return (
    <>
      {FlattenText(children).map((part, i) =>
        typeof part === "string" ? (
          <Fragment key={i}>{withCitations(part, citations)}</Fragment>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        )
      )}
    </>
  );
}

function FlattenText(children: ReactNode): ReactNode[] {
  const out: ReactNode[] = [];
  const walk = (nodes: ReactNode) => {
    if (nodes == null || typeof nodes === "boolean") return;
    if (typeof nodes === "string" || typeof nodes === "number") {
      out.push(String(nodes));
      return;
    }
    if (Array.isArray(nodes)) {
      nodes.forEach(walk);
      return;
    }
    out.push(nodes);
  };
  walk(children);
  return out;
}

export function Markdown({
  content,
  citations,
  anchorHeadings,
}: {
  content: string;
  citations?: Citation[];
  anchorHeadings?: boolean;
}) {
  const openArtifact = useStore((s) => s.openArtifact);
  const toc = anchorHeadings ? extractDocToc(content) : [];
  const idxRef = useRef(0);
  idxRef.current = 0;

  function heading(Tag: "h1" | "h2" | "h3") {
    return function Heading({ children }: { children?: ReactNode }) {
      if (!anchorHeadings) return <Tag>{children}</Tag>;
      const id = toc[idxRef.current++]?.id;
      return <Tag id={id}>{children}</Tag>;
    };
  }

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeKatex]}
      components={{
        h1: heading("h1"),
        h2: heading("h2"),
        h3: heading("h3"),
        p({ children }) {
          return (
            <p>
              <TextWithCites citations={citations}>{children}</TextWithCites>
            </p>
          );
        },
        li({ children }) {
          return (
            <li>
              <TextWithCites citations={citations}>{children}</TextWithCites>
            </li>
          );
        },
        a({ href, children, ...props }) {
          return (
            <a href={href} target="_blank" rel="noreferrer" {...props}>
              {children}
            </a>
          );
        },
        pre({ children }) {
          return <>{children}</>;
        },
        code({ className, children, ...props }) {
          const match = /language-(\w+)/.exec(className || "");
          const text = String(children).replace(/\n$/, "");
          const isBlock = Boolean(match) || text.includes("\n");

          if (isBlock) {
            const lang = match?.[1] || "text";
            let html = text;
            try {
              html = match
                ? hljs.highlight(text, { language: match[1] }).value
                : hljs.highlightAuto(text).value;
            } catch {
              /* keep plain */
            }
            const title = lang === "text" ? "代码" : `${lang} 代码`;
            return (
              <div className="code-block">
                <div className="code-toolbar">
                  <span className="code-lang">{lang}</span>
                  <button
                    type="button"
                    className="code-open"
                    onClick={() =>
                      openArtifact({
                        id: `${lang}-${text.length}-${text.slice(0, 24)}`,
                        title,
                        language: lang,
                        content: text,
                        kind: "code",
                      })
                    }
                  >
                    在 Artifact 打开
                  </button>
                </div>
                <pre>
                  <code className={className} dangerouslySetInnerHTML={{ __html: html }} {...props} />
                </pre>
              </div>
            );
          }

          return (
            <code className={className} {...props}>
              {children}
            </code>
          );
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
