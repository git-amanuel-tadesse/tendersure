# apps/web: the web app

React, TanStack Router (file routes in `src/routes`), Query and Table,
Tailwind, Vite.

- Reaches the API only through `@tendersure/api-client` (`make test` checks).
- `src/lib/api.ts`: one Query hook per API call. Jobs are polled while they
  run, and found again after a reload.
- `src/lib/answers.ts`: the Decisions grammar, built from the controls and
  read back (tested in `answers.test.ts`).

`make dev` runs it at http://127.0.0.1:5173/ against the API on :8765;
`make build` puts it in `dist/` for `tendersure-api` to serve.
