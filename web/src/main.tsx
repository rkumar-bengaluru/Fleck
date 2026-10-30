import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, Unauthorized, type Me } from "./api";
import Nav from "./components/Nav";
import Settings from "./routes/Settings";
import SignIn from "./routes/SignIn";
import Today, { EventPage } from "./routes/Today";
import "./theme.css";

function Shell() {
  const [me, setMe] = useState<Me | null | "signed-out">(null);
  useEffect(() => {
    api<Me>("/api/me").then(setMe).catch(e => { if (e instanceof Unauthorized) setMe("signed-out"); });
  }, []);
  if (me === "signed-out") return <Navigate to="/signin" replace />;
  if (me === null) return null;
  return (
    <div className="shell">
      <Nav />
      <main className="main">
        <Routes>
          <Route path="/" element={<Today me={me} />} />
          <Route path="/events/:id/*" element={<EventPage me={me} />} />
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
        <Route path="/signin" element={<SignIn />} />
        <Route path="/*" element={<Shell />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
