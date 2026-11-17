import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Flame, Hand, MessageSquare, Target, Users } from "lucide-react";
import { Link } from "react-router";
import { api, qs } from "../api/client";
import { LEAD_STATUSES, type Handoff, type Lead, type OverviewStats, type Page } from "../api/types";
import { Card, Empty, ErrorNote, MeterList, PageHeader, Spinner, StatTile, TempBadge, label, timeAgo } from "../components/ui";

export default function Overview() {
  // refetchInterval: the overview refreshes itself every 20 s (good for a live demo)
  const stats = useQuery({ queryKey: ["stats"], queryFn: () => api<OverviewStats>("/stats/overview"), refetchInterval: 20_000 });
  const hot = useQuery({ queryKey: ["leads", "hot-recent"],
    queryFn: () => api<Page<Lead>>(`/leads${qs({ temperature: "hot", sort: "updated", size: 5 })}`) });
  const handoffs = useQuery({ queryKey: ["handoffs", "pending"],
    queryFn: () => api<Page<Handoff>>(`/handoffs${qs({ status: "pending", size: 5 })}`) });

  if (stats.isLoading) return <Spinner />;
  if (stats.error) return <ErrorNote error={stats.error} />;
  const s = stats.data!;

  return (
    <>
      <PageHeader title="Overview" subtitle="How the WhatsApp agent is performing right now" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Total customers" value={s.total_customers} icon={<Users size={16} />} note={`${s.messages_last_24h} messages in 24h`} />
        <StatTile label="Total leads" value={s.total_leads} icon={<Target size={16} />} />
        <StatTile label="Hot leads" value={s.hot_leads} icon={<Flame size={16} />} note={`${s.warm_leads} warm · ${s.cold_leads} cold`} />
        <StatTile label="Active conversations" value={s.active_conversations} icon={<MessageSquare size={16} />}
                  note={`${s.conversations_in_human_mode} handled by a person`} />
        <StatTile label="Pending handoffs" value={s.pending_handoffs} icon={<Hand size={16} />}
                  note={s.pending_handoffs ? "Waiting for a team member" : "None waiting"} alert={s.pending_handoffs > 0} />
        <StatTile label="Automation failures" value={s.automation_failures_open} icon={<AlertTriangle size={16} />}
                  note={s.automation_failures_open ? "⚠ Needs attention" : "All clear"} alert={s.automation_failures_open > 0} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Leads by temperature">
          <MeterList label="Leads by temperature" rows={[
            { name: "hot", value: s.hot_leads }, { name: "warm", value: s.warm_leads }, { name: "cold", value: s.cold_leads }]} />
        </Card>
        <Card title="Leads by pipeline status">
          <MeterList label="Leads by pipeline status"
                     rows={LEAD_STATUSES.map((st) => ({ name: label(st), value: s.leads_by_status[st] ?? 0 }))} />
        </Card>

        <Card title="Latest hot leads" actions={<Link to="/leads?temperature=hot" className="text-xs text-brand">View all</Link>}>
          {hot.data?.items.length ? (
            <ul className="divide-y divide-line">
              {hot.data.items.map((l) => (
                <li key={l.id}>
                  <Link to={`/leads/${l.id}`} className="flex items-center justify-between gap-3 py-2 hover:opacity-80">
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">{l.customer.name ?? `+${l.customer.wa_id}`}</span>
                      <span className="block truncate text-xs text-ink-3">{l.requirements ?? "No requirements yet"}</span>
                    </span>
                    <span className="flex shrink-0 items-center gap-2 text-xs text-ink-3">
                      <span className="tabular-nums">{l.score}/100</span><TempBadge t={l.temperature} />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : <Empty>No hot leads yet.</Empty>}
        </Card>

        <Card title="Waiting for a human" actions={<Link to="/conversations" className="text-xs text-brand">Conversations</Link>}>
          {handoffs.data?.items.length ? (
            <ul className="divide-y divide-line">
              {handoffs.data.items.map((h) => (
                <li key={h.id}>
                  <Link to={`/conversations/${h.conversation_id}`} className="flex items-center justify-between gap-3 py-2 hover:opacity-80">
                    <span className="truncate text-sm">{h.reason}</span>
                    <span className="shrink-0 text-xs text-ink-3">{timeAgo(h.created_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : <Empty>Nobody is waiting. 🎉</Empty>}
        </Card>
      </div>
    </>
  );
}
