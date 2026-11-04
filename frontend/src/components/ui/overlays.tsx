"use client";

// Overlay components built on Radix UI (dialog, side sheet, dropdown menu, tooltip). Radix handles focus, keyboard
// and screen-reader behaviour; this file only adds the app's styles.

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as Menu from "@radix-ui/react-dropdown-menu";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

/* ----------------------------------------------------------------------------------------------- dialog */
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

/** A centred modal with a required `title` (read by screen readers), an optional description and a close button. */
export function DialogContent({ className, children, title, description, ...props }:
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & { title: string; description?: string }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-[#0b2545]/40 backdrop-blur-[2px]" />
      <DialogPrimitive.Content
        className={cn("fixed left-1/2 top-1/2 z-50 w-[min(92vw,560px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl",
          "border border-border bg-card p-6 shadow-2xl focus:outline-none", className)}
        {...props}
      >
        <DialogPrimitive.Title className="text-lg font-semibold">{title}</DialogPrimitive.Title>
        {description && <DialogPrimitive.Description className="mt-1 text-sm text-muted-foreground">{description}</DialogPrimitive.Description>}
        <div className="mt-5">{children}</div>
        <DialogPrimitive.Close className="absolute right-4 top-4 rounded-md p-1 text-muted-foreground hover:bg-muted" aria-label="Close">
          <X className="size-4" />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

/* --------------------------------------------------------------------------------------- side sheet */
/** A panel that slides in from the right edge. Used for the evidence list, hence its fixed screen-reader text. */
export function Sheet({ open, onOpenChange, title, children }:
  { open: boolean; onOpenChange: (open: boolean) => void; title: string; children: React.ReactNode }) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-[#0b2545]/20" />
        <DialogPrimitive.Content className="fixed right-0 top-0 z-50 flex h-full w-[min(100vw,460px)] flex-col border-l border-border bg-card shadow-2xl focus:outline-none">
          <div className="flex items-center justify-between border-b border-border px-5 py-4">
            <DialogPrimitive.Title className="text-base font-semibold">{title}</DialogPrimitive.Title>
            <DialogPrimitive.Description className="sr-only">Sources behind the answer</DialogPrimitive.Description>
            <DialogPrimitive.Close className="rounded-md p-1 text-muted-foreground hover:bg-muted" aria-label="Close">
              <X className="size-4" />
            </DialogPrimitive.Close>
          </div>
          <div className="scrollbar-thin flex-1 overflow-y-auto p-5">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/* --------------------------------------------------------------------------------------- dropdown menu */
export const DropdownMenu = Menu.Root;
export const DropdownMenuTrigger = Menu.Trigger;

/** The menu panel, rendered in a portal so it is not clipped by scrolling containers; aligned to the trigger's end. */
export function DropdownMenuContent({ className, ...props }: React.ComponentPropsWithoutRef<typeof Menu.Content>) {
  return (
    <Menu.Portal>
      <Menu.Content sideOffset={6} align="end"
        className={cn("z-50 min-w-44 rounded-lg border border-border bg-card p-1 text-sm text-foreground shadow-lg", className)} {...props} />
    </Menu.Portal>
  );
}

/** One clickable menu row; highlighted by keyboard or pointer through Radix data attributes. */
export function DropdownMenuItem({ className, ...props }: React.ComponentPropsWithoutRef<typeof Menu.Item>) {
  return (
    <Menu.Item className={cn("flex cursor-pointer select-none items-center gap-2 rounded-md px-2 py-1.5 outline-none",
      "data-[highlighted]:bg-muted data-[disabled]:opacity-50 [&_svg]:size-4", className)} {...props} />
  );
}

/** A thin line between menu groups. */
export const DropdownMenuSeparator = () => <Menu.Separator className="my-1 h-px bg-border" />;
/** A small heading above a group of menu items. */
export const DropdownMenuLabel = ({ children }: { children: React.ReactNode }) =>
  <Menu.Label className="px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{children}</Menu.Label>;

/* --------------------------------------------------------------------------------------------- tooltip */
/** A hover or focus tooltip around `children` (which must accept a ref). Each one has its own provider, so it can be
 *  used anywhere without a provider at the app root. */
export function Tooltip({ content, children }: { content: React.ReactNode; children: React.ReactNode }) {
  return (
    <TooltipPrimitive.Provider delayDuration={250}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content sideOffset={6}
            className="z-50 max-w-72 rounded-md bg-[#0b2545] px-2.5 py-1.5 text-xs text-white shadow-lg">
            {content}
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}
