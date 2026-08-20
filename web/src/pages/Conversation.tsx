import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import type { Conversation as Conv } from "../types";

export default function Conversation() {
  const { tenant = "", channel = "", chatId = "" } = useParams();
  const [conv, setConv] = useState<Conv | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .conversation(tenant, channel, Number(chatId))
      .then(setConv)
      .catch((e) => setError(e.message ?? String(e)));
  }, [tenant, channel, chatId]);

  if (error) return <div className="error">{error}</div>;
  if (!conv) return <div className="muted">Loading…</div>;

  return (
    <div>
      <div className="crumbs">
        <Link to={`/tenants/${tenant}/chats`}>← {tenant} chats</Link>
      </div>
      <h2 className="mono thread">{conv.thread_id}</h2>
      {conv.messages.length === 0 && (
        <p className="muted">No messages stored for this thread.</p>
      )}
      <div className="chat">
        {conv.messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="msg-role">
              {m.name ? `${m.role} · ${m.name}` : m.role}
            </div>
            {m.text && <div className="msg-text">{m.text}</div>}
            {m.has_image && <div className="msg-meta">🖼 image attached</div>}
            {m.tool_calls?.map((tc, j) => (
              <div key={j} className="msg-tool">
                → {tc.name}({JSON.stringify(tc.args)})
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
