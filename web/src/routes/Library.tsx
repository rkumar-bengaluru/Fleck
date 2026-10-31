import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, setTitle, type ClientRef } from "../api";

interface Doc { id: number; title: string; filename: string; kind: "client" | "firm" | "reference"; client: string | null; status: string; error: string | null;
  used_count: number; progress_done: number; progress_total: number }

const KIND = { client: "Our past work", firm: "Firm", reference: "Reference" } as const;

function status(d: Doc) {
  if (d.status === "indexed") return "Indexed";
  if (d.status === "failed") return `Failed: ${d.error ?? "unknown reason"}`;
  return d.status === "indexing" && d.progress_total ? `Indexing, ${d.progress_done} of ${d.progress_total} passages` : d.status === "indexing" ? "Indexing" : "Waiting to index";
}

function AddDialog({ onClose, onDone }: { onClose: () => void; onDone: (close: boolean) => void }) {
  const [kind, setKind] = useState<Doc["kind"]>("firm");
  const [clients, setClients] = useState<ClientRef[]>([]);
  const [clientId, setClientId] = useState("");  // "" none, "new" the typed name
  const [newName, setNewName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<ClientRef[]>("/api/clients").then(setClients).catch(() => {}); }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!files.length || (kind === "client" && !clientId) || (clientId === "new" && !newName.trim())) { setError("Choose the files, and a client for past work."); return; }
    setBusy(true);
    setError("");
    try {
      let id = clientId;
      if (id === "new") id = String((await api<ClientRef>("/api/clients", { method: "POST", body: { name: newName.trim() } })).id);
      for (const f of files) {
        const form = new FormData();
        form.append("file", f), form.append("kind", kind);
        if (id) form.append("client_id", id);
        const r = await fetch("/api/library/documents", { method: "POST", body: form, credentials: "same-origin" });
        if (!r.ok) throw new Error(r.status === 415 ? "Upload a PDF, DOCX, PPTX, XLSX, Markdown or text file." : `${f.name} was not accepted (${r.status}).`);
      }
      onDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't upload. Try again.");
      onDone(false);  // earlier files in the batch were accepted: show them, keep the message
    } finally {
      setBusy(false);
    }
  }
  return (
    <div role="dialog" aria-modal="true" aria-label="Add documents" className="after-card dialog">
      <h2>Add documents</h2>
      <form className="dialog-form" onSubmit={submit}>
        <label htmlFor="files">Files</label>
        <input id="files" type="file" multiple accept=".pdf,.docx,.pptx,.xlsx,.md,.txt" onChange={e => setFiles(Array.from(e.target.files ?? []))} />
        <label htmlFor="doc-kind">Kind of document</label>
        <select id="doc-kind" value={kind} onChange={e => setKind(e.target.value as Doc["kind"])}>
          {Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <label htmlFor="doc-client">Client</label>
        <select id="doc-client" value={clientId} onChange={e => setClientId(e.target.value)}>
          <option value="">{kind === "client" ? "Choose a client" : "No client"}</option>
          {clients.map(c => <option key={c.id} value={String(c.id)}>{c.name}</option>)}
          <option value="new">New client…</option>
        </select>
        {clientId === "new" && <><label htmlFor="new-client">New client name</label><input id="new-client" value={newName} onChange={e => setNewName(e.target.value)} /></>}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>Close</button>
          <button type="submit" className="btn-primary" disabled={busy}>Upload</button>
        </div>
      </form>
    </div>
  );
}

export default function Library() {
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState(false);
  const [params] = useSearchParams();
  const focus = params.get("doc");
  const load = useCallback(() => {
    const qs = new URLSearchParams();
    if (kind) qs.set("kind", kind);
    if (q) qs.set("q", q);
    return api<Doc[]>(`/api/library/documents${qs.size ? `?${qs}` : ""}`).then(d => { setDocs(d); setError(false); }).catch(() => setError(true));
  }, [kind, q]);
  useEffect(() => setTitle("Library"), []);
  useEffect(() => { void load(); }, [load]);
  const busy = docs?.some(d => d.status === "queued" || d.status === "indexing");
  useEffect(() => { if (!busy) return; const t = setInterval(() => void load(), 2000); return () => clearInterval(t); }, [busy, load]);
  useEffect(() => { document.getElementById(`doc-${focus}`)?.scrollIntoView?.({ block: "center" }); }, [focus, docs === null]);

  return (
    <>
      <header className="page-head">
        <div><h1>Library</h1><p className="subtitle">What Fleck answers from. Shared with everyone on your team.</p></div>
        <button type="button" className="btn-primary" onClick={() => setAdding(true)}>Add documents</button>
      </header>
      {adding && <AddDialog onClose={() => setAdding(false)} onDone={close => { if (close) setAdding(false); void load(); }} />}
      <div className="lib-filters">
        <input type="search" aria-label="Search the library" placeholder="Search the library" value={q} onChange={e => setQ(e.target.value)} />
        <label htmlFor="lib-kind">Kind</label>
        <select id="lib-kind" value={kind} onChange={e => setKind(e.target.value)}>
          <option value="">All kinds</option>
          {Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </div>
      {error && <p className="error" role="alert">Couldn't load the library.</p>}
      <div className="lib-table">
        <table>
          <thead><tr><th scope="col">Document</th><th scope="col">Client</th><th scope="col">Kind</th><th scope="col">Used in answers</th><th scope="col">Status</th></tr></thead>
          <tbody>
            {docs?.map(d => (
              <tr key={d.id} id={`doc-${d.id}`} aria-current={String(d.id) === focus ? "true" : undefined}>
                <td><a href={`/api/library/documents/${d.id}/file`}>{d.title}</a><div className="muted small">{d.filename}</div></td>
                <td className="muted">{d.client ?? "None"}</td>
                <td><span className={`kind kind-${d.kind}`}>{KIND[d.kind]}</span></td>
                <td className={d.used_count ? "" : "muted"}>{d.used_count || "None yet"}</td>
                <td className={`status-${d.status}`}>{status(d)}</td>
              </tr>
            ))}
            {docs?.length === 0 && <tr><td colSpan={5} className="muted">No documents match.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
