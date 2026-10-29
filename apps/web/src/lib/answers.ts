/**
 * The Decisions grammar (spec 02), built from the browser's controls and read
 * back from a saved answer. The API applies answers with the same parser as a
 * QA workbook typed in Excel (review.apply_answers), so these functions must
 * write exactly what that parser reads:
 *
 *   Q  remove all | remove: 3, 7 | keep: 1, 2 | reword: 9 | reword 9: own wording   (joined with "; ")
 *      the numbers are CATEGORY numbers: the question is removed from those sheets
 *   A  add all | add: 1, 3 | skip        the numbers are the suggestions listed on the line
 *   M  company 10, technical 20, ...      sections and their marks; the sheet must total 100
 *   C  a value, or "majority"
 *   T  yes | no   (T-ADOPT-MAJORITY);   keep | set flags   (T-UPLOAD-FLAG)
 *
 * An empty answer accepts the proposal.
 */

/** 'ERCS-ET-2026-019' -> '19', as review.number_of does. */
export function numberOf(code: string): string {
  const m = /(\d+)$/.exec(code);
  return m ? String(parseInt(m[1], 10)) : code;
}

// --- Q: a question copied into several categories -----------------------------------

export interface QChoice {
  /** codes the question is removed from */
  remove: string[];
  /** code -> the model's proposed wording (true) or one's own */
  reword: Record<string, true | string>;
}

const list = (codes: string[]) => codes.map(numberOf).join(", ");

export function qAnswer(codes: string[], choice: QChoice): string {
  const clauses: string[] = [];
  const removed = codes.filter((c) => choice.remove.includes(c));
  if (removed.length === codes.length && codes.length > 0) clauses.push("remove all");
  else if (removed.length) clauses.push(`remove: ${list(removed)}`);
  const kept = codes.filter((c) => !choice.remove.includes(c));
  const proposed = kept.filter((c) => choice.reword[c] === true);
  if (proposed.length) clauses.push(`reword: ${list(proposed)}`);
  for (const c of kept) {
    const own = choice.reword[c];
    if (typeof own === "string" && own.trim()) clauses.push(`reword ${numberOf(c)}: ${own.trim().replace(/\s+/g, " ")}`);
  }
  return clauses.length ? clauses.join("; ") : "keep in all";
}

/** A saved answer back into choices; null when it is not in the grammar (shown as text instead). */
export function parseQAnswer(answer: string, codes: string[], proposedWording: Record<string, string>): QChoice | null {
  const byNum = new Map(codes.map((c) => [numberOf(c), c]));
  const nums = (text: string) => {
    const got = text.split(",").map((n) => n.trim()).filter(Boolean);
    return got.every((n) => byNum.has(n)) ? got.map((n) => byNum.get(n)!) : null;
  };
  const choice: QChoice = { remove: [], reword: {} };
  for (const clause of answer.split(";").map((c) => c.trim()).filter(Boolean)) {
    const low = clause.toLowerCase();
    const own = /^reword\s+(\d+)\s*:\s*(.+)$/i.exec(clause);
    if (own) {
      const c = byNum.get(own[1]);
      if (!c) return null;
      choice.reword[c] = own[2].replace(/\s+/g, " ");
    } else if (low === "reword all" || low === "reword") {
      for (const c of Object.keys(proposedWording)) choice.reword[c] = true;
    } else if (low.startsWith("reword")) {
      const got = nums(low.split(":").slice(1).join(":").replace("reword", ""));
      if (!got) return null;
      got.forEach((c) => (choice.reword[c] = true));
    } else if (low === "remove all" || low === "remove") {
      choice.remove = [...codes];
    } else if (low.startsWith("remove")) {
      const got = nums(low.split(":").slice(1).join(":").replace("remove", ""));
      if (!got) return null;
      choice.remove.push(...got);
    } else if (low.startsWith("keep:") || (low.startsWith("keep ") && /\d/.test(low))) {
      const got = nums(low.split(":").slice(1).join(":").replace("keep", ""));
      if (!got) return null;
      choice.remove.push(...codes.filter((c) => !got.includes(c)));
    } else if (low !== "keep" && low !== "keep in all") {
      return null;
    }
  }
  return choice;
}

// --- A: questions proposed for addition ------------------------------------------------

export function aAnswer(count: number, picked: number[]): string {
  const p = [...new Set(picked)].filter((n) => n >= 1 && n <= count).sort((a, b) => a - b);
  if (!p.length) return "skip";
  if (p.length === count) return "add all";
  return `add: ${p.join(", ")}`;
}

/** Which suggestions an answer adds (blank: the proposal); null when it is not in the grammar. */
export function parseAAnswer(answer: string, count: number, proposed: string): number[] | null {
  const a = (answer.trim() || proposed).toLowerCase();
  const all = Array.from({ length: count }, (_, i) => i + 1);
  if (a === "add all") return all;
  if (["skip", "none", "no"].includes(a)) return [];
  const m = /^add\s*:?\s*([\d,\s]+)$/.exec(a);
  return m ? m[1].split(",").map((n) => parseInt(n, 10)).filter((n) => n >= 1 && n <= count) : null;
}

// --- M: the marks of each section --------------------------------------------------------

export type Marks = [section: string, marks: number][];

/** 'company 10, technical 20' -> [['company', 10], ['technical', 20]] (review._PAIR). */
export function parseMarks(text: string): Marks {
  return [...text.matchAll(/([a-z_]+)\s*[:=]?\s*(\d+(?:\.\d+)?)/gi)].map((m) => [m[1].toLowerCase(), Number(m[2])]);
}

export const total = (marks: Marks) => marks.reduce((s, [, v]) => s + v, 0);

/** The whole split, every section named: a sheet whose questions changed was already rescaled, so
 * naming only the changed sections could leave a total that is not 100. */
export function marksAnswer(marks: Marks): string {
  return marks.map(([k, v]) => `${k} ${+v.toFixed(2)}`).join(", ");
}

// --- C: a client's sheets disagree -------------------------------------------------------

/** "... for 'Years in business' (2: 5, 3: 1)" -> ['2', '3'], most common first. */
export function rulingOptions(decide: string): string[] {
  const inner = /\(([^()]*)\)\s*$/.exec(decide)?.[1] ?? "";
  return [...inner.matchAll(/(?:^|,\s*)(.+?):\s*\d+(?=,|$)/g)].map((m) => m[1].trim());
}

// --- which control fits an item -----------------------------------------------------------

export type Kind = "question" | "add" | "marks" | "ruling" | "team" | "check";

export function kindOf(item: { id: string; type: string }): Kind {
  const k = item.type as Kind;
  return ["question", "add", "marks", "ruling", "team", "check"].includes(k) ? k : "check";
}

/** T items and the answers they take. */
export function teamOptions(id: string): string[] {
  if (id === "T-ADOPT-MAJORITY") return ["no", "yes"];
  if (id === "T-UPLOAD-FLAG") return ["keep", "set flags"];
  return [];
}
