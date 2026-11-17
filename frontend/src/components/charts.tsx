import { useEffect, useRef, useState } from "react";
import { cx } from "./ui";

/* Small dependency-free SVG charts, following the data-viz rules used in this project:
   thin marks with rounded data-ends, a 2px surface gap between stacked segments, recessive grid,
   legend whenever there are 2+ series, a hover tooltip, and a table view (accessibility). */

export interface Series { key: string; name: string; color: string }   // color = CSS variable
export interface Point { label: string; values: Record<string, number> }

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(240, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

export function niceMax(v: number): number {
  if (v <= 4) return 4;
  // 4 gridline steps; pick the step from the size of ONE step (v/4), and keep ticks whole numbers.
  const pow = 10 ** Math.floor(Math.log10(v / 4));
  const step = [1, 2, 2.5, 5, 10].find((s) => s * pow * 4 >= v && Number.isInteger(s * pow))! * pow;
  return step * 4;
}

export function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;                       // one series: the title names it
  return (
    <ul className="mb-2 flex flex-wrap gap-4 text-xs text-ink-2" aria-label="Legend">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5">
          <span className="inline-block size-2.5 rounded-sm" style={{ background: `var(${s.color})` }} aria-hidden />
          {s.name}
        </li>
      ))}
    </ul>
  );
}

/** Stacked column chart over days. */
export function ColumnChart({ data, series, height = 200, ariaLabel, formatLabel = (l) => l }: {
  data: Point[]; series: Series[]; height?: number; ariaLabel: string; formatLabel?: (l: string) => string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const totals = data.map((d) => series.reduce((s, x) => s + (d.values[x.key] ?? 0), 0));
  const max = niceMax(Math.max(0, ...totals));
  const pad = { l: 32, r: 8, t: 8, b: 22 };
  const w = width - pad.l - pad.r;
  const h = height - pad.t - pad.b;
  const slot = w / Math.max(1, data.length);
  const bar = Math.max(2, Math.min(28, slot * 0.68));
  const y = (v: number) => pad.t + h - (v / max) * h;
  const every = Math.ceil(data.length / Math.max(2, Math.floor(w / 64)));   // avoid label collisions
  const empty = totals.every((t) => t === 0);

  return (
    <div>
      <div className="flex items-start justify-between gap-2">
        <Legend series={series} />
        <button onClick={() => setTable((t) => !t)} className="ml-auto text-xs text-brand hover:underline">
          {table ? "Show chart" : "Show table"}
        </button>
      </div>
      {table ? (
        <div className="max-h-72 overflow-auto">
          <table className="w-full text-left text-xs tabular-nums">
            <thead className="text-ink-3"><tr><th className="py-1 font-medium">Day</th>
              {series.map((s) => <th key={s.key} className="py-1 text-right font-medium">{s.name}</th>)}</tr></thead>
            <tbody>{data.map((d) => (
              <tr key={d.label} className="border-t border-line"><td className="py-1">{formatLabel(d.label)}</td>
                {series.map((s) => <td key={s.key} className="py-1 text-right">{d.values[s.key] ?? 0}</td>)}</tr>))}
            </tbody>
          </table>
        </div>
      ) : (
        <div ref={ref} className="relative" onMouseLeave={() => setHover(null)}>
          <svg width={width} height={height} role="img" aria-label={ariaLabel} className="block">
            {[0, 0.25, 0.5, 0.75, 1].map((f) => (
              <g key={f}>
                <line x1={pad.l} x2={width - pad.r} y1={y(max * f)} y2={y(max * f)}
                      stroke="var(--color-grid)" strokeWidth={1} />
                <text x={pad.l - 6} y={y(max * f) + 3} textAnchor="end" fontSize={10} fill="var(--color-ink-3)">
                  {Math.round(max * f)}
                </text>
              </g>
            ))}
            {data.map((d, i) => {
              const x = pad.l + i * slot + (slot - bar) / 2;
              let base = 0;
              const parts = series.map((s) => ({ s, v: d.values[s.key] ?? 0 })).filter((p) => p.v > 0);
              return (
                <g key={d.label} opacity={hover === null || hover === i ? 1 : 0.45}>
                  {parts.map(({ s, v }, j) => {
                    const top = y(base + v);
                    const bottom = y(base);
                    base += v;
                    const isTop = j === parts.length - 1;
                    const gap = j > 0 ? 2 : 0;                                // surface gap between segments
                    const hgt = Math.max(1, bottom - top - gap);
                    const r = isTop ? Math.min(4, bar / 2, hgt) : 0;          // rounded data-end on top only
                    const path = `M${x},${top + hgt} V${top + r} Q${x},${top} ${x + r},${top} H${x + bar - r} `
                      + `Q${x + bar},${top} ${x + bar},${top + r} V${top + hgt} Z`;
                    return <path key={s.key} d={path} fill={`var(${s.color})`} />;
                  })}
                  {i % every === 0 && (
                    <text x={x + bar / 2} y={height - 6} textAnchor="middle" fontSize={10} fill="var(--color-ink-3)">
                      {formatLabel(d.label)}
                    </text>
                  )}
                  {/* hit target wider than the mark */}
                  <rect x={pad.l + i * slot} y={pad.t} width={slot} height={h} fill="transparent"
                        onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={0}
                        aria-label={`${formatLabel(d.label)}: ${series.map((s) => `${s.name} ${d.values[s.key] ?? 0}`).join(", ")}`} />
                </g>
              );
            })}
          </svg>
          {empty && <p className="absolute inset-0 grid place-items-center text-sm text-ink-3">No activity in this period</p>}
          {hover !== null && !empty && (
            <div role="tooltip"
                 className="pointer-events-none absolute z-10 rounded-lg border border-line bg-panel px-3 py-2 text-xs shadow-sm"
                 style={{ left: Math.min(width - 150, Math.max(0, pad.l + hover * slot + slot / 2 - 70)), top: 0 }}>
              <div className="mb-1 font-medium">{formatLabel(data[hover].label)}</div>
              {series.map((s) => (
                <div key={s.key} className="flex items-center justify-between gap-4 text-ink-2">
                  <span className="flex items-center gap-1.5">
                    <span className="inline-block size-2 rounded-sm" style={{ background: `var(${s.color})` }} />{s.name}
                  </span>
                  <span className="tabular-nums text-ink">{data[hover].values[s.key] ?? 0}</span>
                </div>
              ))}
              {series.length > 1 && <div className="mt-1 flex justify-between border-t border-line pt-1 text-ink-2">
                <span>Total</span><span className="tabular-nums text-ink">{totals[hover]}</span></div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Rate per row ("won / total"), one hue, number always shown as text. */
export function RateList({ rows, label }: { rows: { name: string; part: number; total: number }[]; label: string }) {
  return (
    <ul aria-label={label} className="space-y-3">
      {rows.map((r) => {
        const pct = r.total ? Math.round((r.part / r.total) * 100) : 0;
        return (
          <li key={r.name} className="grid grid-cols-[5rem_1fr_6.5rem] items-center gap-3 text-sm">
            <span className="capitalize text-ink-2">{r.name}</span>
            <span className="h-2 rounded-full bg-viz-track">
              <span className={cx("block h-2 rounded-full bg-viz")} style={{ width: `${pct}%`, minWidth: r.part ? 8 : 0 }} />
            </span>
            <span className="text-right tabular-nums">{r.part} / {r.total}<span className="ml-1 text-xs text-ink-3">{pct}%</span></span>
          </li>
        );
      })}
    </ul>
  );
}
