/** The margin beside the list (spec 21 B, D): write a comment; the one waiting, with its instructions and
 * changes to tick, Apply or Reject; the decided comments, newest first, the latest applied one undoable. */
import { useEffect, useState } from "react";
import { Badge, Button, cx, ErrorNotice, Input, Spinner, Textarea } from "../ui";
import { type ListChange, type ListComment, type ListState, useJob, useListActions, keys } from "../../lib/api";
import { ChangeList } from "./ChangeList";

const AUTHOR = "tendersure-author";

const DECIDED: Record<string, "good" | "neutral" | "warn"> = { applied: "good", rejected: "neutral", undone: "warn" };

function Waiting({ exerciseId, state, pending }: { exerciseId: string; state: ListState; pending: ListComment }) {
  const { decide, reread } = useListActions(exerciseId);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  useEffect(() => {
    setTicked(new Set(pending.changes.filter((c) => c.ticked).map((c) => c.id)));
  }, [pending.id, pending.state, pending.changes]);
  useJob(pending.job || undefined, [keys.list(exerciseId)]);
  const stale = pending.state === "ready" && pending.list_version !== state.version;
  const toggle = (id: string) => setTicked((t) => { const n = new Set(t); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const count = pending.changes.filter((c) => ticked.has(c.id)).length;
  return (
    <div className="rounded-lg border border-amber-400 bg-amber-50 p-3 text-sm dark:bg-amber-950/40">
      <div className="flex items-center justify-between text-xs text-zinc-500">
        <span><b className="text-zinc-700 dark:text-zinc-200">{pending.author}</b> · {pending.at.slice(11, 16)}</span>
        <Badge tone="warn">{pending.state === "reading" ? "being read" : pending.state === "failed" ? "failed" : "waiting"}</Badge>
      </div>
      <p className="mt-1 whitespace-pre-wrap">{pending.text}</p>
      {pending.state === "reading" && (
        <div className="mt-2 flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
          <Spinner /> The AI is reading it against the list, the draft and the approved lists…
        </div>
      )}
      {pending.state !== "reading" && pending.note && <p className="mt-2 text-xs italic text-zinc-600 dark:text-zinc-400">{pending.note}</p>}
      {pending.state === "ready" && (
        <div className="mt-2">
          <ChangeList comment={pending} rows={state.rows} ticked={(c: ListChange) => ticked.has(c.id)} onToggle={toggle} />
        </div>
      )}
      {stale && (
        <p className="mt-2 rounded bg-sky-100 px-2 py-1 text-xs text-sky-900 dark:bg-sky-950 dark:text-sky-200">
          The list changed since this comment was read. Read it again before applying.
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {pending.state === "ready" && !stale && (
          <Button busy={decide.isPending} disabled={!count}
                  onClick={() => decide.mutate({ id: pending.id, apply: true, ticked: [...ticked] })}>
            Apply {count} change{count === 1 ? "" : "s"}
          </Button>
        )}
        {(stale || pending.state === "failed") && (
          <Button busy={reread.isPending} onClick={() => reread.mutate(pending.id)}>Read it again</Button>
        )}
        {pending.state !== "reading" && (
          <Button variant="ghost" busy={decide.isPending} onClick={() => decide.mutate({ id: pending.id, apply: false, ticked: [] })}>
            Reject
          </Button>
        )}
      </div>
      <div className="mt-2"><ErrorNotice error={decide.error ?? reread.error} /></div>
    </div>
  );
}

function Decided({ exerciseId, state, c }: { exerciseId: string; state: ListState; c: ListComment }) {
  const { undo } = useListActions(exerciseId);
  const latest = c.state === "applied" && c.version_after === state.version;
  const applied = c.changes.filter((x) => x.ticked).length;
  return (
    <details className={cx("rounded-lg border border-zinc-200 bg-white p-3 text-sm dark:border-zinc-800 dark:bg-zinc-900",
                           c.state !== "applied" && "opacity-70")}>
      <summary className="cursor-pointer list-none">
        <div className="flex items-center justify-between text-xs text-zinc-500">
          <span><b className="text-zinc-700 dark:text-zinc-200">{c.author}</b> · {c.at.slice(5, 16).replace("T", " ")}</span>
          <Badge tone={DECIDED[c.state] ?? "neutral"}>
            {c.state}{c.state === "applied" ? ` · ${applied} change${applied === 1 ? "" : "s"}` : ""}
          </Badge>
        </div>
        <p className="mt-1 line-clamp-3 whitespace-pre-wrap">{c.text}</p>
      </summary>
      <div className="mt-2">
        {c.note && <p className="mb-2 text-xs italic text-zinc-600 dark:text-zinc-400">{c.note}</p>}
        <ChangeList comment={c} rows={state.rows} ticked={(x) => c.state === "applied" && x.ticked} />
      </div>
      {latest && (
        <div className="mt-2">
          <Button variant="ghost" busy={undo.isPending} onClick={() => undo.mutate()}>Undo this comment</Button>
          <ErrorNotice error={undo.error} />
        </div>
      )}
    </details>
  );
}

export function CommentMargin({ exerciseId, state }: { exerciseId: string; state: ListState }) {
  const { comment } = useListActions(exerciseId);
  const [text, setText] = useState("");
  const [author, setAuthor] = useState(() => localStorage.getItem(AUTHOR) ?? "");
  const waiting = state.pending;
  const send = () => comment.mutate({ text, author }, { onSuccess: () => setText("") });
  return (
    <aside className="space-y-3">
      <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
        <Input className="mb-2 w-full" placeholder="Your name" value={author}
               onChange={(e) => { setAuthor(e.target.value); localStorage.setItem(AUTHOR, e.target.value); }} />
        <Textarea rows={4} className="w-full" value={text} onChange={(e) => setText(e.target.value)}
                  placeholder={"Write a comment, as you would in feedback, e.g.\n"
                    + "Use PSI's group headings for every category.\n"
                    + "Put rapporteur and facilitation into event management."} />
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="text-xs text-zinc-500">
            {waiting ? "Decide on the waiting comment first." : "One or two instructions per comment work best."}
          </span>
          <Button disabled={!text.trim() || !!waiting} busy={comment.isPending} onClick={send}>Comment</Button>
        </div>
        <div className="mt-2"><ErrorNotice error={comment.error} /></div>
      </div>
      {waiting && <Waiting exerciseId={exerciseId} state={state} pending={waiting} />}
      <div className="space-y-2">
        {[...state.thread].reverse().map((c) => <Decided key={c.id} exerciseId={exerciseId} state={state} c={c} />)}
      </div>
    </aside>
  );
}
