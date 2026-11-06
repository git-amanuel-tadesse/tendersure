/** A comment's result (spec 21 B, C): each instruction it held, its status and note, and under it the changes
 * made for it: what each change does, the source line it copied, and a flag when there is a reason to look.
 * Flagged changes start unticked. */
import type { Schemas } from "@tendersure/api-client";
import { Badge, cx } from "../ui";
import type { ListChange, ListComment } from "../../lib/api";

type Row = Schemas["ListRow"];

const STATUS: Record<string, { label: string; tone: "good" | "neutral" | "warn" | "bad" }> = {
  done: { label: "done", tone: "good" },
  nothing_to_change: { label: "nothing to change", tone: "neutral" },
  partly_done: { label: "partly done", tone: "warn" },
  unclear: { label: "not understood", tone: "bad" },
};

/** One change in words: what it does to which category. */
export function describe(c: ListChange, rows: Row[]): { what: string; before?: string; after?: string } {
  const name = (k: string) => rows.find((r) => r.key === k)?.description ?? k;
  switch (c.kind) {
    case "reword": return { what: "reword", before: name(c.key), after: c.text };
    case "group": return { what: `move to ${c.group}`, before: name(c.key) };
    case "remove": return { what: "remove", before: name(c.key) };
    case "fold": return { what: `put under “${name(c.target)}” as a sub-category`, before: name(c.key) };
    case "subs": return { what: "sub-categories", before: name(c.key), after: c.subs.join(" | ") };
    case "add": return { what: `add${c.group ? ` under ${c.group}` : ""}`, after: c.text };
    case "split": return { what: "split", before: name(c.key), after: c.texts.join(" | ") };
    case "order": return { what: "order the groups", after: c.groups.join(" → ") };
    case "merge": return { what: "merge into one", before: c.keys.map(name).join(" + "), after: c.text };
    default: return { what: c.kind };
  }
}

function Item({ c, rows, ticked, onToggle }: {
  c: ListChange; rows: Row[]; ticked: boolean; onToggle?: () => void;
}) {
  const d = describe(c, rows);
  return (
    <li className={cx("flex gap-2 rounded px-1.5 py-1", c.risky && "bg-red-50 dark:bg-red-950/30")}>
      {onToggle
        ? <input type="checkbox" className="mt-0.5" checked={ticked} onChange={onToggle} aria-label="Apply this change" />
        : <span className={cx("mt-0.5 w-3 text-center", ticked ? "text-emerald-600" : "text-zinc-400")}>{ticked ? "✓" : "–"}</span>}
      <div className="min-w-0">
        <span className="font-semibold">{d.what}</span>
        {d.before && <> <s className="text-zinc-500">{d.before}</s></>}
        {d.after && <> → <span className="text-emerald-700 dark:text-emerald-400">{d.after}</span></>}
        {c.source && (
          <div className="text-[11px] text-zinc-500">
            from {c.source}{c.source_text && c.source_text !== c.text ? `: “${c.source_text}”` : ""}
          </div>
        )}
        {c.why && <div className="text-[11px] text-zinc-500">{c.why}</div>}
        {c.risky && <div className="text-[11px] font-medium text-red-700 dark:text-red-300">⚠ {c.risky}</div>}
      </div>
    </li>
  );
}

export function ChangeList({ comment, rows, ticked, onToggle }: {
  comment: ListComment; rows: Row[]; ticked: (c: ListChange) => boolean; onToggle?: (id: string) => void;
}) {
  const loose = comment.changes.filter((c) => c.instruction < 0 || c.instruction >= comment.instructions.length);
  const flagged = comment.changes.filter((c) => c.risky).length;
  const items = (cs: ListChange[]) => (
    <ul className="mt-1 max-h-72 space-y-0.5 overflow-auto">
      {[...cs].sort((a, b) => Number(!!b.risky) - Number(!!a.risky)).map((c) => (
        <Item key={c.id} c={c} rows={rows} ticked={ticked(c)} onToggle={onToggle && (() => onToggle(c.id))} />
      ))}
    </ul>
  );
  return (
    <div className="space-y-2 text-xs">
      {onToggle && flagged > 0 && (
        <div className="rounded bg-red-100 px-2 py-1 text-red-800 dark:bg-red-950 dark:text-red-200">
          {flagged} change{flagged === 1 ? " is" : "s are"} flagged and left unticked: look at each before ticking it.
        </div>
      )}
      {comment.instructions.map((ins, i) => {
        const cs = comment.changes.filter((c) => c.instruction === i);
        const s = STATUS[ins.status] ?? { label: ins.status, tone: "neutral" as const };
        return (
          <div key={i} className="rounded border border-zinc-200 p-2 dark:border-zinc-700">
            <div className="flex items-start justify-between gap-2">
              <span className="font-medium">{i + 1}. {ins.text}</span>
              <Badge tone={s.tone}>{s.label}{cs.length ? ` · ${cs.length}` : ""}</Badge>
            </div>
            {ins.note && <div className="mt-1 italic text-zinc-600 dark:text-zinc-400">{ins.note}</div>}
            {cs.length > 0 && items(cs)}
          </div>
        );
      })}
      {loose.length > 0 && items(loose)}
    </div>
  );
}
