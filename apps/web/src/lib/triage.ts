/**
 * Which Decisions need a person (spec 10). Pure functions over the run's items,
 * so the tabs, the sheet view and the bulk answers agree, and Vitest can hold
 * them to the statuses the API writes (review.apply_answers, reviewer.ai_review).
 */
import { numberOf, parseMarks, type Marks } from "./answers";

export interface Decision {
  id: string;
  type: string;
  codes: string[];
  decide: string;
  proposed: string;
  why?: string;
  status?: string;
  keys?: string[];
}

export type Tab = "needs" | "disagreed" | "bench" | "changed" | "all";
export const TABS: Tab[] = ["needs", "disagreed", "bench", "changed", "all"];
export const TAB_LABEL: Record<Tab, string> = {
  needs: "Needs you", disagreed: "AI disagreed", bench: "Marks vs client", changed: "Changed by you", all: "All",
};

export const isBench = (d: Pick<Decision, "id">) => d.id.startsWith("M-BENCH-");

/** A proposal written in the answer grammar, so it can be given as the answer itself. */
const writable = (d: Pick<Decision, "id" | "proposed">) =>
  /^(keep|remove|reword|add|skip|yes|no)\b/i.test(d.proposed.trim())
  || /^[a-z_]+\s+\d+(\.\d+)?(\s*,\s*[a-z_]+\s+\d+(\.\d+)?)*$/i.test(d.proposed.trim());   // a whole split, nothing else

/** What "as proposed" is written as. A blank accepts a proposal - but a blank on an open card
 * (no answer, no status: nothing decided yet, as after a build without AI) or on a freed-marks
 * line (made again on every apply) changes nothing and leaves it open; there the proposal itself
 * is written, so the card is answered, Apply has something to apply and the run can finish. */
export const asProposed = (d: Pick<Decision, "id" | "proposed"> & { status?: string }) =>
  (d.id.startsWith("M-FREED-") || !d.status) && writable(d) ? d.proposed : "";

/** The reviewer's notes in an AI status: "AI: applied: removed from 1 | 22: removal not applied, reviewer: …". */
export function aiNotes(status = ""): string {
  const i = status.indexOf(" | ");
  return status.startsWith("AI:") && i >= 0 ? status.slice(i + 3) : "";
}

/** Not applied, or a problem: a person has to look. */
export const failed = (status = "") => /^(AI: )?(NOT APPLIED|PROBLEM)/.test(status) || /\bNOT APPLIED\b/.test(status);

/** No answer and nothing done with it yet. A check item counts: it is fixed in the sheet, not
 * answered, but the run list counts it as open, so the review shows it where people look. */
export const open = (d: Decision, answer: string) => !answer && !d.status;

export function inTab(tab: Tab, d: Decision, answer: string, changed: boolean): boolean {
  switch (tab) {
    case "needs": return !isBench(d) && (open(d, answer) || failed(d.status));
    case "disagreed": return !isBench(d) && !!aiNotes(d.status);
    case "bench": return isBench(d);
    case "changed": return changed;
    default: return true;
  }
}

/** A marks-vs-client line already answered in an earlier run (kept ours, or the client's split applied). */
export const benchDecided = (d: Decision) => isBench(d) && !!d.status && !failed(d.status);

/** What an answered marks-vs-client line chose, in words. */
export const benchChoice = (answer: string) =>
  !answer.trim() ? "kept ours" : answer.trim().toLowerCase() === "client" ? "client's split" : "own split";

/** Nothing to do: shown as one line until opened. */
export const quiet = (d: Decision, answer: string) =>
  isBench(d) ? benchDecided(d) : !open(d, answer) && !failed(d.status) && !aiNotes(d.status);

/** The status in plain words, for a badge. */
export function plainStatus(status = ""): string {
  if (!status) return "";
  if (failed(status)) return status.replace(/^AI: /, "");
  if (aiNotes(status)) return "AI disagreed";
  if (status.startsWith("AI:")) return "AI agreed";
  return status;
}

/** An M-BENCH line: our split (its proposal) and the client's (its key), section by section. */
export interface BenchRow { section: string; ours: number; client: number }
export function benchRows(d: Decision): BenchRow[] {
  const ours = new Map<string, number>(parseMarks(d.proposed));
  const client = new Map<string, number>(parseMarks(d.keys?.[0] ?? ""));
  return [...new Set([...ours.keys(), ...client.keys()])]
    .map((section) => ({ section, ours: ours.get(section) ?? 0, client: client.get(section) ?? 0 }))
    .filter((r) => r.ours || r.client);
}
export const oursOf = (d: Decision): Marks => parseMarks(d.proposed);

/** The sheets any decision touches, by category number, in order. */
export function sheetsOf(items: Pick<Decision, "codes">[]): string[] {
  return [...new Set(items.flatMap((d) => d.codes))].sort((a, b) => Number(numberOf(a)) - Number(numberOf(b)));
}

export function matches(d: Decision, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || `${d.id} ${d.decide} ${d.proposed} ${d.why ?? ""} ${d.status ?? ""}`.toLowerCase().includes(q);
}
