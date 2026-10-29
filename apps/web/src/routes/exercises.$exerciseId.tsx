import { createFileRoute, Link, Outlet } from "@tanstack/react-router";
import { ErrorNotice, Spinner } from "../components/ui";
import { useExercise } from "../lib/api";

export const Route = createFileRoute("/exercises/$exerciseId")({ component: ExerciseLayout });

const tab = "border-b-2 border-transparent px-1 pb-2 text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100";
const active = { className: "border-accent-700 font-medium text-zinc-900 dark:text-zinc-100" };

function ExerciseLayout() {
  const { exerciseId } = Route.useParams();
  const ex = useExercise(exerciseId);
  if (ex.error) return <ErrorNotice error={ex.error} />;
  if (!ex.data) return <Spinner />;
  const steps = [
    { to: "/exercises/$exerciseId/refine", label: "1 · Refine categories" },
    { to: "/exercises/$exerciseId/categories", label: "2 · Categories table" },
    { to: "/exercises/$exerciseId/generate", label: "3 · Generate criteria" },
    { to: "/exercises/$exerciseId/files", label: "Files" },
  ] as const;
  return (
    <>
      <div className="mb-4">
        <Link to="/" className="text-xs text-zinc-500 hover:underline">← Exercises</Link>
        <h1 className="mt-1 text-xl font-semibold tracking-tight">{ex.data.name}</h1>
        <p className="text-sm text-zinc-500">
          {ex.data.client} · sheet codes {ex.data.prefix}001…{ex.data.client_name && ` · “${ex.data.client_name}” in questions`}
        </p>
      </div>
      <nav className="mb-6 flex gap-6 border-b border-zinc-200 dark:border-zinc-800">
        {steps.map((s) => (
          <Link key={s.to} to={s.to} params={{ exerciseId }} className={tab} activeProps={active}>{s.label}</Link>
        ))}
      </nav>
      <Outlet />
    </>
  );
}
