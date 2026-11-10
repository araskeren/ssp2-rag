import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  deleteSession,
  getToken,
  listSessions,
  login,
  logout,
  me,
  sessionMessages,
  streamChat,
  type Me,
  type MessageFromBe,
  type Session,
} from "~/lib/api";

type Source = {
  ref: string;
  filename: string;
  title?: string;
  page?: number;
  section?: string;
  snippet?: string;
  score?: number;
};

type SqlBlock = {
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

type Tool = { name: string; status?: string; summary?: string };

type Msg = {
  id: string;
  role: "user" | "assistant";
  text: string;
  intent?: string;
  sources?: Source[];
  sql?: SqlBlock | null;
  tools?: Tool[];
  error?: string;
};

const WELCOME: Msg = {
  id: "welcome",
  role: "assistant",
  text: "Halo. Tanyakan informasi, alur pelaporan, atau analisis data surveilans.",
};

function uid() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/** Baca stream SSE byte-per-byte, panggil onEvent per event (name, payload). */
async function readSse(
  resp: Response,
  onEvent: (name: string, ev: Record<string, unknown>) => void,
): Promise<void> {
  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let evName = "";
        let dataStr = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) evName = line.slice(6).trim();
          else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
        }
        if (!dataStr) continue;
        let ev: Record<string, unknown>;
        try {
          ev = JSON.parse(dataStr);
        } catch {
          continue;
        }
        onEvent(evName, ev);
      }
    }
  } finally {
    reader.releaseLock();
  }
}

const INTENT_LABEL: Record<string, string> = {
  helpdesk: "Bantuan",
  data_analysis: "Analisis Data",
  denied: "Akses Ditolak",
  smalltalk: "Obrolan",
};

const TOOL_LABEL: Record<string, string> = {
  cari_dokumen: "Pencarian Dokumen",
  analisis_data: "Analisis Data SQL",
};

const PERSONAS = [
  { sub: "42", label: "Admin Kemenkes — analis GLOBAL" },
  { sub: "45", label: "Eksekutif KabKota — analis KABKOTA 33.74" },
  { sub: "44", label: "Admin Provinsi — public PROVINCE 33" },
  { sub: "43", label: "Operator Faskes — public FASKES" },
  { sub: "46", label: "Viewer Pusat — public GLOBAL" },
];

function AssistantAvatar() {
  return (
    <span className="avatar" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8" />
      </svg>
    </span>
  );
}

function ThemeMonitorIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2" /><path d="M8 21h8M12 17v4" /></svg>
  );
}

function ThemeSunIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
  );
}

function ThemeMoonIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>
  );
}

type Theme = "system" | "light" | "dark";
const THEME_KEY = "theme";

function getTheme(): Theme {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

function applyTheme(t: Theme) {
  const dark = t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  try {
    if (t === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, t);
  } catch {
    /* localStorage tidak tersedia */
  }
}

export default function App() {
  const [user, setUser] = useState<Me | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [sub, setSub] = useState("42");
  const [busy, setBusy] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [msgs, setMsgs] = useState<Msg[]>([WELCOME]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(true);
  const [theme, setTheme] = useState<Theme>(getTheme);
  const abortRef = useRef<AbortController | null>(null);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  function toggleSidebar() {
    if (typeof window !== "undefined" && window.innerWidth <= 860) {
      setSidebarOpen((prev) => !prev);
    } else {
      setDesktopSidebarOpen((prev) => !prev);
    }
  }

  useEffect(() => {
    (async () => {
      const u = await me(getToken());
      setUser(u);
      setAuthChecked(true);
    })();
  }, []);

  const refreshSessions = useCallback(async () => {
    if (!getToken()) return;
    try {
      setSessions(await listSessions());
    } catch {
      setSessions([]);
    }
  }, []);

  useEffect(() => {
    if (user && getToken()) void refreshSessions();
  }, [user, refreshSessions]);

  function autosize() {
    const t = taRef.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 160) + "px";
  }

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    applyTheme(theme);
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (theme === "system") applyTheme("system");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (sending || loadingHistory || nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  }, [msgs, sending, status, loadingHistory]);

  async function handleLogin(e: React.SyntheticEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await login(sub);
      const u = await me(getToken());
      setUser(u);
      await refreshSessions();
    } catch {
      setUser(null);
    } finally {
      setBusy(false);
    }
  }

  async function handleLogout() {
    await logout();
    setUser(null);
    setSessions([]);
    setMsgs([WELCOME]);
    setSessionId("");
  }

  function startNew() {
    abortRef.current?.abort();
    setMsgs([WELCOME]);
    setSessionId("");
    setLoadingHistory(false);
    setSidebarOpen(false);
  }

  async function openSession(id: string) {
    if (id === sessionId && !loadingHistory) return;
    abortRef.current?.abort();
    setSidebarOpen(false);
    setSessionId(id);
    setMsgs([WELCOME]);
    setLoadingHistory(true);
    try {
      const data = await sessionMessages(id);
      const loaded: Msg[] = data.map((m: MessageFromBe) => ({
        id: String(m.id),
        role: m.role === "user" ? ("user" as const) : ("assistant" as const),
        text: m.content || "",
        intent: m.intent,
        sources: m.sources,
        sql: m.sql,
      }));
      setMsgs(loaded.length ? loaded : [WELCOME]);
    } catch {
      setMsgs([WELCOME]);
    } finally {
      setLoadingHistory(false);
    }
  }

  async function handleDeleteSession(id: string) {
    await deleteSession(id);
    if (id === sessionId) startNew();
    await refreshSessions();
  }

  function patchAt(id: string, fn: (m: Msg) => Msg) {
    setMsgs((prev) => prev.map((m) => (m.id === id ? fn(m) : m)));
  }

  async function send() {
    const q = input.trim();
    if (!q || sending || user?.available === false) return;
    setSending(true);
    setStatus("Mengirim…");

    const userMsgId = uid();
    const assistantMsgId = uid();

    setMsgs((m) => [
      ...m,
      { id: userMsgId, role: "user" as const, text: q },
      { id: assistantMsgId, role: "assistant" as const, text: "" },
    ]);
    setInput("");

    const controller = new AbortController();
    abortRef.current = controller;

    const body: Record<string, unknown> = { query: q };
    if (sessionId) body.session_id = sessionId;

    let resp: Response;
    try {
      resp = await streamChat(body as { query: string; session_id?: string }, controller.signal);
    } catch {
      if (controller.signal.aborted) {
        setSending(false);
        setStatus("");
        return;
      }
      patchAt(assistantMsgId, (m) => ({ ...m, text: "Jaringan gagal.", error: "Jaringan gagal." }));
      setSending(false);
      setStatus("");
      return;
    }

    if (!resp.ok || !resp.body) {
      let msg = "Terjadi kesalahan.";
      try {
        const e = await resp.json();
        msg = e.message || msg;
      } catch {
        /* polos */
      }
      patchAt(assistantMsgId, (m) => ({ ...m, text: msg, error: msg }));
      setSending(false);
      setStatus("");
      return;
    }

    let acc = "";
    const tools: Tool[] = [];
    let sources: Source[] = [];
    let sql: SqlBlock | null = null;
    let intent = "";

    setStatus("Asisten mengetik…");

    try {
      await readSse(resp, (name, ev) => {
        switch (name) {
          case "meta":
            if (typeof ev.session_id === "string") {
              setSessionId(ev.session_id);
              void refreshSessions();
            }
            break;
          case "token":
            if (typeof ev.t === "string") {
              acc += ev.t;
              patchAt(assistantMsgId, (m) => ({ ...m, text: acc }));
            }
            break;
          case "tool":
            tools.push({
              name: String(ev.name ?? ""),
              status: typeof ev.status === "string" ? ev.status : undefined,
              summary: typeof ev.summary === "string" ? ev.summary : undefined,
            });
            patchAt(assistantMsgId, (m) => ({ ...m, tools: [...tools] }));
            break;
          case "sources":
            if (Array.isArray(ev.items)) sources = ev.items as Source[];
            break;
          case "sql":
            sql = ev as unknown as SqlBlock;
            break;
          case "done":
            intent = typeof ev.intent === "string" ? ev.intent : "";
            patchAt(assistantMsgId, (m) => ({
              ...m,
              text: acc || m.text,
              intent,
              sources,
              sql,
              tools,
            }));
            void refreshSessions();
            break;
          case "error":
            patchAt(assistantMsgId, (m) => ({
              ...m,
              error: String(ev.message ?? "Terjadi kesalahan."),
              text: acc || String(ev.message ?? "Terjadi kesalahan."),
            }));
            break;
        }
      });
    } catch {
      if (!controller.signal.aborted) {
        patchAt(assistantMsgId, (m) => ({ ...m, error: "Koneksi terputus." }));
      }
    }
    abortRef.current = null;

    setSending(false);
    setStatus("");
  }

  if (!authChecked) return <div className="shell"><div className="muted">Memuat…</div></div>;

  if (!user) {
    return (
      <div className="shell">
        <section className="login-card">
          <h1>Masuk</h1>
          <p className="muted">
            Mode dev: pilih persona mock-BE untuk melihat efek peran &amp; wilayah
            (scope) terhadap jawaban.
          </p>
          <form onSubmit={handleLogin}>
            <label htmlFor="persona">Persona</label>
            <select id="persona" value={sub} onChange={(e) => setSub(e.target.value)}>
              {PERSONAS.map((p) => (
                <option key={p.sub} value={p.sub}>
                  {p.label}
                </option>
              ))}
            </select>
            <button className="btn primary" type="submit" disabled={busy}>
              {busy ? "Masuk…" : "Masuk"}
            </button>
          </form>
        </section>
      </div>
    );
  }

  const assistantOff = user.available === false;

  return (
    <div className="app-root">
      <header className="topbar">
        <div className="topbar-left">
          <button
            className="sidebar-toggle-btn"
            type="button"
            onClick={toggleSidebar}
            aria-label="Toggle riwayat percakapan"
            title="Riwayat chat"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
            <span className="toggle-label">Riwayat</span>
          </button>
          <span className="brand">
            <span className="brand-mark" aria-hidden="true" />
            <span className="brand-title">Asisten Satu Data Penyakit</span>
          </span>
        </div>
        <div className="me">
          <div className="me-user">
            <span className="me-name">{user.display_name}</span>
            <span className="me-role">{user.role_name}</span>
          </div>
          <div className="me-badges">
            {user.ai_role === "analyst" && (
              <span className="badge analyst">Analis</span>
            )}
            {user.ai_role === "analyst" && user.features?.analysis === false && (
              <span className="badge scope">Analisis nonaktif</span>
            )}
            <span className="badge scope">{user.scope_label}</span>
          </div>
          <div className="me-actions">
            <button className="btn ghost" onClick={handleLogout}>
              Keluar
            </button>
            <button
              className="btn ghost theme-toggle"
              type="button"
              onClick={() =>
                setTheme(theme === "system" ? "light" : theme === "light" ? "dark" : "system")
              }
              aria-label="Ganti tema (sistem/terang/gelap)"
              title={
                theme === "system"
                  ? "Tema: Ikuti Sistem"
                  : theme === "light"
                    ? "Tema: Terang"
                    : "Tema: Gelap"
              }
            >
              {theme === "system" ? (
                <ThemeMonitorIcon />
              ) : theme === "light" ? (
                <ThemeSunIcon />
              ) : (
                <ThemeMoonIcon />
              )}
            </button>
          </div>
        </div>
      </header>

      <div
        className={
          "chat-layout" +
          (sidebarOpen ? " nav-open" : "") +
          (!desktopSidebarOpen ? " desktop-sidebar-closed" : "")
        }
      >
        <div className="sidebar-backdrop" aria-hidden="true" onClick={() => setSidebarOpen(false)} />
        <aside className="sidebar">
          <div className="sidebar-head">
            <h2>Riwayat</h2>
            <button className="btn outline" onClick={startNew}>
              Baru
            </button>
          </div>
          <ul className="session-list">
            {sessions.map((s) => (
              <li key={s.session_id}>
                <div className="session-item-wrap">
                  <button
                    className={"session-item has-del" + (s.session_id === sessionId ? " active" : "")}
                    onClick={() => openSession(s.session_id)}
                  >
                    <span className="session-title">{s.title || "Tanpa judul"}</span>
                    <span className="session-meta">
                      {new Date(s.last_message_at).toLocaleDateString("id-ID")} ·{" "}
                      {s.message_count} pesan
                    </span>
                  </button>
                  <button
                    className="session-del"
                    aria-label="Hapus sesi"
                    onClick={() => handleDeleteSession(s.session_id)}
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" /></svg>
                  </button>
                </div>
              </li>
            ))}
            {sessions.length === 0 && (
              <li className="muted">
                Belum ada sesi. Ajukan pertanyaan di kolom chat untuk memulai.
              </li>
            )}
          </ul>
        </aside>

        <section className="chat-main">
          <div className="messages" ref={messagesRef}>
            {loadingHistory ? (
              <div className="skeleton-list" aria-label="Memuat riwayat…">
                <div className="skeleton skeleton-msg" />
                <div className="skeleton skeleton-msg skeleton-msg--short" />
                <div className="skeleton skeleton-msg skeleton-msg--long" />
              </div>
            ) : (
              <>
                {msgs.map((m) => (
                  <Message key={m.id} msg={m} />
                ))}
                {sending && (
                  <div className="msg assistant pending">
                    <AssistantAvatar />
                    <span className="dots" aria-label="mengetik">
                      <i /><i /><i />
                    </span>
                  </div>
                )}
              </>
            )}
          </div>

          {assistantOff && (
            <div className="assistant-off" role="status">
              Asisten dinonaktifkan oleh administrator. Ajuan tidak dapat diproses
              untuk saat ini.
            </div>
          )}

          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <textarea
              ref={taRef}
              value={input}
              disabled={assistantOff}
              onChange={(e) => {
                setInput(e.target.value);
                autosize();
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              placeholder="Tulis pertanyaan…"
              rows={1}
              aria-label="Pertanyaan"
            />
            <button
              className="btn primary send"
              type="submit"
              aria-label="Kirim"
              disabled={sending || !input.trim() || assistantOff}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4 20-7z" /></svg>
            </button>
          </form>
          {status && <div className="statusline">{status}</div>}
        </section>
      </div>
    </div>
  );
}

function Message({ msg }: { msg: Msg }) {
  const isUser = msg.role === "user";
  return (
    <div className={"msg " + (isUser ? "user" : "assistant")}>
      {!isUser && <AssistantAvatar />}
      <div className="bubble">
        {!isUser && (
          <div className="msg-meta">
            {msg.intent && !(msg.sql && msg.sql.status === "ok") && (
              <span className={"badge intent intent-" + msg.intent}>
                {INTENT_LABEL[msg.intent] || msg.intent}
              </span>
            )}
            {msg.tools && msg.tools.length > 0 && !(msg.sql && msg.sql.status === "ok") && (
              <span className="tools">
                {msg.tools
                  .filter((t, i, a) => a.findIndex((x) => x.name === t.name) === i)
                  .map((t) => (
                    <span key={t.name} className="badge tool">
                      {TOOL_LABEL[t.name] || t.name}{" "}
                      {t.status === "end" ? (
                        <svg
                          className="tool-ok"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="3"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          aria-hidden="true"
                        >
                          <path d="M20 6 9 17l-5-5" />
                        </svg>
                      ) : (
                        "…"
                      )}
                    </span>
                  ))}
              </span>
            )}
          </div>
        )}
        {isUser ? (
          <div className="user-text">{msg.text}</div>
        ) : msg.error ? (
          <div className="error-text">{msg.error}</div>
        ) : (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              table: ({ node: _node, ...rest }: any) => (
                <div className="table-scroll" role="region" aria-label="Tabel hasil" tabIndex={0}>
                  <table {...rest} />
                </div>
              ),
              th: ({ node: _node, ...rest }: any) => <th scope="col" {...rest} />,
            }}
          >
            {msg.text || "…"}
          </ReactMarkdown>
        )}
        {!isUser && msg.sources && msg.sources.length > 0 && (
          <div className="sources">
            <div className="sources-head">Sumber</div>
            {msg.sources.map((s, i) => (
              <div key={i} className="source">
                <span className="ref">{s.ref}</span>
                <span className="src-title">{s.title || s.filename}</span>
                {s.page ? <span className="src-page">hlm. {s.page}</span> : null}
                {s.snippet ? <span className="src-snippet">{s.snippet}</span> : null}
              </div>
            ))}
          </div>
        )}
        {!isUser && msg.sql && msg.sql.status === "ok" && (
          <div className="sql-block">
            <div className="sql-head">
              <span>Hasil analisis</span>
              {msg.sql.scope_label ? (
                <span className="badge scope">{msg.sql.scope_label}</span>
              ) : null}
              {msg.sql.elapsed_ms != null ? (
                <span className="muted">
                  {(msg.sql.elapsed_ms / 1000).toFixed(1)}s
                </span>
              ) : null}
            </div>
            {msg.sql.rows && (
              <div className="table-scroll sql-table-scroll" role="region" aria-label="Hasil analisis" tabIndex={0}>
                <table>
                  <thead>
                  <tr>
                    {(msg.sql.columns || []).map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {msg.sql.rows.map((row, i) => (
                    <tr key={i}>
                      {row.map((cell, j) => (
                        <td key={j}>{String(cell)}</td>
                      ))}
                    </tr>
                  ))}
                  </tbody>
                </table>
              </div>
            )}
            {msg.sql.truncated ? (
              <div className="muted">Baris dipotong (menampilkan sebagian).</div>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}
