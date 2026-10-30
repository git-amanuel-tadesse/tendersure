/** The client's rules (spec 12 C): said once, applied to every sheet on every run. */
import { useEffect, useState } from "react";
import { type ClientRule, useClientRules, useSaveClientRules } from "../lib/api";
import { Badge, Button, Card, cx, ErrorNotice, Input, Select } from "./ui";

const ACTIONS: Record<string, { match: string; value: string }> = {
  remove: { match: "questions containing…", value: "" },
  replace: { match: "the term, e.g. LPO", value: "with, e.g. PO" },
  reword: { match: "questions containing…", value: "the new wording" },
  limit: { match: "section, e.g. technical", value: "at most, e.g. 3" },
  marks: { match: "sub-section, e.g. Ref 1", value: "marks, e.g. 15" },
};
const blank: ClientRule = { action: "remove", match: "", value: "", groups: "", note: "", problem: "" };

export function ClientRulesCard({ exerciseId }: { exerciseId: string }) {
  const data = useClientRules(exerciseId).data;
  const save = useSaveClientRules(exerciseId);
  const [rules, setRules] = useState<ClientRule[]>([]);
  useEffect(() => setRules(data?.rules ?? []), [data]);
  const dirty = JSON.stringify(rules) !== JSON.stringify(data?.rules ?? []);
  const set = (i: number, patch: Partial<ClientRule>) => setRules((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch, problem: "" } : r)));
  return (
    <Card
      title={<span className="flex items-center gap-2">Client rules {data?.client && <Badge>{data.client}</Badge>} <Badge>{rules.length}</Badge></span>}
      actions={
        <>
          <Button variant="secondary" onClick={() => setRules((rs) => [...rs, { ...blank }])}>Add a rule</Button>
          {dirty && <Button variant="ghost" onClick={() => setRules(data?.rules ?? [])}>Undo</Button>}
          <Button disabled={!dirty} busy={save.isPending} onClick={() => save.mutate(rules)}>Save rules</Button>
        </>
      }
    >
      <p className="mb-3 text-sm text-zinc-500">
        What this client wants on every sheet, said once: applied to every run from the next Build or Apply. Groups: words of the group
        names, “!engineering” for every group but engineering, empty for all.
      </p>
      {rules.length === 0 ? <p className="text-sm text-zinc-500">No rules yet.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-zinc-500">
              <tr><th className="py-1 font-normal">Action</th><th className="font-normal">Match</th><th className="font-normal">Value</th>
                <th className="font-normal">Groups</th><th className="font-normal">Why</th><th /></tr>
            </thead>
            <tbody>
              {rules.map((r, i) => (
                <tr key={i} className="align-top">
                  <td className="py-1 pr-2">
                    <Select value={r.action} onChange={(e) => set(i, { action: e.target.value })} aria-label="Action">
                      {Object.keys(ACTIONS).map((a) => <option key={a}>{a}</option>)}
                    </Select>
                  </td>
                  <td className="pr-2"><Input className="w-56" value={r.match} placeholder={ACTIONS[r.action]?.match} onChange={(e) => set(i, { match: e.target.value })} /></td>
                  <td className="pr-2">
                    <Input className={cx("w-56", !ACTIONS[r.action]?.value && "opacity-40")} disabled={!ACTIONS[r.action]?.value}
                           value={r.value} placeholder={ACTIONS[r.action]?.value} onChange={(e) => set(i, { value: e.target.value })} />
                  </td>
                  <td className="pr-2"><Input className="w-40" value={r.groups} placeholder="all" onChange={(e) => set(i, { groups: e.target.value })} /></td>
                  <td className="pr-2"><Input className="w-56" value={r.note} placeholder="the client's comment" onChange={(e) => set(i, { note: e.target.value })} /></td>
                  <td>
                    <Button variant="ghost" aria-label="Remove rule" onClick={() => setRules((rs) => rs.filter((_, j) => j !== i))}>✕</Button>
                    {r.problem && <div className="text-xs text-red-600">{r.problem}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-2"><ErrorNotice error={save.error} /></div>
    </Card>
  );
}
