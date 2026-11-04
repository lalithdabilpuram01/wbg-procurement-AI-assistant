"""Watch mode: run the pipeline whenever files land in the inbox. A run starts once the inbox has been quiet for
`settle_seconds` and no pending file is still growing, so a large file being copied is never read half-written."""
from __future__ import annotations

import threading
import time
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .layout import MANAGED, Inbox


class _Changes(FileSystemEventHandler):
    """Remembers when the inbox last changed; events in the managed folders (the pipeline's own moves) are ignored."""

    def __init__(self) -> None:
        self.last = 0.0
        self.event = threading.Event()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if not event.is_directory and not any(f"/{m}/" in str(event.src_path) for m in MANAGED):
            self.last = time.monotonic()
            self.event.set()


def watch(inbox: Inbox, run: Callable[[], dict], settle_seconds: float = 3.0, on_report: Callable[[dict], None] = print) -> None:
    """Run the pipeline each time new files have settled in the inbox, until interrupted (Ctrl+C)."""
    inbox.create()
    changes = _Changes()
    observer = Observer()
    observer.schedule(changes, str(inbox.root), recursive=True)
    observer.start()
    if inbox.pending():
        changes.event.set()                                              # files that arrived while nothing was watching
    try:
        while True:
            changes.event.wait()
            while time.monotonic() - changes.last < settle_seconds or _growing(inbox):
                time.sleep(0.5)
            changes.event.clear()
            if inbox.pending():
                on_report(run())
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


def _growing(inbox: Inbox) -> bool:
    """True when a pending file changes size within one second, i.e. it is still being copied."""
    before = {p: p.stat().st_size for _, p in inbox.pending()}
    time.sleep(1.0)
    return any(p.exists() and p.stat().st_size != size for p, size in before.items())
