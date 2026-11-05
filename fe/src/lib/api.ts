// Klien API langsung (tanpa BFF). FE memegang JWT (localStorage) dan
// mengirimkannya sebagai Bearer; backend yang memvalidasi.
import { AI_URL, BE_URL } from "./env";

const TOKEN_KEY = "access_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function setToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  const t = getToken();
  if (t) h.Authorization = `Bearer ${t}`;
  return h;
}

/** Login ke (mock) BE → simpan JWT. */
export async function login(sub: string): Promise<void> {
  const resp = await fetch(`${BE_URL}/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ sub }),
  });
  if (!resp.ok) throw new Error("Login gagal");
  const data = (await resp.json()) as { access_token?: string };
  if (!data.access_token) throw new Error("Token tidak diterima");
  setToken(data.access_token);
}

export async function logout(): Promise<void> {
  try {
    await fetch(`${BE_URL}/auth/logout`, { method: "POST" });
  } finally {
    setToken(null);
  }
}

export type Me = {
  user_id?: string;
  email?: string;
  display_name?: string;
  role_name?: string;
  ai_role?: "public" | "analyst";
  analyst_reason?: string;
  scope?: {
    level: string;
    provinsi_kode?: string | null;
    kabupaten_kota_kode?: string | null;
    facility_id?: number | null;
  };
  scope_label?: string;
  features?: { helpdesk?: boolean; analysis?: boolean };
  available?: boolean;
};

export async function me(token: string | null = getToken()): Promise<Me | null> {
  if (!token) return null;
  const resp = await fetch(`${AI_URL}/me`, { headers: authHeaders() });
  if (resp.status === 401) return null;
  if (!resp.ok) return null;
  return (await resp.json()) as Me;
}

export type Session = {
  session_id: string;
  title: string;
  message_count: number;
  created_at: string;
  last_message_at: string;
};

export async function listSessions(): Promise<Session[]> {
  const resp = await fetch(`${AI_URL}/chat/sessions`, { headers: authHeaders() });
  if (!resp.ok) return [];
  const data = (await resp.json()) as { items?: Session[] };
  return data.items || [];
}

export type MessageFromBe = {
  id: string;
  role: string;
  content?: string;
  intent?: string;
  sources?: Source[];
  sql?: SqlBlock | null;
};

export type Source = {
  ref: string;
  filename: string;
  title?: string;
  page?: number;
  section?: string;
  snippet?: string;
  score?: number;
};

export type SqlBlock = {
  backend: string;
  status: string;
  query?: string;
  columns?: string[];
  rows?: (string | number)[][];
  row_count?: number;
  truncated?: boolean;
  scope_label?: string;
  elapsed_ms?: number;
};

export async function sessionMessages(
  id: string,
): Promise<MessageFromBe[]> {
  const resp = await fetch(
    `${AI_URL}/chat/sessions/${encodeURIComponent(id)}/messages`,
    { headers: authHeaders() },
  );
  if (!resp.ok) return [];
  const data = (await resp.json()) as { items?: MessageFromBe[] };
  return data.items || [];
}

export async function deleteSession(id: string): Promise<void> {
  await fetch(`${AI_URL}/chat/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
}

export type StreamBody = { query: string; session_id?: string };

/** Buka koneksi stream chat (SSE). Kembalikan Response mentah untuk dibaca. */
export function streamChat(
  body: StreamBody,
  signal: AbortSignal,
): Promise<Response> {
  return fetch(`${AI_URL}/chat/stream`, {
    method: "POST",
    headers: authHeaders({ "content-type": "application/json" }),
    body: JSON.stringify(body),
    signal,
  });
}
