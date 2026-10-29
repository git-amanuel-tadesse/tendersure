import type { Schemas } from "@tendersure/api-client";
import { useUpload } from "../lib/api";
import { ErrorNotice, linkButton, Spinner } from "./ui";

/** Upload one file into the exercise's uploads/; `onUploaded` gets where it landed. */
export function UploadButton({ exerciseId, label, accept = ".xlsx,.csv,.pdf", onUploaded }: {
  exerciseId: string; label: string; accept?: string; onUploaded?: (f: Schemas["StoredFile"]) => void;
}) {
  const upload = useUpload(exerciseId);
  return (
    <span className="inline-flex flex-col gap-1">
      <label className={linkButton()}>
        {upload.isPending && <Spinner />}
        {label}
        <input
          type="file"
          className="sr-only"
          accept={accept}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate(f, { onSuccess: (stored) => onUploaded?.(stored) });
            e.target.value = "";
          }}
        />
      </label>
      <ErrorNotice error={upload.error} />
    </span>
  );
}
