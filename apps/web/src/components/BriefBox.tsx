/** The client's brief for the exercise (spec 07 B): saved when the box is left, read first by every model step. */
import { useEffect, useState } from "react";
import { useBrief, useSetBrief } from "../lib/api";
import { ErrorNotice, Field, Textarea } from "./ui";

export function BriefBox({ exerciseId }: { exerciseId: string }) {
  const saved = useBrief(exerciseId).data?.text ?? "";
  const save = useSetBrief(exerciseId);
  const [text, setText] = useState(saved);
  useEffect(() => setText(saved), [saved]);
  const changed = text.trim() !== saved;
  return (
    <div className="space-y-2">
      <Field
        label="Client's brief"
        hint={save.isPending ? "saving…" : changed ? "not saved yet: leave the box to save"
          : "read first by the model, above past questionnaires; the template rules still apply. Not used without a model."}
      >
        <Textarea
          rows={3} className="w-full" value={text} maxLength={20000}
          placeholder="What the client is looking for, e.g. works contractors with their own dump truck and a site engineer with 5 years' experience"
          onChange={(e) => setText(e.target.value)}
          onBlur={() => changed && save.mutate(text)}
        />
      </Field>
      <ErrorNotice error={save.error} />
    </div>
  );
}
