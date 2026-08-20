import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { UsageRollup } from "../types";
import { fmt } from "../util";

export default function Dashboard() {
  const [rows, setRows] = useState<UsageRollup[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .usage()
      .then(setRows)
      .catch((e) => setError(e.message ?? String(e)));
  }, []);

  if (error) return <div className="error">{error}</div>;
  if (!rows) return <div className="muted">Loading…</div>;

  const grandTotal = rows.reduce((acc, r) => acc + r.spent_total_tokens, 0);

  return (
    <div>
      <h2>Token usage</h2>
      <div className="cards">
        {rows.map((r) => (
          <Link
            className="card stat"
            key={r.tenant}
            to={`/tenants/${r.tenant}/chats`}
          >
            <div className="stat-name">{r.name}</div>
            <div className="stat-big">{fmt(r.spent_total_tokens)}</div>
            <div className="stat-sub">
              {r.chats} chats · in {fmt(r.spent_input_tokens)} · out{" "}
              {fmt(r.spent_output_tokens)} · cached{" "}
              {fmt(r.spent_cached_input_tokens)}
            </div>
          </Link>
        ))}
      </div>
      <p className="muted total-line">
        Total across tenants: <strong>{fmt(grandTotal)}</strong> tokens
      </p>
    </div>
  );
}
