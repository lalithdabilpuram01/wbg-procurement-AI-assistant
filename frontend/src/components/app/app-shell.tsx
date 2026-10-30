"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";
import {
  api, streamJob, waitForJob, type Bookmark, type Conversation, type ConversationDetail, type FeedbackReason, type Folder, type Investigation,
  type Job, type LiveEvent, type LiveStep, type Me, type PackageAssessmentInput, type PlanInput, type TenderInput,
} from "@/lib/api";
import { Sidebar, type View } from "./sidebar";
import { ChatView } from "./chat";
import { ReviewsView } from "./reviews";
import { WatchView } from "./watch";
import { DashboardView } from "./dashboard";
import { PackageDialog } from "./package-dialog";
import { PlanDialog } from "./plan-dialog";
import { TenderDialog } from "./tender-dialog";

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
  const [live, setLive] = useState<{ steps: LiveStep[]; text: string }>({ steps: [], text: "" });
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [packageOpen, setPackageOpen] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);
  const [tenderOpen, setTenderOpen] = useState(false);
  const [scrollToSeq, setScrollToSeq] = useState<number | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [watchedId, setWatchedId] = useState<string | null>(null);
  const [newChatFolder, setNewChatFolder] = useState<string | null>(null);    // the folder a new chat will be filed in

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
    // one investigation that cannot be read (e.g. an old run paused for an approval) must not keep the chat closed
    const settled = await Promise.allSettled(needed.map(async (r) => [r, await api.investigation(id, r)] as const));
    const fetched = settled.flatMap((s) => (s.status === "fulfilled" ? [s.value] : []));
    setInvestigations((current) => ({ ...current, ...Object.fromEntries(fetched) }));
    setNewChatFolder(null);
    setActiveId(id);
    setDetail(loaded);
    setView("chat");
    setScrollToSeq(scrollSeq);
  }, [investigations]);

  const ensureConversation = async (title: string): Promise<string> => {
    if (activeId) return activeId;
    const created = await api.createConversation(title.slice(0, 120));
    if (newChatFolder) {                                                         // started from a folder: file it there
      await api.fileConversation(created.conversation_id, newChatFolder);
      setNewChatFolder(null);
    }
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
      setLive({ steps: [], text: "" });
      const onEvent = (event: LiveEvent) => {                                    // live steps only; the checked answer
        if (event.type === "step") setLive((l) => ({ ...l, steps: mergeStep(l.steps, event) }));   // appears whole at the end
      };
      await streamJob(started.job_id, onEvent).catch(() => false);              // falls back to polling below
      const job = await waitForJob(started);
      if (job.state === "failed") setError(FAILED_JOB);
      await openConversation(id, null, job.request_id ? [...reload, job.request_id] : reload);
      await refreshLists();
    } catch (e) {
      report(e);
    } finally {
      setPending(null);
      setJobId(null);
      setLive({ steps: [], text: "" });
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

  const assess = async (input: PackageAssessmentInput) => {
    const project = input.project_id ?? `a new ${input.sector} project (${input.country_code})`;
    const id = await ensureConversation(`Package · ${input.project_id ?? "new project"} · ${input.procurement_category}`);
    const text = `Assess a ${input.procurement_category} package for ${project}: ${input.procurement_method}, ` +
      `${input.review_type} review, ${input.bid_window_days}-day bid window, tender on ${input.tender_date}.`;
    void runJob(id, "Running the three models on your inputs and searching past lessons…",
      () => api.assessPackage(id, input), text);
  };

  const checkTender = async (input: TenderInput) => {
    const id = await ensureConversation(`Tender check · ${input.project_id} · ${input.procurement_category}`);
    const text = `Check the tender design of a ${input.procurement_category} package for ${input.project_id}: ` +
      `${input.procurement_method}, ${input.review_type} review, ${input.bid_window_days}-day bid window, ${input.lots} lot(s).`;
    void runJob(id, "Reading the draft, comparing it with similar past packages and searching the project's documents…",
      () => api.checkTender(id, input), text);
  };

  const planProject = async (input: PlanInput) => {
    const id = await ensureConversation(`Plan · ${input.profile.sector} · ${input.profile.country_code}`);
    const text = `Plan a new ${input.profile.sector} project in ${input.profile.country_code}: ${input.profile.description}`;
    void runJob(id, "Finding similar past projects, reading their reports and data, and running the models…",
      () => api.planProject(id, input), text);
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
          onNew={(folderId) => { setActiveId(null); setDetail(null); setNewChatFolder(folderId ?? null); setView("chat"); }}
          onReviews={() => setView("reviews")}
          onWatch={() => setView("watch")}
          onDashboard={() => setView("dashboard")}
          onCreateFolder={(name) => withRefresh(() => api.createFolder(name))()}
          onRenameFolder={(id, name) => withRefresh(() => api.renameFolder(id, name))()}
          onDeleteFolder={(id) => withRefresh(() => api.deleteFolder(id))()}
          onRenameConversation={(id, title) => withRefresh(() => api.renameConversation(id, title))()}
          onDeleteConversation={(id) => withRefresh(async () => {
            await api.deleteConversation(id);
            if (id === activeId) { setActiveId(null); setDetail(null); setView("chat"); }
          })()}
          onFile={(id, folderId) => withRefresh(() => api.fileConversation(id, folderId))()}
          onCollapse={() => setSidebarOpen(false)} />
      )}
      <main className="relative flex min-w-0 flex-1">
        {view === "reviews" ? (
          <ReviewsView onError={report} onChanged={setPendingReviews} />
        ) : view === "dashboard" ? (
          <DashboardView onError={report} />
        ) : view === "watch" ? (
          <WatchView projectId={watchedId} onProject={setWatchedId} onError={report} />
        ) : (
          <ChatView title={active?.title ?? null} detail={detail} investigations={investigations} pending={pending} live={live}
            bookmarkedSeqs={bookmarkedSeqs} chatBookmarks={chatBookmarks} sidebarOpen={sidebarOpen} scrollToSeq={scrollToSeq}
            onRemoveBookmark={(b) => withRefresh(() => api.deleteBookmark(b.bookmark_id))()}
            onOpenSidebar={() => setSidebarOpen(true)} onSend={send} onStop={stop} onAssess={() => setPackageOpen(true)}
            onPlan={() => setPlanOpen(true)} onTender={() => setTenderOpen(true)}
            onToggleBookmark={toggleBookmark} defaultTableLens={defaultTableLens}
            onFeedback={me.feedback ? giveFeedback : undefined} onWatch={watchProject} />
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
      <TenderDialog open={tenderOpen} onOpenChange={setTenderOpen} onSubmit={checkTender} />
    </div>
  );
}

/** A step's latest state replaces its earlier one; new steps are added in the order they start. */
function mergeStep(steps: LiveStep[], step: LiveStep): LiveStep[] {
  const { key, label, state, ms } = step;
  const next = { key, label, state, ms };
  return steps.some((s) => s.key === key) ? steps.map((s) => (s.key === key ? next : s)) : [...steps, next];
}
