import { useEffect } from "react";
import { setTitle } from "../api";
import ChatPanel from "../components/ChatPanel";

export default function AskAll() {
  useEffect(() => setTitle("Ask across meetings"), []);
  return (
    <>
      <header className="page-head"><div><h1>Ask across meetings</h1><p className="subtitle">Answers come from every recorded meeting and the library, with sources you can open.</p></div></header>
      <ChatPanel meetingId={null} />
    </>
  );
}
