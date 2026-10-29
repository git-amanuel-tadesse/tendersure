/** A job's status and its lines as they come (the lines the CLI prints). */
import type { QueryKey } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { isRunning, useJob } from "../lib/api";
import { Badge, Notice, Spinner } from "./ui";

const tone = { queued: "neutral", running: "info", done: "good", failed: "bad", interrupted: "warn" } as const;

export function JobPanel({ jobId, refresh, compact }: { jobId?: string; refresh?: QueryKey[]; compact?: boolean }) {
  const { data: job } = useJob(jobId, refresh);
  const box = useRef<HTMLPreElement>(null);
  const n = job?.lines.length ?? 0;
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [n]);
  if (!jobId || !job) return null;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-sm">
        {isRunning(job) && <Spinner className="text-sky-600" />}
        <span className="font-medium">{job.title}</span>
        <Badge tone={tone[job.status as keyof typeof tone] ?? "neutral"}>{job.status}</Badge>
        {job.finished && <span className="text-xs text-zinc-500">{job.finished.replace("T", " ")}</span>}
      </div>
      {job.error && <Notice tone={job.status === "interrupted" ? "warn" : "bad"}>{job.error}</Notice>}
      {!compact && n > 0 && (
        <pre
          ref={box}
          className="max-h-64 overflow-auto rounded-md bg-zinc-950 p-3 font-mono text-xs leading-relaxed break-all whitespace-pre-wrap text-zinc-200"
        >
          {job.lines.join("\n")}
        </pre>
      )}
    </div>
  );
}
