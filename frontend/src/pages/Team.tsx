import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Agent, Role } from "../api/types";
import { useAuth } from "../auth";
import { Badge, Button, Card, ErrorNote, Input, Label, PageHeader, Select, Spinner, label, timeAgo } from "../components/ui";

const ROLES: Role[] = ["admin", "sales_agent", "viewer"];

function AddMember({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ full_name: "", email: "", phone: "", role: "sales_agent" as Role, password: "" });
  const add = useMutation({
    mutationFn: () => api<Agent>("/team", { method: "POST", body: { ...f, phone: f.phone || null } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["team"] }); onDone(); },
  });
  return (
    <form className="mb-4 grid gap-3 rounded-lg border border-line p-3 sm:grid-cols-2 lg:grid-cols-5"
          onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate(); }}>
      <Label text="Full name"><Input required minLength={2} value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Label>
      <Label text="Email"><Input type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Label>
      <Label text="Phone"><Input value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Label>
      <Label text="Role">
        <Select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as Role })}>
          {ROLES.map((r) => <option key={r} value={r}>{label(r)}</option>)}
        </Select>
      </Label>
      <Label text="Temporary password (min 10)">
        <Input type="password" required minLength={10} autoComplete="new-password" value={f.password}
               onChange={(e) => setF({ ...f, password: e.target.value })} />
      </Label>
      <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-5">
        <Button type="submit" loading={add.isPending}>Add member</Button>
        <Button type="button" variant="ghost" onClick={onDone}>Cancel</Button>
      </div>
      <div className="sm:col-span-2 lg:col-span-5"><ErrorNote error={add.error} /></div>
    </form>
  );
}

export default function Team() {
  const { can, user } = useAuth();
  const admin = can("admin");
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const team = useQuery({ queryKey: ["team"], queryFn: () => api<Agent[]>("/team") });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<Agent> }) => api<Agent>(`/team/${id}`, { method: "PATCH", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["team"] }),
  });

  return (
    <>
      <PageHeader title="Team" subtitle="People who handle handoffs and leads · roles control what they can do"
        actions={admin && !adding && <Button onClick={() => setAdding(true)}><Plus size={16} /> Add member</Button>} />
      <Card>
        {adding && <AddMember onDone={() => setAdding(false)} />}
        <ErrorNote error={update.error} />
        {team.isLoading ? <Spinner /> : (
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full min-w-[40rem] text-left text-sm">
              <thead className="text-xs text-ink-3"><tr className="border-b border-line">
                <th className="px-4 py-2 font-medium">Name</th><th className="px-2 py-2 font-medium">Role</th>
                <th className="px-2 py-2 font-medium">Status</th><th className="px-4 py-2 text-right font-medium">Last login</th></tr></thead>
              <tbody>
                {team.data?.map((a) => {
                  const self = a.id === user?.id;
                  return (
                    <tr key={a.id} className="border-b border-line last:border-0">
                      <td className="px-4 py-3"><div className="font-medium">{a.full_name}{self && <span className="text-ink-3"> (you)</span>}</div>
                        <div className="text-xs text-ink-3">{a.email}</div></td>
                      <td className="px-2 py-3">
                        {admin && !self ? (
                          <Select aria-label={`Role of ${a.full_name}`} className="w-36" value={a.role}
                                  onChange={(e) => update.mutate({ id: a.id, body: { role: e.target.value as Role } })}>
                            {ROLES.map((r) => <option key={r} value={r}>{label(r)}</option>)}
                          </Select>) : <Badge>{label(a.role)}</Badge>}
                      </td>
                      <td className="px-2 py-3">
                        {admin && !self ? (
                          <Button variant="secondary" onClick={() => update.mutate({ id: a.id, body: { is_active: !a.is_active } })}>
                            {a.is_active ? "Active · deactivate" : "Inactive · activate"}</Button>
                        ) : <Badge tone={a.is_active ? "good" : "neutral"}>{a.is_active ? "active" : "inactive"}</Badge>}
                      </td>
                      <td className="px-4 py-3 text-right text-xs text-ink-3">{timeAgo(a.last_login_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <p className="mt-3 text-xs text-ink-3">Admin: everything · Sales agent: leads, conversations, follow-ups · Viewer: read-only.
        The server enforces these rules; hidden buttons are only for convenience.</p>
    </>
  );
}
