import { useSearchParams } from "react-router-dom";

const ERRORS: Record<string, string> = {
  not_allowed: "That Google account isn't allowed to use Fleck.",
  denied: "Sign-in was cancelled.",
  failed: "Sign-in didn't complete. Try again.",
};

export default function SignIn() {
  const error = useSearchParams()[0].get("error");
  return (
    <div className="shell signin">
      <main className="signin-card">
        <div className="brand" style={{ padding: 0 }}><span className="brand-dot" />Fleck</div>
        {error && <p className="error" role="alert">{ERRORS[error] ?? ERRORS.failed}</p>}
        <button type="button" className="btn-primary" onClick={() => { window.location.href = "/api/auth/login"; }}>
          Sign in with Google
        </button>
      </main>
    </div>
  );
}
