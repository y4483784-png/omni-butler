import { useEffect, useRef, useState } from "react";
import {
  deleteDocument,
  listDocuments,
  reingestDocument,
  uploadDocuments,
} from "../api/client";
import type { KbDocument } from "../api/client";
import { useStore } from "../store/useStore";

const ACCEPT = ".pdf,.docx,.xlsx,.csv,.txt,.md";

const STAGE_LABEL: Record<string, string> = {
  pending: "排队中",
  parsing: "解析中",
  chunking: "分块中",
  embedding: "向量化中",
  ready: "完成",
  failed: "失败",
};

const STAGE_PCT: Record<string, number> = {
  pending: 8,
  parsing: 25,
  chunking: 50,
  embedding: 75,
  ready: 100,
  failed: 100,
};

function isInFlight(d: KbDocument) {
  return d.status === "pending" || d.status === "processing";
}

function stageLabel(d: KbDocument) {
  const stage = d.stage || d.status;
  if (STAGE_LABEL[stage]) return STAGE_LABEL[stage];
  if (d.status === "ready") return "完成";
  if (d.status === "failed") return "失败";
  return stage || d.status;
}

function stagePct(d: KbDocument) {
  const stage = d.stage || d.status;
  return STAGE_PCT[stage] ?? (isInFlight(d) ? 15 : 0);
}

export function KnowledgeBasePanel() {
  const { kbPanelOpen, setKbPanelOpen, kbFocusIds, toggleKbFocus } = useStore();
  const [docs, setDocs] = useState<KbDocument[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  async function refresh() {
    setDocs(await listDocuments());
  }

  useEffect(() => {
    if (kbPanelOpen) refresh().catch(() => setErr("加载知识库失败"));
  }, [kbPanelOpen]);

  // Poll while any document is still ingesting
  useEffect(() => {
    if (!kbPanelOpen) return;
    if (!docs.some(isInFlight)) return;
    const id = window.setInterval(() => {
      refresh().catch(() => {});
    }, 1500);
    return () => window.clearInterval(id);
  }, [kbPanelOpen, docs]);

  if (!kbPanelOpen) return null;

  async function onUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    if (files.length > 5) {
      setErr("单次最多上传 5 个文件");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await uploadDocuments(files);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "上传失败");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function onDelete(id: number) {
    if (!window.confirm("确定删除该文档及其分块？")) return;
    try {
      await deleteDocument(id);
      if (kbFocusIds.includes(id)) toggleKbFocus(id);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "删除失败");
    }
  }

  async function onReingest(id: number) {
    setErr("");
    try {
      await reingestDocument(id);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "重新解析失败");
    }
  }

  return (
    <aside className="kb-panel" aria-label="知识库">
      <div className="kb-head">
        <span className="kb-title">知识库</span>
        <button type="button" className="ghost-btn" onClick={() => setKbPanelOpen(false)}>
          ×
        </button>
      </div>
      <p className="kb-hint">
        支持 {ACCEPT} · 单文件 ≤20MB · 单次 ≤5 个
        <br />
        勾选「问答优先」后，知识库问答仅检索所选文档；不勾选则搜全库
      </p>
      <div className="kb-actions">
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          onChange={(e) => onUpload(e.target.files)}
        />
        <button
          type="button"
          className="new-btn"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
        >
          {busy ? "上传中…" : "上传文档"}
        </button>
      </div>
      {err ? <div className="kb-error">{err}</div> : null}
      <ul className="kb-list">
        {docs.length === 0 ? <li className="kb-empty">暂无文档</li> : null}
        {docs.map((d) => (
          <li key={d.id} className={`kb-item status-${d.status}`}>
            <label className="kb-focus" title="问答时仅检索此文档（可多选）">
              <input
                type="checkbox"
                checked={kbFocusIds.includes(d.id)}
                disabled={d.status !== "ready"}
                onChange={() => toggleKbFocus(d.id)}
              />
              <span>优先</span>
            </label>
            <div className="kb-item-main">
              <span className="kb-name" title={d.filename}>
                {d.filename}
              </span>
              <span className="kb-meta">
                {stageLabel(d)}
                {d.status === "ready"
                  ? ` · ${d.char_count ?? 0} 字 · ${d.chunk_count} 块`
                  : ""}
              </span>
              {isInFlight(d) ? (
                <div
                  className="kb-progress"
                  role="progressbar"
                  aria-valuenow={stagePct(d)}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <div className="kb-progress-bar" style={{ width: `${stagePct(d)}%` }} />
                </div>
              ) : null}
              {d.status === "ready" && d.warning ? (
                <span className="kb-warn" title={d.warning}>
                  {d.warning}
                </span>
              ) : null}
              {d.status === "failed" && d.error ? (
                <span className="kb-fail" title={d.error}>
                  {d.error}
                </span>
              ) : null}
            </div>
            <div className="kb-item-actions">
              <button
                type="button"
                className="kb-reparse"
                disabled={busy}
                onClick={() => onReingest(d.id)}
                title="按最新解析/分块策略重新处理（可强制重试卡住的文档）"
              >
                重新解析
              </button>
              <button
                type="button"
                className="del-btn"
                onClick={() => onDelete(d.id)}
                title="删除"
              >
                ×
              </button>
            </div>
          </li>
        ))}
      </ul>
    </aside>
  );
}
