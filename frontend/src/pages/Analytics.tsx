import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { LANG_LABEL, type Analytics as AnalyticsData, type Lang } from "../api/types";
import { ColumnChart, RateList, type Series } from "../components/charts";
import { Card, ErrorNote, MeterList, PageHeader, Select, Spinner, StatTile, label } from "../components/ui";

const REPLIES: Series[] = [
  { key: "ai", name: "AI replies", color: "--color-series-1" },
  { key: "human", name: "Team replies", color: "--color-series-2" },
];
// Ordered categories (cold < warm < hot): one hue, light -> dark, never three unrelated colors.
const LEADS: Series[] = [
  { key: "cold", name: "Cold", color: "--color-ord-1" },
  { key: "warm", name: "Warm", color: "--color-ord-2" },
  { key: "hot", name: "Hot", color: "--color-ord-3" },
];
const INBOUND: Series[] = [{ key: "inbound", name: "Customer messages", color: "--color-series-1" }];

const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);
export function duration(s: number | null): string {
  if (s === null) return "—";
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}
const dayLabel = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" });

export default function Analytics() {
  const [days, setDays] = useState(30);
  const q = useQuery({ queryKey: ["analytics", days], queryFn: () => api<AnalyticsData>(`/stats/analytics?days=${days}`) });

  const picker = (
    <Select aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-36">
      <option value={7}>Last 7 days</option>
      <option value={30}>Last 30 days</option>
      <option value={90}>Last 90 days</option>
    </Select>
  );
  if (q.isLoading) return <><PageHeader title="Analytics" actions={picker} /><Spinner /></>;
  if (q.error || !q.data) return <><PageHeader title="Analytics" actions={picker} /><ErrorNote error={q.error} /></>;
  const a = q.data;
  const t = a.totals;
  const daily = a.daily.map((d) => ({ label: d.day, values: {
    inbound: d.inbound, ai: d.ai_replies, human: d.human_replies,
    hot: d.new_leads_hot, warm: d.new_leads_warm, cold: d.new_leads_cold } }));

  return (
    <>
      <PageHeader title="Analytics" subtitle={`Trends for the last ${a.days} days · days counted in ${a.timezone}`} actions={picker} />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="Customer messages" value={t.inbound} note={`${t.conversations_started} new conversations`} />
        <StatTile label="Answered by AI" value={pct(t.ai_share)} note={`${t.ai_replies} AI · ${t.human_replies} team replies`} />
        <StatTile label="Median first response" value={duration(a.response_time.median_seconds)}
                  note={`90% within ${duration(a.response_time.p90_seconds)}`} />
        <StatTile label="Handoff rate" value={pct(t.handoff_rate)} note={`${t.handoffs} ${t.handoffs === 1 ? "handoff" : "handoffs"} to the team`}
                  alert={(t.handoff_rate ?? 0) > 0.5} />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card title="Customer messages per day">
          <ColumnChart data={daily} series={INBOUND} formatLabel={dayLabel} ariaLabel="Customer messages per day" />
        </Card>
        <Card title="Who replied: AI vs team">
          <ColumnChart data={daily} series={REPLIES} formatLabel={dayLabel} ariaLabel="Replies per day by AI and team" />
        </Card>
        <Card title="New leads per day">
          <ColumnChart data={daily} series={LEADS} formatLabel={dayLabel} ariaLabel="New leads per day by temperature" />
        </Card>
        <Card title="Win rate by lead temperature (all time)">
          <RateList label="Won leads by temperature"
                    rows={a.win_rate_by_temperature.map((r) => ({ name: r.name, part: r.won, total: r.total }))} />
          <p className="mt-4 text-xs text-ink-3">
            Checks whether the scoring works: hot leads should be won more often than cold ones.
          </p>
        </Card>
        <Card title="What customers ask about">
          {a.intents.length ? <MeterList label="Message intents"
                                         rows={a.intents.map((r) => ({ name: label(r.name), value: r.count }))} />
            : <p className="text-sm text-ink-3">No classified messages in this period</p>}
        </Card>
        <Card title="Customer languages">
          {a.languages.length ? <MeterList label="Customer languages"
                                           rows={a.languages.map((r) => ({ name: LANG_LABEL[r.name as Lang] ?? "Unknown", value: r.count }))} />
            : <p className="text-sm text-ink-3">No active customers in this period</p>}
          <dl className="mt-4 grid grid-cols-2 gap-2 text-xs text-ink-2">
            <dt>Answered from FAQs (AI unavailable)</dt><dd className="text-right tabular-nums">{t.fallback_replies}</dd>
            <dt>Replies blocked by price guardrail</dt><dd className="text-right tabular-nums">{t.guardrail_blocks}</dd>
            <dt>New leads</dt><dd className="text-right tabular-nums">{t.new_leads}</dd>
          </dl>
        </Card>
      </div>
    </>
  );
}
