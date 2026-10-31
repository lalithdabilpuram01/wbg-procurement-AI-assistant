"use client";

import { useState } from "react";
import { UserPlus } from "lucide-react";
import { AuthLink, AuthShell, MIN_PASSWORD, RecoveryCodePanel, USERNAME_HINT } from "@/components/app/auth-shell";
import { Button } from "@/components/ui/button";
import { Input, Label, Spinner } from "@/components/ui/primitives";

const USERNAME = /^[a-z0-9][a-z0-9._-]{2,31}$/;

// Create an account: a normal user, signed in at once, then shown their recovery code once.
export default function SignupPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [code, setCode] = useState<string | null>(null);

  const name = username.trim().toLowerCase();
  const problem = !name || !password ? null
    : !USERNAME.test(name) ? USERNAME_HINT
    : password.length < MIN_PASSWORD ? `The password needs at least ${MIN_PASSWORD} characters.`
    : confirm && confirm !== password ? "The two passwords do not match." : null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const response = await fetch("/api/signup", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: name, password }),
    }).catch(() => null);
    const body = await response?.json().catch(() => ({}));
    if (response?.ok && body?.recovery_code) {
      setCode(body.recovery_code);
      return;
    }
    setError(body?.detail ?? "The API is not reachable.");
    setBusy(false);
  };

  if (code) {
    return (
      <AuthShell icon={UserPlus} title="Your account is ready" subtitle={`You are signed in as ${name}.`}>
        <RecoveryCodePanel code={code} intro="This is your recovery code. If you ever forget your password, you can set a new one with it." />
      </AuthShell>
    );
  }

  return (
    <AuthShell icon={UserPlus} title="Create an account" subtitle="Choose a username and a password.">
      <form onSubmit={submit} className="mt-6 space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="username">Username</Label>
          <Input id="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" />
          <p className="text-xs text-muted-foreground">{USERNAME_HINT}</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          <p className="text-xs text-muted-foreground">At least {MIN_PASSWORD} characters.</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="confirm">Confirm password</Label>
          <Input id="confirm" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
        </div>
        {(problem || error) && <p className="text-sm text-danger">{problem ?? error}</p>}
        <Button type="submit" className="w-full" disabled={!name || !password || confirm !== password || !!problem || busy}>
          {busy && <Spinner />} Create account
        </Button>
      </form>
      <p className="mt-6 text-center text-sm text-muted-foreground">Already have an account? <AuthLink href="/login">Sign in</AuthLink></p>
    </AuthShell>
  );
}
