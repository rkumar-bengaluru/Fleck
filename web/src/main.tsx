import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes, useNavigate } from "react-router-dom";
import { api, safeNext, Unauthorized, type Me } from "./api";
import Nav from "./components/Nav";
import Brief from "./routes/Brief";
import MeetingLive from "./routes/MeetingLive";
import Meetings from "./routes/Meetings";
import Settings from "./routes/Settings";
import SignIn from "./routes/SignIn";
import Today, { EventPage } from "./routes/Today";
import "./theme.css";

const NEXT_KEY = "fleck-next";

function Shell() {
  const [me, setMe] = useState<Me | null | "error">(null);
  const navigate = useNavigate();
  const load = () => {
    setMe(null);
    api<Me>("/api/me").then(m => {
      setMe(m);
      let next: string | null = null;
      try { next = safeNext(sessionStorage.getItem(NEXT_KEY)); sessionStorage.removeItem(NEXT_KEY); } catch { /* storage blocked */ }
      if (next) navigate(next, { replace: true });
    }).catch(e => { if (!(e instanceof Unauthorized)) setMe("error"); });  // 401: api() already went to sign-in
  };
  useEffect(load, []);
  if (me === "error") {
    return <div className="shell signin"><p className="error" role="alert">Couldn't reach Fleck.{" "}
      <button type="button" className="btn" onClick={load}>Retry</button></p></div>;
  }
  if (me === null) return null;
  return (
    <div className="shell">
      <Nav />
      <main className="main">
        <Routes>
          <Route path="/" element={<Today me={me} />} />
          <Route path="/events/:id/brief" element={<Brief />} />
          <Route path="/events/:id/*" element={<EventPage me={me} />} />
          <Route path="/meetings" element={<Meetings />} />
          <Route path="/meetings/:id" element={<MeetingLive />} />
          <Route path="/settings" element={<Settings me={me} />} />
          <Route path="*" element={null} />
        </Routes>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/signin" element={<SignIn nextKey={NEXT_KEY} />} />
        <Route path="/*" element={<Shell />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
