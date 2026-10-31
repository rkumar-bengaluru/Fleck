import { useEffect, useState } from "react";
import { api, setTitle, type Me } from "../api";

export default function Settings({ me }: { me: Me }) {
  useEffect(() => setTitle("Settings"), []);
  const [code, setCode] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  async function signOut() {
    await api("/api/auth/logout", { method: "POST" });
    window.location.href = "/signin";
  }
  const getCode = () => { setFailed(false); api<{ code: string }>("/api/devices/pairing-code", { method: "POST" }).then(r => setCode(r.code)).catch(() => setFailed(true)); };
  return (
    <>
      <h1>Settings</h1>
      <p className="subtitle">Signed in as {me.email}.</p>
      <div><button type="button" className="btn" onClick={signOut}>Sign out</button></div>
      <h2>Desktop app</h2>
      <p className="subtitle">Pair the desktop app with your account. The code works once, for 10 minutes.</p>
      <div><button type="button" className="btn" onClick={getCode}>Get a pairing code</button></div>
      {code && <p>In the Fleck folder, run: <code>uv run --package fleck-helper fleck-helper pair {code}</code></p>}
      {failed && <p className="error" role="alert">Couldn't get a code. Try again.</p>}
    </>
  );
}
