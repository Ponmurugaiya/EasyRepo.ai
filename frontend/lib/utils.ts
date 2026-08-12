import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatRelativeTime(timestamp: number): string {
  const diff = Date.now() - timestamp;
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function truncate(str: string, max: number): string {
  if (str.length <= max) return str;
  return str.slice(0, max - 1) + "…";
}

export function repoDisplayName(urlOrPath: string): string {
  // Extract last path component, strip .git
  const clean = urlOrPath.replace(/\.git$/, "").replace(/\/$/, "");
  const parts = clean.split(/[/\\]/);
  return parts[parts.length - 1] ?? urlOrPath;
}

export function isGitHubUrl(source: string): boolean {
  return /^https?:\/\/(www\.)?github\.com\//i.test(source);
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
