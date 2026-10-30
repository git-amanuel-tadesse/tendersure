/** Step 3: criteria sheets for the categories table, their Decisions answered, applied as the next run (spec 06 S5). */
import { fileUrl } from "@tendersure/api-client";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { BenchmarkCard } from "../components/BenchmarkCard";
import { BriefBox } from "../components/BriefBox";
import { ClientRulesCard } from "../components/ClientRulesCard";
import { ProfileCard } from "../components/ProfileCard";
import { SheetPreviewCard } from "../components/SheetPreview";
import { Decisions } from "../components/Decisions";
import { FinishCard } from "../components/FinishCard";
import { JobPanel } from "../components/JobPanel";
import { Badge, Button, Card, Check, cx, ErrorNotice, Field, Input, linkButton, Notice, Select, Stat } from "../components/ui";
import { isRunning, keys, type Run, useCategories, useGenerateActions, useJob, useLatestJob, useRun, useRuns } from "../lib/api";

export const Route = createFileRoute("/exercises/$exerciseId/generate")({
  component: GeneratePage,
  validateSearch: (s: Record<string, unknown>): { run?: number } => (Number(s.run) > 0 ? { run: Number(s.run) } : {}),
});

function GeneratePage() {
  const { exerciseId } = Route.useParams();
  const { run: selected } = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const runs = useRuns(exerciseId).data?.runs ?? [];
  const finalRun = useRuns(exerciseId).data?.final ?? null;
  const current = selected ?? runs[0]?.number;
  const select = (n: number) => navigate({ search: { run: n } });
  return (
    <div className="space-y-6">
      <BenchmarkCard exerciseId={exerciseId} />
      <ProfileCard exerciseId={exerciseId} />
      <ClientRulesCard exerciseId={exerciseId} />
      <BuildCard exerciseId={exerciseId} onStarted={() => navigate({ search: {} })} />
      {runs.length > 0 && (
        <div className="grid gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
          <Card title="Runs">
            <ul className="-m-2 space-y-1">
              {runs.map((r) => (
                <li key={r.number}>
                  <button onClick={() => select(r.number)}
                          className={cx("w-full rounded-md px-2 py-2 text-left text-sm hover:bg-zinc-50 dark:hover:bg-zinc-800",
                                        r.number === current && "bg-accent-50 ring-1 ring-accent-600/30 dark:bg-accent-800/20")}>
                    <div className="flex items-center justify-between">
                      <span className="font-medium">Run {r.number}</span>
                      <span className="flex gap-1">
                        {r.number === finalRun && <Badge tone="accent">final</Badge>}
                        <Badge tone={r.status === "done" ? "good" : r.status === "failed" ? "bad" : "info"}>{r.status}</Badge>
                      </span>
                    </div>
                    <div className="mt-0.5 text-xs text-zinc-500">
                      {r.created.replace("T", " ").slice(0, 16)}
                      {r.settings.review_of && ` · answers of run ${r.settings.review_of}`}
                    </div>
                    {r.status === "done" && (
                      <div className="mt-0.5 text-xs text-zinc-500">
                        {r.written} written{r.not_written ? ` · ${r.not_written} not` : ""}
                        {` · ${Object.values(r.open).reduce((a, b) => a + b, 0)} open`}
                      </div>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          {current && <RunView exerciseId={exerciseId} n={current} onApplied={select} isFinal={finalRun === current} />}
        </div>
      )}
    </div>
  );
}

// --- a first build ----------------------------------------------------------------------------

function BuildCard({ exerciseId, onStarted }: { exerciseId: string; onStarted: () => void }) {
  const table = useCategories(exerciseId).data;
  const { start } = useGenerateActions(exerciseId);
  const latest = useLatestJob(exerciseId, "generate");
  const jobId = start.data?.id ?? latest.data?.id;
  const job = useJob(jobId, [keys.runs(exerciseId)]).data;
  const [only, setOnly] = useState("");
  const [model, setModel] = useState("auto");
  const [aiReview, setAiReview] = useState(true);
  const [rebuild, setRebuild] = useState(false);
  const [formulas, setFormulas] = useState(false);
  const [splitFiles, setSplitFiles] = useState(false);
  const numbers = only.split(",").map((s) => parseInt(s, 10)).filter((n) => n > 0);
  if (table && !table.categories.length)
    return <Notice tone="warn" title="No categories table yet">Apply the refinement review and bring its list into step 2 first.</Notice>;
  return (
    <Card title="Build criteria sheets">
      {table && table.errors > 0 && (
        <div className="mb-3"><Notice tone="warn">The categories table has {table.errors} problem(s): those sheets will be listed as NOT WRITTEN.</Notice></div>
      )}
      <div className="mb-3"><BriefBox exerciseId={exerciseId} /></div>
      <div className="flex flex-wrap items-end gap-4">
        <Field label="Categories" hint={`all ${table?.categories.length ?? ""}, or numbers e.g. 3, 13, 31`}>
          <Input className="w-48" placeholder="all" value={only} onChange={(e) => setOnly(e.target.value)} />
        </Field>
        <Field label="Model">
          <Select value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="auto">When configured</option>
            <option value="off">No AI step</option>
          </Select>
        </Field>
        <div className="flex flex-col gap-1 pb-1">
          <Check label="AI review of the proposals" checked={aiReview} disabled={model === "off"} onChange={(e) => setAiReview(e.target.checked)} />
          <Check label="Rebuild sheets that already exist" checked={rebuild} onChange={(e) => setRebuild(e.target.checked)} />
        </div>
        <div className="flex flex-col gap-1 pb-1">
          <Check label="=SUM() subtotals" checked={formulas} onChange={(e) => setFormulas(e.target.checked)} />
          <Check label="One workbook per category" checked={splitFiles} onChange={(e) => setSplitFiles(e.target.checked)} />
        </div>
        <Button
          busy={start.isPending || isRunning(job)}
          onClick={() => start.mutate({
            only: numbers.length ? numbers : null, use_model: model === "off" ? false : null, ai_review: aiReview,
            rebuild_existing: rebuild, formulas, split_files: splitFiles,
          }, { onSuccess: onStarted })}
        >
          Build
        </Button>
      </div>
      <div className="mt-3 space-y-3">
        <ErrorNotice error={start.error} />
        {isRunning(job) && <JobPanel jobId={jobId} refresh={[keys.runs(exerciseId)]} />}
      </div>
    </Card>
  );
}

// --- a run: its result and its review ------------------------------------------------------------

function RunView({ exerciseId, n, onApplied, isFinal }: {
  exerciseId: string; n: number; onApplied: (n: number) => void; isFinal: boolean;
}) {
  const run = useRun(exerciseId, n);
  const r = run.data;
  const job = useJob(r?.job_id, [keys.run(exerciseId, n), keys.runs(exerciseId)]).data;
  if (run.error) return <ErrorNotice error={run.error} />;
  if (!r) return null;
  if (!r.result) {
    return (
      <Card title={`Run ${n}`}>
        <JobPanel jobId={r.job_id} refresh={[keys.run(exerciseId, n), keys.runs(exerciseId)]} />
        {!isRunning(job) && r.error && <ErrorNotice error={r.error} />}
      </Card>
    );
  }
  const res = r.result;
  const files = [...res.files, res.qa, res.learned_review].filter((f): f is string => !!f);
  // spec 11: nothing open and no answer waiting - the work moves on
  const ready = r.status === "done" && Object.values(res.open).reduce((a, b) => a + b, 0) === 0 && Object.keys(r.answers).length === 0;
  return (
    <div className="space-y-6">
      <Card title={`Run ${n}${r.settings.review_of ? `: run ${r.settings.review_of}'s answers applied` : ""}`}
            actions={files.map((f) => (
              <a key={f} className={linkButton()} href={fileUrl(exerciseId, f)}>{f.split("/").pop()}</a>
            ))}>
        <div className="flex flex-wrap gap-6">
          <Stat label="Sheets written" value={res.written.length} />
          <Stat label="Not written" value={<span className={cx(res.failed.length > 0 && "text-red-600")}>{res.failed.length}</span>} />
          <Stat label="Decisions" value={res.items.length} />
          <Stat label="Open" value={Object.values(res.open).reduce((a, b) => a + b, 0)} />
        </div>
        {r.settings.review_of && <AppliedSummary run={r} />}
        {r.settings.benchmark && (
          <p className="mt-3 text-xs text-zinc-500"><span className="font-medium">Benchmark:</span> {r.settings.benchmark.replace("uploads/", "")}</p>
        )}
        {r.settings.brief && (
          <p className="mt-3 text-xs text-zinc-500"><span className="font-medium">Brief this run read:</span> {r.settings.brief}</p>
        )}
        {res.failed.length > 0 && (
          <div className="mt-4"><Notice tone="bad" title="NOT WRITTEN">
            <ul className="list-disc pl-5">{res.failed.map((f) => <li key={f.code}><b>{f.code}</b>: {f.reason}</li>)}</ul>
          </Notice></div>
        )}
        {res.review_problems.length > 0 && (
          <div className="mt-4"><Notice tone="warn" title="Answers that could not be applied">
            <ul className="list-disc pl-5">{res.review_problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </Notice></div>
        )}
        <details className="mt-4 text-sm">
          <summary className="cursor-pointer text-zinc-500">The build's lines</summary>
          <div className="mt-2"><JobPanel jobId={r.job_id} /></div>
        </details>
      </Card>
      {ready && <FinishCard exerciseId={exerciseId} run={r} isFinal={isFinal} />}
      {r.status === "done" && res.written.length > 0 && <SheetPreviewCard exerciseId={exerciseId} n={n} />}
      <Decisions exerciseId={exerciseId} run={r} onApplied={onApplied} />
    </div>
  );
}


/** A run made from another run's answers: what those answers did, so it is plain that something moved. */
function AppliedSummary({ run }: { run: Run }) {
  const items = run.result?.items ?? [];
  const did = (re: RegExp) => items.filter((i) => re.test(i.status ?? "")).length;
  const applied = did(/^applied/);
  const refused = items.filter((i) => /^NOT APPLIED|^PROBLEM/.test(i.status ?? ""));
  const accepted = did(/^accepted/);
  return (
    <div className="mt-3 space-y-2">
      <Notice tone={refused.length ? "warn" : "good"} title={`Run ${run.settings.review_of}'s answers applied`}>
        {applied} answer{applied === 1 ? "" : "s"} changed the sheets · {accepted} accepted as proposed
        {refused.length > 0 && ` · ${refused.length} not applied:`}
        {refused.length > 0 && (
          <ul className="mt-1 list-disc pl-5">{refused.slice(0, 8).map((i) => <li key={i.id}><b>{i.id}</b>: {i.status}</li>)}</ul>
        )}
      </Notice>
    </div>
  );
}

