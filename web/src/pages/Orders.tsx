import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import TenantTabs from "../components/TenantTabs";
import type { Order } from "../types";
import { ago, fmt } from "../util";

export default function Orders() {
  const { tenant = "" } = useParams();
  const [rows, setRows] = useState<Order[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(() => {
    setRows(null);
    setError(null);
    api
      .orders(tenant)
      .then(setRows)
      .catch((e) => setError(e.message ?? String(e)));
  }, [tenant]);

  useEffect(load, [load]);

  const setStatus = async (o: Order, status: string) => {
    setBusy(o.id);
    try {
      await api.setOrderStatus(tenant, o.id, status);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
        <>
          <h2>
            {tenant} · orders <span className="muted">({rows.length})</span>
          </h2>
          {rows.length === 0 && <p className="muted">No orders yet.</p>}
          {rows.length > 0 && (
            <table className="grid">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Bouquet</th>
                  <th>Total</th>
                  <th>Recipient</th>
                  <th>Delivery</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((o) => (
                  <tr key={o.id}>
                    <td className="mono">{o.id}</td>
                    <td>
                      {o.bouquet_name}
                      {o.is_surprise && (
                        <span className="muted"> · surprise</span>
                      )}
                    </td>
                    <td>{fmt(o.total_sum)} сум</td>
                    <td>
                      {o.recipient_name || "—"}
                      <br />
                      <span className="muted mono">{o.recipient_phone}</span>
                    </td>
                    <td>
                      {o.delivery_time || "—"}
                      <br />
                      <span className="muted">{o.address}</span>
                    </td>
                    <td>
                      <span
                        className={`badge ${o.status === "paid" ? "on" : "off"}`}
                      >
                        {o.status.toUpperCase()}
                      </span>
                    </td>
                    <td className="muted">{ago(o.created_at)}</td>
                    <td className="actions">
                      {o.status !== "paid" ? (
                        <button
                          className="sm"
                          disabled={busy === o.id}
                          onClick={() => setStatus(o, "paid")}
                        >
                          Mark paid
                        </button>
                      ) : (
                        <button
                          className="sm"
                          disabled={busy === o.id}
                          onClick={() => setStatus(o, "pending")}
                        >
                          Revert to pending
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}
