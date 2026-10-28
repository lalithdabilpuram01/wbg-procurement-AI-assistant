"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";
import {
  api, waitForJob, type Bookmark, type Conversation, type ConversationDetail, type FeedbackReason, type Folder, type Investigation,
  type Job, type Me, type PackageInput, type PlanInput,
} from "@/lib/api";
import { Sidebar, type View } from "./sidebar";
import { ChatView } from "./chat";
import { MemoryView } from "./memory";
import { ReviewsView } from "./reviews";
import { WatchView } from "./watch";
import { PackageDialog } from "./package-dialog";
import { PlanDialog } from "./plan-dialog";

const FAILED_JOB = "The request could not be completed. Please try again.";

export function AppShell() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [view, setView] = useState<View>("chat");
  const [me, setMe] = useState<Me>({ user_id: "", reviewer: false, feedback: false });
  const [defaultTableLens, setDefaultTableLens] = useState(false);
  const [pendingReviews, setPendingReviews] = useState(0);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [investigations, setInvestigations] = useState<Record<string, Investigation>>({});
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [packageOpen, setPackageOpen] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);
  const [scrollToSeq, setScrollToSeq] = useState<number | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [watchedId, setWatchedId] = useState<string | null>(null);

  const report = useCallback((e: unknown) => setError(e instanceof Error ? e.message : String(e)), []);

  const refreshLists = useCallback(async () => {
    const [c, f, b] = await Promise.all([api.conversations(), api.folders(), api.bookmarks()]);
    setConversations(c);
    setFolders(f);
    setBookmarks(b);
  }, []);

  useEffect(() => { refreshLists().catch(report); }, [refreshLists, report]);

  useEffect(() => {
    (async () => {
      const who = await api.me();
      setMe(who);
      if (!who.feedback) return;
      setDefaultTableLens(!!(await api.memory()).preferences.table_lens);
      if (who.reviewer) setPendingReviews((await api.reviews()).length);
    })().catch(report);
  }, [report]);

  /** Loads a conversation and the investigations behind its answers (only those not loaded yet). */
  const openConversation = useCallback(async (id: string, scrollSeq: number | null = null, reload: string[] = []) => {
    const loaded = await api.conversation(id);
    const known = new Set(loaded.investigations.map((i) => i.request_id));
    const needed = loaded.turns
      .filter((t) => t.role === "assistant" && t.request_id && known.has(t.request_id))
      .map((t) => t.request_id!)
      .filter((r) => reload.includes(r) || !investigations[r]);
    const fetched = await Promise.all(needed.map(async (r) => [r, await api.investigation(id, r)] as const));
    setInvestigations((current) => ({ ...current, ...Object.fromEntries(fetched) }));
    setActiveId(id);
    setDetail(loaded);
    setView("chat");
    setScrollToSeq(scrollSeq);
  }, [investigations]);

  const ensureConversation = async (title: string): Promise<string> => {
    if (activeId) return activeId;
    const created = await api.createConversation(title.slice(0, 120));
    setActiveId(created.conversation_id);
    setDetail({ conversation_id: created.conversation_id, turns: [], investigations: [] });
    return created.conversation_id;
  };

  const runJob = async (id: string, label: string, start: () => Promise<Job>, userText: string, reload: string[] = []) => {
    setError(null);
    setPending(label);
    if (userText) {
      setDetail((d) => d && {
        ...d,
        turns: [...d.turns, { seq: (d.turns.at(-1)?.seq ?? 0) + 1, role: "user", text: userText, status: null, route: null,
          request_id: null, created_at: new Date().toISOString() }],
      });
    }
    try {
      const started = await start();
      setJobId(started.job_id);
      const job = await waitForJob(started);
      if (job.state === "failed") setError(FAILED_JOB);
      await openConversation(id, null, job.request_id ? [...reload, job.request_id] : reload);
      await refreshLists();
    } catch (e) {
      report(e);
    } finally {
      setPending(null);
      setJobId(null);
    }
  };

  const stop = async () => {
    if (!jobId) return;
    setPending("Stopping after the current step…");
    try { await api.cancelJob(jobId); } catch (e) { report(e); }
  };

  const send = async (text: string, execute: boolean, tableLens = false) => {
    try {
      const id = await ensureConversation(text);
      await runJob(id, "Working on it – routing, searching and checking sources…", () => api.sendMessage(id, text, execute, tableLens), text);
    } catch (e) { report(e); }
  };

  const assess = async (input: PackageInput) => {
    const id = await ensureConversation(`Package · ${input.project_id} · ${input.procurement_category}`);
    const text = `Assess a ${input.procurement_category} package for ${input.project_id}: ${input.procurement_method}, ` +
      `${input.review_type} review, ${input.bid_window_days}-day bid window, tender on ${input.tender_date}.`;
    void runJob(id, "Looking up the project, running the three models and searching past lessons…",
      () => api.assessPackage(id, input), text);
  };

  const planProject = async (input: PlanInput) => {
    const id = await ensureConversation(`Plan · ${input.profile.sector} · ${input.profile.country_code}`);
    const text = `Plan a new ${input.profile.sector} project in ${input.profile.country_code}: ${input.profile.description}`;
    void runJob(id, "Finding similar past projects, reading their reports and data, and running the models…",
      () => api.planProject(id, input), text);
  };

  const decide = async (requestId: string, approvalId: string, approved: boolean) => {
    if (!activeId) return;
    await runJob(activeId, approved ? "Running the approved query…" : "Recording your decision…",
      () => api.decide(activeId, requestId, approvalId, approved), "", [requestId]);
  };

  /** Feedback on an answer; a thumbs-down with a correction re-runs the question (a revision job). */
  const giveFeedback = async (seq: number, rating: "up" | "down", reasons: FeedbackReason[] = [], correction?: string) => {
    if (!activeId) return;
    const id = activeId;
    try {
      const { job } = await api.giveFeedback(id, seq, rating, reasons, correction);
      if (job) await runJob(id, "Re-running the question with your correction…", async () => job, "");
      else await openConversation(id, null);
      if (me.reviewer) setPendingReviews((await api.reviews()).length);
    } catch (e) { report(e); }
  };

  const bookmarkedSeqs = useMemo(
    () => new Set(bookmarks.filter((b) => b.thread_id === activeId).map((b) => b.turn_seq)), [bookmarks, activeId]);
  const chatBookmarks = useMemo(
    () => bookmarks.filter((b) => b.thread_id === activeId).sort((a, b) => a.turn_seq - b.turn_seq), [bookmarks, activeId]);

  const toggleBookmark = async (seq: number) => {
    if (!activeId) return;
    const existing = bookmarks.find((b) => b.thread_id === activeId && b.turn_seq === seq);
    try {
      if (existing) await api.deleteBookmark(existing.bookmark_id);
      else await api.addBookmark(activeId, seq);
      setBookmarks(await api.bookmarks());
    } catch (e) { report(e); }
  };

  const withRefresh = (action: () => Promise<unknown>) => async () => {
    try { await action(); await refreshLists(); } catch (e) { report(e); }
  };

  const watchProject = (projectId: string) => {
    setWatchedId(projectId);
    setView("watch");
  };

  const active = conversations.find((c) => c.conversation_id === activeId);

  return (
    <div className="flex h-screen overflow-hidden">
      {sidebarOpen && (
        <Sidebar conversations={conversations} folders={folders} activeId={activeId} view={view}
          reviewer={me.reviewer} feedback={me.feedback} pendingReviews={pendingReviews}
          onSelect={(id) => openConversation(id).catch(report)}
          onNew={() => { setActiveId(null); setDetail(null); setView("chat"); }}
          onMemory={() => setView("memory")}
          onReviews={() => setView("reviews")}
          onWatch={() => setView("watch")}
          onCreateFolder={(name) => withRefresh(() => api.createFolder(name))()}
          onRenameFolder={(id, name) => withRefresh(() => api.renameFolder(id, name))()}
          onDeleteFolder={(id) => withRefresh(() => api.deleteFolder(id))()}
          onRenameConversation={(id, title) => withRefresh(() => api.renameConversation(id, title))()}
          onFile={(id, folderId) => withRefresh(() => api.fileConversation(id, folderId))()}
          onCollapse={() => setSidebarOpen(false)} />
      )}
      <main className="relative flex min-w-0 flex-1">
        {view === "memory" ? (
          <MemoryView reviewer={me.reviewer} onError={report} onPreferences={(p) => setDefaultTableLens(!!p.table_lens)} />
        ) : view === "reviews" ? (
          <ReviewsView onError={report} onChanged={setPendingReviews} />
        ) : view === "watch" ? (
          <WatchView projectId={watchedId} onProject={setWatchedId} onError={report} />
        ) : (
          <ChatView title={active?.title ?? null} detail={detail} investigations={investigations} pending={pending}
            bookmarkedSeqs={bookmarkedSeqs} chatBookmarks={chatBookmarks} sidebarOpen={sidebarOpen} scrollToSeq={scrollToSeq}
            onRemoveBookmark={(b) => withRefresh(() => api.deleteBookmark(b.bookmark_id))()}
            onOpenSidebar={() => setSidebarOpen(true)} onSend={send} onStop={stop} onAssess={() => setPackageOpen(true)}
            onPlan={() => setPlanOpen(true)}
            onToggleBookmark={toggleBookmark} onDecide={decide} defaultTableLens={defaultTableLens}
            onFeedback={me.feedback ? giveFeedback : undefined} onManageMemory={() => setView("memory")} onWatch={watchProject} />
        )}
        {error && (
          <div role="alert" className="absolute bottom-24 left-1/2 flex -translate-x-1/2 items-center gap-3 rounded-xl border border-danger/30 bg-danger-soft px-4 py-2.5 text-sm text-danger shadow-lg">
            {error}
            <button onClick={() => setError(null)} aria-label="Dismiss"><X className="size-4" /></button>
          </div>
        )}
      </main>
      <PackageDialog open={packageOpen} onOpenChange={setPackageOpen} onSubmit={assess} />
      <PlanDialog open={planOpen} onOpenChange={setPlanOpen} onSubmit={planProject} />
    </div>
  );
}
