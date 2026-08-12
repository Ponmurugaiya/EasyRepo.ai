// ─────────────────────────────────────────────────────────────────────────────
// Graph API client — wraps the /repositories/{id}/graph endpoints
// ─────────────────────────────────────────────────────────────────────────────

import type { FileExpandResponse, FileGraphResponse } from "../types/graph";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function graphRequest<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export interface GetFileGraphOptions {
  root?: string;
  depth?: number;
  includeImports?: boolean;
  showAll?: boolean;
}

export async function getFileGraph(
  repoId: string,
  options: GetFileGraphOptions = {}
): Promise<FileGraphResponse> {
  const params = new URLSearchParams();
  if (options.root) params.set("root", options.root);
  if (options.depth !== undefined) params.set("depth", String(options.depth));
  if (options.includeImports !== undefined)
    params.set("include_imports", String(options.includeImports));
  if (options.showAll) params.set("show_all", "true");

  const qs = params.toString();
  return graphRequest<FileGraphResponse>(
    `/repositories/${repoId}/graph${qs ? `?${qs}` : ""}`
  );
}

export async function expandFileNode(
  repoId: string,
  fileEntityId: string
): Promise<FileExpandResponse> {
  return graphRequest<FileExpandResponse>(
    `/repositories/${repoId}/graph/${encodeURIComponent(fileEntityId)}/expand`
  );
}
