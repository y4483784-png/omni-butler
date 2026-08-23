import { useEffect, useMemo, useState } from "react";
import hljs from "highlight.js";
import { useStore } from "../store/useStore";
import { ChartArtifact } from "./ChartArtifact";
import { DocumentArtifact } from "./DocumentArtifact";

const THEME_KEY = "omni-artifact-code-theme";

function loadCodeTheme(): "dark" | "light" {
  try {
    return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function ArtifactsPanel() {
  const { artifact, artifactOpen, closeArtifact } = useStore();
  const [codeTheme, setCodeTheme] = useState<"dark" | "light">(loadCodeTheme);

  useEffect(() => {
    try {
      localStorage.setItem(THEME_KEY, codeTheme);
    } catch {
      /* ignore */
    }
  }, [codeTheme]);

  const looksChart = Boolean(
    artifact?.kind === "image" ||
      artifact?.imageUrl ||
      artifact?.svg ||
      artifact?.content?.startsWith("data:image") ||
      (artifact?.content || "").includes("<svg") ||
      artifact?.language === "png" ||
      artifact?.language === "svg"
  );
  const isImage = looksChart;
  const isDoc = !isImage && artifact?.kind === "document";
  const kind = isImage ? "image" : isDoc ? "document" : "code";
  const html = useMemo(() => {
    if (!artifact || isImage || isDoc) return "";
    try {
      if (artifact.language && hljs.getLanguage(artifact.language)) {
        return hljs.highlight(artifact.content, { language: artifact.language }).value;
      }
      return hljs.highlightAuto(artifact.content).value;
    } catch {
      return escapeHtml(artifact.content);
    }
  }, [artifact, isImage, isDoc]);

  if (!artifactOpen || !artifact) return null;

  async function copy() {
    try {
      await navigator.clipboard.writeText(artifact!.content || "");
    } catch {
      /* ignore */
    }
  }

  const imgSrc = artifact.imageUrl || (artifact.content.startsWith("data:image") ? artifact.content : "");

  return (
    <aside className="artifacts" data-code-theme={codeTheme} aria-label="Artifact 面板">
      <div className="artifacts-head">
        <div className="artifacts-meta">
          <span className="artifacts-label">Artifact</span>
          <span className="artifacts-title" title={artifact.title}>
            {artifact.title}
          </span>
          {artifact.language ? <span className="artifacts-lang">{artifact.language}</span> : null}
        </div>
        <div className="artifacts-actions">
          {kind === "code" ? (
            <>
              <button
                type="button"
                className="ghost-btn"
                title="切换语法高亮主题"
                onClick={() => setCodeTheme((t) => (t === "dark" ? "light" : "dark"))}
              >
                {codeTheme === "dark" ? "浅色高亮" : "深色高亮"}
              </button>
              <button type="button" className="ghost-btn" onClick={copy}>
                复制
              </button>
            </>
          ) : null}
          {isDoc ? (
            <button type="button" className="ghost-btn" onClick={copy}>
              复制
            </button>
          ) : null}
          <button type="button" className="ghost-btn" onClick={closeArtifact} title="关闭">
            ×
          </button>
        </div>
      </div>
      {isImage ? (
        <ChartArtifact
          title={artifact.title}
          imageUrl={imgSrc || undefined}
          svg={artifact.svg}
          points={artifact.chartPoints || []}
        />
      ) : isDoc ? (
        <DocumentArtifact content={artifact.content} />
      ) : (
        <pre className="artifacts-body">
          <code className={`hljs language-${artifact.language}`} dangerouslySetInnerHTML={{ __html: html }} />
        </pre>
      )}
    </aside>
  );
}

function escapeHtml(s: string) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
