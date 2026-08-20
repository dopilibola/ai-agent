import type {
  AnfaCatalogItem,
  AnfaDoctor,
  BouquetPage,
  CatalogImportSummary,
  Chat,
  Conversation,
  Order,
  PromptDetail,
  PromptInfo,
  Tenant,
  TenantUsage,
  UsageRollup,
} from "./types";

const BASE = import.meta.env.BASE_URL.replace(/\/$/, "") + "/api";

/** Multipart file upload (import endpoints). Doesn't set Content-Type — the
 *  browser adds the multipart boundary. */
async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(BASE + path, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(BASE + path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // non-JSON error body; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  me: () => req<{ username: string }>("/auth/me"),
  login: (username: string, password: string) =>
    req<{ username: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => req<{ ok: boolean }>("/auth/logout", { method: "POST" }),

  tenants: () => req<Tenant[]>("/tenants"),
  usage: () => req<UsageRollup[]>("/usage"),
  tenantUsage: (tenant: string) =>
    req<TenantUsage>(`/tenants/${tenant}/usage`),

  chats: (tenant: string) => req<Chat[]>(`/tenants/${tenant}/chats`),
  mute: (tenant: string, chatId: number) =>
    req(`/tenants/${tenant}/chats/${chatId}/mute`, { method: "POST" }),
  unmute: (tenant: string, chatId: number) =>
    req(`/tenants/${tenant}/chats/${chatId}/unmute`, { method: "POST" }),

  conversation: (tenant: string, channel: string, chatId: number) =>
    req<Conversation>(
      `/tenants/${tenant}/conversations/${channel}/${chatId}`,
    ),

  bouquets: (
    tenant: string,
    opts: {
      q?: string;
      includeInactive?: boolean;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const p = new URLSearchParams();
    if (opts.q) p.set("q", opts.q);
    if (opts.includeInactive) p.set("include_inactive", "true");
    p.set("limit", String(opts.limit ?? 50));
    p.set("offset", String(opts.offset ?? 0));
    return req<BouquetPage>(`/tenants/${tenant}/bouquets?${p}`);
  },
  deactivateBouquet: (tenant: string, id: string) =>
    req(`/tenants/${tenant}/bouquets/${encodeURIComponent(id)}/deactivate`, {
      method: "POST",
    }),
  reactivateBouquet: (tenant: string, id: string) =>
    req(`/tenants/${tenant}/bouquets/${encodeURIComponent(id)}/reactivate`, {
      method: "POST",
    }),

  orders: (tenant: string, status?: string) =>
    req<Order[]>(
      `/tenants/${tenant}/orders${status ? `?status=${status}` : ""}`,
    ),
  setOrderStatus: (tenant: string, orderId: number, status: string) =>
    req(`/tenants/${tenant}/orders/${orderId}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),

  services: (tenant: string) =>
    req<AnfaCatalogItem[]>(`/tenants/${tenant}/services`),
  setServiceActive: (tenant: string, id: number, active: boolean) =>
    req(
      `/tenants/${tenant}/services/${id}/${active ? "reactivate" : "deactivate"}`,
      { method: "POST" },
    ),
  importCatalog: (tenant: string, file: File) =>
    upload<CatalogImportSummary>(`/tenants/${tenant}/catalog/import`, file),

  doctors: (tenant: string) =>
    req<AnfaDoctor[]>(`/tenants/${tenant}/doctors`),
  setDoctorActive: (tenant: string, id: number, active: boolean) =>
    req(
      `/tenants/${tenant}/doctors/${id}/${active ? "reactivate" : "deactivate"}`,
      { method: "POST" },
    ),
  importDoctors: (tenant: string, file: File) =>
    upload<CatalogImportSummary>(`/tenants/${tenant}/doctors/import`, file),

  prompts: (tenant: string) =>
    req<PromptInfo[]>(`/tenants/${tenant}/prompts`),
  prompt: (tenant: string, key: string) =>
    req<PromptDetail>(`/tenants/${tenant}/prompts/${key}`),
  savePrompt: (tenant: string, key: string, content: string) =>
    req<{ key: string; bytes: number }>(`/tenants/${tenant}/prompts/${key}`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
};
