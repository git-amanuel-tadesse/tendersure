/** Step 1: the draft category list -> revised list -> client review -> final list (spec 01, spec 06 S3). */
import { type Inputs, links, type Schemas } from "@tendersure/api-client";
import { createColumnHelper } from "@tanstack/react-table";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { DataTable } from "../components/DataTable";
import { JobPanel } from "../components/JobPanel";
import {
  Badge, Button, Card, Check, cx, Empty, ErrorNotice, Field, Input, linkButton, Notice, Select, Stat,
} from "../components/ui";
import { UploadButton } from "../components/UploadButton";
import {
  isRunning, keys, type Line, useExercise, useJob, useLatestJob, useRefine, useRefineActions,
} from "../lib/api";

export const Route = createFileRoute("/exercises/$exerciseId/refine")({ component: RefinePage });

const STATUS_TONE: Record<string, "neutral" | "info" | "accent" | "warn" | "good" | "bad"> = {
  Kept: "neutral", Renamed: "info", Merged: "accent", Separated: "warn", Repeated: "neutral", Additional: "good", Removed: "bad",
};
const DECISIONS = ["", "accept", "reject", "remove", "edit"];

type Edit = Partial<Pick<Line, "decision" | "comment" | "code">>;

function RefinePage() {
  const { exerciseId } = Route.useParams();
  const state = useRefine(exerciseId);
  if (state.error) return <ErrorNotice error={state.error} />;
  if (!state.data) return null;
  const { draft, revised, final } = state.data;
  return (
    <div className="space-y-6">
      <DraftStep exerciseId={exerciseId} draft={draft ?? null} hasRevised={!!revised} />
      {draft && <ProposeStep exerciseId={exerciseId} hasRevised={!!revised} target={revised?.target ?? null} />}
      {draft && revised && <ReviewStep exerciseId={exerciseId} revised={revised} draft={draft} />}
      {revised && <ApplyStep exerciseId={exerciseId} final={final ?? null} />}
    </div>
  );
}

// --- 1. the draft ---------------------------------------------------------------------------

function DraftStep({ exerciseId, draft, hasRevised }: {
  exerciseId: string; draft: Schemas["DraftState"] | null; hasRevised: boolean;
}) {
  const ex = useExercise(exerciseId).data;
  const { chooseDraft } = useRefineActions(exerciseId);
  const [file, setFile] = useState(draft?.file ?? "");
  const uploads = (ex?.files ?? []).filter((f) => f.path.startsWith("uploads/") && /\.(xlsx|csv)$/i.test(f.path));
  const choose = (body: Inputs["ChooseDraft"]) => {
    if (hasRevised && !confirm("A new draft starts the refinement again (the current proposal is kept in history). Go on?")) return;
    chooseDraft.mutate(body);
  };
  const groups = useMemo(() => [...new Set((draft?.drafts ?? []).map((d) => d.group ?? "–"))], [draft]);
  return (
    <Card title="1 · The draft list" actions={draft && <Badge tone="good">{draft.drafts.length} rows read</Badge>}>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Draft file">
          <Select value={file} onChange={(e) => setFile(e.target.value)} className="min-w-72">
            <option value="">Choose an uploaded file…</option>
            {uploads.map((f) => <option key={f.path} value={f.path}>{f.path.replace("uploads/", "")}</option>)}
          </Select>
        </Field>
        <UploadButton exerciseId={exerciseId} label="Upload a draft…" accept=".xlsx,.csv" onUploaded={(f) => setFile(f.path)} />
        <Button disabled={!file} busy={chooseDraft.isPending} onClick={() => choose({ file })}>Read the draft</Button>
        {draft && draft.tabs.length > 1 && (
          <Field label="Tab with the list">
            <Select value={draft.sheet ?? draft.tabs[0]} onChange={(e) => choose({ file: draft.file, sheet: e.target.value })}>
              {draft.tabs.map((t) => <option key={t}>{t}</option>)}
            </Select>
          </Field>
        )}
      </div>
      <div className="mt-3"><ErrorNotice error={chooseDraft.error} /></div>
      {draft && (
        <details className="mt-4 text-sm">
          <summary className="cursor-pointer text-zinc-600 dark:text-zinc-400">
            {draft.file.replace("uploads/", "")}{draft.sheet && ` / ${draft.sheet}`}: {draft.drafts.length} categories in {groups.length} groups
          </summary>
          <ol className="mt-2 max-h-72 overflow-auto rounded-md border border-zinc-200 p-2 dark:border-zinc-800">
            {draft.drafts.map((d) => (
              <li key={d.id} className="flex gap-3 py-0.5">
                <span className="w-10 shrink-0 text-right text-zinc-400 tabular-nums">{d.id}</span>
                <span className="flex-1">{d.description}</span>
                <span className="text-xs text-zinc-400">{d.group}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
    </Card>
  );
}

// --- 2. the proposal -------------------------------------------------------------------------

function ProposeStep({ exerciseId, hasRevised, target: used }: {
  exerciseId: string; hasRevised: boolean; target: number[] | null;
}) {
  const { propose } = useRefineActions(exerciseId);
  const latest = useLatestJob(exerciseId, "refine.propose");
  const jobId = propose.data?.id ?? latest.data?.id;
  const job = useJob(jobId, [keys.refine(exerciseId)]).data;
  const [low, setLow] = useState(used ? String(used[0]) : "");   // the last proposal's target, to propose again
  const [high, setHigh] = useState(used ? String(used[1]) : "");
  const [country, setCountry] = useState("");
  const [model, setModel] = useState("auto");
  const target = low && high ? [Number(low), Number(high)] : null;
  return (
    <Card title="2 · Propose the revised list">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Target count" hint="reported against, never forced">
          <div className="flex items-center gap-1">
            <Input className="w-20" inputMode="numeric" placeholder="50" value={low} onChange={(e) => setLow(e.target.value)} />
            <span>–</span>
            <Input className="w-20" inputMode="numeric" placeholder="60" value={high} onChange={(e) => setHigh(e.target.value)} />
          </div>
        </Field>
        <Field label="Country" hint="for researched licences, e.g. ET">
          <Input className="w-20" value={country} maxLength={2} onChange={(e) => setCountry(e.target.value.toUpperCase())} />
        </Field>
        <Field label="Model">
          <Select value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="auto">When configured</option>
            <option value="off">Rules only</option>
          </Select>
        </Field>
        <Button
          busy={propose.isPending || isRunning(job)}
          onClick={() => {
            if (hasRevised && !confirm("A new proposal replaces the revised list and its review answers. Go on?")) return;
            propose.mutate({ target, country, use_model: model === "off" ? false : null });
          }}
        >
          {hasRevised ? "Propose again" : "Propose"}
        </Button>
      </div>
      <div className="mt-3 space-y-3">
        <ErrorNotice error={propose.error} />
        <JobPanel jobId={jobId} refresh={[keys.refine(exerciseId)]} compact={!isRunning(job) && job?.status === "done"} />
      </div>
    </Card>
  );
}

// --- 3. the review ---------------------------------------------------------------------------

const col = createColumnHelper<Line & { index: number }>();

function ReviewStep({ exerciseId, revised, draft }: {
  exerciseId: string; revised: Schemas["Revised"]; draft: Schemas["DraftState"];
}) {
  const { changeLines } = useRefineActions(exerciseId);
  const [edits, setEdits] = useState<Record<number, Edit>>({});
  const [status, setStatus] = useState("");
  const [group, setGroup] = useState("");
  const [questionsOnly, setQuestionsOnly] = useState(false);
  const [search, setSearch] = useState("");

  const lines = revised.lines.map((ln, index) => ({ ...ln, ...edits[index], index }));
  const dirty = Object.keys(edits).length;
  const edit = (index: number, patch: Edit) =>
    setEdits((e) => {
      const next = { ...e[index], ...patch };
      const orig = revised.lines[index];
      const same = (Object.keys(next) as (keyof Edit)[]).every((k) => (next[k] ?? "") === (orig[k] ?? ""));
      const { [index]: _, ...rest } = e;
      return same ? rest : { ...e, [index]: next };
    });

  const shown = lines.filter((ln) =>
    (!status || ln.status === status) && (!group || (ln.group ?? "–") === group) && (!questionsOnly || ln.question)
    && (!search || `${ln.description} ${ln.reason} ${ln.from_draft.join(" ")}`.toLowerCase().includes(search.toLowerCase())));
  const statuses = Object.entries(revised.statuses);
  const groups = [...new Set(revised.lines.map((ln) => ln.group ?? "–"))];
  const [lo, hi] = revised.target ?? [];
  const inTarget = !revised.target || (revised.proposed >= lo && revised.proposed <= hi);

  const columns = useMemo(() => [
    col.accessor("status", {
      header: "Status", size: 96,
      cell: (c) => <Badge tone={STATUS_TONE[c.getValue()] ?? "neutral"}>{c.getValue()}</Badge>,
    }),
    col.accessor("description", {
      header: "Category", size: 340,
      cell: ({ row: { original: ln } }) => (
        <div className="space-y-1">
          <div className={cx(ln.status === "Repeated" && "text-zinc-400 line-through")}>{ln.description}</div>
          {ln.technical_name && ln.technical_name !== ln.description && (
            <div className="text-xs text-zinc-500">technical name: {ln.technical_name}</div>
          )}
          {ln.sub_categories.length > 0 && <div className="text-xs text-zinc-500">sub-categories: {ln.sub_categories.join(" · ")}</div>}
          {ln.eligibility.length > 0 && <div className="text-xs text-zinc-500">eligibility: {ln.eligibility.join(" · ")}</div>}
          {ln.licence.length > 0 && <div className="text-xs text-zinc-500">licence: {ln.licence.join(" · ")}</div>}
          {ln.hr.length > 0 && <div className="text-xs text-zinc-500">HR: {ln.hr.join(" · ")}</div>}
          {ln.technical.length > 0 && <div className="text-xs text-zinc-500">technical: {ln.technical.join(" · ")}</div>}
          {ln.suggested_eligibility.length > 0 && (
            <div className="text-xs text-amber-700 dark:text-amber-300">suggested (not applied): {ln.suggested_eligibility.join(" · ")}</div>
          )}
        </div>
      ),
    }),
    col.accessor((ln) => ln.from_draft.join(", "), { id: "from", header: "Draft #", size: 64 }),
    col.accessor("reason", {
      header: "Reason · question for the client", size: 280, enableSorting: false,
      cell: ({ row: { original: ln } }) => (
        <div className="space-y-1 text-xs">
          {ln.reason && <div className="text-zinc-600 dark:text-zinc-400">{ln.reason}</div>}
          {ln.question && <div className="rounded bg-sky-50 px-1.5 py-1 text-sky-800 dark:bg-sky-950 dark:text-sky-200">? {ln.question}</div>}
          {ln.applies_to.length > 0 && <div className="text-zinc-500">applies to: {ln.applies_to.join(", ")}</div>}
        </div>
      ),
    }),
    col.display({
      id: "decision", header: "Decision", size: 136,
      cell: ({ row: { original: ln } }) => (
        <Select value={ln.decision} aria-label="Decision" onChange={(e) => edit(ln.index, { decision: e.target.value })}
                className={cx("w-full min-w-[7.5rem]", edits[ln.index]?.decision !== undefined && "ring-2 ring-amber-400")}>
          {DECISIONS.map((d) => <option key={d} value={d}>{d || "(accept)"}</option>)}
        </Select>
      ),
    }),
    col.display({
      id: "comment", header: "Client comment · new wording", size: 240,
      cell: ({ row: { original: ln } }) => (
        <Input value={ln.comment} aria-label="Client comment" placeholder={ln.decision === "edit" ? "the new wording" : ""}
               onChange={(e) => edit(ln.index, { comment: e.target.value })}
               className={cx("w-full", edits[ln.index]?.comment !== undefined && "ring-2 ring-amber-400",
                             ln.decision === "edit" && !ln.comment && "ring-2 ring-red-400")} />
      ),
    }),
    col.display({
      id: "code", header: "Code", size: 88,
      cell: ({ row: { original: ln } }) => (
        <Input value={ln.code} aria-label="Category code" onChange={(e) => edit(ln.index, { code: e.target.value })}
               className={cx("w-full", edits[ln.index]?.code !== undefined && "ring-2 ring-amber-400")} />
      ),
    }),
  ], [edits, revised.lines]); // edit() reads both

  return (
    <Card
      title="3 · Review"
      actions={
        <>
          <a className={linkButton()} href={links.revised(exerciseId)}>Download for the client (.xlsx)</a>
          {dirty > 0 && <Button variant="ghost" onClick={() => setEdits({})}>Discard</Button>}
          <Button disabled={!dirty} busy={changeLines.isPending}
                  onClick={() => changeLines.mutate(Object.entries(edits).map(([i, e]) => ({ index: Number(i), ...e })),
                                                    { onSuccess: () => setEdits({}) })}>
            Save {dirty || ""} change{dirty === 1 ? "" : "s"}
          </Button>
        </>
      }
    >
      <div className="mb-4 flex flex-wrap items-center gap-6">
        <Stat label={`Proposed categories${revised.target ? ` (target ${lo}–${hi})` : ""}`}
              value={<span className={cx(!inTarget && "text-amber-600")}>{revised.proposed}</span>} />
        <Stat label="From draft rows" value={draft.drafts.length} />
        <Stat label="Questions for the client" value={revised.questions} />
        <div className="flex flex-wrap gap-1.5">
          {statuses.map(([s, n]) => (
            <button key={s} onClick={() => setStatus(status === s ? "" : s)}>
              <Badge tone={status === s ? "accent" : STATUS_TONE[s] ?? "neutral"}>{s} {n}</Badge>
            </button>
          ))}
        </div>
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Input placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} className="w-56" />
        <Select value={group} onChange={(e) => setGroup(e.target.value)}>
          <option value="">All groups</option>
          {groups.map((g) => <option key={g}>{g}</option>)}
        </Select>
        <Check label="Only lines with a question" checked={questionsOnly} onChange={(e) => setQuestionsOnly(e.target.checked)} />
        <span className="text-xs text-zinc-500">{shown.length} of {lines.length} lines</span>
      </div>
      <ErrorNotice error={changeLines.error} />
      <DataTable data={shown} columns={columns} getRowId={(ln) => String(ln.index)}
                 rowClass={(ln) => (edits[ln.index] ? "bg-amber-50/60 dark:bg-amber-950/20" : undefined)} />
      <ImportAnswers exerciseId={exerciseId} lines={revised.lines} />
    </Card>
  );
}

function ImportAnswers({ exerciseId, lines }: { exerciseId: string; lines: Line[] }) {
  const ex = useExercise(exerciseId).data;
  const { importAnswers } = useRefineActions(exerciseId);
  const [file, setFile] = useState("");
  const preview = importAnswers.data && !importAnswers.data.applied ? importAnswers.data : null;
  const uploads = (ex?.files ?? []).filter((f) => f.path.startsWith("uploads/") && f.path.endsWith(".xlsx"));
  return (
    <details className="mt-6 rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
      <summary className="cursor-pointer text-sm font-medium">The client answered in Excel? Bring their answers in</summary>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <Field label="Answered revised list">
          <Select value={file} onChange={(e) => setFile(e.target.value)} className="min-w-72">
            <option value="">Choose an uploaded file…</option>
            {uploads.map((f) => <option key={f.path} value={f.path}>{f.path.replace("uploads/", "")}</option>)}
          </Select>
        </Field>
        <UploadButton exerciseId={exerciseId} label="Upload it…" accept=".xlsx" onUploaded={(f) => setFile(f.path)} />
        <Button variant="secondary" disabled={!file} busy={importAnswers.isPending} onClick={() => importAnswers.mutate({ file })}>
          Show what changes
        </Button>
      </div>
      <div className="mt-3 space-y-3">
        <ErrorNotice error={importAnswers.error} />
        {importAnswers.data?.applied && <Notice tone="good">{importAnswers.data.changes.length} answer(s) brought in.</Notice>}
        {preview && (
          <>
            {preview.changes.length === 0 ? <Notice>Nothing to change: the answers are the same as here.</Notice> : (
              <ul className="space-y-1 text-sm">
                {preview.changes.map((c) => (
                  <li key={`${c.index}-${c.field}`}>
                    <span className="text-zinc-500">{lines[c.index]?.description}</span> · {c.field}:{" "}
                    <s className="text-zinc-400">{c.before || "–"}</s> → <b>{c.after || "–"}</b>
                  </li>
                ))}
              </ul>
            )}
            {preview.unmatched.length > 0 && (
              <Notice tone="warn" title="Rows that match no line here (not brought in)">{preview.unmatched.join("; ")}</Notice>
            )}
            {preview.changes.length > 0 && (
              <Button onClick={() => importAnswers.mutate({ file, confirm: true })}>Replace with these {preview.changes.length} answers</Button>
            )}
          </>
        )}
      </div>
    </details>
  );
}

// --- 4. apply --------------------------------------------------------------------------------

function ApplyStep({ exerciseId, final }: { exerciseId: string; final: Schemas["ApplyResult"] | null }) {
  const { apply } = useRefineActions(exerciseId);
  const latest = useLatestJob(exerciseId, "refine.apply");
  const jobId = apply.data?.id ?? latest.data?.id;
  const job = useJob(jobId, [keys.refine(exerciseId), keys.categories(exerciseId), keys.exercise(exerciseId)]).data;
  const [version, setVersion] = useState("");
  return (
    <Card title="4 · Apply the review" actions={final && (
      <>
        <a className={linkButton()} href={links.final(exerciseId)}>Final list (.xlsx)</a>
        <Link to="/exercises/$exerciseId/categories" params={{ exerciseId }} className={linkButton("primary")}>
          Categories table →
        </Link>
      </>
    )}>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Version label" hint="optional, e.g. 2026-09 final"><Input value={version} onChange={(e) => setVersion(e.target.value)} /></Field>
        <Button busy={apply.isPending || isRunning(job)} onClick={() => apply.mutate({ version: version || null })}>
          {final ? "Apply again" : "Apply"}
        </Button>
      </div>
      <div className="mt-3 space-y-3">
        <ErrorNotice error={apply.error} />
        <JobPanel jobId={jobId} compact refresh={[keys.refine(exerciseId)]} />
        {final && (
          <>
            <div className="flex flex-wrap gap-6">
              <Stat label="Final categories" value={final.final.length} />
              <Stat label="Removed, rejected or folded" value={final.excluded.length} />
              <Stat label="Client comments" value={final.notes.length} />
            </div>
            {final.problems.length > 0 && (
              <Notice tone="warn" title="Problems in the review"><ul className="list-disc pl-5">{final.problems.map((p) => <li key={p}>{p}</li>)}</ul></Notice>
            )}
            {final.notes.length > 0 && (
              <div className="space-y-2">
                <h3 className="text-sm font-semibold">Client comments: a proposed change each, never applied</h3>
                {final.notes.map((n, i) => (
                  <div key={i} className="rounded-md border border-zinc-200 p-2 text-sm dark:border-zinc-800">
                    <div className="font-medium">{n.line.description}</div>
                    <div className="text-zinc-600 dark:text-zinc-400">“{n.comment}”</div>
                    {n.change && <div className="text-xs text-accent-700">→ {n.change}</div>}
                  </div>
                ))}
              </div>
            )}
            {!final.final.length && <Empty>No category left after the review.</Empty>}
          </>
        )}
      </div>
    </Card>
  );
}
