import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, MessageSquare } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { api } from "../api/client";
import { LEAD_STATUSES, type Agent, type Customer, type LeadDetail } from "../api/types";
import { useAuth } from "../auth";
import { Badge, Button, Card, ErrorNote, Input, Label, PageHeader, Select, Spinner, TempBadge, label, timeAgo } from "../components/ui";

function Fact({ name, value }: { name: string; value: string | null | undefined }) {
  return (
    <div>
      <dt className="text-xs text-ink-3">{name}</dt>
      <dd className="mt-0.5 text-sm">{value || "—"}</dd>
    </div>
  );
}

export default function LeadDetailPage() {
  const { id } = useParams();
  const { can } = useAuth();
  const staff = can("admin", "sales_agent");
  const qc = useQueryClient();
  const lead = useQuery({ queryKey: ["lead", id], queryFn: () => api<LeadDetail>(`/leads/${id}`) });
  const team = useQuery({ queryKey: ["team"], queryFn: () => api<Agent[]>("/team") });

  const [status, setStatus] = useState("");
  const [assignee, setAssignee] = useState("");
  const [note, setNote] = useState("");
  useEffect(() => {
    if (lead.data) { setStatus(lead.data.status); setAssignee(lead.data.assigned_agent_id ?? ""); }
  }, [lead.data]);

  const save = useMutation({
    mutationFn: () => api<LeadDetail>(`/leads/${id}`, { method: "PATCH", body: {
      status, ...(assignee ? { assigned_agent_id: assignee } : {}), ...(note.trim() ? { note: note.trim() } : {}) } }),
    onSuccess: (data) => {
      qc.setQueryData(["lead", id], data);
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      setNote("");
    },
  });

  if (lead.isLoading) return <Spinner />;
  if (lead.error || !lead.data) return <ErrorNote error={lead.error ?? "Lead not found"} />;
  const l = lead.data;
  const c: Customer = l.customer;

  return (
    <>
      <Link to="/leads" className="mb-3 inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink"><ArrowLeft size={14} /> Leads</Link>
      <PageHeader title={c.name ?? `+${c.wa_id}`} subtitle={`Lead since ${new Date(l.created_at).toLocaleDateString()}`}
        actions={l.conversation_id && (
          <Link to={`/conversations/${l.conversation_id}`}><Button variant="secondary"><MessageSquare size={16} /> Open conversation</Button></Link>)} />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card title="AI qualification">
            <div className="flex flex-wrap items-center gap-3">
              <TempBadge t={l.temperature} />
              <span className="text-2xl font-semibold tabular-nums">{l.score}<span className="text-sm text-ink-3">/100</span></span>
              <span className="h-2 min-w-32 flex-1 rounded-full bg-viz-track" role="meter" aria-valuenow={l.score} aria-valuemin={0} aria-valuemax={100} aria-label="Lead score">
                <span className="block h-2 rounded-full bg-viz" style={{ width: `${l.score}%` }} />
              </span>
            </div>
            <p className="mt-3 text-sm text-ink-2">{l.qualification_reason ?? "Not qualified yet."}</p>
          </Card>

          <Card title="What we know">
            <dl className="grid gap-4 sm:grid-cols-2">
              <Fact name="Requirements" value={l.requirements} />
              <Fact name="Interested in" value={l.interested_services?.join(", ")} />
              <Fact name="Budget" value={l.budget_text} />
              <Fact name="Timeline" value={l.timeline} />
              <Fact name="WhatsApp" value={`+${c.wa_id}`} />
              <Fact name="Email" value={c.email} />
              <Fact name="Company" value={c.company} />
              <Fact name="Marketing consent" value={c.opted_out_at ? "Opted out" : c.marketing_opt_in ? "Opted in" : "Not given"} />
            </dl>
          </Card>

          <Card title="Activity">
            <ol className="relative space-y-4 border-l border-line pl-5">
              {l.activities.map((a) => (
                <li key={a.id}>
                  <span className="absolute -left-1.5 mt-1.5 size-3 rounded-full border-2 border-panel bg-viz" aria-hidden />
                  <div className="flex flex-wrap items-center gap-2 text-xs text-ink-3">
                    <Badge tone={a.actor_type === "ai" ? "brand" : "neutral"}>{a.actor_type}</Badge>
                    <span>{label(a.activity_type)}</span><span>· {timeAgo(a.created_at)}</span>
                  </div>
                  <p className="mt-1 text-sm">{a.description}</p>
                </li>
              ))}
            </ol>
          </Card>
        </div>

        <Card title="Update lead" className="h-fit">
          {!staff ? <p className="text-sm text-ink-3">Your role is read-only.</p> : (
            <form className="space-y-3" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
              <Label text="Status">
                <Select value={status} onChange={(e) => setStatus(e.target.value)}>
                  {LEAD_STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}
                </Select>
              </Label>
              <Label text="Assigned to">
                <Select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
                  <option value="">Unassigned</option>
                  {team.data?.filter((a) => a.is_active && a.role !== "viewer").map((a) =>
                    <option key={a.id} value={a.id}>{a.full_name}</option>)}
                </Select>
              </Label>
              <Label text="Add a note">
                <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Called, sending proposal" maxLength={2000} />
              </Label>
              <ErrorNote error={save.error} />
              {save.isSuccess && <p className="text-sm text-green-700 dark:text-green-400" role="status">Saved ✓</p>}
              <Button type="submit" loading={save.isPending} className="w-full">Save changes</Button>
            </form>
          )}
        </Card>
      </div>
    </>
  );
}
