import type {
  Chat,
  Conversation,
  PromptDetail,
  PromptInfo,
  Tenant,
  TenantUsage,
  UsageRollup,
} from "./types";

const BASE = import.meta.env.BASE_URL.replace(/\/$/, "") + "/api";

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
