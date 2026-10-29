/** Step 3: criteria sheets for the categories table, their Decisions answered, applied as the next run (spec 06 S5). */
import { fileUrl } from "@tendersure/api-client";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { BriefBox } from "../components/BriefBox";
import { JobPanel } from "../components/JobPanel";
import {
  Badge, Button, Card, Check, cx, Empty, ErrorNotice, Field, Input, linkButton, Notice, Select, Stat,
} from "../components/ui";
import {
  isRunning, type Item, keys, type Run, useCategories, useGenerateActions, useJob, useLatestJob, useRun, useRuns,
} from "../lib/api";
import {
  aAnswer, kindOf, type Marks, marksAnswer, numberOf, parseAAnswer, parseMarks, parseQAnswer, qAnswer, rulingOptions,
  teamOptions, total,
} from "../lib/answers";

export const Route = createFileRoute("/exercises/$exerciseId/generate")({
  component: GeneratePage,
  validateSearch: (s: Record<string, unknown>): { run?: number } => (Number(s.run) > 0 ? { run: Number(s.run) } : {}),
});

const KIND_LABEL: Record<string, string> = {
  question: "Questions copied into these sheets", add: "Questions proposed for addition", marks: "Marks per section",
  ruling: "Where the client's sheets disagree", team: "Decided once for the batch", check: "To fix by hand",
};
const KIND_ORDER = ["question", "add", "marks", "ruling", "team", "check"];

function GeneratePage() {
  const { exerciseId } = Route.useParams();
  const { run: selected } = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const runs = useRuns(exerciseId).data?.runs ?? [];
  const current = selected ?? runs[0]?.number;
  const select = (n: number) => navigate({ search: { run: n } });
  return (
    <div className="space-y-6">
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
                      <Badge tone={r.status === "done" ? "good" : r.status === "failed" ? "bad" : "info"}>{r.status}</Badge>
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
          {current && <RunView exerciseId={exerciseId} n={current} onApplied={select} />}
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

function RunView({ exerciseId, n, onApplied }: { exerciseId: string; n: number; onApplied: (n: number) => void }) {
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
      <Decisions exerciseId={exerciseId} run={r} onApplied={onApplied} />
    </div>
  );
}

function Decisions({ exerciseId, run, onApplied }: { exerciseId: string; run: Run; onApplied: (n: number) => void }) {
  const { answer, apply } = useGenerateActions(exerciseId);
  const items = run.result!.items;
  const saved = useMemo(() => Object.fromEntries(items.map((i) => [i.id, run.answers[i.id] ?? i.answer ?? ""])), [items, run.answers]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  useEffect(() => setDraft({}), [run.number]);
  const value = (id: string) => draft[id] ?? saved[id] ?? "";
  const dirty = Object.keys(draft).filter((id) => draft[id] !== saved[id]);
  const applyJob = useJob(apply.data?.id, [keys.runs(exerciseId)]).data;
  useEffect(() => {
    const next = (applyJob?.result as { run?: number } | null)?.run;
    if (applyJob?.status === "done" && next) onApplied(next);
  }, [applyJob?.status]); // once, when the applying job has finished
  const save = () => answer.mutateAsync({ n: run.number, answers: Object.fromEntries(dirty.map((id) => [id, draft[id]])) })
    .then(() => setDraft({}));
  const groups = KIND_ORDER.map((k) => [k, items.filter((i) => kindOf(i) === k)] as const).filter(([, xs]) => xs.length);
  const answered = items.filter((i) => value(i.id)).length;
  return (
    <Card
      title={`Decisions · ${answered} answered of ${items.length}`}
      actions={
        <>
          {dirty.length > 0 && <Button variant="ghost" onClick={() => setDraft({})}>Discard</Button>}
          <Button variant="secondary" disabled={!dirty.length} busy={answer.isPending} onClick={save}>
            Save {dirty.length || ""} answer{dirty.length === 1 ? "" : "s"}
          </Button>
          <Button busy={apply.isPending || isRunning(applyJob)}
                  onClick={async () => { if (dirty.length) await save(); apply.mutate(run.number); }}>
            Apply answers → run {run.number + 1}
          </Button>
        </>
      }
    >
      <p className="mb-4 text-sm text-zinc-500">
        A blank answer accepts the proposal. Applying builds the next run with the same model proposals - no new model call
        for them - and keeps this run as it is.
      </p>
      <div className="space-y-3">
        <ErrorNotice error={answer.error ?? apply.error} />
        {apply.data && <JobPanel jobId={apply.data.id} refresh={[keys.runs(exerciseId)]} />}
      </div>
      {!items.length && <Empty>Nothing to decide in this run.</Empty>}
      <div className="space-y-8">
        {groups.map(([kind, xs]) => (
          <section key={kind}>
            <h3 className="mb-2 text-sm font-semibold">{KIND_LABEL[kind]} <span className="text-zinc-400">({xs.length})</span></h3>
            <div className="space-y-3">
              {xs.map((it) => (
                <ItemCard key={it.id} item={it} value={value(it.id)} changed={draft[it.id] !== undefined && draft[it.id] !== saved[it.id]}
                          onChange={(v) => setDraft((d) => ({ ...d, [it.id]: v }))} />
              ))}
            </div>
          </section>
        ))}
      </div>
    </Card>
  );
}

// --- one Decisions item: its control and its answer -----------------------------------------------

function ItemCard({ item, value, changed, onChange }: { item: Item; value: string; changed: boolean; onChange: (v: string) => void }) {
  const kind = kindOf(item);
  const statusTone = !item.status ? "neutral" : item.status.startsWith("NOT") || item.status.startsWith("PROBLEM") ? "bad" : "good";
  return (
    <article className={cx("rounded-md border p-3", changed ? "border-amber-400 bg-amber-50/40 dark:bg-amber-950/10" : "border-zinc-200 dark:border-zinc-800")}>
      <header className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <Badge>{item.id}</Badge>
        <span className="text-zinc-500">categories {item.codes.map(numberOf).join(", ")}</span>
        {item.status && <Badge tone={statusTone} title={item.status}>{item.status.length > 70 ? `${item.status.slice(0, 70)}…` : item.status}</Badge>}
      </header>
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-1">
          <p className="text-sm whitespace-pre-wrap">{item.decide}</p>
          <p className="text-xs"><span className="text-zinc-500">Proposed:</span> <b>{item.proposed}</b></p>
          {item.why && <p className="text-xs text-zinc-500">{item.why}</p>}
        </div>
        <div className="space-y-2">
          {kind === "question" && <QControl item={item} value={value} onChange={onChange} />}
          {kind === "add" && <AControl item={item} value={value} onChange={onChange} />}
          {kind === "marks" && <MControl item={item} value={value} onChange={onChange} />}
          {kind === "ruling" && <Choice options={["majority", ...rulingOptions(item.decide)]} value={value} onChange={onChange} />}
          {kind === "team" && <Choice options={teamOptions(item.id)} value={value} onChange={onChange} />}
          {kind === "check" ? (
            <p className="text-xs text-zinc-500">Nothing to answer here: fix it in the workbook, then run the check.</p>
          ) : (
            <Field label="Answer" hint="as in the QA workbook's Decisions tab; blank accepts the proposal">
              <Input className="font-mono text-xs" value={value} placeholder={item.proposed} onChange={(e) => onChange(e.target.value)} />
            </Field>
          )}
        </div>
      </div>
    </article>
  );
}

function Choice({ options, value, onChange }: { options: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <Button variant={!value ? "primary" : "secondary"} className="px-2 py-1 text-xs" onClick={() => onChange("")}>as proposed</Button>
      {options.map((o) => (
        <Button key={o} variant={value.toLowerCase() === o.toLowerCase() ? "primary" : "secondary"} className="px-2 py-1 text-xs"
                onClick={() => onChange(o)}>{o}</Button>
      ))}
    </div>
  );
}

function QControl({ item, value, onChange }: { item: Item; value: string; onChange: (v: string) => void }) {
  const wording = Object.fromEntries(((item.payload ?? []) as { code: string; text: string }[]).map((p) => [p.code, p.text]));
  const choice = value ? parseQAnswer(value, item.codes, wording) : null;
  if (value && !choice) return <p className="text-xs text-amber-700">Answer written by hand: edit it below.</p>;
  const c = choice ?? { remove: [], reword: {} };
  const set = (next: typeof c) => onChange(qAnswer(item.codes, next));
  const single = !item.decide.includes(" questions:\n");
  return (
    <div className="space-y-2">
      <div className="text-xs text-zinc-500">Keep the question in each sheet, or remove it:</div>
      <div className="flex flex-wrap gap-1.5">
        {item.codes.map((code) => {
          const removed = c.remove.includes(code);
          return (
            <button key={code} title={code} onClick={() => set({ ...c, remove: removed ? c.remove.filter((x) => x !== code) : [...c.remove, code] })}
                    className={cx("rounded px-2 py-0.5 text-xs ring-1", removed
                      ? "bg-red-50 text-red-700 line-through ring-red-300 dark:bg-red-950/30"
                      : "bg-white text-zinc-700 ring-zinc-300 dark:bg-zinc-900 dark:text-zinc-200 dark:ring-zinc-700")}>
              {numberOf(code)}
            </button>
          );
        })}
        <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => set({ remove: [...item.codes], reword: {} })}>remove all</Button>
        <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => onChange("")}>as proposed</Button>
      </div>
      {Object.keys(wording).length > 0 && (
        <div className="space-y-1 text-xs">
          {Object.entries(wording).filter(([code]) => !c.remove.includes(code)).map(([code, text]) => (
            <Check key={code} label={<span>{numberOf(code)}: use “{text}”</span>} checked={c.reword[code] === true}
                   onChange={(e) => {
                     const { [code]: _, ...rest } = c.reword;
                     set({ ...c, reword: e.target.checked ? { ...rest, [code]: true } : rest });
                   }} />
          ))}
        </div>
      )}
      {single && c.remove.length < item.codes.length && (
        <details className="text-xs">
          <summary className="cursor-pointer text-zinc-500">Own wording for one sheet…</summary>
          <OwnWording codes={item.codes.filter((x) => !c.remove.includes(x))} onSet={(code, text) => set({ ...c, reword: { ...c.reword, [code]: text } })} />
        </details>
      )}
    </div>
  );
}

function OwnWording({ codes, onSet }: { codes: string[]; onSet: (code: string, text: string) => void }) {
  const [code, setCode] = useState(codes[0] ?? "");
  const [text, setText] = useState("");
  return (
    <div className="mt-1 flex gap-1">
      <Select value={code} onChange={(e) => setCode(e.target.value)}>{codes.map((c) => <option key={c} value={c}>{numberOf(c)}</option>)}</Select>
      <Input className="flex-1" value={text} placeholder="the new wording" onChange={(e) => setText(e.target.value)} />
      <Button variant="secondary" className="px-2 text-xs" disabled={!text.trim()} onClick={() => onSet(code, text)}>Set</Button>
    </div>
  );
}

function AControl({ item, value, onChange }: { item: Item; value: string; onChange: (v: string) => void }) {
  const entries = (item.payload ?? []) as { section: string; text: string; kind: string }[];
  const picked = parseAAnswer(value, entries.length, item.proposed);
  if (!picked) return <p className="text-xs text-amber-700">Answer written by hand: edit it below.</p>;
  return (
    <div className="space-y-1 text-xs">
      {entries.map((e, i) => (
        <Check key={i} checked={picked.includes(i + 1)}
               label={<span><b>{i + 1}.</b> [{e.section}] {e.text} {e.kind === "draft" && <Badge tone="info">new</Badge>}</span>}
               onChange={(ev) => onChange(aAnswer(entries.length, ev.target.checked ? [...picked, i + 1] : picked.filter((n) => n !== i + 1)))} />
      ))}
      <p className="text-zinc-500">Added questions arrive with 0 marks: give their section marks on its marks line.</p>
    </div>
  );
}

function MControl({ item, value, onChange }: { item: Item; value: string; onChange: (v: string) => void }) {
  const marks: Marks = parseMarks(value || item.proposed);
  const sum = total(marks);
  const set = (i: number, v: number) => {
    const next = marks.map(([k, m], j) => [k, j === i ? v : m] as [string, number]);
    const text = marksAnswer(next);
    onChange(text === marksAnswer(parseMarks(item.proposed)) ? "" : text);
  };
  return (
    <div className="space-y-1">
      <div className="grid grid-cols-2 gap-1.5">
        {marks.map(([k, m], i) => (
          <label key={k} className="flex items-center justify-between gap-2 text-xs">
            <span className="truncate text-zinc-600 dark:text-zinc-400">{k}</span>
            <Input type="number" min={0} step={1} className="w-16 py-0.5 text-right" value={m}
                   onChange={(e) => set(i, Number(e.target.value))} />
          </label>
        ))}
      </div>
      <div className={cx("text-xs font-medium", sum === 100 ? "text-emerald-700" : "text-red-600")}>
        total {+sum.toFixed(2)}{sum !== 100 && " - every sheet must total 100"}
      </div>
    </div>
  );
}
