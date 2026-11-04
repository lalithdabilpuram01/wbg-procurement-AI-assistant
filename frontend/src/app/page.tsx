// Home page "/": the signed-in workspace. The middleware sends visitors without a session to /login first.
import { AppShell } from "@/components/app/app-shell";

export default function Home() {
  return <AppShell />;
}
