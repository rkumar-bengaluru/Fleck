import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { safeNext, setTitle } from "../api";

const ERRORS: Record<string, string> = {
  not_allowed: "That Google account isn't allowed to use Fleck.",
  denied: "Sign-in was cancelled.",
  failed: "Sign-in didn't complete. Try again.",
};

export default function SignIn({ nextKey }: { nextKey: string }) {
  const [params] = useSearchParams();
  const error = params.get("error");
  useEffect(() => setTitle("Sign in"), []);
  function signIn() {
    const next = safeNext(params.get("next"));
    try { if (next) sessionStorage.setItem(nextKey, next); } catch { /* storage blocked: land on Today */ }
    window.location.href = "/api/auth/login";
  }
  return (
    <div className="shell signin">
      <main className="signin-card">
        <h1 className="brand" style={{ padding: 0 }}><span className="brand-dot" />Fleck</h1>
        {error && <p className="error" role="alert">{ERRORS[error] ?? ERRORS.failed}</p>}
        <button type="button" className="btn-primary" onClick={signIn}>
          Sign in with Google
        </button>
      </main>
    </div>
  );
}
