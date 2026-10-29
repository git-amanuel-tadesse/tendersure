import { links } from "@tendersure/api-client";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { JobPanel } from "../components/JobPanel";
import { Button, Card, ErrorNotice, linkButton, Notice, PageTitle, Stat } from "../components/ui";
import { keys, useBank, useIngest, useJob, useLatestJob } from "../lib/api";

export const Route = createFileRoute("/bank")({ component: BankPage });

const pick = (list: FileList | null) => [...(list ?? [])].filter((f) => f.name.toLowerCase().endsWith(".xlsx") && !f.name.startsWith("~$"));

function BankPage() {
  const bank = useBank();
  const ingest = useIngest();
  const latest = useLatestJob(undefined, "ingest");
  const [files, setFiles] = useState<File[]>([]);
  const jobId = ingest.data?.id ?? latest.data?.id;
  const job = useJob(jobId, [keys.bank]).data;
  const result = job?.status === "done" ? (job.result as Record<string, number> | null) : null;
  return (
    <>
      <PageTitle title="Bank" subtitle="The history every build learns from: past criteria workbooks of every client. It stays on this machine." />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-6">
          <Card title="Add workbooks">
            <div className="space-y-3 text-sm">
              <p className="text-zinc-600 dark:text-zinc-400">
                Pick a folder (subfolders, e.g. one per year, are read too) or single workbooks. A workbook added again under the
                same path replaces its earlier version.
              </p>
              <div className="flex flex-wrap gap-3">
                <label className={linkButton()}>
                  Choose a folder…
                  {/* @ts-expect-error webkitdirectory is not in React's types */}
                  <input type="file" className="sr-only" webkitdirectory="" multiple onChange={(e) => setFiles(pick(e.target.files))} />
                </label>
                <label className={linkButton()}>
                  Choose workbooks…
                  <input type="file" className="sr-only" accept=".xlsx" multiple onChange={(e) => setFiles(pick(e.target.files))} />
                </label>
              </div>
              {files.length > 0 && <p>{files.length} workbook(s) chosen{files.length <= 6 && `: ${files.map((f) => f.name).join(", ")}`}</p>}
              <ErrorNotice error={ingest.error} />
              <Button disabled={!files.length} busy={ingest.isPending} onClick={() => ingest.mutate(files)}>Add to the bank</Button>
            </div>
          </Card>
          {jobId && (
            <Card title="Last ingest">
              <JobPanel jobId={jobId} refresh={[keys.bank]} />
              {result && (
                <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Stat label="Added" value={result.added} />
                  <Stat label="Unchanged" value={result.unchanged} />
                  <Stat label="Replaced" value={result.replaced} />
                  <Stat label="Conflicts" value={result.conflicts} />
                </div>
              )}
            </Card>
          )}
        </div>
        <div className="space-y-6">
          <Card title="In the bank">
            {bank.data?.exists ? (
              <div className="space-y-4">
                <div className="grid grid-cols-3 gap-3">
                  <Stat label="Workbooks" value={bank.data.workbooks} />
                  <Stat label="Sheets" value={bank.data.sheets} />
                  <Stat label="Questions" value={bank.data.questions.toLocaleString()} />
                </div>
                <div className="text-sm">Clients: {bank.data.clients.join(", ") || "–"}</div>
                <div className="text-xs text-zinc-500">Last added {bank.data.last_ingest ?? "–"}</div>
              </div>
            ) : (
              <Notice tone="warn">Empty: add workbooks to start.</Notice>
            )}
          </Card>
          <Card title="Reports">
            <div className="flex flex-col gap-2 text-sm">
              <a className="text-accent-700 hover:underline" href={links.bankReport("validation_report.xlsx")}>validation_report.xlsx</a>
              <a className="text-accent-700 hover:underline" href={links.bankReport("bank.xlsx")}>bank.xlsx</a>
              <span className="text-xs text-zinc-500">Written by the last ingest.</span>
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
