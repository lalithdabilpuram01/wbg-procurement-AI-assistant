"use client";

// Sign-in page "/login". It posts to the /api/session route, which keeps the session token in an httpOnly cookie.

import { useState } from "react";
import { KeyRound } from "lucide-react";
import { AuthLink, AuthShell } from "@/components/app/auth-shell";
import { Button } from "@/components/ui/button";
import { Input, Label, Spinner } from "@/components/ui/primitives";

/** The sign-in form; on success it does a full page load of "/" so the middleware sees the new cookie. */
export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const response = await fetch("/api/session", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: username.trim(), password }),
    }).catch(() => null);
    if (response?.ok) {
      window.location.assign("/");
      return;
    }
    // The API's message for a wrong login is reworded for display; a null response means the route was unreachable.
    const body = await response?.json().catch(() => ({}));
    setError(body?.detail === "wrong username or password" ? "Wrong username or password." : body?.detail ?? "The API is not reachable.");
    setBusy(false);
  };

  return (
    <AuthShell icon={KeyRound} title="Sign in" subtitle="Sign in with your username and password.">
      <form onSubmit={submit} className="mt-6 space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="username">Username</Label>
          <Input id="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" />
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label htmlFor="password">Password</Label>
            <span className="text-xs"><AuthLink href="/forgot-password">Forgot password?</AuthLink></span>
          </div>
          <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </div>
        {error && <p className="text-sm text-danger">{error}</p>}
        <Button type="submit" className="w-full" disabled={!username.trim() || !password || busy}>{busy && <Spinner />} Sign in</Button>
      </form>
      <p className="mt-6 text-center text-sm text-muted-foreground">New here? <AuthLink href="/signup">Create an account</AuthLink></p>
    </AuthShell>
  );
}
