"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { post } from "@/lib/api";
import { ErrorBox } from "@/components/ui";

export default function Login() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await post(`/auth/${mode}`, { email, password });
      router.push("/");
    } catch (x: any) {
      setErr(x.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="panel w-full max-w-sm space-y-4 p-6">
        <div>
          <h1 className="text-lg font-semibold">Pullback Radar</h1>
          <p className="text-xs text-mute">Research and decision support for U.S. stock pullback setups. Not financial advice.</p>
        </div>
        <div>
          <label className="label" htmlFor="email">Email</label>
          <input id="email" className="input" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div>
          <label className="label" htmlFor="pw">Password {mode === "register" && "(10+ characters)"}</label>
          <input id="pw" className="input" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        <ErrorBox error={err} />
        <button className="btn-primary w-full justify-center" disabled={busy}>
          {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
        <button type="button" className="w-full text-center text-xs text-info hover:underline" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "No account? Create one" : "Have an account? Sign in"}
        </button>
      </form>
    </div>
  );
}
