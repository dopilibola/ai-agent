import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import TenantTabs from "../components/TenantTabs";
import type { AnfaCatalogItem, CatalogImportSummary, Tenant } from "../types";
import { fmt } from "../util";

export default function Services() {
  const { tenant = "" } = useParams();
  const [rows, setRows] = useState<AnfaCatalogItem[] | null>(null);
  const [info, setInfo] = useState<Tenant | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [summary, setSummary] = useState<CatalogImportSummary | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    setRows(null);
    setError(null);
    api
      .services(tenant)
      .then(setRows)
      .catch((e) => setError(e.message ?? String(e)));
  }, [tenant]);

  useEffect(load, [load]);
  useEffect(() => {
    api
      .tenants()
      .then((ts) => setInfo(ts.find((t) => t.id === tenant) ?? null))
      .catch(() => setInfo(null));
  }, [tenant]);

  const toggle = async (s: AnfaCatalogItem) => {
    setBusy(s.id);
    try {
      await api.setServiceActive(tenant, s.id, !s.active);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const onUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setUploading(true);
      setError(null);
      setSummary(null);
      try {
        setSummary(await api.importCatalog(tenant, file));
        load();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setUploading(false);
        if (fileRef.current) fileRef.current.value = "";
      }
    }
  };

  return (
    <div>
      <TenantTabs tenant={tenant} />

      {info?.has_catalog_import && (
        <div className="toolbar">
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xlsm"
            style={{ display: "none" }}
            onChange={onUpload}
          />
          <button
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
          >
            {uploading ? "Importing…" : "Import Excel export"}
          </button>
          {summary && (
            <span className="muted">
              Imported: parsed {summary.parsed} · added {summary.added} · updated{" "}
              {summary.updated} · removed {summary.removed} · total{" "}
              {summary.total}
            </span>
          )}
        </div>
      )}

      {error && <div className="error">{error}</div>}
      {!rows && !error && <div className="muted">Loading…</div>}
      {rows && (
        <>
          <h2>
            {tenant} · catalog <span className="muted">({rows.length})</span>
          </h2>
          <table className="grid">
            <thead>
              <tr>
                <th>Group</th>
                <th>Category</th>
                <th>Service</th>
                <th>Price</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id}>
                  <td className="muted">{s.tab}</td>
                  <td className="muted">{s.category || "—"}</td>
                  <td>{s.title}</td>
                  <td>{s.price ? `${fmt(s.price)} ${s.currency}` : "—"}</td>
                  <td>
                    <span className={`badge ${s.active ? "on" : "off"}`}>
                      {s.active ? "ACTIVE" : "OFF"}
                    </span>
                  </td>
                  <td className="actions">
                    <button
                      className="sm"
                      disabled={busy === s.id}
                      onClick={() => toggle(s)}
                    >
                      {s.active ? "Deactivate" : "Reactivate"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
