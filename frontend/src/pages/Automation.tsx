import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleSlash, ShieldAlert, XCircle } from "lucide-react";
import { useState } from "react";
import { api, qs } from "../api/client";
import type { AutomationError, FollowUp, N8nStatus, Page } from "../api/types";
import { useAuth } from "../auth";
import { Badge, Button, Card, Empty, ErrorNote, PageHeader, Select, Spinner, label, timeAgo } from "../components/ui";

function ExecStatus({ s }: { s: string }) {
  if (s === "success") return <Badge tone="good"><CheckCircle2 size={12} aria-hidden /> success</Badge>;
  if (s === "error" || s === "crashed") return <Badge tone="bad"><XCircle size={12} aria-hidden /> {s}</Badge>;
  return <Badge>{s}</Badge>;
}

function Workflows() {
  const n8n = useQuery({ queryKey: ["n8n"], queryFn: () => api<N8nStatus>("/monitoring/n8n"), refetchInterval: 30_000 });
  if (n8n.isLoading) return <Card title="Workflows"><Spinner /></Card>;
  const d = n8n.data;
  return (
    <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
      <Card title="Workflow status">
        {!d?.configured || !d.reachable ? (
          <p className="text-sm text-ink-2">{d?.hint ?? "n8n status unavailable."}</p>
        ) : (
          <ul className="space-y-2">
            {d.workflows.map((w) => (
              <li key={w.id} className="flex items-center justify-between gap-2 text-sm">
                <span className="truncate">{w.name}</span>
                {w.active ? <Badge tone="good"><CheckCircle2 size={12} aria-hidden /> active</Badge>
                          : <Badge><CircleSlash size={12} aria-hidden /> inactive</Badge>}
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Execution history (latest 20)">
        {!d?.executions.length ? <Empty>No executions to show.</Empty> : (
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full min-w-[32rem] text-left text-sm">
              <thead className="text-xs text-ink-3"><tr className="border-b border-line">
                <th className="px-4 py-2 font-medium">#</th><th className="px-2 py-2 font-medium">Workflow</th>
                <th className="px-2 py-2 font-medium">Trigger</th><th className="px-2 py-2 font-medium">Status</th>
                <th className="px-4 py-2 text-right font-medium">Started</th></tr></thead>
              <tbody>{d.executions.map((e) => (
                <tr key={e.id} className="border-b border-line last:border-0">
                  <td className="px-4 py-2 tabular-nums text-ink-3">{e.id}</td><td className="px-2 py-2">{e.workflow_name}</td>
                  <td className="px-2 py-2 text-ink-2">{e.mode}</td><td className="px-2 py-2"><ExecStatus s={e.status} /></td>
                  <td className="px-4 py-2 text-right text-xs text-ink-3">{timeAgo(e.started_at)}</td>
                </tr>))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

const RETRYABLE = ["send_message", "follow_up", "n8n_execution"];

/** Buttons for a human decision. A risky retry may duplicate a customer message: admin only,
 *  and the browser asks for confirmation first. The backend enforces the same rules. */
function ErrorActions({ e }: { e: AutomationError }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const act = useMutation({
    mutationFn: (action: "approve-retry" | "resolve" | "ignore") =>
      api<AutomationError>(`/automation-errors/${e.id}/${action}`, { method: "POST", body: action === "approve-retry" ? undefined : {} }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["automation-errors"] }); qc.invalidateQueries({ queryKey: ["stats"] }); },
  });
  if (!can("admin", "sales_agent") || ["resolved", "ignored"].includes(e.status)) return null;
  const risky = e.risk_level === "risky";
  const canRetry = e.operation && RETRYABLE.includes(e.operation) && (!risky || can("admin"));
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      {canRetry && (
        <Button variant={risky ? "danger" : "secondary"} loading={act.isPending && act.variables === "approve-retry"}
          onClick={() => (!risky || confirm("This retry could send a DUPLICATE message to the customer. Approve it?"))
                         && act.mutate("approve-retry")}>
          {risky ? "Approve retry" : "Retry now"}
        </Button>
      )}
      <Button variant="secondary" loading={act.isPending && act.variables === "resolve"} onClick={() => act.mutate("resolve")}>Mark resolved</Button>
      <Button variant="ghost" loading={act.isPending && act.variables === "ignore"} onClick={() => act.mutate("ignore")}>Ignore</Button>
      {risky && !can("admin") && <span className="text-xs text-ink-3">Only an admin can approve a risky retry.</span>}
      <ErrorNote error={act.error} />
    </div>
  );
}

function Errors() {
  const [status, setStatus] = useState("");
  const errors = useQuery({ queryKey: ["automation-errors", status],
    queryFn: () => api<Page<AutomationError>>(`/automation-errors${qs({ status, size: 30 })}`), refetchInterval: 30_000 });
  return (
    <Card title="Automation errors" actions={
      <Select aria-label="Error status" className="w-44" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">All</option><option value="open">Open</option><option value="needs_approval">Needs approval</option>
        <option value="retrying">Retrying</option><option value="resolved">Resolved</option><option value="ignored">Ignored</option>
      </Select>}>
      {errors.isLoading ? <Spinner /> : !errors.data?.items.length ? <Empty>No automation errors. ✅</Empty> : (
        <ul className="divide-y divide-line">
          {errors.data.items.map((e) => (
            <li key={e.id} className="py-3">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <Badge>{e.source}</Badge><Badge>{label(e.error_type)}</Badge>
                {e.risk_level === "risky"
                  ? <Badge tone="bad"><ShieldAlert size={12} aria-hidden /> risky: human approval</Badge>
                  : <Badge tone="good">safe</Badge>}
                <Badge tone={e.status === "resolved" ? "good" : e.status === "needs_approval" ? "bad" : "warn"}>{label(e.status)}</Badge>
                {e.occurrences > 1 && <Badge tone="warn">×{e.occurrences}</Badge>}
                <span className="text-ink-3">
                  retries {e.retry_count}/{e.max_retries} · last {timeAgo(e.last_seen_at ?? e.created_at)}
                  {e.status === "retrying" && e.next_retry_at && ` · next retry ${new Date(e.next_retry_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}
                  {e.alerted_at && " · alert sent"}
                </span>
              </div>
              <p className="mt-1 break-words text-sm">
                {e.workflow_name && <span className="font-medium">{e.workflow_name}{e.node_name ? ` › ${e.node_name}` : ""}: </span>}
                {e.error_message}
              </p>
              {e.resolution_note && <p className="mt-1 text-xs text-ink-3">Note: {e.resolution_note}</p>}
              <ErrorActions e={e} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function FollowUpQueue() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["follow-ups"], queryFn: () => api<Page<FollowUp>>(`/follow-ups${qs({ size: 30 })}`) });
  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "cancel" }) =>
      api<FollowUp>(`/follow-ups/${id}/${action}`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["follow-ups"] }),
  });
  return (
    <Card title="Follow-up queue" actions={<span className="text-xs text-ink-3">Marketing messages are sent only after approval</span>}>
      <ErrorNote error={act.error} />
      {q.isLoading ? <Spinner /> : !q.data?.items.length ? <Empty>No follow-ups planned.</Empty> : (
        <ul className="divide-y divide-line">
          {q.data.items.map((f) => (
            <li key={f.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div className="min-w-0">
                <div className="text-sm font-medium">{f.customer_name ?? `+${f.customer_wa_id}`}</div>
                <div className="text-xs text-ink-3">
                  {f.message_type === "template" ? `Template "${f.template_name}"` : "Text message"} · planned {timeAgo(f.created_at)}
                  {f.last_error && <span className="text-red-600"> · {f.last_error}</span>}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={f.status === "sent" ? "good" : f.status === "pending_approval" ? "warn" : "neutral"}>{label(f.status)}</Badge>
                {f.status === "pending_approval" && can("admin", "sales_agent") && (<>
                  <Button variant="secondary" onClick={() => act.mutate({ id: f.id, action: "cancel" })}>Cancel</Button>
                  <Button onClick={() => act.mutate({ id: f.id, action: "approve" })}>Approve</Button>
                </>)}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function Automation() {
  return (
    <>
      <PageHeader title="Automation monitoring" subtitle="n8n workflows, execution history, errors and follow-ups" />
      <div className="space-y-4">
        <Workflows />
        <div className="grid gap-4 xl:grid-cols-2"><Errors /><FollowUpQueue /></div>
      </div>
    </>
  );
}
