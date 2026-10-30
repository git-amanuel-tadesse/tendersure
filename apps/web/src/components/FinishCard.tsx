/** Spec 11: a run that needs nothing moves on - download, mark final, check an edited copy, add to the bank. */
import { fileUrl } from "@tendersure/api-client";
import { useState } from "react";
import { keys, type Run, useExercise, useFinishActions, useJob } from "../lib/api";
import { JobPanel } from "./JobPanel";
import { UploadButton } from "./UploadButton";
import { Badge, Button, Card, ErrorNotice, linkButton, Notice, Select } from "./ui";

type CheckOut = { errors: number; warnings: number; report: string; rules: Record<string, number> };
type BankOut = { added: number; replaced: number; unchanged: number; sheets_added: number; tiers: Record<string, number> };

export function FinishCard({ exerciseId, run, isFinal }: { exerciseId: string; run: Run; isFinal: boolean }) {
  const res = run.result!;
  const { final, check, bank } = useFinishActions(exerciseId);
  const files = (useExercise(exerciseId).data?.files ?? []).filter((f) => f.path.startsWith("uploads/") && f.path.endsWith(".xlsx"));
  const [edited, setEdited] = useState("");
  const checkJob = useJob(check.data?.id).data;
  const bankJob = useJob(bank.data?.id, [keys.bank]).data;
  const checked = checkJob?.status === "done" ? (checkJob.result as CheckOut) : null;
  const banked = bankJob?.status === "done" ? (bankJob.result as BankOut) : null;
  const book = res.files[0];
  return (
    <Card title={<span className="flex items-center gap-2">Ready to send {isFinal && <Badge tone="good">final</Badge>}</span>}>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        {res.written.length} sheets written · nothing needs you · no answer waiting. What comes next:
      </p>
      <ol className="mt-4 space-y-4 text-sm">
        <li>
          <div className="font-medium">1 · The workbook</div>
          <div className="mt-1 flex flex-wrap gap-2">
            {[book, res.qa].filter(Boolean).map((f) => <a key={f} className={linkButton()} href={fileUrl(exerciseId, f!)}>{f!.split("/").pop()}</a>)}
            <Button variant={isFinal ? "secondary" : "primary"} busy={final.isPending} disabled={isFinal} onClick={() => final.mutate(run.number)}>
              {isFinal ? "This run is final" : "Mark as final"}
            </Button>
          </div>
        </li>
        <li>
          <div className="font-medium">2 · Check the team's edited copy</div>
          <div className="text-xs text-zinc-500">Upload the workbook as edited in Excel: it is checked, and what changed since this run is listed.</div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <Select value={edited} onChange={(e) => setEdited(e.target.value)}>
              <option value="">this run's own workbook</option>
              {files.map((f) => <option key={f.path} value={f.path}>{f.path.replace("uploads/", "")}</option>)}
            </Select>
            <UploadButton exerciseId={exerciseId} label="Upload the edited copy…" accept=".xlsx" onUploaded={(f) => setEdited(f.path)} />
            <Button variant="secondary" busy={check.isPending} onClick={() => check.mutate({ n: run.number, file: edited })}>Check</Button>
          </div>
          {check.data && !checked && <div className="mt-2"><JobPanel jobId={check.data.id} compact /></div>}
          {checked && (
            <div className="mt-2">
              <Notice tone={checked.errors ? "bad" : "good"} title={checked.errors ? `${checked.errors} error(s): fix before sending` : "No errors"}>
                {checked.warnings} warning(s){Object.keys(checked.rules).length > 0 && ": " + Object.entries(checked.rules).slice(0, 6).map(([r, n]) => `${r} ${n}`).join(", ")}
                {" · "}<a className="underline" href={fileUrl(exerciseId, checked.report)}>the report</a>
              </Notice>
            </div>
          )}
        </li>
        <li>
          <div className="font-medium">3 · Add to the bank</div>
          <div className="text-xs text-zinc-500">
            The {edited ? "edited copy" : "run's workbook"} becomes this client's history: their next exercise starts from these sheets.
          </div>
          <div className="mt-1">
            <Button variant="secondary" busy={bank.isPending} onClick={() => bank.mutate({ n: run.number, file: edited })}>
              Add {edited ? "the edited copy" : "this run's workbook"} to the bank
            </Button>
          </div>
          {bank.data && !banked && <div className="mt-2"><JobPanel jobId={bank.data.id} compact /></div>}
          {banked && (
            <div className="mt-2">
              <Notice tone="good" title={banked.added || banked.replaced ? "Added to the bank" : "Already in the bank"}>
                {banked.sheets_added} sheet(s) added{banked.replaced ? ` · replaced the earlier copy` : ""}
                {Object.keys(banked.tiers).length > 0 && ` · ${Object.entries(banked.tiers).map(([t, n]) => `${n} ${t}`).join(", ")}`}
              </Notice>
            </div>
          )}
        </li>
      </ol>
      <div className="mt-3"><ErrorNotice error={final.error ?? check.error ?? bank.error} /></div>
    </Card>
  );
}
