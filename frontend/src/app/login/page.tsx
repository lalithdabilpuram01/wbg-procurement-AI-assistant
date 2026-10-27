"use client";

import { useState } from "react";
import { BrainCircuit, Database, FileSearch, KeyRound, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, Input, Label, Spinner } from "@/components/ui/primitives";

const FEATURES = [
  { icon: Database, text: "Contract awards, notices and project data from World Bank open data" },
  { icon: FileSearch, text: "237 appraisal, status, completion and procurement-plan documents" },
  { icon: BrainCircuit, text: "Tender-duration and risk models for new packages" },
  { icon: ShieldCheck, text: "Every answer checked against its cited sources" },
];

export default function LoginPage() {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const response = await fetch("/api/session", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
    }).catch(() => null);
    if (response?.ok) {
      window.location.assign("/");
      return;
    }
    const body = await response?.json().catch(() => ({}));
    setError(body?.detail === "invalid token" ? "That access token is not valid." : body?.detail ?? "The API is not reachable.");
    setBusy(false);
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <section className="relative hidden overflow-hidden bg-gradient-to-br from-[#0b2545] via-[#0f3a6b] to-[#0078d4] p-12 text-white lg:flex lg:flex-col">
        <div className="absolute -right-24 -top-24 size-96 rounded-full bg-white/5" />
        <div className="absolute -bottom-32 -left-16 size-[28rem] rounded-full bg-[#2b88d8]/20" />
        <div className="relative flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-xl bg-white/15 font-bold">PI</div>
          <span className="text-lg font-semibold">Procurement Intelligence</span>
        </div>
        <div className="relative mt-auto max-w-md">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight">Plan procurement with the evidence of past projects.</h1>
          <ul className="mt-8 space-y-4">
            {FEATURES.map((f) => (
              <li key={f.text} className="flex items-start gap-3 text-sm text-white/85">
                <f.icon className="mt-0.5 size-5 shrink-0 text-[#9fd0ff]" /> {f.text}
              </li>
            ))}
          </ul>
        </div>
        <p className="relative mt-12 text-xs text-white/50">Assessment prototype · public World Bank Group data</p>
      </section>
      <section className="flex items-center justify-center p-6">
        <Card className="w-full max-w-sm p-8">
          <div className="grid size-10 place-items-center rounded-xl bg-primary-soft text-primary"><KeyRound className="size-5" /></div>
          <h2 className="mt-4 text-xl font-semibold">Sign in</h2>
          <p className="mt-1 text-sm text-muted-foreground">Use the access token issued for you with <code className="rounded bg-muted px-1">api add-user</code>.</p>
          <form onSubmit={submit} className="mt-6 space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="token">Access token</Label>
              <Input id="token" type="password" value={token} onChange={(e) => setToken(e.target.value)} autoFocus autoComplete="off" />
            </div>
            {error && <p className="text-sm text-danger">{error}</p>}
            <Button type="submit" className="w-full" disabled={!token || busy}>{busy && <Spinner />} Continue</Button>
          </form>
        </Card>
      </section>
    </div>
  );
}
