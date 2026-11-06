/** The list as the client will see it (spec 21 A): group headings, #, Category Description, sub-categories,
 * each line's draft rows in small print; the waiting comment's ticked changes are marked where they land. */
import { Fragment } from "react";
import type { Schemas } from "@tendersure/api-client";
import { cx } from "../ui";
import type { ListChange } from "../../lib/api";
import { describe } from "./ChangeList";

type Row = Schemas["ListRow"];

export function grouped(rows: Row[]): [string, Row[]][] {
  const out = new Map<string, Row[]>();
  for (const r of rows) out.set(r.group || "–", [...(out.get(r.group || "–") ?? []), r]);
  return [...out.entries()];
}

export function ListDocument({ rows, marks }: { rows: Row[]; marks: ListChange[] }) {
  const markOf = (key: string) => marks.filter((c) => c.key === key || c.keys.includes(key) || c.target === key);
  let n = 0;
  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr className="bg-emerald-700 text-left text-white">
          <th className="w-12 p-2">#</th>
          <th className="p-2">Category Description</th>
          <th className="w-1/3 p-2">Sub-category</th>
        </tr>
      </thead>
      <tbody>
        {grouped(rows).map(([group, rs]) => (
          <Fragment key={group}>
            <tr><td colSpan={3} className="bg-orange-500/90 p-2 font-semibold text-white">{group}</td></tr>
            {rs.map((r) => {
              const ms = markOf(r.key);
              const gone = ms.some((c) => c.kind === "remove" || (c.kind === "fold" && c.key === r.key)
                || (c.kind === "merge" && c.keys[0] !== r.key));
              if (!gone) n += 1;
              return (
                <tr key={r.key} className={cx("border-b border-zinc-100 align-top dark:border-zinc-800",
                                              ms.length > 0 && "bg-amber-50 dark:bg-amber-950/40")}>
                  <td className="p-2 tabular-nums text-zinc-500">{gone ? "" : n}</td>
                  <td className="p-2">
                    <div className={cx(gone && "text-red-600 line-through")}>{r.description}</div>
                    {ms.map((c) => {
                      const d = describe(c, rows);
                      return (
                        <div key={c.id} className="text-xs text-violet-700 dark:text-violet-300">
                          → {d.what}{d.after ? `: ${d.after}` : ""}
                        </div>
                      );
                    })}
                    <div className="mt-0.5 text-[11px] text-zinc-400">draft {r.from_draft.join(", ") || "—"}</div>
                  </td>
                  <td className="p-2 text-zinc-600 dark:text-zinc-400">{r.subs.map((s) => <div key={s}>{s}</div>)}</td>
                </tr>
              );
            })}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}
