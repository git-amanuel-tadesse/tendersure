import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Button, Card, Empty, ErrorNotice, Field, Input, Notice, PageTitle, Stat } from "../components/ui";
import { useBank, useCreateExercise, useExercises, useSettings } from "../lib/api";

export const Route = createFileRoute("/")({ component: Home });

function NewExercise() {
  const create = useCreateExercise();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", client: "", prefix: "", client_name: "" });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  return (
    <Card title="New exercise">
      <form
        className="grid gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(form, { onSuccess: (ex) => navigate({ to: "/exercises/$exerciseId/refine", params: { exerciseId: ex.id } }) });
        }}
      >
        <Field label="Name" hint="e.g. ERCS 2026–2029"><Input required value={form.name} onChange={set("name")} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Client code" hint="e.g. ERCS"><Input required value={form.client} onChange={set("client")} /></Field>
          <Field label="Sheet code prefix" hint="e.g. ERCS-ET-2026-"><Input required value={form.prefix} onChange={set("prefix")} /></Field>
        </div>
        <Field label="Client name in questions" hint="optional, when not the code">
          <Input value={form.client_name} onChange={set("client_name")} />
        </Field>
        <ErrorNotice error={create.error} />
        <div><Button type="submit" busy={create.isPending}>Create</Button></div>
      </form>
    </Card>
  );
}

function Home() {
  const exercises = useExercises();
  const bank = useBank();
  const settings = useSettings();
  return (
    <>
      <PageTitle title="Exercises" subtitle="One client's prequalification round: draft list → revised → final → criteria sheets." />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <Card title="All exercises">
          <ErrorNotice error={exercises.error} />
          {exercises.data && !exercises.data.exercises.length && <Empty>No exercise yet: create the first one.</Empty>}
          <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {exercises.data?.exercises.map((ex) => (
              <li key={ex.id}>
                <Link to="/exercises/$exerciseId/refine" params={{ exerciseId: ex.id }}
                      className="flex items-center justify-between gap-4 rounded-md px-2 py-3 hover:bg-zinc-50 dark:hover:bg-zinc-800/50">
                  <div>
                    <div className="font-medium">{ex.name}</div>
                    <div className="text-xs text-zinc-500">{ex.client} · {ex.prefix}{ex.client_name && ` · ${ex.client_name}`}</div>
                  </div>
                  <span className="text-xs text-zinc-500">{ex.created.slice(0, 10)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
        <div className="space-y-6">
          <NewExercise />
          <Card title="Bank" actions={<Link to="/bank" className="text-sm text-accent-700 hover:underline">Open</Link>}>
            {bank.data?.exists ? (
              <div className="grid grid-cols-3 gap-3">
                <Stat label="Workbooks" value={bank.data.workbooks} />
                <Stat label="Sheets" value={bank.data.sheets} />
                <Stat label="Questions" value={bank.data.questions.toLocaleString()} />
              </div>
            ) : bank.data ? (
              <Notice tone="warn">The bank is empty: add past criteria workbooks before generating.</Notice>
            ) : null}
          </Card>
          {settings.data && !settings.data.model.runs && (
            <Notice tone="neutral" title="Working without AI">{settings.data.model.why_not}. Every step runs by rules only.</Notice>
          )}
        </div>
      </div>
    </>
  );
}
