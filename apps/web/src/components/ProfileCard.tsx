/** The client's profile (spec 16 B): the house rules asked once, before the first build, stored as client rules. */
import { useEffect, useState } from "react";
import { type ClientProfile, useProfile, useSaveProfile } from "../lib/api";
import { Badge, Button, Card, ErrorNotice, Input, Notice } from "./ui";

const TEXT: { key: keyof ClientProfile; label: string; hint: string }[] = [
  { key: "licence_term", label: "Licence term", hint: "business permit is written as… e.g. trade license" },
  { key: "purchase_order", label: "Purchase order term", hint: "LPO is written as… e.g. PO" },
  { key: "purchase_order_except", label: "…except in groups", hint: "e.g. engineering" },
  { key: "office_evidence", label: "Office evidence wording", hint: "e.g. Attach evidence of the office: rent agreement and utility bill" },
  { key: "reference_marks", label: "Reference marks (Ref 1, 2, 3)", hint: "e.g. 15, 15, 10 — empty keeps ours" },
  { key: "technical_limit", label: "Most technical questions", hint: "e.g. 3 — empty for no limit" },
];
const CHECKS: { key: "local_suppliers" | "no_conflict_of_interest"; label: string }[] = [
  { key: "local_suppliers", label: "Local suppliers only: no sort code, no IBAN" },
  { key: "no_conflict_of_interest", label: "No conflict-of-interest question" },
];

export function ProfileCard({ exerciseId }: { exerciseId: string }) {
  const data = useProfile(exerciseId).data;
  const save = useSaveProfile(exerciseId);
  const [p, setP] = useState<ClientProfile | null>(null);
  useEffect(() => setP(data?.profile ?? null), [data]);
  if (!data || !p) return null;
  const dirty = JSON.stringify(p) !== JSON.stringify(data.profile);
  const set = (patch: Partial<ClientProfile>) => setP({ ...p, ...patch });
  return (
    <Card
      title={<span className="flex items-center gap-2">Client profile <Badge>{data.client}</Badge>
        {data.saved ? <Badge tone="good">saved</Badge> : <Badge tone="warn">not set yet</Badge>}</span>}
      actions={<>
        {dirty && <Button variant="ghost" onClick={() => setP(data.profile)}>Undo</Button>}
        <Button disabled={!dirty && data.saved} busy={save.isPending} onClick={() => save.mutate(p)}>Save profile</Button>
      </>}
    >
      {!data.saved && (
        <div className="mb-3"><Notice tone="warn" title="Answer before the first build">
          The client's house rules, said once instead of learned from their comments.
          {data.country && ` Prefilled with ${data.country}'s defaults.`} Saved as client rules below.
        </Notice></div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {TEXT.map((f) => (
          <label key={f.key} className="text-sm">
            <span className="text-zinc-600 dark:text-zinc-400">{f.label}</span>
            <Input className="mt-1 w-full" value={String(p[f.key] ?? "")} placeholder={f.hint}
                   onChange={(e) => set({ [f.key]: e.target.value } as Partial<ClientProfile>)} />
          </label>
        ))}
        {CHECKS.map((f) => (
          <label key={f.key} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={Boolean(p[f.key])} onChange={(e) => set({ [f.key]: e.target.checked })} />
            {f.label}
          </label>
        ))}
      </div>
      <div className="mt-2"><ErrorNotice error={save.error} /></div>
    </Card>
  );
}
