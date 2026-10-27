"use client";

import { BookmarkX, MessageSquare } from "lucide-react";
import type { Bookmark } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/primitives";
import { Tooltip } from "@/components/ui/overlays";
import { relativeTime } from "@/lib/utils";

export function BookmarksView({ bookmarks, onOpen, onRemove }:
  { bookmarks: Bookmark[]; onOpen: (b: Bookmark) => void; onRemove: (b: Bookmark) => void }) {
  return (
    <div className="scrollbar-thin h-full flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl px-4 py-8">
        <h1 className="text-xl font-semibold tracking-tight">Bookmarks</h1>
        <p className="mt-1 text-sm text-muted-foreground">Important answers, one click from their conversation.</p>
        {bookmarks.length === 0 ? (
          <Card className="mt-8 p-10 text-center text-sm text-muted-foreground">
            No bookmarks yet. Use the bookmark button under an answer to keep it here.
          </Card>
        ) : (
          <div className="mt-6 grid gap-3 md:grid-cols-2">
            {bookmarks.map((b) => (
              <Card key={b.bookmark_id} className="group flex flex-col p-4 transition-shadow hover:shadow-md">
                <button onClick={() => onOpen(b)} className="flex-1 text-left">
                  <div className="flex items-center gap-2 text-xs font-semibold text-primary">
                    <MessageSquare className="size-3.5" /> {b.conversation_title || "Conversation"}
                  </div>
                  {b.note && <p className="mt-2 text-sm font-medium">{b.note}</p>}
                  <p className="mt-1.5 line-clamp-4 text-sm text-muted-foreground">{b.excerpt}</p>
                </button>
                <div className="mt-3 flex items-center border-t border-border pt-2 text-[11px] text-muted-foreground">
                  Saved {relativeTime(b.created_at)}
                  <Tooltip content="Remove bookmark">
                    <Button variant="ghost" size="icon" className="ml-auto" onClick={() => onRemove(b)}><BookmarkX /></Button>
                  </Tooltip>
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
