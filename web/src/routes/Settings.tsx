import { api, type Me } from "../api";

export default function Settings({ me }: { me: Me }) {
  async function signOut() {
    await api("/api/auth/logout", { method: "POST" });
    window.location.href = "/signin";
  }
  return (
    <>
      <h1>Settings</h1>
      <p className="subtitle">Signed in as {me.email}.</p>
      <div><button type="button" className="btn" onClick={signOut}>Sign out</button></div>
    </>
  );
}
