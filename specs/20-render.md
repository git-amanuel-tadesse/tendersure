# Spec 20: The team server on Render

Spec 13 put the one shared bank on a cloud VM that only the tailnet can
reach. This spec hosts that same server on Render instead. Most of spec 13
still holds. What changes is how the team reaches the server and who keeps
it running.

## Agreed choices (2026-11-06)

- **Render hosts the team server.** It runs as one Render web service
  from a Docker image, with a persistent disk that holds the home.
- **A public URL behind one team password.** The service has a public
  `https://<name>.onrender.com` address. Every request needs the team's
  user name and password (HTTP Basic). This replaces spec 13 H1 "reached
  over Tailscale only". Clients still never get the password.

## R1. Shape

- **One service, one instance, one process.** `tendersure-api --serve` runs
  the app under waitress with 8 request threads and the one in-process job
  worker. A persistent disk allows only one instance, which keeps SQLite
  and the CSV files with a single writer.
- **The home is the disk.** `TENDERSURE_HOME=/app/home` is the disk's mount
  path, so `data/`, `workspaces/` and `.cache/` live there and outlive
  deploys. The image holds code only: `.dockerignore` keeps client data,
  `.env` and `.git` out of it.
- **Keys are Render environment variables**, not a `.env` file. The privacy
  layer and the paid-tier guard work as before.
- **Region: Frankfurt** (EU), the default from spec 13's open question 1.
  Render can't change the region after the service is created.

## R2. The team password

- **`--serve` refuses to start without one.** `TENDERSURE_PASSWORD` must be
  set and at least 16 characters long. The Blueprint has Render generate a
  random 256-bit value. The user name is `TENDERSURE_USER` (default `team`).
- **Every request needs it except `GET /api/health`**, which Render's health
  check calls without credentials and which reveals only the version. The
  browser asks for the password once and sends it with every request after.
  The web app needs no change.
- **Other sites stay out.** With a password set, the Host header is not
  limited, because the server has a public name. An `Origin` header, when
  present, must be the server itself. Writes still need `X-TenderSure: 1`,
  so a page on another site can't use a signed-in browser to change
  anything.
- **Without a password the app is local**, exactly as in spec 06 W2:
  127.0.0.1 only, local Host and Origin only.

## R3. Deploying

- **By hand.** `autoDeployTrigger: off`. A deploy stops the one instance
  before it starts the new one (a disk rules out zero-downtime deploys), so
  a running job is cut off and shows `interrupted`. Deploy with Manual
  Deploy when no job runs. Rerunning the job reuses the cached model
  answers.
- **Moving in (spec 13 H8).** Copy this machine's `data/`, `workspaces/`
  and `.cache/` onto the disk with `scp -s` over Render SSH.

## R4. Backups

- **Render snapshots the disk every day and keeps each snapshot for 7
  days.** Until spec 13 H6 is built for Render, those snapshots are the
  backup.

## What spec 13 still asks for

| Spec 13 | On Render |
|---|---|
| H2 identity from Tailscale headers | Not available. Everyone signs in as one user, so the audit can't tell who did what. Per-person passwords would be the next step. |
| H3 write lock | Still needed: request threads and the job worker share the files. A CLI run in Render's shell needs it too. |
| H4 CLI on the server | Render shell or `render ssh`, against the same home. |
| H5 waitress, `--serve` | Built here. The systemd unit, provision wizard and `make deploy` are not needed. |
| H6 nightly restic backups | Replaced by Render's daily snapshots (7 days). An offsite copy is still to do. |
| H7 retention | Unchanged, still to do. |

## Open questions

1. **Per-person sign-in.** One shared password means nobody knows who saved
   what, and anyone who leaves the team needs a password change. Is that
   acceptable for now?
2. **Backups beyond 7 days.** Is Render's snapshot window enough, or should
   H6's offsite copy come next?
