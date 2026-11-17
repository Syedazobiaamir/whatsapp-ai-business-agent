import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, qs } from "../api/client";
import { LEAD_STATUSES, type Lead, type Page } from "../api/types";
import { Badge, Card, Empty, ErrorNote, Input, PageHeader, Pager, Select, Spinner, TempBadge, label, timeAgo } from "../components/ui";

const SIZE = 20;

export default function LeadsPage() {
  // Filters live in the URL (?temperature=hot&page=2): shareable links + browser back button work.
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [q, setQ] = useState(params.get("q") ?? "");
  const filters = {
    q: params.get("q") ?? "", temperature: params.get("temperature") ?? "", status: params.get("status") ?? "",
    sort: params.get("sort") ?? "updated", page: Number(params.get("page") ?? 1),
  };
  const update = (patch: Record<string, string | number>) => {
    const next = { ...filters, page: 1, ...patch };
    setParams(Object.fromEntries(Object.entries(next).filter(([, v]) => v !== "" && v !== 1 && v !== "updated")
      .map(([k, v]) => [k, String(v)])));
  };

  // Debounce: search 300 ms after the user stops typing, not on every key press.
  useEffect(() => {
    const t = setTimeout(() => { if (q !== filters.q) update({ q }); }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const leads = useQuery({
    queryKey: ["leads", filters],
    queryFn: () => api<Page<Lead>>(`/leads${qs({ ...filters, size: SIZE })}`),
    placeholderData: keepPreviousData,   // keep the old page visible while the next loads
  });

  return (
    <>
      <PageHeader title="Leads" subtitle="Qualified automatically by the AI · update status as you work them" />
      <Card>
        <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-[1fr_10rem_10rem_10rem]">
          <label className="relative">
            <span className="sr-only">Search leads</span>
            <Search size={16} className="absolute left-3 top-2.5 text-ink-3" aria-hidden />
            <Input className="pl-9" placeholder="Search name, phone, company, needs…" value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
          <Select aria-label="Temperature" value={filters.temperature} onChange={(e) => update({ temperature: e.target.value })}>
            <option value="">All temperatures</option><option value="hot">Hot</option><option value="warm">Warm</option><option value="cold">Cold</option>
          </Select>
          <Select aria-label="Status" value={filters.status} onChange={(e) => update({ status: e.target.value })}>
            <option value="">All statuses</option>
            {LEAD_STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}
          </Select>
          <Select aria-label="Sort" value={filters.sort} onChange={(e) => update({ sort: e.target.value })}>
            <option value="updated">Recently updated</option><option value="score">Highest score</option><option value="newest">Newest</option>
          </Select>
        </div>

        <ErrorNote error={leads.error} />
        {leads.isLoading ? <Spinner /> : !leads.data?.items.length ? <Empty>No leads match these filters.</Empty> : (<>
          {/* Phones: a card list (tables need sideways scrolling on small screens) */}
          <ul className="divide-y divide-line sm:hidden">
            {leads.data.items.map((l) => (
              <li key={l.id}>
                <button onClick={() => navigate(`/leads/${l.id}`)} className="w-full py-3 text-left">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-medium">{l.customer.name ?? `+${l.customer.wa_id}`}</span>
                    <TempBadge t={l.temperature} />
                  </div>
                  <div className="mt-1 flex items-center justify-between gap-2 text-xs text-ink-3">
                    <span className="truncate">{l.interested_services?.join(", ") || l.requirements || "No details yet"}</span>
                    <span className="shrink-0 tabular-nums">{l.score}/100 · {label(l.status)}</span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
          <div className="-mx-4 hidden overflow-x-auto sm:block">
            <table className="w-full min-w-[44rem] text-left text-sm">
              <thead className="text-xs text-ink-3">
                <tr className="border-b border-line">
                  <th className="px-4 py-2 font-medium">Customer</th><th className="px-2 py-2 font-medium">Lead</th>
                  <th className="px-2 py-2 font-medium">Score</th><th className="px-2 py-2 font-medium">Status</th>
                  <th className="px-2 py-2 font-medium">Interested in</th><th className="px-2 py-2 font-medium">Budget</th>
                  <th className="px-4 py-2 text-right font-medium">Updated</th>
                </tr>
              </thead>
              <tbody>
                {leads.data.items.map((l) => (
                  <tr key={l.id} tabIndex={0} onClick={() => navigate(`/leads/${l.id}`)}
                      onKeyDown={(e) => e.key === "Enter" && navigate(`/leads/${l.id}`)}
                      className="cursor-pointer border-b border-line last:border-0 hover:bg-black/[.03] dark:hover:bg-white/[.03]">
                    <td className="px-4 py-3">
                      <div className="font-medium">{l.customer.name ?? "Unknown"}</div>
                      <div className="text-xs text-ink-3">+{l.customer.wa_id}</div>
                    </td>
                    <td className="px-2 py-3"><TempBadge t={l.temperature} /></td>
                    <td className="px-2 py-3 tabular-nums">{l.score}</td>
                    <td className="px-2 py-3"><Badge>{label(l.status)}</Badge></td>
                    <td className="max-w-[14rem] truncate px-2 py-3 text-ink-2">{l.interested_services?.join(", ") || "—"}</td>
                    <td className="px-2 py-3 text-ink-2">{l.budget_text ?? "—"}</td>
                    <td className="px-4 py-3 text-right text-xs text-ink-3">{timeAgo(l.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>)}
        {leads.data && <Pager page={filters.page} size={SIZE} total={leads.data.total} onPage={(p) => update({ page: p })} />}
      </Card>
    </>
  );
}
