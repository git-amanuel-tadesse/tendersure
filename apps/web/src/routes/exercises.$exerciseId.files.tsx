import { fileUrl } from "@tendersure/api-client";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Card, Check, Empty } from "../components/ui";
import { UploadButton } from "../components/UploadButton";
import { useExercise } from "../lib/api";

export const Route = createFileRoute("/exercises/$exerciseId/files")({ component: Files });

const size = (n: number) => (n < 1024 ? `${n} B` : n < 1 << 20 ? `${(n / 1024).toFixed(0)} KB` : `${(n / (1 << 20)).toFixed(1)} MB`);

function Files() {
  const { exerciseId } = Route.useParams();
  const ex = useExercise(exerciseId).data;
  const [state, setState] = useState(false);
  const files = (ex?.files ?? []).filter((f) => state || !f.path.endsWith(".json"));
  return (
    <Card title="Files of this exercise" actions={<>
      <Check label="Show the app's own state (.json)" checked={state} onChange={(e) => setState(e.target.checked)} />
      <UploadButton exerciseId={exerciseId} label="Upload a file…" />
    </>}>
      {!files.length && <Empty>No file yet. Uploads go to uploads/; what the steps write appears here too.</Empty>}
      <ul className="divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
        {files.map((f) => (
          <li key={f.path} className="flex items-center justify-between gap-4 py-2">
            <a className="text-accent-700 hover:underline" href={fileUrl(exerciseId, f.path)}>{f.path}</a>
            <span className="text-xs text-zinc-500 tabular-nums">{size(f.size)} · {f.modified.replace("T", " ")}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
