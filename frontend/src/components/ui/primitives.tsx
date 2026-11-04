// Small styled building blocks shared by the pages: form fields, label, badge, card and spinner.
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

// Common look of every text field, select and text area, so forms line up.
const field =
  "w-full rounded-lg border border-input bg-card px-3 text-sm text-foreground placeholder:text-muted-foreground/70 " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

/** A styled text input; accepts every native <input> prop. */
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => <input ref={ref} className={cn(field, "h-9", className)} {...props} />,
);
Input.displayName = "Input";

/** A styled native <select>; the right padding leaves room for the browser's arrow. */
export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...props }, ref) => <select ref={ref} className={cn(field, "h-9 pr-8", className)} {...props} />,
);
Select.displayName = "Select";

/** A styled multi-line text field. */
export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => <textarea ref={ref} className={cn(field, "py-2", className)} {...props} />,
);
Textarea.displayName = "Textarea";

/** A small form label. */
export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("text-xs font-semibold text-muted-foreground", className)} {...props} />;
}

const badgeVariants = cva("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold [&_svg]:size-3", {
  variants: {
    tone: {
      neutral: "bg-muted text-muted-foreground",
      info: "bg-primary-soft text-primary-strong",
      success: "bg-success-soft text-success",
      warning: "bg-warning-soft text-warning",
      danger: "bg-danger-soft text-danger",
      synthetic: "bg-synthetic-soft text-synthetic",
    },
  },
  defaultVariants: { tone: "neutral" },
});

/** A small rounded tag; `tone` picks the colour (the "synthetic" tone marks results from synthetic-label models). */
export function Badge({ className, tone, ...props }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

/** A bordered, rounded panel used for most content blocks. */
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl border border-border bg-card text-card-foreground shadow-sm", className)} {...props} />;
}

/** A small spinning ring shown while something loads; it takes the current text colour. */
export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cn("inline-block size-4 animate-spin rounded-full border-2 border-current border-r-transparent", className)}
      aria-label="Loading"
    />
  );
}
