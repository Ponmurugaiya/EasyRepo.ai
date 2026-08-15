import { cn } from "../../lib/utils";
import type { RepoStatus } from "../../types/api";

const config: Record<
  RepoStatus,
  { label: string; dot: string; text: string }
> = {
  pending: {
    label: "Pending",
    dot: "bg-yellow-400",
    text: "text-yellow-700 dark:text-yellow-400",
  },
  indexing: {
    label: "Indexing…",
    dot: "bg-blue-400 animate-pulse",
    text: "text-blue-700 dark:text-blue-400",
  },
  ready: {
    label: "Ready",
    dot: "bg-green-400",
    text: "text-green-700 dark:text-green-400",
  },
  failed: {
    label: "Failed",
    dot: "bg-red-400",
    text: "text-red-700 dark:text-red-400",
  },
  cancelled: {
    label: "Cancelled",
    dot: "bg-zinc-500",
    text: "text-zinc-500 dark:text-zinc-400",
  },
};

export function StatusBadge({ status }: { status: RepoStatus }) {
  const c = config[status];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs font-medium", c.text)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", c.dot)} />
      {c.label}
    </span>
  );
}
