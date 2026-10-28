"use client";

import { Bookmark as BookmarkIcon, BookmarkX } from "lucide-react";
import type { Bookmark } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/ui/overlays";
import { relativeTime } from "@/lib/utils";

/** The bookmarks of the open chat only, from a button at the top of the chat. Picking one scrolls to that answer. */
export function ChatBookmarks({ bookmarks, onJump, onRemove }:
  { bookmarks: Bookmark[]; onJump: (seq: number) => void; onRemove: (bookmark: Bookmark) => void }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" aria-label="Bookmarks in this chat">
          <BookmarkIcon /> Bookmarks
          {bookmarks.length > 0 && <span className="rounded-full bg-primary-soft px-1.5 text-[11px] font-semibold text-primary-strong">{bookmarks.length}</span>}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-80">
        <DropdownMenuLabel>Bookmarks in this chat</DropdownMenuLabel>
        {bookmarks.length === 0 ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">No bookmarks yet. Use the bookmark button under an answer.</p>
        ) : (
          bookmarks.map((b) => (
            <DropdownMenuItem key={b.bookmark_id} onSelect={() => onJump(b.turn_seq)} className="items-start">
              <div className="min-w-0 flex-1">
                {b.note && <p className="truncate text-xs font-semibold">{b.note}</p>}
                <p className="line-clamp-2 text-xs text-muted-foreground">{b.excerpt}</p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">Saved {relativeTime(b.created_at)}</p>
              </div>
              <button type="button" aria-label="Remove bookmark" className="rounded p-0.5 text-muted-foreground hover:text-danger"
                onClick={(e) => { e.stopPropagation(); onRemove(b); }} onPointerDown={(e) => e.stopPropagation()}>
                <BookmarkX className="size-4" />
              </button>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
