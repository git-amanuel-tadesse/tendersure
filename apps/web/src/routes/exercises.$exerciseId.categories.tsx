/** Step 2: the categories table generation reads, edited in place, checked row by row (spec 06 S4). */
import { links } from "@tendersure/api-client";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import {
  Badge, Button, Card, Check, cx, Empty, ErrorNotice, Input, linkButton, Notice, Select,
} from "../components/ui";
import { UploadButton } from "../components/UploadButton";
import { type Category, useCategories, useCategoryActions, useExercise, useRefine } from "../lib/api";

export const Route = createFileRoute("/exercises/$exerciseId/categories")({ component: CategoriesPage });

const TYPES = ["Sole Proprietor", "Partnership", "Private Limited Company", "Share Company", "Individual Consultant"];
const split = (text: string) => text.split("|").map((s) => s.trim()).filter(Boolean);

function CategoriesPage() {
  const { exerciseId } = Route.useParams();
  const table = useCategories(exerciseId);
  const refine = useRefine(exerciseId).data;
  const ex = useExercise(exerciseId).data;
  const { changeRow, replace, importTable } = useCategoryActions(exerciseId);
  const [csv, setCsv] = useState("");
  const t = table.data;
  if (table.error) return <ErrorNotice error={table.error} />;
  if (!t) return null;
  const problemsOf = (n: number) => t.problems.filter((p) => p.number === n);
  const csvs = (ex?.files ?? []).filter((f) => f.path.startsWith("uploads/") && f.path.endsWith(".csv"));
  const importFrom = (source: string) => {
    if (t.categories.length && !confirm("Replace the table? The current one is kept in history/.")) return;
    importTable.mutate(source);
  };
  const addRow = () => {
    const n = Math.max(0, ...t.categories.map((c) => c.number)) + 1;
    replace.mutate([...t.categories, {
      number: n, description: "New category", group: null, sub_categories: [], client: ex?.client ?? null, version: null,
      requirements: "", eligibility: [], gate_question: false, applies_to: [], licence: [], hr: [], technical: [],
      kind: "",   // kind: computed by the API
    }]);
  };
  return (
    <div className="space-y-6">
      <Card
        title="Where the table comes from"
        actions={t.categories.length > 0 && (
          <a className={linkButton()} href={links.categoriesCsv(exerciseId)}>Download categories.csv</a>
        )}
      >
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <Button variant="secondary" disabled={!refine?.final} busy={importTable.isPending && importTable.variables === "refine"}
                  onClick={() => importFrom("refine")}>
            From the applied review{refine?.final && ` (${refine.final.categories.length})`}
          </Button>
          <span className="text-zinc-400">or</span>
          <Select value={csv} onChange={(e) => setCsv(e.target.value)}>
            <option value="">an uploaded CSV…</option>
            {csvs.map((f) => <option key={f.path} value={f.path}>{f.path.replace("uploads/", "")}</option>)}
          </Select>
          <UploadButton exerciseId={exerciseId} label="Upload a CSV…" accept=".csv" onUploaded={(f) => setCsv(f.path)} />
          <Button variant="secondary" disabled={!csv} onClick={() => importFrom(csv)}>Import it</Button>
          {t.updated && <span className="ml-auto text-xs text-zinc-500">saved {t.updated.replace("T", " ")}</span>}
        </div>
        <div className="mt-3"><ErrorNotice error={importTable.error} /></div>
      </Card>

      <Card
        title={<span className="flex items-center gap-2">The categories table <Badge>{t.categories.length}</Badge></span>}
        actions={
          <>
            <Button variant="secondary" onClick={addRow} busy={replace.isPending}>Add a row</Button>
            {t.categories.length > 0 && (
              <Link to="/exercises/$exerciseId/generate" params={{ exerciseId }} className={linkButton("primary")}>Generate →</Link>
            )}
          </>
        }
      >
        {t.errors > 0 ? (
          <Notice tone="bad" title={`${t.errors} problem${t.errors === 1 ? "" : "s"} to fix before generating`}>
            A category whose Gate Question disagrees with Applies To is not written.
          </Notice>
        ) : t.categories.length > 0 ? (
          <Notice tone="good">No problems: every row can be generated.</Notice>
        ) : null}
        <div className="mt-3"><ErrorNotice error={changeRow.error ?? replace.error} /></div>
        {!t.categories.length ? (
          <Empty>No table yet: apply the refinement review, then bring its list in here.</Empty>
        ) : (
          <div className="mt-4 overflow-x-auto rounded-md border border-zinc-200 dark:border-zinc-800">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 text-left text-xs text-zinc-500 dark:bg-zinc-900">
                <tr>
                  {["#", "Description · group", "Sub-categories", "Eligibility documents", "Licence · HR · technical", "Gate",
                    "Applies to", ""].map((h) => (
                    <th key={h} className="border-b border-zinc-200 px-3 py-2 font-medium dark:border-zinc-800">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {t.categories.map((c) => (
                  <Row key={c.number} c={c} problems={problemsOf(c.number)}
                       save={(change) => changeRow.mutate({ number: c.number, ...change })}
                       remove={() => confirm(`Remove category ${c.number}?`)
                         && replace.mutate(t.categories.filter((x) => x.number !== c.number))} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

/** A text cell that saves when it is left, if it changed. */
function TextCell({ value, onSave, placeholder, wide }: {
  value: string; onSave: (v: string) => void; placeholder?: string; wide?: boolean;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <Input value={v} placeholder={placeholder} onChange={(e) => setV(e.target.value)}
           onBlur={() => v.trim() !== value && onSave(v.trim())}
           className={cx(wide ? "w-80" : "w-56", "max-w-full")} />
  );
}

function Row({ c, problems, save, remove }: {
  c: Category; problems: { field: string; severity: string; message: string }[];
  save: (change: Partial<Category>) => void; remove: () => void;
}) {
  const bad = (field: string) => problems.some((p) => p.field === field && p.severity === "error");
  return (
    <>
      <tr className={cx("align-top", problems.length ? "" : "border-b border-zinc-100 dark:border-zinc-800/60")}>
        <td className={cx("px-3 py-2 tabular-nums", bad("number") && "text-red-600")}>{c.number}</td>
        <td className="space-y-1 px-3 py-2">
          <TextCell wide value={c.description} onSave={(description) => save({ description })} />
          <TextCell value={c.group ?? ""} placeholder="group" onSave={(group) => save({ group: group || null })} />
          {c.kind && <div className="text-xs text-zinc-500">{c.kind}</div>}
        </td>
        <td className="px-3 py-2">
          <TextCell value={c.sub_categories.join(" | ")} placeholder="none (a | b)" onSave={(v) => save({ sub_categories: split(v) })} />
        </td>
        <td className="px-3 py-2">
          <TextCell value={c.eligibility.join(" | ")} placeholder="none"
                    onSave={(v) => save({ eligibility: split(v) })} />
        </td>
        <td className="space-y-1 px-3 py-2">
          <TextCell value={c.licence.join(" | ")} placeholder="licence / grade"
                    onSave={(v) => save({ licence: split(v) })} />
          <TextCell value={c.hr.join(" | ")} placeholder="key staff (a | b)" onSave={(v) => save({ hr: split(v) })} />
          <TextCell value={c.technical.join(" | ")} placeholder="technical documents, (equipment, list)"
                    onSave={(v) => save({ technical: split(v) })} />
        </td>
        <td className={cx("px-3 py-2", bad("gate_question") && "bg-red-50 dark:bg-red-950/30")}>
          <Check label="" aria-label="Gate question" checked={c.gate_question}
                 onChange={(e) => save({ gate_question: e.target.checked })} />
        </td>
        <td className={cx("px-3 py-2", bad("gate_question") && "bg-red-50 dark:bg-red-950/30")}>
          <div className="flex flex-col gap-1">
            {TYPES.map((ty) => (
              <Check key={ty} label={<span className="text-xs">{ty}</span>} checked={c.applies_to.includes(ty)}
                     onChange={(e) => save({ applies_to: e.target.checked ? [...c.applies_to, ty] : c.applies_to.filter((x) => x !== ty) })} />
            ))}
          </div>
        </td>
        <td className="px-3 py-2"><Button variant="ghost" aria-label={`Remove category ${c.number}`} onClick={remove}>✕</Button></td>
      </tr>
      {problems.length > 0 && (
        <tr className="border-b border-zinc-100 dark:border-zinc-800/60">
          <td />
          <td colSpan={7} className="px-3 pb-2">
            {problems.map((p, i) => (
              <div key={i} className={cx("text-xs", p.severity === "error" ? "text-red-700 dark:text-red-300" : "text-amber-700 dark:text-amber-300")}>
                {p.severity === "error" ? "✕" : "!"} {p.message}
                {p.field === "gate_question" && " - tick Individual Consultant, or clear the Gate."}
              </div>
            ))}
          </td>
        </tr>
      )}
    </>
  );
}
