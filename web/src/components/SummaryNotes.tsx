export interface Section { heading: string; items: { user_text: string; ai_text: string }[] }

/** The summary: the user's own words in ink, Claude's additions in grey. */
export default function SummaryNotes({ sections }: { sections: Section[] | null }) {
  return (
    <section className="after-card" aria-labelledby="summary">
      <div className="after-card-head">
        <h2 id="summary">Notes</h2>
        <span className="key"><span className="user-text">Your words</span><span className="ai-text">Added by Claude</span></span>
      </div>
      {!sections?.length && <p className="muted">No notes for this meeting.</p>}
      {sections?.map(s => (
        <div key={s.heading} className="notes-section">
          <h3>{s.heading}</h3>
          <ul>{s.items.map((it, i) => (
            <li key={i}>{it.user_text && <span className="user-text">{it.user_text}</span>}{it.user_text && it.ai_text && " "}{it.ai_text && <span className="ai-text">{it.ai_text}</span>}</li>
          ))}</ul>
        </div>
      ))}
    </section>
  );
}
