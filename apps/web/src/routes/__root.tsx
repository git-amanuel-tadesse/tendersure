import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Link, Outlet } from "@tanstack/react-router";
import { Badge, ErrorNotice } from "../components/ui";
import { useSettings } from "../lib/api";

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: Shell,
  errorComponent: ({ error }) => (
    <main className="mx-auto max-w-3xl p-6"><ErrorNotice error={error} /></main>
  ),
  notFoundComponent: () => <main className="mx-auto max-w-3xl p-6 text-sm text-zinc-500">No such page.</main>,
});

const nav = "rounded-md px-2.5 py-1.5 text-sm text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800";
const active = { className: "bg-zinc-100 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100" };

function ModelStatus() {
  const { data } = useSettings();
  if (!data) return null;
  const m = data.model;
  return m.runs ? (
    <Badge tone="accent" title={`${m.provider} / ${m.model}: requests are anonymised first`}>AI: {m.model}</Badge>
  ) : (
    <Badge tone="neutral" title={m.why_not}>AI off: {m.why_not}</Badge>
  );
}

function Shell() {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-zinc-200 bg-white/90 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/90">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-4 px-4 py-2.5">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="grid size-6 place-items-center rounded-md bg-accent-700 text-xs text-white">✓</span>
            TenderSure
          </Link>
          <nav className="flex gap-1">
            <Link to="/" className={nav} activeProps={active} activeOptions={{ exact: true }}>Exercises</Link>
            <Link to="/bank" className={nav} activeProps={active}>Bank</Link>
          </nav>
          <div className="ml-auto"><ModelStatus /></div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
