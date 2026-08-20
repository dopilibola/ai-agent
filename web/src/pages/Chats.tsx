import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import TenantTabs from "../components/TenantTabs";
import type { Chat } from "../types";
import { ago, fmt } from "../util";

export default function Chats() {
  const { tenant = "" } = useParams();
  const [rows, setRows] = useState<Chat[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(() => {
    setRows(null);
    setError(null);
    api
      .chats(tenant)
      .then(setRows)
      .catch((e) => setError(e.message ?? String(e)));
  }, [tenant]);

  useEffect(load, [load]);

  const toggle = async (c: Chat) => {
    setBusy(c.chat_id);
    try {
      if (c.muted) await api.unmute(tenant, c.chat_id);
      else await api.mute(tenant, c.chat_id);
      load();
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <TenantTabs tenant={tenant} />
      {error && <div className="error">{error}</div>}
      {!rows && !error && <div className="muted">Loading…</div>}
      {rows && (
        <ChatsTable rows={rows} tenant={tenant} busy={busy} toggle={toggle} />
      )}
    </div>
  );
}

function ChatsTable({
  rows,
  tenant,
  busy,
  toggle,
}: {
  rows: Chat[];
  tenant: string;
  busy: number | null;
  toggle: (c: Chat) => void;
}) {
  return (
    <div>
      <h2>
        {tenant} · chats <span className="muted">({rows.length})</span>
      </h2>
      {rows.length === 0 && (
        <p className="muted">No conversations recorded yet.</p>
      )}
      {rows.length > 0 && (
        <table className="grid">
          <thead>
            <tr>
              <th>Chat</th>
              <th>Chat ID</th>
              <th>Channels</th>
              <th>AI</th>
              <th>Spent</th>
              <th>Last activity</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.chat_id}>
                <td>
                  {c.name || <span className="muted">Unknown</span>}
                  {c.username && (
                    <span className="muted"> @{c.username}</span>
                  )}
                </td>
                <td className="mono">{c.chat_id}</td>
                <td>{c.channels.join(", ") || "—"}</td>
                <td>
                  {c.muted ? (
                    <span className="badge off">OFF</span>
                  ) : (
                    <span className="badge on">ON</span>
                  )}
                </td>
                <td>{fmt(c.spent_total_tokens)}</td>
                <td className="muted">{ago(c.updated_at)}</td>
                <td className="actions">
                  <button
                    className="sm"
                    disabled={busy === c.chat_id}
                    onClick={() => toggle(c)}
                  >
                    {c.muted ? "Enable AI" : "Take over"}
                  </button>
                  {c.channels.map((ch) => (
                    <Link
                      key={ch}
                      className="sm linkbtn"
                      to={`/tenants/${tenant}/conversations/${ch}/${c.chat_id}`}
                    >
                      View {ch}
                    </Link>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
