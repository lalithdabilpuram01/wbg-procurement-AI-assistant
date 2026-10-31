"use client";

import { useState } from "react";
import { LifeBuoy } from "lucide-react";
import { AuthLink, AuthShell, MIN_PASSWORD, RecoveryCodePanel } from "@/components/app/auth-shell";
import { Button } from "@/components/ui/button";
import { Input, Label, Spinner } from "@/components/ui/primitives";

// Forgot password: the recovery code (shown when the account was created) sets a new password. The user is then
// signed in, signed out everywhere else, and shown a new recovery code, because each code works once.
export default function ForgotPasswordPage() {
  const [username, setUsername] = useState("");
  const [recoveryCode, setRecoveryCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newCode, setNewCode] = useState<string | null>(null);

  const problem = !password ? null
    : password.length < MIN_PASSWORD ? `The new password needs at least ${MIN_PASSWORD} characters.`
    : confirm && confirm !== password ? "The two passwords do not match." : null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const response = await fetch("/api/reset", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username.trim().toLowerCase(), recovery_code: recoveryCode.trim(), new_password: password }),
    }).catch(() => null);
    const body = await response?.json().catch(() => ({}));
    if (response?.ok && body?.recovery_code) {
      setNewCode(body.recovery_code);
      return;
    }
    setError(body?.detail === "wrong username or recovery code" ? "The username or recovery code is not right."
      : body?.detail ?? "The API is not reachable.");
    setBusy(false);
  };

  if (newCode) {
    return (
      <AuthShell icon={LifeBuoy} title="Your password is changed" subtitle="You are signed in, and signed out on every other device.">
        <RecoveryCodePanel code={newCode} intro="The code you just used no longer works. This is your new recovery code." />
      </AuthShell>
    );
  }

  return (
    <AuthShell icon={LifeBuoy} title="Forgot password" subtitle="Set a new password with the recovery code you saved when you created your account.">
      <form onSubmit={submit} className="mt-6 space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="username">Username</Label>
          <Input id="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="code">Recovery code</Label>
          <Input id="code" value={recoveryCode} onChange={(e) => setRecoveryCode(e.target.value)} placeholder="XXXX-XXXX-XXXX-XXXX"
                 autoComplete="off" spellCheck={false} className="font-mono uppercase" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">New password</Label>
          <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="confirm">Confirm new password</Label>
          <Input id="confirm" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
        </div>
        {(problem || error) && <p className="text-sm text-danger">{problem ?? error}</p>}
        <Button type="submit" className="w-full"
                disabled={!username.trim() || !recoveryCode.trim() || !password || confirm !== password || !!problem || busy}>
          {busy && <Spinner />} Set new password
        </Button>
      </form>
      <p className="mt-4 text-xs text-muted-foreground">
        Lost your recovery code too? Ask the person who runs the app for a new one.
      </p>
      <p className="mt-4 text-center text-sm text-muted-foreground"><AuthLink href="/login">Back to sign in</AuthLink></p>
    </AuthShell>
  );
}
