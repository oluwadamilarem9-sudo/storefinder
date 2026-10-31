import { clearSession, getToken } from "./auth";
import type {
  DiscoveryRun,
  Lead,
  Project,
  Rejected,
  SavedFilter,
  StoreCompareResult,
  StoreProduct,
  User,
  WatchlistItem,
} from "./types";

const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  options: RequestInit & { auth?: boolean } = {},
): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (!headers.has("Content-Type") && options.body) {
    headers.set("Content-Type", "application/json");
  }
  if (options.auth !== false) {
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* ignore */
    }
    // Stale JWT after API restart left the UI frozen on an old activity log.
    if (response.status === 401 && options.auth !== false && !path.startsWith("/auth/")) {
      clearSession();
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function signup(name: string, email: string, password: string) {
  return request<{ id: string; token: string; user: User }>("/auth/signup", {
    method: "POST",
    auth: false,
    body: JSON.stringify({ name, email, password }),
  });
}

export function login(email: string, password: string) {
  return request<{ token: string; user: User }>("/auth/login", {
    method: "POST",
    auth: false,
    body: JSON.stringify({ email, password }),
  });
}

export function listProjects() {
  return request<Project[]>("/projects");
}

export function createProject(name: string) {
  return request<Project>("/projects", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function listRuns(projectId: string) {
  return request<DiscoveryRun[]>(`/projects/${projectId}/runs`);
}

export function getRun(projectId: string, runId: string) {
  return request<DiscoveryRun>(`/projects/${projectId}/runs/${runId}`);
}

export type RunCreatePayload = {
  name?: string;
  max_domains?: number;
  on_page?: boolean;
  settings?: {
    max_domains?: number;
    enable_secondary?: boolean;
    target_countries?: string[];
    keep_unknown_country?: boolean;
    store_age_filter?: "all" | "new" | "established" | "unknown";
  };
};

export function createRun(projectId: string, payload: RunCreatePayload) {
  return request<DiscoveryRun>(`/projects/${projectId}/runs`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listLeads(projectId: string) {
  return request<Lead[]>(`/projects/${projectId}/leads`);
}

export function listRejected(projectId: string) {
  return request<Rejected[]>(`/projects/${projectId}/rejected`);
}

export function listProducts(projectId: string) {
  return request<StoreProduct[]>(`/projects/${projectId}/products`);
}

export function compareStores(projectId: string, domainA: string, domainB: string) {
  return request<StoreCompareResult>(`/projects/${projectId}/compare`, {
    method: "POST",
    body: JSON.stringify({ domain_a: domainA, domain_b: domainB }),
  });
}

export function listWatchlist(projectId: string) {
  return request<WatchlistItem[]>(`/projects/${projectId}/watchlist`);
}

export function addWatchlist(projectId: string, domain: string, note?: string) {
  return request<WatchlistItem>(`/projects/${projectId}/watchlist`, {
    method: "POST",
    body: JSON.stringify({ domain, note: note || null }),
  });
}

export function removeWatchlist(projectId: string, itemId: string) {
  return request<{ ok: boolean }>(`/projects/${projectId}/watchlist/${itemId}`, {
    method: "DELETE",
  });
}

export function listSavedFilters(projectId: string) {
  return request<SavedFilter[]>(`/projects/${projectId}/filters`);
}

export function createSavedFilter(
  projectId: string,
  name: string,
  config: SavedFilter["config"],
) {
  return request<SavedFilter>(`/projects/${projectId}/filters`, {
    method: "POST",
    body: JSON.stringify({ name, config }),
  });
}

export function deleteSavedFilter(projectId: string, filterId: string) {
  return request<{ ok: boolean }>(`/projects/${projectId}/filters/${filterId}`, {
    method: "DELETE",
  });
}

export function downloadCsv(filename: string, rows: Record<string, unknown>[], columns: string[]) {
  const escape = (value: unknown) => {
    const text = value == null ? "" : String(value);
    if (/[",\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
    return text;
  };
  const lines = [columns.join(",")];
  for (const row of rows) {
    lines.push(columns.map((col) => escape(row[col])).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
