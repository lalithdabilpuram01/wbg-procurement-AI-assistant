// Small shared helpers for the components: class-name merging and friendly timestamps.
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Joins class names and lets later Tailwind classes override earlier ones (for example a caller's padding). */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** A short "how long ago" label for an ISO timestamp; older than a week falls back to the local date. */
export function relativeTime(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days < 7 ? `${days} d ago` : new Date(iso).toLocaleDateString();
}
