import type { Citation } from "../events";

export type Kind = "library" | "reference" | "general" | "not_in_material" | null;

const LABEL: Record<NonNullable<Kind>, string> = {
  library: "Our material", general: "General knowledge", not_in_material: "Not in our material", reference: "Reference contract",
};

/** Where an answer came from: the label, then the cited titles or the caveat. Shared by live, after-meeting and brief pages.
 * `late`: the helper's late recheck found the question, so the answer may land after a newer one (decision 128). */
export default function SourceChip({ kind, citations, late = false }: { kind: Kind; citations: Citation[]; late?: boolean }) {
  const titles = citations.map(c => c.title).join(", ");
  return (
    <span className="chips">
      <span className={`chip chip-${kind ?? "thinking"}`}>{kind ? LABEL[kind] : "Thinking…"}</span>
      {late && <span className="chip chip-late" title="Answer to an earlier question">Late</span>}
      {kind === "library" && titles && <span className="chip-title">{titles}</span>}
      {kind === "general" && <span className="chip-note">Not from our material</span>}
      {kind === "reference" && <span className="chip-note">Not our work: {titles}</span>}
    </span>
  );
}
