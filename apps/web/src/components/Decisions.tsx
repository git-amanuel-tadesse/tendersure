/**
 * The Decisions of a run, laid out for a person (spec 10): triage tabs first,
 * one sheet at a time when wanted, bulk answers for what is on screen, calm
 * cards for what needs nothing, and the Save / Apply bar always in reach.
 * Answers are written in the QA workbook's grammar (lib/answers.ts).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  aAnswer, kindOf, type Marks, marksAnswer, numberOf, parseAAnswer, parseMarks, parseQAnswer, qAnswer, rulingOptions,
  teamOptions, total,
} from "../lib/answers";
import { type Item, keys, type Run, useCategories, useGenerateActions, useRuns } from "../lib/api";
import {
  aiNotes, asProposed, benchChoice, benchDecided, benchRows, failed, inTab, isBench, matches, plainStatus, quiet, sheetsOf,
  type Tab, TAB_LABEL, TABS,
} from "../lib/triage";
import { JobPanel } from "./JobPanel";
import { Badge, Button, Card, Check, cx, Empty, ErrorNotice, Field, Input, Notice, Select } from "./ui";

const KIND_LABEL: Record<string, string> = {
  question: "Questions copied into these sheets", add: "Questions proposed for addition", marks: "Marks per section",
  ruling: "Where the client's sheets disagree", team: "Decided once for the batch", check: "To fix by hand",
};
const KIND_ORDER = ["question", "add", "marks", "ruling", "team", "check"];

export function Decisions({ exerciseId, run, onApplied }: { exerciseId: string; run: Run; onApplied: (n: number) => void }) {
  const { answer, apply } = useGenerateActions(exerciseId);
  const items = run.result!.items;
  const failedSheets = run.result!.failed;
  const cats = useCategories(exerciseId).data;
  const titles = useMemo(() => Object.fromEntries((cats?.categories ?? []).map((c) => [String(c.number), c.description])), [cats]);
  const saved = useMemo(() => Object.fromEntries(items.map((i) => [i.id, run.answers[i.id] ?? i.answer ?? ""])), [items, run.answers]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [tab, setTab] = useState<Tab | null>(null);
  const [sheet, setSheet] = useState("");
  const [query, setQuery] = useState("");
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const [focus, setFocus] = useState(0);
  useEffect(() => { setDraft({}); setTab(null); setSheet(""); setOpened({}); }, [run.number]);
  const value = (id: string) => draft[id] ?? saved[id] ?? "";
  const changed = (id: string) => draft[id] !== undefined && draft[id] !== saved[id];
  const dirty = Object.keys(draft).filter(changed);
  const set = (id: string, v: string) => setDraft((d) => ({ ...d, [id]: v }));
  // a typed split that is not 100 is refused when applied: say so before
  const offTotal = items.filter((i) => {
    const v = value(i.id).trim();
    return kindOf(i) === "marks" && v && v.toLowerCase() !== "client" && Math.abs(total(parseMarks(v)) - 100) > 1e-9;
  }).map((i) => i.id);

  const counts = useMemo(() => Object.fromEntries(TABS.map((t) => [t, items.filter((i) => inTab(t, i, value(i.id), changed(i.id))).length])),
    [items, draft, saved]) as Record<Tab, number>;
  const current: Tab = tab ?? (TABS.find((t) => t !== "changed" && counts[t] > 0) ?? "all");
  const shown = items.filter((i) => inTab(current, i, value(i.id), changed(i.id)) && (!sheet || i.codes.includes(sheet)) && matches(i, query));
  const sheets = useMemo(() => sheetsOf(items), [items]);
  const title = (code: string) => titles[numberOf(code)] ?? "";

  // As soon as the new run exists, show it (its progress, then its result): staying on this run
  // while the next one builds looked as if nothing had happened.
  const runs = useRuns(exerciseId).data?.runs ?? [];
  const started = apply.data?.id ? runs.find((r) => r.job_id === apply.data!.id) : undefined;
  useEffect(() => { if (started) onApplied(started.number); }, [started?.number]);
  const save = () => answer.mutateAsync({ n: run.number, answers: Object.fromEntries(dirty.map((id) => [id, draft[id]])) })
    .then(() => setDraft({}));

  // keyboard: j / k between the cards shown, o opens or closes one (not while typing)
  const cards = useRef<(HTMLElement | null)[]>([]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key !== "j" && e.key !== "k" && e.key !== "o") return;
      e.preventDefault();
      if (e.key === "o") { const id = shown[focus]?.id; if (id) setOpened((o) => ({ ...o, [id]: !o[id] })); return; }
      const next = Math.max(0, Math.min(shown.length - 1, focus + (e.key === "j" ? 1 : -1)));
      setFocus(next);
      cards.current[next]?.scrollIntoView({ block: "center", behavior: "smooth" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shown, focus]);
  useEffect(() => setFocus(0), [current, sheet, query]);

  const benchShown = shown.filter(isBench);
  const benchTodo = items.filter((i) => isBench(i) && !benchDecided(i) || isBench(i) && changed(i.id)).length;
  const bulk = (patch: (i: Item) => string | undefined) =>
    setDraft((d) => { const n = { ...d }; for (const i of shown) { const v = patch(i); if (v !== undefined) n[i.id] = v; } return n; });
  const sheetIndex = sheets.indexOf(sheet);
  const why = sheet ? failedSheets.find((f) => f.code === sheet)?.reason : undefined;
  const groups = KIND_ORDER.map((k) => [k, shown.filter((i) => kindOf(i) === k)] as const).filter(([, xs]) => xs.length);
  let n = -1;

  return (
    <Card title={`Decisions · ${items.length}`}>
      <div className="sticky top-0 z-10 -mx-4 mb-4 space-y-3 border-b border-zinc-200 bg-white/95 px-4 pb-3 pt-1 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/95">
        <div className="flex flex-wrap items-center gap-2">
          <nav className="flex flex-wrap gap-1" aria-label="Which decisions">
            {TABS.map((t) => (
              <button key={t} onClick={() => setTab(t)}
                      className={cx("rounded-md px-2.5 py-1 text-sm ring-1", current === t
                        ? "bg-zinc-900 text-white ring-zinc-900 dark:bg-zinc-100 dark:text-zinc-900"
                        : "text-zinc-600 ring-zinc-200 hover:bg-zinc-50 dark:text-zinc-300 dark:ring-zinc-700 dark:hover:bg-zinc-900")}>
                {TAB_LABEL[t]}{" "}
                <span className={cx("ml-1 tabular-nums", current === t ? "opacity-80" : "text-zinc-400")}>
                  {t === "bench" && benchTodo < counts.bench ? `${benchTodo} to decide · ${counts.bench}` : counts[t]}
                </span>
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            {offTotal.length > 0 && (
              <span className="text-xs text-red-600" title={offTotal.join(", ")}>
                {offTotal.length} marks answer{offTotal.length === 1 ? "" : "s"} not at 100: {offTotal.length === 1 ? "it" : "they"} will not apply
              </span>
            )}
            {dirty.length > 0 && <Button variant="ghost" onClick={() => setDraft({})}>Undo {dirty.length}</Button>}
            <Button variant="secondary" disabled={!dirty.length} busy={answer.isPending} onClick={save}>
              Save {dirty.length || ""} answer{dirty.length === 1 ? "" : "s"}
            </Button>
            <Button busy={apply.isPending || (!!apply.data && !started)} disabled={!dirty.length && !Object.keys(run.answers).length}
                    title={!dirty.length && !Object.keys(run.answers).length ? "nothing new to apply: answer or change a decision first" : undefined}
                    onClick={async () => { if (dirty.length) await save(); apply.mutate(run.number); }}>
              {!dirty.length && !Object.keys(run.answers).length ? "Nothing new to apply"
                : `Apply answers → run ${Math.max(run.number, ...runs.map((x) => x.number)) + 1}`}
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Input className="w-64" placeholder="Search questions, reasons…" value={query} onChange={(e) => setQuery(e.target.value)} />
          <Select value={sheet} onChange={(e) => setSheet(e.target.value)} aria-label="Sheet" className="max-w-md">
            <option value="">All sheets ({sheets.length})</option>
            {sheets.map((c) => <option key={c} value={c}>{numberOf(c).padStart(3, "0")} · {title(c) || c}</option>)}
          </Select>
          {sheet && (
            <>
              <Button variant="ghost" disabled={sheetIndex <= 0} onClick={() => setSheet(sheets[sheetIndex - 1])}>‹ prev</Button>
              <Button variant="ghost" disabled={sheetIndex >= sheets.length - 1} onClick={() => setSheet(sheets[sheetIndex + 1])}>next ›</Button>
            </>
          )}
          <span className="text-xs text-zinc-500">{shown.length} shown · j / k to move, o to open</span>
        </div>
        {shown.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-zinc-500">For the {shown.length} shown:</span>
            {benchShown.length > 0 && (
              <>
                <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => bulk((i) => (isBench(i) ? "client" : undefined))}>
                  Take the client's split ({benchShown.length})
                </Button>
                <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => bulk((i) => (isBench(i) ? asProposed(i) : undefined))}>
                  Keep ours ({benchShown.length})
                </Button>
              </>
            )}
            <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => bulk((i) => (i.type === "check" ? undefined : asProposed(i)))}>
              Accept as proposed
            </Button>
            {shown.some((i) => changed(i.id)) && (
              <Button variant="ghost" className="px-2 py-1 text-xs"
                      onClick={() => setDraft((d) => Object.fromEntries(Object.entries(d).filter(([id]) => !shown.some((i) => i.id === id))))}>
                Undo my changes here
              </Button>
            )}
          </div>
        )}
      </div>

      <div className="space-y-3">
        <ErrorNotice error={answer.error ?? apply.error} />
        {apply.data && <JobPanel jobId={apply.data.id} refresh={[keys.runs(exerciseId)]} />}
        {sheet && (
          <div className="rounded-md border border-zinc-200 p-3 text-sm dark:border-zinc-800">
            <div className="font-medium">{numberOf(sheet).padStart(3, "0")} · {title(sheet) || sheet}</div>
            {why ? <div className="mt-1 text-red-700 dark:text-red-300">NOT WRITTEN: {why}</div>
                 : <div className="mt-1 text-xs text-zinc-500">written in this run</div>}
          </div>
        )}
      </div>

      {!shown.length && (
        <div className="mt-4"><Empty>{current === "needs" ? "Nothing needs you here. Look at “AI disagreed” or “Marks vs client”, or apply."
                                                           : "Nothing in this view."}</Empty></div>
      )}
      <div className="mt-4 space-y-8">
        {groups.map(([kind, xs]) => (
          <section key={kind}>
            <h3 className="mb-2 text-sm font-semibold">{KIND_LABEL[kind]} <span className="text-zinc-400">({xs.length})</span></h3>
            <div className="space-y-2">
              {xs.map((it) => {
                n += 1;
                const i = n;
                const isOpen = opened[it.id] ?? !quiet(it, value(it.id));
                return (
                  <div key={it.id} ref={(el) => { cards.current[i] = el; }} className={cx(focus === i && "rounded-md ring-2 ring-sky-400")}>
                    {isOpen
                      ? <ItemCard item={it} value={value(it.id)} changed={changed(it.id)} onChange={(v) => set(it.id, v)}
                                  onClose={quiet(it, value(it.id)) ? () => setOpened((o) => ({ ...o, [it.id]: false })) : undefined} />
                      : <QuietRow item={it} value={value(it.id)} onOpen={() => setOpened((o) => ({ ...o, [it.id]: true }))} />}
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </Card>
  );
}

// --- cards ------------------------------------------------------------------------------------------

function StatusBadge({ status }: { status?: string }) {
  if (!status) return null;
  const tone = failed(status) ? "bad" : aiNotes(status) ? "warn" : "good";
  return <Badge tone={tone} title={status}>{plainStatus(status)}</Badge>;
}

function QuietRow({ item, value, onOpen }: { item: Item; value: string; onOpen: () => void }) {
  return (
    <button onClick={onOpen} className="flex w-full items-center gap-2 rounded-md border border-zinc-100 px-3 py-1.5 text-left text-xs hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900">
      {isBench(item) ? <Badge tone="good">{benchChoice(value)}</Badge> : <StatusBadge status={item.status} />}
      <span className="min-w-0 flex-1 truncate text-zinc-700 dark:text-zinc-300">{item.decide.split("\n")[0]}</span>
      <span className="shrink-0 text-zinc-400">{item.codes.length} sheet{item.codes.length === 1 ? "" : "s"}</span>
      <span className="shrink-0 font-mono text-zinc-500">{value || item.proposed}</span>
    </button>
  );
}

function ItemCard({ item, value, changed, onChange, onClose }: {
  item: Item; value: string; changed: boolean; onChange: (v: string) => void; onClose?: () => void;
}) {
  const kind = kindOf(item);
  const notes = aiNotes(item.status);
  return (
    <article className={cx("rounded-md border p-3", changed ? "border-amber-400 bg-amber-50/40 dark:bg-amber-950/10" : "border-zinc-200 dark:border-zinc-800")}>
      <header className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <Badge>{item.id}</Badge>
        <span className="text-zinc-500">sheets {item.codes.map(numberOf).join(", ")}</span>
        <StatusBadge status={item.status} />
        {onClose && <button className="ml-auto text-zinc-400 hover:text-zinc-600" onClick={onClose}>close</button>}
      </header>
      {notes && <Notice tone="warn" title="The AI review went against the proposal">{notes}</Notice>}
      {failed(item.status) && <Notice tone="bad">{item.status!.replace(/^AI: /, "")}</Notice>}
      <div className="mt-2 grid gap-3 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="space-y-1">
          <p className="text-sm whitespace-pre-wrap">{item.decide}</p>
          {!isBench(item) && <p className="text-xs"><span className="text-zinc-500">Proposed:</span> <b>{item.proposed}</b></p>}
          {item.why && !isBench(item) && <p className="text-xs text-zinc-500">{item.why}</p>}
        </div>
        <div className="space-y-2">
          {isBench(item) ? <BenchControl item={item} value={value} onChange={onChange} /> : (
            <>
              {kind === "question" && <QControl item={item} value={value} onChange={onChange} />}
              {kind === "add" && <AControl item={item} value={value} onChange={onChange} />}
              {kind === "marks" && <MControl item={item} value={value} onChange={onChange} />}
              {kind === "ruling" && <Choice options={["majority", ...rulingOptions(item.decide)]} value={value} onChange={onChange} />}
              {kind === "team" && <Choice options={teamOptions(item.id)} value={value} onChange={onChange} />}
            </>
          )}
          {kind === "check" ? (
            <p className="text-xs text-zinc-500">Nothing to answer here: fix it in the workbook, then run the check.</p>
          ) : (
            <Field label="Answer" hint="as in the QA workbook's Decisions tab; blank accepts the proposal">
              <Input className="font-mono text-xs" value={value} placeholder={isBench(item) ? "blank keeps ours" : item.proposed}
                     onChange={(e) => onChange(e.target.value)} />
            </Field>
          )}
        </div>
      </div>
    </article>
  );
}

/** Our split and the client's benchmark tab, side by side (spec 08 / 10). */
function BenchControl({ item, value, onChange }: { item: Item; value: string; onChange: (v: string) => void }) {
  const rows = benchRows(item);
  const takes = value.trim().toLowerCase() === "client";
  const [editing, setEditing] = useState(false);
  const custom = !!value && !takes;
  return (
    <div className="space-y-2">
      <table className="w-full text-xs tabular-nums">
        <thead className="text-zinc-500"><tr><th className="text-left font-normal">section</th><th className="text-right font-normal">ours</th>
          <th className="text-right font-normal">client's</th><th className="text-right font-normal">diff</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.section} className={cx(r.ours !== r.client && "font-medium")}>
              <td>{r.section}</td><td className="text-right">{r.ours}</td><td className="text-right">{r.client}</td>
              <td className={cx("text-right", r.ours > r.client ? "text-amber-700" : r.ours < r.client ? "text-sky-700" : "text-zinc-400")}>
                {r.ours === r.client ? "·" : `${r.ours > r.client ? "+" : ""}${r.ours - r.client}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex flex-wrap gap-1.5">
        <Button variant={!value || value === asProposed(item) ? "primary" : "secondary"} className="px-2 py-1 text-xs"
                onClick={() => { setEditing(false); onChange(asProposed(item)); }}>Keep ours</Button>
        <Button variant={takes ? "primary" : "secondary"} className="px-2 py-1 text-xs" onClick={() => { setEditing(false); onChange("client"); }}>Take client's</Button>
        <Button variant={custom || editing ? "primary" : "secondary"} className="px-2 py-1 text-xs" onClick={() => setEditing((e) => !e)}>Edit</Button>
      </div>
      {(editing || custom) && <MControl item={item} value={custom ? value : ""} onChange={onChange} />}
    </div>
  );
}

function Choice({ options, value, onChange }: { options: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <Button variant={!value ? "primary" : "secondary"} className="px-2 py-1 text-xs" onClick={() => onChange("")}>as proposed</Button>
      {options.map((o) => (
        <Button key={o} variant={value.toLowerCase() === o.toLowerCase() ? "primary" : "secondary"} className="px-2 py-1 text-xs"
                onClick={() => onChange(o)}>{o}</Button>
      ))}
    </div>
  );
}

function QControl({ item, value, onChange }: { item: Item; value: string; onChange: (v: string) => void }) {
  const wording = Object.fromEntries(((item.payload ?? []) as { code: string; text: string }[]).map((p) => [p.code, p.text]));
  const choice = value ? parseQAnswer(value, item.codes, wording) : null;
  if (value && !choice) return <p className="text-xs text-amber-700">Answer written by hand: edit it below.</p>;
  const c = choice ?? { remove: [], reword: {} };
  const set = (next: typeof c) => onChange(qAnswer(item.codes, next));
  const single = !item.decide.includes(" questions:\n");
  return (
    <div className="space-y-2">
      <div className="text-xs text-zinc-500">Keep the question in each sheet, or remove it:</div>
      <div className="flex flex-wrap gap-1.5">
        {item.codes.map((code) => {
          const removed = c.remove.includes(code);
          return (
            <button key={code} title={code} onClick={() => set({ ...c, remove: removed ? c.remove.filter((x) => x !== code) : [...c.remove, code] })}
                    className={cx("rounded px-2 py-0.5 text-xs ring-1", removed
                      ? "bg-red-50 text-red-700 line-through ring-red-300 dark:bg-red-950/30"
                      : "bg-white text-zinc-700 ring-zinc-300 dark:bg-zinc-900 dark:text-zinc-200 dark:ring-zinc-700")}>
              {numberOf(code)}
            </button>
          );
        })}
        <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => set({ remove: [...item.codes], reword: {} })}>remove all</Button>
        <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => onChange(asProposed(item))}>as proposed</Button>
      </div>
      {Object.keys(wording).length > 0 && (
        <div className="space-y-1 text-xs">
          {Object.entries(wording).filter(([code]) => !c.remove.includes(code)).map(([code, text]) => (
            <Check key={code} label={<span>{numberOf(code)}: use “{text}”</span>} checked={c.reword[code] === true}
                   onChange={(e) => {
                     const { [code]: _, ...rest } = c.reword;
                     set({ ...c, reword: e.target.checked ? { ...rest, [code]: true } : rest });
                   }} />
          ))}
        </div>
      )}
      {single && c.remove.length < item.codes.length && (
        <details className="text-xs">
          <summary className="cursor-pointer text-zinc-500">Own wording for one sheet…</summary>
          <OwnWording codes={item.codes.filter((x) => !c.remove.includes(x))} onSet={(code, text) => set({ ...c, reword: { ...c.reword, [code]: text } })} />
        </details>
      )}
    </div>
  );
}

function OwnWording({ codes, onSet }: { codes: string[]; onSet: (code: string, text: string) => void }) {
  const [code, setCode] = useState(codes[0] ?? "");
  const [text, setText] = useState("");
  return (
    <div className="mt-1 flex gap-1">
      <Select value={code} onChange={(e) => setCode(e.target.value)}>{codes.map((c) => <option key={c} value={c}>{numberOf(c)}</option>)}</Select>
      <Input className="flex-1" value={text} placeholder="the new wording" onChange={(e) => setText(e.target.value)} />
      <Button variant="secondary" className="px-2 text-xs" disabled={!text.trim()} onClick={() => onSet(code, text)}>Set</Button>
    </div>
  );
}

function AControl({ item, value, onChange }: { item: Item; value: string; onChange: (v: string) => void }) {
  const entries = (item.payload ?? []) as { section: string; text: string; kind: string }[];
  const picked = parseAAnswer(value, entries.length, item.proposed);
  if (!picked) return <p className="text-xs text-amber-700">Answer written by hand: edit it below.</p>;
  return (
    <div className="space-y-1 text-xs">
      {entries.map((e, i) => (
        <Check key={i} checked={picked.includes(i + 1)}
               label={<span><b>{i + 1}.</b> [{e.section}] {e.text} {e.kind === "draft" && <Badge tone="info">new</Badge>}</span>}
               onChange={(ev) => onChange(aAnswer(entries.length, ev.target.checked ? [...picked, i + 1] : picked.filter((n) => n !== i + 1)))} />
      ))}
      <p className="text-zinc-500">Added questions arrive with 0 marks: give their section marks on its marks line.</p>
    </div>
  );
}

function MControl({ item, value, onChange }: { item: Item; value: string; onChange: (v: string) => void }) {
  const marks: Marks = parseMarks(value || item.proposed);
  const sum = total(marks);
  const set = (i: number, v: number) => {
    const next = marks.map(([k, m], j) => [k, j === i ? v : m] as [string, number]);
    const text = marksAnswer(next);
    onChange(text === marksAnswer(parseMarks(item.proposed)) ? asProposed(item) : text);
  };
  return (
    <div className="space-y-1">
      {item.id.startsWith("M-FREED-") && (
        <Button variant={value === item.proposed ? "primary" : "secondary"} className="px-2 py-1 text-xs"
                onClick={() => onChange(item.proposed)}>Accept the proposed spread</Button>
      )}
      <div className="grid grid-cols-2 gap-1.5">
        {marks.map(([k, m], i) => (
          <label key={k} className="flex items-center justify-between gap-2 text-xs">
            <span className="truncate text-zinc-600 dark:text-zinc-400">{k}</span>
            <Input type="number" min={0} step={1} className="w-16 py-0.5 text-right" value={m}
                   onChange={(e) => set(i, Number(e.target.value))} />
          </label>
        ))}
      </div>
      <div className={cx("flex flex-wrap items-center gap-2 text-xs font-medium", sum === 100 ? "text-emerald-700" : "text-red-600")}>
        <span>
          total {+sum.toFixed(2)}
          {sum !== 100 && ` - ${Math.abs(+(100 - sum).toFixed(2))} mark${Math.abs(100 - sum) === 1 ? "" : "s"} ${sum < 100 ? "left to place" : "too many"}`}
        </span>
        {sum !== 100 && marks.length > 0 && (() => {
          const big = marks.reduce((b, m, i) => (m[1] > marks[b][1] ? i : b), 0);
          return (
            <Button variant="secondary" className="px-2 py-0.5 text-xs" onClick={() => set(big, +(marks[big][1] + 100 - sum).toFixed(2))}>
              {sum < 100 ? "put it on" : "take it from"} {marks[big][0]}
            </Button>
          );
        })()}
      </div>
    </div>
  );
}
