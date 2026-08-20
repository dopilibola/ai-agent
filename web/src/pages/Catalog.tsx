import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import TenantTabs from "../components/TenantTabs";
import type { Bouquet, BouquetPage } from "../types";
import { ago, fmt } from "../util";

const LIMIT = 50;

export default function Catalog() {
  const { tenant = "" } = useParams();
  const [data, setData] = useState<BouquetPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showInactive, setShowInactive] = useState(false);
  const [q, setQ] = useState(""); // live input
  const [search, setSearch] = useState(""); // debounced, applied
  const [offset, setOffset] = useState(0);

  // Debounce the input; a new search resets to the first page.
  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(q.trim());
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  const load = useCallback(() => {
    setError(null);
    api
      .bouquets(tenant, { q: search, includeInactive: showInactive, limit: LIMIT, offset })
      .then(setData)
      .catch((e) => setError(e.message ?? String(e)));
  }, [tenant, search, showInactive, offset]);

  useEffect(load, [load]);

  const act = async (b: Bouquet, activate: boolean) => {
    if (
      !activate &&
      !window.confirm(`Take "${b.name}" off sale? You can reactivate it later.`)
    )
      return;
    setBusy(b.id);
    try {
      if (activate) await api.reactivateBouquet(tenant, b.id);
      else await api.deactivateBouquet(tenant, b.id);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const total = data?.total ?? 0;
  const rows = data?.items ?? [];
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + LIMIT, total);

  return (
    <div>
      <TenantTabs tenant={tenant} />
      <div className="toolbar">
        <input
          className="search"
          placeholder="Search bouquets by name…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <label className="filter">
          <input
            type="checkbox"
            checked={showInactive}
            onChange={(e) => {
              setShowInactive(e.target.checked);
              setOffset(0);
            }}
          />{" "}
          show inactive
        </label>
      </div>

      {error && <div className="error">{error}</div>}
      {!data && !error && <div className="muted">Loading…</div>}
      {data && (
        <>
          <h2>
            {tenant} · catalogue <span className="muted">({total})</span>
          </h2>
          {rows.length === 0 && (
            <p className="muted">{search ? "No matches." : "No bouquets."}</p>
          )}
          {rows.length > 0 && (
            <>
              <table className="grid">
                <thead>
                  <tr>
                    <th />
                    <th>Name</th>
                    <th>Flowers</th>
                    <th>Price</th>
                    <th>Status</th>
                    <th>Added</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((b) => (
                    <tr key={b.id}>
                      <td>
                        {b.photo_url ? (
                          <img
                            className="thumb"
                            src={b.photo_url}
                            alt={b.name}
                            loading="lazy"
                          />
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td>
                        {b.name}
                        <br />
                        <span className="muted">{b.tags.join(", ")}</span>
                      </td>
                      <td className="muted">
                        {b.products_spent
                          .map((p) => `${p.flower_name} ×${p.quantity}`)
                          .join(", ") || "—"}
                      </td>
                      <td>{fmt(b.price)} сум</td>
                      <td>
                        <span className={`badge ${b.active ? "on" : "off"}`}>
                          {b.active ? "ACTIVE" : "INACTIVE"}
                        </span>
                      </td>
                      <td className="muted">{ago(b.created_at)}</td>
                      <td className="actions">
                        <button
                          className="sm"
                          disabled={busy === b.id}
                          onClick={() => act(b, !b.active)}
                        >
                          {b.active ? "Take off sale" : "Reactivate"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="pager">
                <button
                  className="sm"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - LIMIT))}
                >
                  ← Prev
                </button>
                <span className="muted">
                  {from}–{to} of {total}
                </span>
                <button
                  className="sm"
                  disabled={offset + LIMIT >= total}
                  onClick={() => setOffset(offset + LIMIT)}
                >
                  Next →
                </button>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
