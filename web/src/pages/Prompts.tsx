import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import TenantTabs from "../components/TenantTabs";
import type { PromptInfo } from "../types";

export default function Prompts() {
  const { tenant = "" } = useParams();
  const [list, setList] = useState<PromptInfo[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [info, setInfo] = useState<PromptInfo | null>(null);
  const [content, setContent] = useState("");
  const [original, setOriginal] = useState("");
  const [loadingBody, setLoadingBody] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load the list of prompts for this tenant, default-select the first.
  useEffect(() => {
    setList(null);
    setSelected(null);
    setError(null);
    api
      .prompts(tenant)
      .then((ps) => {
        setList(ps);
        setSelected(ps[0]?.key ?? null);
      })
      .catch((e) => setError(e.message ?? String(e)));
  }, [tenant]);

  // Load the selected prompt's content.
  const loadBody = useCallback(() => {
    if (!selected) return;
    setLoadingBody(true);
    setError(null);
    setSaved(false);
    api
      .prompt(tenant, selected)
      .then((p) => {
        setInfo(p);
        setContent(p.content);
        setOriginal(p.content);
      })
      .catch((e) => setError(e.message ?? String(e)))
      .finally(() => setLoadingBody(false));
  }, [tenant, selected]);

  useEffect(loadBody, [loadBody]);

  const dirty = content !== original;

  const save = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await api.savePrompt(tenant, selected, content);
      setOriginal(content);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <TenantTabs tenant={tenant} />
      {error && <div className="error">{error}</div>}
      {!list && !error && <div className="muted">Loading…</div>}
      {list && (
        <>
          <h2>
            {tenant} · prompts{" "}
            <span className="muted">({list.length})</span>
          </h2>
          {list.length === 0 && (
            <p className="muted">This tenant has no editable prompts.</p>
          )}
          {list.length > 0 && (
            <div className="prompts-layout">
              <div className="prompts-list">
                {list.map((p) => (
                  <button
                    key={p.key}
                    className={p.key === selected ? "active" : ""}
                    onClick={() => setSelected(p.key)}
                  >
                    <span className="prompt-label">{p.label}</span>
                    <span className="muted mono prompt-file">{p.filename}</span>
                  </button>
                ))}
              </div>
              <div className="prompt-editor">
                {info && <p className="muted prompt-note">{info.note}</p>}
                <textarea
                  className="prompt"
                  value={content}
                  spellCheck={false}
                  disabled={loadingBody || busy}
                  onChange={(e) => {
                    setContent(e.target.value);
                    setSaved(false);
                  }}
                />
                <div className="actions prompt-actions">
                  <button
                    type="submit"
                    disabled={!dirty || busy || loadingBody}
                    onClick={save}
                  >
                    {busy ? "Saving…" : "Save"}
                  </button>
                  <button
                    className="sm"
                    disabled={!dirty || busy || loadingBody}
                    onClick={() => {
                      setContent(original);
                      setSaved(false);
                    }}
                  >
                    Discard changes
                  </button>
                  {dirty && <span className="muted">Unsaved changes</span>}
                  {!dirty && saved && (
                    <span className="badge on">SAVED</span>
                  )}
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
