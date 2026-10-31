import type { Citation } from "../events";

export type Kind = "library" | "reference" | "general" | "not_in_material" | null;

const LABEL: Record<NonNullable<Kind>, string> = {
  library: "Our material", general: "General knowledge", not_in_material: "Not in our material", reference: "Reference contract",
};

/** Where an answer came from: the label, then the cited titles or the caveat. Shared by live, after-meeting and brief pages. */
export default function SourceChip({ kind, citations }: { kind: Kind; citations: Citation[] }) {
  const titles = citations.map(c => c.title).join(", ");
  return (
    <span className="chips">
      <span className={`chip chip-${kind ?? "thinking"}`}>{kind ? LABEL[kind] : "Thinking…"}</span>
      {kind === "library" && titles && <span className="chip-title">{titles}</span>}
      {kind === "general" && <span className="chip-note">Not from our material</span>}
      {kind === "reference" && <span className="chip-note">Not our work: {titles}</span>}
    </span>
  );
}
