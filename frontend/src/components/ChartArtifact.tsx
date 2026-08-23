import { useCallback, useState, type MouseEvent } from "react";
import type { ChartPoint } from "../utils/chartPoints";
import { formatChartValue, pointsAtRatio } from "../utils/chartPoints";
import { artifactFileStem, downloadDataUrl, downloadText } from "../utils/download";

function svgMarkup(raw: string): string {
  const i = raw.indexOf("<svg");
  return i >= 0 ? raw.slice(i) : raw;
}

export function ChartArtifact({
  title,
  imageUrl,
  svg,
  points,
}: {
  title: string;
  imageUrl?: string;
  svg?: string;
  points: ChartPoint[];
}) {
  const [tip, setTip] = useState<{ x: number; y: number; items: ChartPoint[] } | null>(null);
  const stem = artifactFileStem(title, "chart");

  const onMove = useCallback(
    (e: MouseEvent<HTMLDivElement>) => {
      if (!points.length) {
        setTip(null);
        return;
      }
      const rect = e.currentTarget.getBoundingClientRect();
      const pad = 0.08;
      const raw = (e.clientX - rect.left) / Math.max(rect.width, 1);
      const ratio = (raw - pad) / (1 - 2 * pad);
      const items = pointsAtRatio(points, ratio);
      if (!items.length) {
        setTip(null);
        return;
      }
      setTip({ x: e.clientX - rect.left, y: e.clientY - rect.top, items });
    },
    [points]
  );

  const svgInner = svg ? svgMarkup(svg) : "";

  function exportPng() {
    if (imageUrl) downloadDataUrl(`${stem}.png`, imageUrl);
  }

  function exportSvg() {
    if (svg) downloadText(`${stem}.svg`, svg, "image/svg+xml;charset=utf-8");
  }

  return (
    <div className="artifacts-chart">
      <div
        className="artifacts-chart-stage"
        onMouseMove={onMove}
        onMouseLeave={() => setTip(null)}
      >
        {imageUrl ? (
          <img className="artifacts-image" src={imageUrl} alt={title || "分析图表"} />
        ) : svgInner ? (
          <div className="artifacts-svg" dangerouslySetInnerHTML={{ __html: svgInner }} />
        ) : (
          <p className="artifacts-empty">图表无法加载（可能未生成或数据为空）。</p>
        )}
        {tip ? (
          <div className="chart-tooltip" style={{ left: tip.x + 12, top: tip.y + 12 }}>
            <div className="chart-tooltip-label">{tip.items[0]?.label}</div>
            {tip.items.map((p, i) => (
              <div key={`${p.series}-${i}`} className="chart-tooltip-row">
                <span>{p.series && p.series !== "value" ? p.series : "数值"}</span>
                <strong>{formatChartValue(p.value)}</strong>
              </div>
            ))}
          </div>
        ) : null}
      </div>
      <div className="artifacts-export">
        {imageUrl ? (
          <button type="button" className="ghost-btn" onClick={exportPng}>
            导出 PNG
          </button>
        ) : null}
        {svg ? (
          <button type="button" className="ghost-btn" onClick={exportSvg}>
            导出 SVG
          </button>
        ) : null}
      </div>
    </div>
  );
}
