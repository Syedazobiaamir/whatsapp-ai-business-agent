import { Bot } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate } from "react-router";
import { useAuth } from "../auth";
import { Button, ErrorNote, Input, Label } from "../components/ui";

export default function Login() {
  const { user, login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-dvh place-items-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-2xl border border-line bg-panel p-6 shadow-sm">
        <div className="flex items-center gap-2">
          <span className="grid size-9 place-items-center rounded-lg bg-brand text-brand-ink"><Bot size={20} aria-hidden /></span>
          <div>
            <h1 className="font-semibold">WhatsApp AI Agent</h1>
            <p className="text-xs text-ink-3">Sign in to the dashboard</p>
          </div>
        </div>
        <Label text="Email">
          <Input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Label>
        <Label text="Password">
          <Input type="password" autoComplete="current-password" required value={password}
                 onChange={(e) => setPassword(e.target.value)} />
        </Label>
        <ErrorNote error={error} />
        <Button type="submit" loading={busy} className="w-full">Sign in</Button>
      </form>
    </div>
  );
}
