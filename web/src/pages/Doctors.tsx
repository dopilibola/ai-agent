import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import TenantTabs from "../components/TenantTabs";
import type { AnfaDoctor, CatalogImportSummary } from "../types";

export default function Doctors() {
  const { tenant = "" } = useParams();
  const [rows, setRows] = useState<AnfaDoctor[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [summary, setSummary] = useState<CatalogImportSummary | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    setRows(null);
    setError(null);
    api
      .doctors(tenant)
      .then(setRows)
      .catch((e) => setError(e.message ?? String(e)));
  }, [tenant]);

  useEffect(load, [load]);

  const toggle = async (d: AnfaDoctor) => {
    setBusy(d.id);
    try {
      await api.setDoctorActive(tenant, d.id, !d.active);
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
        setSummary(await api.importDoctors(tenant, file));
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

      <div className="toolbar">
        <input
          ref={fileRef}
          type="file"
          accept=".docx"
          style={{ display: "none" }}
          onChange={onUpload}
        />
        <button disabled={uploading} onClick={() => fileRef.current?.click()}>
          {uploading ? "Importing…" : "Import Word roster"}
        </button>
        {summary && (
          <span className="muted">
            Imported: parsed {summary.parsed} · added {summary.added} · updated{" "}
            {summary.updated} · removed {summary.removed} · total {summary.total}
          </span>
        )}
      </div>

      {error && <div className="error">{error}</div>}
      {!rows && !error && <div className="muted">Loading…</div>}
      {rows && (
        <>
          <h2>
            {tenant} · doctors <span className="muted">({rows.length})</span>
          </h2>
          <table className="grid">
            <thead>
              <tr>
                <th>Doctor</th>
                <th>Speciality</th>
                <th>Experience</th>
                <th>Walk-in hours</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.id}>
                  <td>{d.fullname}</td>
                  <td className="muted">{d.speciality}</td>
                  <td className="muted">{d.experience || "—"}</td>
                  <td className="muted">{d.hours_label || "—"}</td>
                  <td>
                    <span className={`badge ${d.active ? "on" : "off"}`}>
                      {d.active ? "ACTIVE" : "OFF"}
                    </span>
                  </td>
                  <td className="actions">
                    <button
                      className="sm"
                      disabled={busy === d.id}
                      onClick={() => toggle(d)}
                    >
                      {d.active ? "Deactivate" : "Reactivate"}
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
