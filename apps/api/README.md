# apps/api: `tendersure-api`

The local server for the web app (Flask, flask-openapi3, pydantic): HTTP in,
`tendersure.flows` out. It listens on 127.0.0.1 only, refuses other Hosts and
Origins, and needs `X-TenderSure: 1` on every write. It serves `apps/web/dist` at `/`.

- `schemas.py`: the API's shapes, mapped from the flows' dataclasses.
- `workspaces.py`, `jobs.py`: exercises on disk, and steps run one at a
  time in the background.
- One module per route group: `exercises`, `bank`, `jobs_api`, `refine_api`,
  `categories_api`, `generate_api`.
- `contract.py` writes the OpenAPI document that `packages/api-client` is
  generated from (`make contract`).

Imports `tendersure.flows` only, and never the CLI.
