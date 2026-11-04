import type { Tone } from "../format";

export function Chip({ label, text, tone }: { label?: string; text: string; tone: Tone }) {
  return (
    <span className={`chip chip-${tone}`}>
      {label && <span className="chip-label">{label}</span>}
      <span className="chip-text">{text}</span>
    </span>
  );
}
