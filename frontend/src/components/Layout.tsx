import { Activity, Bot, LayoutDashboard, LogOut, Menu, MessageSquare, Settings, Users, Workflow, X } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router";
import { useAuth } from "../auth";
import { cx, label } from "./ui";

const NAV = [
  { to: "/", text: "Overview", icon: LayoutDashboard, end: true },
  { to: "/leads", text: "Leads", icon: Activity },
  { to: "/conversations", text: "Conversations", icon: MessageSquare },
  { to: "/automation", text: "Automation", icon: Workflow },
  { to: "/settings", text: "Settings", icon: Settings },
  { to: "/team", text: "Team", icon: Users },
];

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Main" className="space-y-1">
      {NAV.map(({ to, text, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} onClick={onNavigate}
          className={({ isActive }) => cx(
            "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium",
            isActive ? "bg-brand/10 text-brand" : "text-ink-2 hover:bg-black/5 dark:hover:bg-white/5")}>
          <Icon size={18} aria-hidden /> {text}
        </NavLink>
      ))}
    </nav>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);

  const brand = (
    <div className="flex items-center gap-2 px-3 pb-6 pt-1">
      <span className="grid size-8 place-items-center rounded-lg bg-brand text-brand-ink"><Bot size={18} aria-hidden /></span>
      <div className="leading-tight">
        <div className="text-sm font-semibold">WhatsApp AI Agent</div>
        <div className="text-xs text-ink-3">Business dashboard</div>
      </div>
    </div>
  );
  const userBox = user && (
    <div className="border-t border-line px-3 pt-4 text-sm">
      <div className="truncate font-medium">{user.full_name}</div>
      <div className="truncate text-xs text-ink-3"><span className="capitalize">{label(user.role)}</span> · {user.email}</div>
      <button onClick={logout} className="mt-3 inline-flex items-center gap-2 text-xs text-ink-2 hover:text-ink">
        <LogOut size={14} aria-hidden /> Sign out
      </button>
    </div>
  );

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-dvh flex-col justify-between border-r border-line bg-panel p-3 lg:flex">
        <div>{brand}<Nav /></div>{userBox}
      </aside>

      {/* Mobile top bar + drawer */}
      <div className="sticky top-0 z-20 flex items-center justify-between border-b border-line bg-panel px-4 py-3 lg:hidden">
        <span className="text-sm font-semibold">WhatsApp AI Agent</span>
        <button aria-label="Open menu" onClick={() => setOpen(true)} className="rounded-lg p-1 hover:bg-black/5"><Menu size={22} /></button>
      </div>
      {open && (
        <div className="fixed inset-0 z-30 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col justify-between bg-panel p-3">
            <div>
              <div className="flex justify-end"><button aria-label="Close menu" onClick={() => setOpen(false)} className="p-1"><X size={20} /></button></div>
              {brand}<Nav onNavigate={() => setOpen(false)} />
            </div>{userBox}
          </aside>
        </div>
      )}

      <main className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  );
}
