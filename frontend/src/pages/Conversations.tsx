import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bot, Clock, Hand, Search, Send, User, UserRound } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { api, qs } from "../api/client";
import { LANG_LABEL, type ConversationDetail, type Conversation, type Message, type Page } from "../api/types";
import { useAuth } from "../auth";
import { Badge, Button, Card, Empty, ErrorNote, Input, PageHeader, Select, Spinner, cx, label, timeAgo } from "../components/ui";

function ModeBadge({ mode }: { mode: "ai" | "human" }) {
  return mode === "ai"
    ? <Badge tone="brand"><Bot size={12} aria-hidden /> AI replying</Badge>
    : <Badge tone="warn"><UserRound size={12} aria-hidden /> Human</Badge>;
}

function ConversationList({ activeId }: { activeId?: string }) {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("");
  const list = useQuery({
    queryKey: ["conversations", q, mode],
    queryFn: () => api<Page<Conversation>>(`/conversations${qs({ q, mode, size: 50 })}`),
    refetchInterval: 10_000,
  });
  return (
    <Card className="h-full">
      <div className="mb-3 grid grid-cols-[1fr_7.5rem] gap-2">
        <label className="relative">
          <span className="sr-only">Search conversations</span>
          <Search size={16} className="absolute left-3 top-2.5 text-ink-3" aria-hidden />
          <Input className="pl-9" placeholder="Name or phone" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <Select aria-label="Mode" value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="">All</option><option value="ai">AI</option><option value="human">Human</option>
        </Select>
      </div>
      {list.isLoading ? <Spinner /> : !list.data?.items.length ? <Empty>No conversations yet.</Empty> : (
        <ul className="-mx-2 max-h-[70dvh] space-y-1 overflow-y-auto">
          {list.data.items.map((c) => (
            <li key={c.id}>
              <Link to={`/conversations/${c.id}`} aria-current={c.id === activeId ? "page" : undefined}
                className={cx("block rounded-lg px-2 py-2", c.id === activeId ? "bg-brand/10" : "hover:bg-black/5 dark:hover:bg-white/5")}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium">{c.customer.name ?? `+${c.customer.wa_id}`}</span>
                  <span className="shrink-0 text-xs text-ink-3">{timeAgo(c.last_message_at)}</span>
                </div>
                <div className="mt-0.5 flex items-center justify-between gap-2">
                  <span dir="auto" className="truncate text-xs text-ink-3">{c.last_message_preview ?? "—"}</span>
                  <span className="flex shrink-0 gap-1">
                    {c.has_pending_handoff && <Badge tone="bad"><Hand size={11} aria-hidden /> Handoff</Badge>}
                    {c.mode === "human" && !c.has_pending_handoff && <Badge tone="warn">Human</Badge>}
                  </span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

const SENDER = {
  customer: { name: "Customer", icon: User },
  ai: { name: "AI assistant", icon: Bot },
  human: { name: "Team member", icon: UserRound },
  system: { name: "Automation", icon: Clock },
} as const;

function Bubble({ m }: { m: Message }) {
  const mine = m.direction === "outbound";
  const S = SENDER[m.sender_type];
  const meta = m.ai_metadata as { modules?: string[]; confidence?: number | null; guardrails?: string[]; fallback?: boolean } | null;
  return (
    <li className={cx("flex", mine ? "justify-end" : "justify-start")}>
      <div className={cx("max-w-[85%] rounded-2xl px-3 py-2 text-sm sm:max-w-[70%]",
        mine ? (m.sender_type === "ai" ? "bg-brand/10" : "bg-amber-50 dark:bg-amber-950") : "border border-line bg-panel")}>
        <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-ink-3">
          <S.icon size={12} aria-hidden /><span className="font-medium">{S.name}</span>
          {m.intent && <Badge>{label(m.intent)}</Badge>}
          {meta?.fallback && <Badge tone="warn">fallback</Badge>}
          {meta?.guardrails?.length ? <Badge tone="bad">guardrail</Badge> : null}
        </div>
        <p dir="auto" className="whitespace-pre-wrap break-words">{m.body}</p>
        <div className="mt-1 flex flex-wrap justify-end gap-2 text-[11px] text-ink-3">
          {meta?.modules?.length ? <span>{meta.modules.join(" → ")}</span> : null}
          {typeof meta?.confidence === "number" && <span>conf {meta.confidence.toFixed(2)}</span>}
          <time dateTime={m.created_at}>{new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
          {mine && <span className={m.send_status === "failed" ? "font-medium text-red-600" : ""}>· {m.send_status}</span>}
        </div>
        {m.error_message && <p className="mt-1 text-xs text-red-600">{m.error_message}</p>}
      </div>
    </li>
  );
}

function ChatView({ id }: { id: string }) {
  const { can } = useAuth();
  const staff = can("admin", "sales_agent");
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const conv = useQuery({ queryKey: ["conversation", id], queryFn: () => api<ConversationDetail>(`/conversations/${id}`),
                          refetchInterval: 5_000 });   // near-real-time: new customer messages show up quickly
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [conv.data?.messages.length]);

  const refresh = (data?: ConversationDetail) => {
    if (data) qc.setQueryData(["conversation", id], data);
    qc.invalidateQueries({ queryKey: ["conversations"] });
    qc.invalidateQueries({ queryKey: ["stats"] });
  };
  const takeover = useMutation({ mutationFn: () => api<ConversationDetail>(`/conversations/${id}/takeover`, { method: "POST", body: {} }), onSuccess: refresh });
  const resume = useMutation({ mutationFn: () => api<ConversationDetail>(`/conversations/${id}/resume`, { method: "POST" }), onSuccess: refresh });
  const reply = useMutation({
    mutationFn: () => api<Message>(`/conversations/${id}/reply`, { method: "POST", body: { text } }),
    onSuccess: () => { setText(""); qc.invalidateQueries({ queryKey: ["conversation", id] }); },
  });

  if (conv.isLoading) return <Card><Spinner /></Card>;
  if (conv.error || !conv.data) return <ErrorNote error={conv.error ?? "Not found"} />;
  const c = conv.data;
  const canReply = staff && c.mode === "human" && c.within_24h_window;

  return (
    <Card title={
      <span className="flex flex-wrap items-center gap-2">
        <Link to="/conversations" className="lg:hidden" aria-label="Back to list"><ArrowLeft size={16} /></Link>
        {c.customer.name ?? `+${c.customer.wa_id}`}<span className="text-xs font-normal text-ink-3">+{c.customer.wa_id}</span>
      </span>}
      actions={
        <span className="flex flex-wrap items-center gap-2">
          <ModeBadge mode={c.mode} />
          {c.customer.language && <Badge>{LANG_LABEL[c.customer.language]}</Badge>}
          {c.has_pending_handoff && <Badge tone="bad"><Hand size={12} aria-hidden /> Handoff open</Badge>}
          <Badge tone={c.within_24h_window ? "good" : "neutral"}>
            <Clock size={12} aria-hidden /> {c.within_24h_window ? "24h window open" : "24h window closed"}
          </Badge>
          {staff && (c.mode === "ai"
            ? <Button variant="secondary" loading={takeover.isPending} onClick={() => takeover.mutate()}>Take over</Button>
            : <Button variant="secondary" loading={resume.isPending} onClick={() => resume.mutate()}>Resume AI</Button>)}
        </span>}>
      <ErrorNote error={takeover.error ?? resume.error} />
      <ol className="max-h-[58dvh] space-y-3 overflow-y-auto pr-1" aria-label="Messages" aria-live="polite">
        {c.messages.map((m) => <Bubble key={m.id} m={m} />)}
        <div ref={bottom} />
      </ol>

      <form className="mt-4 flex gap-2 border-t border-line pt-4"
            onSubmit={(e: FormEvent) => { e.preventDefault(); if (text.trim()) reply.mutate(); }}>
        <label className="flex-1">
          <span className="sr-only">Reply</span>
          <Input dir="auto" value={text} onChange={(e) => setText(e.target.value)} maxLength={4096} disabled={!canReply}
            placeholder={!staff ? "Read-only role" : c.mode === "ai" ? "Take over to reply as a person" :
              !c.within_24h_window ? "24h window closed: only templates allowed" : "Type a WhatsApp reply…"} />
        </label>
        <Button type="submit" disabled={!canReply || !text.trim()} loading={reply.isPending} aria-label="Send reply"><Send size={16} /></Button>
      </form>
      <ErrorNote error={reply.error} />
    </Card>
  );
}

export default function ConversationsPage() {
  const { id } = useParams();
  return (
    <>
      <PageHeader title="Conversations" subtitle="Message history, AI replies and human handoffs" />
      <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
        {/* On phones show either the list or the chat; on desktop both side by side. */}
        <div className={cx(id && "hidden lg:block")}><ConversationList activeId={id} /></div>
        <div className={cx(!id && "hidden lg:block")}>
          {id ? <ChatView key={id} id={id} /> : <Card><Empty>Select a conversation.</Empty></Card>}
        </div>
      </div>
    </>
  );
}
