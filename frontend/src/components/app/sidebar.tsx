"use client";

import { useMemo, useState } from "react";
import {
  Bookmark, ChevronRight, Folder as FolderIcon, FolderOpen, FolderPlus, LogOut, MessageSquare, Moon, MoreHorizontal,
  PanelLeftClose, Pencil, Plus, Search, Sun, Trash2,
} from "lucide-react";
import type { Conversation, Folder } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/primitives";
import {
  Dialog, DialogContent, DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/overlays";
import { cn } from "@/lib/utils";

type Props = {
  conversations: Conversation[];
  folders: Folder[];
  activeId: string | null;
  view: "chat" | "bookmarks";
  bookmarkCount: number;
  onSelect: (id: string) => void;
  onNew: () => void;
  onBookmarks: () => void;
  onCreateFolder: (name: string) => Promise<void>;
  onRenameFolder: (id: string, name: string) => Promise<void>;
  onDeleteFolder: (id: string) => Promise<void>;
  onRenameConversation: (id: string, title: string) => Promise<void>;
  onFile: (conversationId: string, folderId: string | null) => Promise<void>;
  onCollapse: () => void;
};

type Naming = { title: string; initial: string; save: (name: string) => Promise<void> } | null;

export function Sidebar(props: Props) {
  const { conversations, folders, activeId, view } = props;
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [naming, setNaming] = useState<Naming>(null);
  const [dark, setDark] = useState(() => typeof document !== "undefined" && document.documentElement.classList.contains("dark"));

  const visible = useMemo(
    () => conversations.filter((c) => c.title.toLowerCase().includes(query.toLowerCase())),
    [conversations, query],
  );
  const unfiled = visible.filter((c) => !c.folder_id || !folders.some((f) => f.folder_id === c.folder_id));

  const toggleTheme = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try { localStorage.setItem("theme", next ? "dark" : "light"); } catch { /* storage unavailable */ }
    setDark(next);
  };
  const signOut = async () => {
    await fetch("/api/session", { method: "DELETE" });
    window.location.assign("/login");
  };

  const item = (c: Conversation) => (
    <div key={c.conversation_id}
      className={cn("group flex items-center rounded-lg pr-1 text-sm transition-colors",
        c.conversation_id === activeId && view === "chat" ? "bg-sidebar-active text-white" : "text-sidebar-foreground/90 hover:bg-sidebar-hover")}>
      <button onClick={() => props.onSelect(c.conversation_id)} className="flex min-w-0 flex-1 items-center gap-2 px-2.5 py-1.5 text-left">
        <MessageSquare className="size-3.5 shrink-0 text-sidebar-muted" />
        <span className="truncate">{c.title}</span>
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger className="rounded-md p-1 text-sidebar-muted opacity-0 hover:text-white group-hover:opacity-100 data-[state=open]:opacity-100" aria-label="Conversation actions">
          <MoreHorizontal className="size-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem onSelect={() => setNaming({ title: "Rename conversation", initial: c.title, save: (t) => props.onRenameConversation(c.conversation_id, t) })}>
            <Pencil /> Rename
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuLabel>Move to folder</DropdownMenuLabel>
          {folders.map((f) => (
            <DropdownMenuItem key={f.folder_id} disabled={c.folder_id === f.folder_id} onSelect={() => props.onFile(c.conversation_id, f.folder_id)}>
              <FolderIcon /> {f.name}
            </DropdownMenuItem>
          ))}
          {folders.length === 0 && <DropdownMenuItem disabled>No folders yet</DropdownMenuItem>}
          {c.folder_id && (
            <DropdownMenuItem onSelect={() => props.onFile(c.conversation_id, null)}><FolderOpen /> Remove from folder</DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex items-center gap-2.5 px-4 pb-3 pt-4">
        <div className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-[#2b88d8] to-[#0078d4] text-sm font-bold text-white shadow">PI</div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-white">Procurement Intelligence</p>
          <p className="text-[11px] text-sidebar-muted">World Bank open data · documents</p>
        </div>
        <Button variant="sidebar" size="icon" onClick={props.onCollapse} aria-label="Hide sidebar"><PanelLeftClose /></Button>
      </div>

      <div className="space-y-2 px-3">
        <Button onClick={props.onNew} className="w-full justify-start bg-white/10 text-white hover:bg-white/15"><Plus /> New chat</Button>
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 size-3.5 text-sidebar-muted" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search chats"
            className="h-8 w-full rounded-lg bg-white/5 pl-8 pr-2 text-xs text-white placeholder:text-sidebar-muted focus:outline-none focus:ring-1 focus:ring-[#2b88d8]" />
        </div>
        <button onClick={props.onBookmarks}
          className={cn("flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm", view === "bookmarks" ? "bg-sidebar-active text-white" : "hover:bg-sidebar-hover")}>
          <Bookmark className="size-4 text-sidebar-muted" /> Bookmarks
          {props.bookmarkCount > 0 && <span className="ml-auto rounded-full bg-white/10 px-1.5 text-[11px]">{props.bookmarkCount}</span>}
        </button>
      </div>

      <nav className="scrollbar-thin mt-3 flex-1 overflow-y-auto px-3 pb-3">
        <div className="flex items-center justify-between px-1.5 pb-1 pt-2">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-sidebar-muted">Folders</span>
          <button onClick={() => setNaming({ title: "New folder", initial: "", save: props.onCreateFolder })}
            className="rounded p-0.5 text-sidebar-muted hover:text-white" aria-label="New folder"><FolderPlus className="size-4" /></button>
        </div>
        {folders.length === 0 && <p className="px-2.5 py-1 text-xs text-sidebar-muted">Group chats by project or country.</p>}
        {folders.map((f) => {
          const inside = visible.filter((c) => c.folder_id === f.folder_id);
          const expanded = open[f.folder_id] ?? true;
          return (
            <div key={f.folder_id}>
              <div className="group flex items-center rounded-lg pr-1 hover:bg-sidebar-hover">
                <button onClick={() => setOpen({ ...open, [f.folder_id]: !expanded })} className="flex min-w-0 flex-1 items-center gap-1.5 px-1.5 py-1.5 text-sm">
                  <ChevronRight className={cn("size-3.5 text-sidebar-muted transition-transform", expanded && "rotate-90")} />
                  {expanded ? <FolderOpen className="size-4 text-[#6cb8f6]" /> : <FolderIcon className="size-4 text-[#6cb8f6]" />}
                  <span className="truncate">{f.name}</span>
                  <span className="ml-auto text-[11px] text-sidebar-muted">{inside.length}</span>
                </button>
                <DropdownMenu>
                  <DropdownMenuTrigger className="rounded-md p-1 text-sidebar-muted opacity-0 hover:text-white group-hover:opacity-100" aria-label="Folder actions">
                    <MoreHorizontal className="size-4" />
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    <DropdownMenuItem onSelect={() => setNaming({ title: "Rename folder", initial: f.name, save: (n) => props.onRenameFolder(f.folder_id, n) })}>
                      <Pencil /> Rename
                    </DropdownMenuItem>
                    <DropdownMenuItem className="text-danger" onSelect={() => props.onDeleteFolder(f.folder_id)}>
                      <Trash2 /> Delete folder (keeps chats)
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
              {expanded && <div className="ml-4 border-l border-white/10 pl-1.5">{inside.map(item)}</div>}
            </div>
          );
        })}

        <p className="px-1.5 pb-1 pt-4 text-[11px] font-semibold uppercase tracking-wider text-sidebar-muted">Chats</p>
        {unfiled.map(item)}
        {visible.length === 0 && <p className="px-2.5 py-1 text-xs text-sidebar-muted">{query ? "No matching chats." : "No chats yet."}</p>}
      </nav>

      <div className="flex items-center gap-1 border-t border-white/10 px-3 py-2.5">
        <Button variant="sidebar" size="sm" onClick={toggleTheme}>{dark ? <Sun /> : <Moon />} {dark ? "Light" : "Dark"}</Button>
        <Button variant="sidebar" size="sm" className="ml-auto" onClick={signOut}><LogOut /> Sign out</Button>
      </div>

      <NameDialog naming={naming} onClose={() => setNaming(null)} />
    </aside>
  );
}

function NameDialog({ naming, onClose }: { naming: Naming; onClose: () => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open={naming !== null} onOpenChange={(o) => { if (!o) onClose(); else setValue(naming?.initial ?? ""); }}>
      {naming && (
        <DialogContent title={naming.title} onOpenAutoFocus={() => setValue(naming.initial)}>
          <form className="space-y-4" onSubmit={async (e) => {
            e.preventDefault();
            if (!value.trim()) return;
            setBusy(true);
            try { await naming.save(value.trim()); onClose(); } finally { setBusy(false); }
          }}>
            <Input value={value} onChange={(e) => setValue(e.target.value)} maxLength={80} autoFocus placeholder="Name" />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={busy || !value.trim()}>Save</Button>
            </div>
          </form>
        </DialogContent>
      )}
    </Dialog>
  );
}
