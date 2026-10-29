/** The client's sample criteria (spec 08): sheets are built from the bank and compared with these tabs. */
import { useState } from "react";
import { type MapLine, useBenchmark, useBenchmarkActions, useExercise, useRefine } from "../lib/api";
import { Badge, Button, Card, cx, ErrorNotice, Notice, Select } from "./ui";
import { UploadButton } from "./UploadButton";

export function BenchmarkCard({ exerciseId }: { exerciseId: string }) {
  const b = useBenchmark(exerciseId).data;
  const draft = useRefine(exerciseId).data?.draft?.file;             // the category list is not a benchmark
  const files = (useExercise(exerciseId).data?.files ?? [])
    .filter((f) => f.path.startsWith("uploads/") && f.path.endsWith(".xlsx") && f.path !== draft);
  const { choose, setMap, stop } = useBenchmarkActions(exerciseId);
  const [file, setFile] = useState("");
  if (!b) return null;
  const tabs = b.tabs.map((t) => t.name);
  const change = (i: number, tab: string) =>
    setMap.mutate(b.map.map((m, j): MapLine => (j === i ? { ...m, tab, source: "team" } : m)));
  return (
    <Card
      title={<span className="flex items-center gap-2">Client's benchmark {b.file && <Badge>{b.file.replace("uploads/", "")}</Badge>}</span>}
      actions={b.file && <Button variant="ghost" onClick={() => confirm("Stop comparing with this workbook?") && stop.mutate()}>Stop using it</Button>}
    >
      {!b.file ? (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-zinc-500">Optional. Sample criteria from the client (one tab per kind of category): each sheet is compared with its group's tab - marks per section and the questions either side asks - in the QA report, and the client's split is offered on an M-BENCH- line.</span>
          <Select value={file} onChange={(e) => setFile(e.target.value)}>
            <option value="">an uploaded workbook…</option>
            {files.map((f) => <option key={f.path} value={f.path}>{f.path.replace("uploads/", "")}</option>)}
          </Select>
          <UploadButton exerciseId={exerciseId} label="Upload…" accept=".xlsx" onUploaded={(f) => setFile(f.path)} />
          <Button variant="secondary" disabled={!file} busy={choose.isPending} onClick={() => choose.mutate(file)}>Use it</Button>
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <div>
            <h3 className="mb-2 text-sm font-medium">Tabs</h3>
            <ul className="space-y-1 text-sm">
              {b.tabs.map((t) => (
                <li key={t.name}>
                  <Badge tone={t.usable ? "good" : "bad"}>{t.usable ? "usable" : "not used"}</Badge>{" "}
                  <b>{t.name}</b> <span className="text-zinc-500">({t.total} marks)</span>
                  {t.refused.map((r) => <div key={r} className="ml-4 text-xs text-red-700 dark:text-red-300">{r}</div>)}
                  {t.warnings.length > 0 && <div className="ml-4 text-xs text-amber-700 dark:text-amber-300">{t.warnings.join("; ")}</div>}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="mb-2 text-sm font-medium">Which tab each group is compared with</h3>
            {b.map.length === 0 ? (
              <Notice tone="warn">No categories table yet: the map is proposed from its groups.</Notice>
            ) : (
              <table className="w-full text-sm">
                <tbody>
                  {b.map.map((m, i) => (
                    <tr key={`${m.group}|${m.kind}`} className="border-b border-zinc-100 dark:border-zinc-800/60">
                      <td className="py-1 pr-2">{m.group || "(no group)"}{m.kind && <span className="text-xs text-zinc-500"> · {m.kind}</span>}</td>
                      <td className="py-1">
                        <Select value={m.tab} aria-label={`Tab for ${m.group}`} onChange={(e) => change(i, e.target.value)}
                                className={cx(b.tabs.find((t) => t.name === m.tab)?.usable === false && "ring-2 ring-red-400")}>
                          {tabs.map((t) => <option key={t} value={t}>{t}</option>)}
                        </Select>
                      </td>
                      <td className="py-1 pl-2 text-xs text-zinc-500">{m.source === "proposed" ? "proposed" : "set by you"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
      <div className="mt-3"><ErrorNotice error={choose.error ?? setMap.error ?? stop.error} /></div>
    </Card>
  );
}
