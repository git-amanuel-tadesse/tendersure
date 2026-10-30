/** A run's sheet as the client will see it (spec 16 F): sections and sub-sections as merged blocks, the whole question wrapped. */
import { useState } from "react";
import { useSheetPreview } from "../lib/api";
import { Badge, Card, cx, ErrorNotice, Select } from "./ui";

export function SheetPreviewCard({ exerciseId, n }: { exerciseId: string; n: number }) {
  const [code, setCode] = useState("");
  const q = useSheetPreview(exerciseId, n, code);
  const d = q.data;
  const rows = d?.rows ?? [];
  const firstOf = (i: number, f: "section" | "sub_section") => i === 0 || rows[i][f] !== rows[i - 1][f] || (f === "sub_section" && rows[i].section !== rows[i - 1].section);
  const span = (i: number, f: "section" | "sub_section") => {
    let j = i + 1;
    while (j < rows.length && !firstOf(j, f)) j++;
    return j - i;
  };
  return (
    <Card title={<span className="flex items-center gap-2">Preview {d && <Badge tone={Math.abs(d.total - 100) < 1e-6 ? "good" : "bad"}>{d.total} marks</Badge>}</span>}
          actions={d && (
            <Select value={d.code} onChange={(e) => setCode(e.target.value)} aria-label="Sheet">
              {d.codes.map((c) => <option key={c}>{c}</option>)}
            </Select>
          )}>
      <ErrorNotice error={q.error} />
      {d && (
        <>
          <p className="mb-2 text-sm font-medium">{d.title}</p>
          <div className="max-h-[36rem] overflow-auto rounded-md border border-zinc-200 dark:border-zinc-800">
            <table className="w-full border-collapse text-xs">
              <thead className="sticky top-0 bg-zinc-50 text-left text-zinc-500 dark:bg-zinc-900">
                <tr>{["Section", "Sub Section", "Criteria", "Marks", "Type", "Applies To"].map((h) => <th key={h} className="border-b border-zinc-200 px-2 py-1 font-medium dark:border-zinc-800">{h}</th>)}</tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className="align-top">
                    {firstOf(i, "section") && <td rowSpan={span(i, "section")} className="w-32 border border-zinc-200 px-2 py-1 font-medium dark:border-zinc-800">{r.section}</td>}
                    {firstOf(i, "sub_section") && <td rowSpan={span(i, "sub_section")} className="w-24 border border-zinc-200 px-2 py-1 dark:border-zinc-800">{r.sub_section}</td>}
                    <td className="whitespace-pre-wrap border border-zinc-200 px-2 py-1 dark:border-zinc-800">
                      {r.criteria}
                      {r.options && <div className="mt-0.5 text-zinc-500">{r.options}</div>}
                    </td>
                    <td className={cx("border border-zinc-200 px-2 py-1 text-right tabular-nums dark:border-zinc-800", r.marks > 0 && "font-medium")}>{r.marks}</td>
                    <td className="border border-zinc-200 px-2 py-1 text-center dark:border-zinc-800">{r.answer_type ?? ""}</td>
                    <td className="w-40 border border-zinc-200 px-2 py-1 dark:border-zinc-800">{r.applies_to}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}
