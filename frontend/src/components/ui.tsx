import { Flame, Loader2, Snowflake, Thermometer } from "lucide-react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import type { Temperature } from "../api/types";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function Card({ title, actions, children, className }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={cx("rounded-xl border border-line bg-panel", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-ink-2">{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}

/** Stat tile: one headline number (no chart needed for a single value). */
export function StatTile({ label, value, note, icon, alert }: {
  label: string; value: number | string; note?: string; icon?: ReactNode; alert?: boolean;
}) {
  return (
    <div className="rounded-xl border border-line bg-panel p-4">
      <div className="flex items-center justify-between text-xs font-medium text-ink-2">
        <span>{label}</span>
        <span aria-hidden className="text-ink-3">{icon}</span>
      </div>
      <div className="mt-2 text-3xl font-semibold tabular-nums">{value}</div>
      {note && <div className={cx("mt-1 text-xs", alert ? "font-medium text-red-600 dark:text-red-400" : "text-ink-3")}>{note}</div>}
    </div>
  );
}

/** Meter list: name + bar + figure per row. One hue; the figure is always visible as text,
 *  so the meaning never depends on color alone. */
export function MeterList({ rows, label }: { rows: { name: string; value: number }[]; label: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  const total = rows.reduce((s, r) => s + r.value, 0);
  return (
    <ul aria-label={label} className="space-y-3">
      {rows.map((r) => (
        <li key={r.name} className="group grid grid-cols-[6.5rem_1fr_3.5rem] items-center gap-3 text-sm"
            title={`${r.name}: ${r.value} of ${total}`}>
          <span className="truncate capitalize text-ink-2">{r.name}</span>
          <span className="h-2 rounded-full bg-viz-track">
            <span className="block h-2 rounded-full bg-viz transition-[width] group-hover:brightness-110"
                  style={{ width: `${(r.value / max) * 100}%`, minWidth: r.value ? 8 : 0 }} />
          </span>
          <span className="text-right tabular-nums text-ink">
            {r.value}
            <span className="ml-1 text-xs text-ink-3">{total ? Math.round((r.value / total) * 100) : 0}%</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

const TEMP = {
  hot: { icon: Flame, cls: "bg-orange-50 text-orange-800 ring-orange-200 dark:bg-orange-950 dark:text-orange-200 dark:ring-orange-900" },
  warm: { icon: Thermometer, cls: "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-900" },
  cold: { icon: Snowflake, cls: "bg-sky-50 text-sky-800 ring-sky-200 dark:bg-sky-950 dark:text-sky-200 dark:ring-sky-900" },
} as const;

/** Icon + word + color: readable for color-blind users and in print. */
export function TempBadge({ t }: { t: Temperature }) {
  const { icon: Icon, cls } = TEMP[t];
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium capitalize ring-1", cls)}>
      <Icon size={12} aria-hidden /> {t}
    </span>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "brand" | "good" | "warn" | "bad" }) {
  const tones = {
    neutral: "bg-black/5 text-ink-2 dark:bg-white/10",
    brand: "bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-200",
    good: "bg-green-50 text-green-800 dark:bg-green-950 dark:text-green-200",
    warn: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
    bad: "bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200",
  };
  return <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", tones[tone])}>{children}</span>;
}

export function Button({ variant = "primary", loading, className, children, ...props }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost"; loading?: boolean }) {
  const styles = {
    primary: "bg-brand text-brand-ink hover:brightness-110",
    secondary: "border border-line bg-panel hover:bg-black/5 dark:hover:bg-white/5",
    danger: "bg-red-600 text-white hover:bg-red-700",
    ghost: "hover:bg-black/5 dark:hover:bg-white/5",
  };
  return (
    <button {...props} disabled={props.disabled || loading}
      className={cx("inline-flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50", styles[variant], className)}>
      {loading && <Loader2 size={14} className="animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

const field = "rounded-lg border border-line bg-panel px-3 py-2 text-sm placeholder:text-ink-3";
// Full width by default, unless the caller sets its own width (e.g. "w-44").
const width = (cls?: string) => (cls && /(^|\s)w-/.test(cls) ? "" : "w-full");

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(field, width(props.className), props.className)} />;
}

export function Select({ children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(field, width(props.className), props.className)}>{children}</select>;
}

export function Label({ text, children }: { text: string; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="block text-xs font-medium text-ink-2 first-letter:uppercase">{text}</span>
      {children}
    </label>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 p-6 text-sm text-ink-2">
      <Loader2 size={16} className="animate-spin" aria-hidden /> {label}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
    {error instanceof Error ? error.message : String(error)}
  </p>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-8 text-center text-sm text-ink-3">{children}</p>;
}

export function Pager({ page, size, total, onPage }: { page: number; size: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="flex items-center justify-between gap-2 pt-3 text-sm text-ink-2">
      <span>{total === 0 ? "No results" : `${(page - 1) * size + 1}–${Math.min(page * size, total)} of ${total}`}</span>
      <div className="flex gap-2">
        <Button variant="secondary" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
        <Button variant="secondary" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button>
      </div>
    </div>
  );
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export const label = (s: string) => s.replace(/_/g, " ");
