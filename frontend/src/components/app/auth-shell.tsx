"use client";

import { useState } from "react";
import Link from "next/link";
import { BrainCircuit, Check, Copy, Database, FileSearch, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/primitives";

// The frame of the sign-in, create-account and forgot-password pages: the product panel on the left, one card on
// the right.

const FEATURES = [
  { icon: Database, text: "Contract awards, notices and project data from World Bank open data" },
  { icon: FileSearch, text: "237 appraisal, status, completion and procurement-plan documents" },
  { icon: BrainCircuit, text: "Tender-duration and risk models for new packages" },
  { icon: ShieldCheck, text: "Every answer checked against its cited sources" },
];

/** The username rule in words; the same rule as USERNAME in orchestrator/wbg_orchestrator/accounts.py. */
export const USERNAME_HINT = "3 to 32 characters: lowercase letters, digits, dots, dashes or underscores.";
/** Shortest password the API accepts (MIN_PASSWORD in accounts.py); checked here only for a quicker message. */
export const MIN_PASSWORD = 8;

/** The two-column auth page layout; `icon`, `title` and `subtitle` head the card that holds the form (`children`). */
export function AuthShell({ icon: Icon, title, subtitle, children }: {
  icon: React.ComponentType<{ className?: string }>; title: string; subtitle: string; children: React.ReactNode;
}) {
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
          <div className="grid size-10 place-items-center rounded-xl bg-primary-soft text-primary"><Icon className="size-5" /></div>
          <h2 className="mt-4 text-xl font-semibold">{title}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
          {children}
        </Card>
      </section>
    </div>
  );
}

/** A text link between the auth pages (sign in, create account, forgot password). */
export function AuthLink({ href, children }: { href: string; children: React.ReactNode }) {
  return <Link href={href} className="font-medium text-primary hover:underline">{children}</Link>;
}

// Shown once after an account is created or a password is reset: the code is needed for "Forgot password", and the
// app cannot show it again.
export function RecoveryCodePanel({ code, intro }: { code: string; intro: string }) {
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState(false);
  // A blocked clipboard (for example on an http page) is ignored; the code stays visible to copy by hand.
  const copy = async () => {
    await navigator.clipboard.writeText(code).catch(() => undefined);
    setCopied(true);
  };
  // The session cookie is already set, so "Continue" just opens "/"; the checkbox asks the user to save the code first.
  return (
    <div className="mt-6 space-y-4">
      <p className="text-sm text-muted-foreground">{intro}</p>
      <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-muted px-3 py-3">
        <code className="font-mono text-base font-semibold tracking-wider">{code}</code>
        <Button type="button" variant="outline" size="sm" onClick={copy}>
          {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Keep it somewhere safe, like a password manager. It is shown only now, and each code works once.
      </p>
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-0.5" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        I have saved my recovery code.
      </label>
      <Button type="button" className="w-full" disabled={!saved} onClick={() => window.location.assign("/")}>Continue to the app</Button>
    </div>
  );
}
