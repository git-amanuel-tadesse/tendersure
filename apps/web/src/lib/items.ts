/** A technical item and its own marks (spec 14 B, 16 E): stored as "text [n]", edited as two fields. */
export type MarkedItem = { text: string; marks: string };

const MARKS = /\s*\[\s*(\d+(?:\.\d+)?)\s*(?:marks?)?\s*\]\s*$/i;

export function readItem(stored: string): MarkedItem {
  const m = stored.match(MARKS);
  return m ? { text: stored.slice(0, m.index).trim(), marks: m[1] } : { text: stored.trim(), marks: "" };
}

export function writeItem({ text, marks }: MarkedItem): string {
  const t = text.trim();
  return t && /^\d+(\.\d+)?$/.test(marks.trim()) ? `${t} [${marks.trim()}]` : t;
}
