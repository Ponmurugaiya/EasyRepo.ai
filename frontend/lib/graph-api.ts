// ─────────────────────────────────────────────────────────────────────────────
// Graph API client — wraps the /repositories/{id}/graph endpoints
// ─────────────────────────────────────────────────────────────────────────────

import type { FileExpandResponse, FileGraphResponse } from "../types/graph";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Read the stored dev token without subscribing to store updates. */
function getDevToken(): string | null {
  try {
    const raw = localStorage.getItem("easyrepo-dev-token");
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { state?: { token?: string | null } };
    return parsed?.state?.token ?? null;
  } catch {
    return null;
  }
}

/** Read the stored login mode without subscribing to store updates. */
function getLoginMode(): string | null {
  try {
    const raw = localStorage.getItem("easyrepo-dev-token");
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { state?: { loginMode?: string | null } };
    return parsed?.state?.loginMode ?? null;
  } catch {
    return null;
  }
}

async function graphRequest<T>(path: string): Promise<T> {
  // Build auth headers exactly the same way as api.ts request()
  const loginMode = getLoginMode();
  const devToken = getDevToken();
  let authHeaders: Record<string, string> = {};

  if (loginMode === "cognito") {
    try {
      const { getCognitoToken } = await import("./cognito");
      const jwt = await getCognitoToken();
      if (jwt) authHeaders = { Authorization: `Bearer ${jwt}` };
    } catch {
      // fall through without auth
    }
  } else if (devToken) {
    authHeaders = { "X-API-Key": devToken };
  }

  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeaders },
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
