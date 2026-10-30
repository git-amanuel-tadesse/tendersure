# Spec 13: One shared server for the team

Today each team member runs TenderSure on their own machine (spec 06), so
each has their own bank and learned files. A name the glossary learns or a
client rule one person adds never reaches the others, and the copies drift
apart. This spec moves the team onto one server that holds the one bank.

## Agreed choices (2026-10-30)

- **The reason is one shared bank.** The bank, the learned files and the
  exercises live in one place, and every team member works on that copy.
  Easier installs and several people editing one exercise at once are not
  goals of this spec.
- **Team only.** Clients never open the app. They keep getting Excel files
  from the team, as today. So the server stays on a private network and needs
  no logins.
- **A cloud VM.** The VM is small and always on, with a persistent disk.

## H1. Shape

- **One VM, one process.** The app runs as one process, with its one job
  worker (spec 06 W4) and the same home layout as today: `data/`,
  `workspaces/`, `.cache/` and `.env` under `TENDERSURE_HOME`. Nothing in the
  storage changes. SQLite and the CSV files keep a single writer process.
- **Reached over Tailscale only.** The app still listens on `127.0.0.1`.
  `tailscale serve` puts it on the tailnet over HTTPS, at
  `https://tendersure.<tailnet>.ts.net`. The VM's firewall has no public
  ports open, not even SSH: SSH also goes over Tailscale.
- **The VM.** The disk is encrypted at rest and the provider can snapshot
  it. About 2 vCPU, 4 GB of memory and 40 GB of disk is enough: today the
  data is under 100 MB, and the work is mostly waiting on model calls.
- **One set of model keys.** The keys live in the server's `.env` (mode
  600), and the privacy layer and paid-tier guard work as today. Team
  members no longer need keys of their own.

## H2. Who did what

There are no logins, but the server still knows who sent each request.

- **Identity.** Tailscale Serve adds the tailnet user to each request it
  forwards (`Tailscale-User-Login`, `Tailscale-User-Name`). The app trusts
  these headers only on requests that come from loopback, which only
  `tailscale serve` can send. Without the headers the user is `local`, so
  running on a laptop works as before.
- **Allowed hosts.** The server's tailnet name is added to the allowed
  host names (`TENDERSURE_HOSTS`). The `X-TenderSure` header check on
  writes stays.
- **What is recorded.**
  - Every job records who started it.
  - Every save of shared or exercise state (review edits, answers, client
    rules, the categories table, uploads, ingest) appends one line to
    `data/audit.jsonl`: when, who, what, and which exercise.
  - The outbox log of model requests records who caused each request.
- **The UI.** It shows the user's name in the header. A job shows who
  started it.

## H3. Shared writes are serialised

- **One lock.** Every write to the bank or a learned file runs under one
  lock, held for the whole read-change-write. That includes saving client
  rules, the glossary names drafted during generation, research findings and
  ingest. The lock works both across threads and across processes (a file
  lock on `data/.lock`), so a CLI command run on the server waits for the app
  and the app waits for the CLI.
- **The lock is held only while writing, not for a whole job.** A client
  rule saved while a generation runs applies to the next run, as it does
  today.
- **SQLite waits for a lock instead of failing at once** (it gets a busy
  timeout).
- **Within one exercise, the last save wins.** Two people editing the same
  review table is out of scope. The audit line shows who saved last.

## H4. The CLI

- **On the server.** The steps that stay CLI-only (`check`, `learn`,
  `evaluate`, `privacy-check`, categories from a PDF) run there over SSH,
  against the server's home and under the same lock. `learn` writes the
  learned files, so the team's learning has to happen there.
- **On a laptop.** The CLI and the local app keep working against a local
  home. That home is a sandbox, not the team's bank.
  - `scripts/pull-snapshot` fills a sandbox from the latest backup.
  - Nothing is pushed back from a sandbox, which is how the copies stay one.

## H5. Serving and deploying

- **Production serving.** The app runs under waitress: one process with
  several request threads, so the in-process job worker stays the only one.
  A systemd unit starts it at boot and restarts it if it crashes.
  `tendersure-api --serve` means no browser and no debug.
- **Deploying.** `make deploy` reaches the VM over Tailscale and:
  1. waits until no job is running (`--force` does not wait);
  2. checks out the branch or tag;
  3. installs the Python packages and builds the web bundle;
  4. restarts the service;
  5. checks `/api/health`.

  A job cut off by a forced deploy is shown as `interrupted`, as today, and
  rerunning it reuses the cached model answers.
- **Setting up the VM.** A wizard (`scripts/provision`) walks through the
  steps only a person can do: create the VM, join the tailnet, create the
  service user and the home, and put in the keys.

## H6. Backups

- **Nightly.** A systemd timer:
  1. takes an online SQLite backup of `data/bank.sqlite`, so it is
     consistent while the app runs;
  2. archives it with the rest of `data/`, `workspaces/` and the `.cache/`
     model answers;
  3. sends the archive encrypted (restic) to object storage at the same
     provider, in another location.

  `.env` is not backed up; the keys are kept apart from the data.
- **Kept.** 14 daily and 8 weekly backups.
- **Restore.** `scripts/restore` puts a backup into an empty home. A
  restore counts as working when the app starts on it and shows the same
  numbers on Home (workbooks, sheets and questions in the bank) and the same
  exercises.

## H7. Retention

The shared server needs retention rules (spec 06 left them for later).

- **The outbox log and job files** are pruned after 90 days.
- **Cached model answers** are pruned after 180 days.
- **Exercises** are kept until the team removes them. Removing one from the
  app is a later task. Until then, an exercise is removed on the server by
  hand, and backups drop it after 8 weeks.

## H8. Moving in

- **The first server home** is a copy of this machine's `data/`,
  `workspaces/` and `.cache/`.
- **Other team members' local learned files** are merged in once, if they
  have any (see the open questions). After that, a laptop is a sandbox
  (H4).

## Out of scope

- Client access, logins and roles.
- Several people editing one exercise at the same moment.
- Running jobs side by side. One queue serves the whole team. If one
  person's generation run often blocks another's refinement, that is the next
  spec.
- Postgres or object storage for the data. The storage seams
  (`TENDERSURE_HOME`, the learned-store driver) keep that open.

## Open questions

1. **Provider and region.** Do the client contracts (ERCS, NRC, PSI) say
   where their data may be stored? If they don't, an EU region (GDPR terms)
   is the default.
2. **Retention.** Are the numbers in H6 and H7 right?
3. **Moving in.** Has anyone besides you built up their own bank or learned
   files that should be merged into the server's first home?
