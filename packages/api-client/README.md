# packages/api-client: `@tendersure/api-client`

The web app's only way to the API:

- `openapi.json` is written by the API (`make contract`).
- `src/schema.d.ts` is generated from it. Both are committed, and `make test`
  fails when they are stale.
- `src/index.ts` provides:
  - `api`, the typed client, which sends `X-TenderSure: 1` on writes;
  - `unwrap`, which throws `ApiError` worded as the API words it;
  - the types: `Schemas` (what the API returns) and `Inputs` (what is sent);
  - `formData`, for uploads, and `links`, download links checked against
    the API's own paths.
