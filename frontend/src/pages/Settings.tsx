import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Pencil, Plus, Trash2, XCircle } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { api, qs } from "../api/client";
import type { AgentStatus, FAQ, N8nStatus, Page, Service, Setting } from "../api/types";
import { useAuth } from "../auth";
import { Badge, Button, Card, Empty, ErrorNote, Input, Label, PageHeader, Spinner, cx, label } from "../components/ui";

const TABS = ["business_info", "ai_config", "rules", "faqs", "services", "connections"] as const;
type Tab = (typeof TABS)[number];
const TAB_NAMES: Record<Tab, string> = { business_info: "Business", ai_config: "AI", rules: "Rules", faqs: "FAQs",
  services: "Services", connections: "API status" };

/** Renders a form for any settings object: text, number, true/false and lists (comma separated).
 *  The backend validates every value with Pydantic, so bad input comes back as a clear error. */
function SettingForm({ setting, readOnly }: { setting: Setting; readOnly: boolean }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Record<string, unknown>>(setting.value);
  useEffect(() => setDraft(setting.value), [setting.value]);
  const save = useMutation({
    mutationFn: () => api<Setting>(`/settings/${setting.key}`, { method: "PUT", body: draft }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
  const set = (k: string, v: unknown) => setDraft((d) => ({ ...d, [k]: v }));

  return (
    <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      {Object.entries(draft).map(([k, v]) => (
        <Label key={k} text={label(k)}>
          {typeof v === "boolean" ? (
            <span className="flex items-center gap-2 py-2 text-sm">
              <input type="checkbox" checked={v} disabled={readOnly} onChange={(e) => set(k, e.target.checked)} className="size-4" />
              {v ? "On" : "Off"}
            </span>
          ) : typeof v === "number" ? (
            <Input type="number" step="any" value={v} disabled={readOnly} onChange={(e) => set(k, Number(e.target.value))} />
          ) : Array.isArray(v) ? (
            <Input value={v.join(", ")} disabled={readOnly}
                   onChange={(e) => set(k, e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
          ) : (
            <Input value={String(v ?? "")} disabled={readOnly} onChange={(e) => set(k, e.target.value)} />
          )}
        </Label>
      ))}
      <div className="flex items-center gap-3 sm:col-span-2">
        {!readOnly && <Button type="submit" loading={save.isPending}>Save</Button>}
        {save.isSuccess && <span className="text-sm text-green-700 dark:text-green-400" role="status">Saved ✓</span>}
        {readOnly && <span className="text-sm text-ink-3">Only admins can change settings.</span>}
      </div>
      <div className="sm:col-span-2"><ErrorNote error={save.error} /></div>
    </form>
  );
}

function FaqEditor({ faq, onDone }: { faq?: FAQ; onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ question: faq?.question ?? "", answer: faq?.answer ?? "", category: faq?.category ?? "general",
                                keywords: faq?.keywords ?? "", is_active: faq?.is_active ?? true });
  const save = useMutation({
    mutationFn: () => faq ? api(`/faqs/${faq.id}`, { method: "PATCH", body: f }) : api("/faqs", { method: "POST", body: f }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["faqs"] }); onDone(); },
  });
  return (
    <form className="space-y-3 rounded-lg border border-line p-3" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      <Label text="Question"><Input required minLength={5} value={f.question} onChange={(e) => setF({ ...f, question: e.target.value })} /></Label>
      <Label text="Answer">
        <textarea required rows={3} value={f.answer} onChange={(e) => setF({ ...f, answer: e.target.value })}
                  className="w-full rounded-lg border border-line bg-panel px-3 py-2 text-sm" />
      </Label>
      <div className="grid gap-3 sm:grid-cols-2">
        <Label text="Category"><Input value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} /></Label>
        <Label text="Keywords (help search)"><Input value={f.keywords} onChange={(e) => setF({ ...f, keywords: e.target.value })} /></Label>
      </div>
      <ErrorNote error={save.error} />
      <div className="flex gap-2"><Button type="submit" loading={save.isPending}>Save FAQ</Button>
        <Button type="button" variant="ghost" onClick={onDone}>Cancel</Button></div>
    </form>
  );
}

function Faqs({ admin }: { admin: boolean }) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const faqs = useQuery({ queryKey: ["faqs", q], queryFn: () => api<Page<FAQ>>(`/faqs${qs({ q, size: 100 })}`) });
  const del = useMutation({ mutationFn: (id: string) => api(`/faqs/${id}`, { method: "DELETE" }),
                            onSuccess: () => qc.invalidateQueries({ queryKey: ["faqs"] }) });
  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <Input placeholder="Search FAQs (full-text)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search FAQs" />
        {admin && <Button onClick={() => setEditing("new")}><Plus size={16} /> Add</Button>}
      </div>
      {editing === "new" && <FaqEditor onDone={() => setEditing(null)} />}
      <ErrorNote error={del.error} />
      {faqs.isLoading ? <Spinner /> : !faqs.data?.items.length ? <Empty>No FAQs found.</Empty> : (
        <ul className="divide-y divide-line">
          {faqs.data.items.map((f) => editing === f.id ? <li key={f.id} className="py-2"><FaqEditor faq={f} onDone={() => setEditing(null)} /></li> : (
            <li key={f.id} className="flex items-start justify-between gap-3 py-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-medium">{f.question}</span>
                  <Badge>{f.category}</Badge>{!f.is_active && <Badge tone="warn">inactive</Badge>}</div>
                <p className="mt-1 text-sm text-ink-2">{f.answer}</p>
              </div>
              {admin && <div className="flex shrink-0 gap-1">
                <Button variant="ghost" aria-label="Edit FAQ" onClick={() => setEditing(f.id)}><Pencil size={14} /></Button>
                <Button variant="ghost" aria-label="Delete FAQ"
                        onClick={() => confirm(`Delete "${f.question}"?`) && del.mutate(f.id)}><Trash2 size={14} /></Button>
              </div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Services({ admin }: { admin: boolean }) {
  const qc = useQueryClient();
  const services = useQuery({ queryKey: ["services"], queryFn: () => api<Service[]>("/services") });
  const toggle = useMutation({
    mutationFn: (s: Service) => api(`/services/${s.id}`, { method: "PATCH", body: { is_active: !s.is_active } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["services"] }),
  });
  if (services.isLoading) return <Spinner />;
  return (
    <>
      <p className="mb-3 text-sm text-ink-2">The AI may only quote prices listed here (price guardrail).</p>
      <ErrorNote error={toggle.error} />
      <ul className="divide-y divide-line">
        {services.data?.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
            <div className="min-w-0"><div className="text-sm font-medium">{s.name}</div><div className="text-xs text-ink-3">{s.description}</div></div>
            <div className="flex items-center gap-3">
              <span className="text-sm tabular-nums">{s.currency} {Number(s.price_from).toLocaleString()}–{Number(s.price_to).toLocaleString()}</span>
              {admin ? <Button variant="secondary" onClick={() => toggle.mutate(s)}>{s.is_active ? "Active" : "Inactive"}</Button>
                     : <Badge tone={s.is_active ? "good" : "neutral"}>{s.is_active ? "active" : "inactive"}</Badge>}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

function Status({ ok, children }: { ok: boolean; children: string }) {
  return (
    <li className="flex items-center justify-between gap-3 py-2 text-sm">
      <span>{children}</span>
      {ok ? <Badge tone="good"><CheckCircle2 size={12} aria-hidden /> OK</Badge> : <Badge tone="warn"><XCircle size={12} aria-hidden /> Not set</Badge>}
    </li>
  );
}

function Connections() {
  const agent = useQuery({ queryKey: ["agent-status"], queryFn: () => api<AgentStatus>("/agent/status") });
  const n8n = useQuery({ queryKey: ["n8n"], queryFn: () => api<N8nStatus>("/monitoring/n8n") });
  const health = useQuery({ queryKey: ["health"], queryFn: () => fetch("/health").then((r) => r.json()) });
  if (agent.isLoading) return <Spinner />;
  const a = agent.data!;
  return (
    <>
      <ul className="divide-y divide-line">
        <Status ok={health.data?.database === "ok"}>Database (PostgreSQL)</Status>
        <Status ok={a.gemini_configured}>{`Gemini AI (${a.gemini_model})`}</Status>
        <Status ok={a.whatsapp_configured}>{`WhatsApp Cloud API (${a.graph_api_version}, ${a.whatsapp_env})`}</Status>
        <Status ok={a.webhook_signature_check}>Webhook signature check (WA_APP_SECRET)</Status>
        <Status ok={Boolean(n8n.data?.reachable)}>n8n API</Status>
      </ul>
      <p className="mt-3 text-sm text-ink-2">
        WhatsApp mode: {a.whatsapp_mock_mode ? <Badge tone="warn">mock (nothing is really sent)</Badge> : <Badge tone="good">live</Badge>}
      </p>
      <p className="mt-2 text-xs text-ink-3">API keys stay on the server and are never shown here.</p>
    </>
  );
}

export default function SettingsPage() {
  const { can } = useAuth();
  const admin = can("admin");
  const [tab, setTab] = useState<Tab>("business_info");
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<Setting[]>("/settings") });
  const byKey = Object.fromEntries((settings.data ?? []).map((s) => [s.key, s]));

  return (
    <>
      <PageHeader title="Settings" subtitle="Business information, AI behaviour, knowledge base and connections" />
      <div role="tablist" aria-label="Settings sections" className="mb-4 flex gap-1 overflow-x-auto border-b border-line">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={cx("whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium",
              tab === t ? "border-brand text-brand" : "border-transparent text-ink-2 hover:text-ink")}>{TAB_NAMES[t]}</button>
        ))}
      </div>
      {settings.isLoading ? <Spinner /> : (
        <Card>
          {(tab === "business_info" || tab === "ai_config") && byKey[tab] && <SettingForm setting={byKey[tab]} readOnly={!admin} />}
          {tab === "rules" && (
            <div className="space-y-8">
              {(["lead_rules", "follow_up_rules", "handoff_rules"] as const).map((k) => byKey[k] && (
                <section key={k}><h3 className="mb-3 text-sm font-semibold capitalize">{label(k)}</h3>
                  <SettingForm setting={byKey[k]} readOnly={!admin} /></section>))}
            </div>
          )}
          {tab === "faqs" && <Faqs admin={admin} />}
          {tab === "services" && <Services admin={admin} />}
          {tab === "connections" && <Connections />}
        </Card>
      )}
    </>
  );
}
