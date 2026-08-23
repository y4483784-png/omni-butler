import type { ArtifactPayload } from "../api/client";
import type { Artifact } from "../store/useStore";
import type { ChartPoint } from "./chartPoints";

export function isChartPayload(a: {
  kind?: string;
  content?: string;
  image_base64?: string;
  svg?: string;
  language?: string;
}): boolean {
  if (a.kind === "image") return true;
  if (a.image_base64) return true;
  if (a.svg && a.svg.includes("<svg")) return true;
  const c = a.content || "";
  if (c.startsWith("data:image")) return true;
  if (c.includes("<svg")) return true;
  const lang = (a.language || "").toLowerCase();
  return lang === "png" || lang === "svg" || lang === "jpeg" || lang === "jpg" || lang === "webp";
}

/** Map SSE/history artifact JSON to panel store shape. */
export function artifactFromPayload(a: ArtifactPayload, id?: string): Artifact {
  const kind: Artifact["kind"] = isChartPayload(a)
    ? "image"
    : a.kind === "document"
      ? "document"
      : "code";
  const imageUrl =
    kind === "image"
      ? a.content?.startsWith("data:")
        ? a.content
        : a.image_base64
          ? `data:image/png;base64,${a.image_base64}`
          : undefined
      : undefined;
  const points = Array.isArray(a.chart_points)
    ? (a.chart_points.filter((p) => p && typeof p.value === "number" && p.label != null) as ChartPoint[])
    : [];
  return {
    id: id || `artifact-${Date.now()}`,
    title: a.title || (kind === "image" ? "分析图表" : kind === "document" ? "长文档" : "分析代码"),
    language: a.language || (kind === "image" ? "png" : kind === "document" ? "markdown" : "python"),
    content: a.content || "",
    kind,
    imageUrl,
    svg: kind === "image" ? a.svg : undefined,
    chartPoints: points,
  };
}

export function artifactButtonLabel(a: ArtifactPayload): string {
  if (isChartPayload(a) || a.kind === "image") return "查看图表";
  if (a.kind === "document") return "查看文档";
  return "查看代码";
}
