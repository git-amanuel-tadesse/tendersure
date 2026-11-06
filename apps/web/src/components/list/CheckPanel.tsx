/** "Check against a list" (spec 21 F): how close the list is to a list the team made, e.g. a past exercise's
 * final list; closed by default: it is for testing the system, not part of the daily work. */
import { useState } from "react";
import { Button, ErrorNotice, Field, Select, Stat } from "../ui";
import { useExercise, useListActions } from "../../lib/api";

export function CheckPanel({ exerciseId, version }: { exerciseId: string; version: number }) {
  const ex = useExercise(exerciseId).data;
  const { check } = useListActions(exerciseId);
  const remembered = `tendersure-check-${exerciseId}`;
  const [file, setFile] = useState(() => localStorage.getItem(remembered) ?? "");
  const uploads = (ex?.files ?? []).filter((f) => f.path.startsWith("uploads/") && /\.xlsx$/i.test(f.path));
  const s = check.data;
  const run = () => { localStorage.setItem(remembered, file); check.mutate({ file, sheets: [] }); };
  return (
    <details className="rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800">
      <summary className="cursor-pointer font-medium">Check against a list</summary>
      <p className="mt-2 text-xs text-zinc-500">
        Compare this list (version {version}) with a list the team made, e.g. a past exercise's final list, uploaded under Files.
      </p>
      <div className="mt-2 flex flex-wrap items-end gap-3">
        <Field label="The team's list">
          <Select value={file} onChange={(e) => setFile(e.target.value)} className="min-w-72">
            <option value="">Choose an uploaded file…</option>
            {uploads.map((f) => <option key={f.path} value={f.path}>{f.path.replace("uploads/", "")}</option>)}
          </Select>
        </Field>
        <Button disabled={!file} busy={check.isPending} onClick={run}>Check</Button>
      </div>
      <ErrorNotice error={check.error} />
      {s && (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap gap-5">
            <Stat label="Team / ours" value={`${s.target} / ${s.ours}`} />
            <Stat label="Same wording" value={s.same_wording} />
            <Stat label="Close wording" value={s.close_wording} />
            <Stat label="Same group" value={s.same_group} />
            <Stat label="Missing" value={s.missing.length} />
            <Stat label="Extra" value={s.extra.length} />
          </div>
          <div className="grid grid-cols-2 gap-4 text-xs">
            <div><b>Missing (the team's, not ours)</b><ul className="list-disc pl-4">{s.missing.map((x) => <li key={x}>{x}</li>)}</ul></div>
            <div><b>Extra (ours, not the team's)</b><ul className="list-disc pl-4">{s.extra.map((x) => <li key={x}>{x}</li>)}</ul></div>
            <div><b>Close wording (team → ours)</b><ul className="list-disc pl-4">{s.close_pairs.map((p) => <li key={p.target}>{p.target} → <i>{p.ours}</i></li>)}</ul></div>
            <div><b>Other group (team / ours)</b><ul className="list-disc pl-4">{s.group_differs.map((g) => <li key={g.category}>{g.category}: {g.target} / <i>{g.ours}</i></li>)}</ul></div>
          </div>
        </div>
      )}
    </details>
  );
}
