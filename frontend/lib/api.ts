// ─────────────────────────────────────────────────────────────────────────────
// API client — thin wrapper around the FastAPI backend
// ─────────────────────────────────────────────────────────────────────────────

import type {
  AskRequest,
  AskResponse,
  EntitySourceResponse,
  RepositoryCreateRequest,
  RepositoryResponse,
  RepositoryStatusResponse,
} from "../types/api";

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

async function request<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs = 120_000   // 2-minute default — covers the full LLM pipeline
): Promise<T> {
  const timeoutController = new AbortController();
  const timer = setTimeout(() => timeoutController.abort(), timeoutMs);

  // Attach auth header based on login mode:
  //  - dev token  → X-API-Key (existing backend contract)
  //  - cognito    → Authorization: Bearer <jwt> (future backend contract)
  const loginMode = getLoginMode();
  const devToken  = getDevToken();
  let authHeaders: Record<string, string> = {};
  if (loginMode === "cognito") {
    // Cognito JWT lives in Amplify's own localStorage keys — import lazily
    // to avoid bundling amplify in paths that never use it.
    try {
      const { getCognitoToken } = await import("./cognito");
      const jwt = await getCognitoToken();
      if (jwt) authHeaders = { Authorization: `Bearer ${jwt}` };
    } catch {
      // Not critical — fall through without auth header
    }
  } else if (devToken) {
    authHeaders = { "X-API-Key": devToken };
  }

  // Combine the caller's signal with the internal timeout signal so either
  // one can abort the fetch. AbortSignal.any() is available in all modern
  // browsers; fall back to just the timeout signal if not (shouldn't happen).
  const callerSignal = options.signal;
  const combinedSignal: AbortSignal =
    callerSignal && typeof AbortSignal.any === "function"
      ? AbortSignal.any([callerSignal, timeoutController.signal])
      : timeoutController.signal;

  // Pull signal out of options so the spread below doesn't re-overwrite it.
  const { signal: _ignored, ...restOptions } = options;

  try {
    const res = await fetch(`${BASE}${path}`, {
      ...restOptions,
      headers: {
        "Content-Type": "application/json",
        ...authHeaders,
        ...restOptions.headers,
      },
      signal: combinedSignal,
    });

    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      let errorCode: string | undefined;
      try {
        const body = await res.json();
        detail = body.detail ?? detail;
        errorCode = body.error_code ?? undefined;
      } catch {
        // ignore parse error
      }
      throw new ApiError(detail, res.status, errorCode);
    }

    // 204 No Content
    if (res.status === 204) return undefined as unknown as T;

    return res.json() as Promise<T>;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      // Distinguish user-cancel from a real timeout.
      const isUserCancel = callerSignal?.aborted === true;
      if (isUserCancel) {
        throw new ApiError("Request cancelled.", 0);
      }
      throw new ApiError(
        "Request timed out. The server is taking too long — please try again.",
        408
      );
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number = 0,
    /** Structured error code from the backend (e.g. "llm_quota_exhausted") */
    public readonly errorCode?: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ── Repositories ────────────────────────────────────────────────────────────

export async function listRepositories(): Promise<RepositoryResponse[]> {
  return request<RepositoryResponse[]>("/repositories");
}

export async function ingestRepository(
  source: string
): Promise<RepositoryResponse> {
  return request<RepositoryResponse>("/repositories", {
    method: "POST",
    body: JSON.stringify({ source } satisfies RepositoryCreateRequest),
  });
}

export async function getRepositoryStatus(
  repoId: string
): Promise<RepositoryStatusResponse> {
  return request<RepositoryStatusResponse>(`/repositories/${repoId}/status`);
}

export async function getRepository(
  repoId: string
): Promise<RepositoryResponse> {
  return request<RepositoryResponse>(`/repositories/${repoId}`);
}

// ── Ask ─────────────────────────────────────────────────────────────────────

export async function askRepository(
  repoId: string,
  payload: AskRequest,
  signal?: AbortSignal
): Promise<AskResponse> {
  return request<AskResponse>(`/repositories/${repoId}/ask`, {
    method: "POST",
    body: JSON.stringify(payload),
    signal,
  });
}

// ── Entity source ────────────────────────────────────────────────────────────

export async function getEntitySource(
  repoId: string,
  entityId: string
): Promise<EntitySourceResponse> {
  return request<EntitySourceResponse>(
    `/repositories/${repoId}/entities/${encodeURIComponent(entityId)}/source`
  );
}

// ── Health ───────────────────────────────────────────────────────────────────

export async function getHealth(): Promise<{ status: string; database: string }> {
  return request("/health");
}
