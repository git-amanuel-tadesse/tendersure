/** Step 1: the category list, shaped by the team's comments (spec 21): the client's draft as written, then
 * comments in the margin, each read by the AI, checked, and applied or rejected by a team member. */
import { type Inputs, links, type Schemas } from "@tendersure/api-client";
import { createFileRoute, Link } from "@tanstack/react-router";
import { type ReactNode, useMemo, useState } from "react";
import { BriefBox } from "../components/BriefBox";
import { CheckPanel } from "../components/list/CheckPanel";
import { CommentMargin } from "../components/list/CommentMargin";
import { grouped, ListDocument } from "../components/list/ListDocument";
import { Badge, Button, Card, Check, ErrorNotice, Field, linkButton, Notice, Select } from "../components/ui";
import { UploadButton } from "../components/UploadButton";
import { useExercise, useList, useListActions, useRefine, useRefineActions } from "../lib/api";

export const Route = createFileRoute("/exercises/$exerciseId/refine")({ component: RefinePage });

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? "") === JSON.stringify(b ?? "");

function RefinePage() {
  const { exerciseId } = Route.useParams();
  const state = useRefine(exerciseId);
  const draft = state.data?.draft ?? null;
  const list = useList(exerciseId, !!draft);
  if (state.error) return <ErrorNotice error={state.error} />;
  if (!state.data) return null;
  const decided = list.data?.thread.filter((c) => c.state === "applied").length ?? 0;
  return (
    <div className="space-y-6">
      <DraftStep exerciseId={exerciseId} draft={draft} commented={decided > 0} />
      {draft && list.error && <ErrorNotice error={list.error} />}
      {draft && list.data && <ListStep exerciseId={exerciseId} state={list.data} />}
    </div>
  );
}

// --- 1. the draft ---------------------------------------------------------------------------

function DraftStep({ exerciseId, draft, commented }: {
  exerciseId: string; draft: Schemas["DraftState"] | null; commented: boolean;
}) {
  const ex = useExercise(exerciseId).data;
  const { chooseDraft } = useRefineActions(exerciseId);
  const [file, setFile] = useState(draft?.file ?? "");
  const [sheets, setSheets] = useState<string[]>(draft?.sheets ?? []);
  const uploads = (ex?.files ?? []).filter((f) => f.path.startsWith("uploads/") && /\.(xlsx|csv)$/i.test(f.path));
  const choose = (body: Inputs["ChooseDraft"]) => {
    if (commented && !confirm("Reading a draft starts a new list from it; the current list and its comments are kept in history. Go on?")) return;
    chooseDraft.mutate(body, { onSuccess: (s) => setSheets(s.draft?.sheets ?? []) });
  };
  const groups = useMemo(() => [...new Set((draft?.drafts ?? []).map((d) => d.group ?? "–"))], [draft]);
  const tabsChanged = !!draft && !same([...sheets].sort(), [...draft.sheets].sort());
  return (
    <Card title="1 · The client's draft" actions={draft && <Badge tone="good">{draft.drafts.length} rows read</Badge>}>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Draft file">
          <Select value={file} onChange={(e) => setFile(e.target.value)} className="min-w-72">
            <option value="">Choose an uploaded file…</option>
            {uploads.map((f) => <option key={f.path} value={f.path}>{f.path.replace("uploads/", "")}</option>)}
          </Select>
        </Field>
        <UploadButton exerciseId={exerciseId} label="Upload a draft…" accept=".xlsx,.csv" onUploaded={(f) => setFile(f.path)} />
        <Button disabled={!file} busy={chooseDraft.isPending} onClick={() => choose({ file })}>Read the draft</Button>
      </div>
      {draft && draft.file === file && draft.tabs.length > 1 && (
        <TabPicker tabs={draft.tabs} chosen={sheets} onChange={setSheets}
                   hint="Tick every tab with part of the list: they are read as one draft. None ticked: the first tab with a list."
                   action={<Button variant={tabsChanged ? "primary" : "ghost"} disabled={!tabsChanged} busy={chooseDraft.isPending}
                                   onClick={() => choose({ file: draft.file, sheets })}>Read these tabs</Button>} />
      )}
      <div className="mt-3"><ErrorNotice error={chooseDraft.error} /></div>
      {draft && (
        <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">
          {draft.file.replace("uploads/", "")}{draft.sheets.length > 0 && ` / ${draft.sheets.join(" + ")}`}:
          {" "}{draft.drafts.length} categories in {groups.length} groups. The list below starts from it, exactly as written.
        </p>
      )}
    </Card>
  );
}

/** Tabs of a workbook as checkboxes (hidden tabs are never offered). */
function TabPicker({ tabs, chosen, onChange, hint, action }: {
  tabs: string[]; chosen: string[]; onChange: (tabs: string[]) => void; hint: string; action?: ReactNode;
}) {
  return (
    <div className="mt-3 rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="mb-2 text-xs text-zinc-500">{hint}</div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        {tabs.map((t) => (
          <Check key={t} label={t} checked={chosen.includes(t)}
                 onChange={(e) => onChange(e.target.checked ? tabs.filter((x) => x === t || chosen.includes(x)) : chosen.filter((x) => x !== t))} />
        ))}
        {action}
      </div>
    </div>
  );
}

// --- 2. the list and its comments --------------------------------------------------------------

function ListStep({ exerciseId, state }: { exerciseId: string; state: Schemas["ListState"] }) {
  const { categories } = useListActions(exerciseId);
  const pending = state.pending;
  const marks = pending?.state === "ready" && pending.list_version === state.version
    ? pending.changes.filter((c) => c.ticked) : [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">2 · The category list</h2>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-zinc-500">
            {state.rows.length} categories · {grouped(state.rows).length} groups · version {state.version}
          </span>
          <a className={linkButton()} href={links.listForClient(exerciseId)}>For the client (.xlsx)</a>
          <Button variant="secondary" busy={categories.isPending} onClick={() => categories.mutate({ version: `v${state.version}` })}>
            Make it the categories table
          </Button>
        </div>
      </div>
      {categories.data && (
        <Notice tone="good" title={`The categories table now has ${categories.data.count} categories`}>
          {categories.data.kept.length > 0 && <>Edits the team made on the table were kept: {categories.data.kept.join("; ")}. </>}
          Next: <Link to="/exercises/$exerciseId/categories" params={{ exerciseId }} className="underline">the categories table</Link>.
        </Notice>
      )}
      <ErrorNotice error={categories.error} />
      <BriefBox exerciseId={exerciseId} />
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_400px]">
        <div className="min-w-0 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <ListDocument rows={state.rows} marks={marks} />
        </div>
        <div className="xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:overflow-auto">
          <CommentMargin exerciseId={exerciseId} state={state} />
        </div>
      </div>
      <CheckPanel exerciseId={exerciseId} version={state.version} />
    </div>
  );
}
