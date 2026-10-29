/**
 * The typed client of the TenderSure API (spec 06 M2).
 *
 * The types in schema.d.ts are generated from openapi.json, which the API
 * writes (`make contract`); nothing here is written by hand from the Python
 * side. The web app reaches the API only through this package.
 */
import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type { components, paths };

/**
 * What the API returns: every field present. The API sends each field of a
 * model, with its default when it has one, so data read from it lacks none.
 */
type Present<T> = T extends (infer U)[] ? Present<U>[] : T extends object ? { [K in keyof T]-?: Present<T[K]> } : T;
export type Schemas = { [K in keyof components["schemas"]]: Present<components["schemas"][K]> };

/** What the app sends: a field with a default may be left out (a PATCH changes only the fields given). */
export type Inputs = components["schemas"];

/** Every request that changes something carries it; the API refuses one without it. */
export const WRITE_HEADER = "X-TenderSure";

const writes: Middleware = {
  onRequest({ request }) {
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) request.headers.set(WRITE_HEADER, "1");
    return request;
  },
};

export function makeClient(baseUrl = "", fetchImpl?: (input: Request) => Promise<Response>) {
  const client = createClient<paths>({ baseUrl, ...(fetchImpl ? { fetch: fetchImpl } : {}) });
  client.use(writes);
  return client;
}

export const api = makeClient();

/** The API's error shape: {"error": "...", "detail": ...}. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly title: string,
    readonly detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : title);
  }

  /** What to show a person: the detail when it is a sentence, else the title and each problem. */
  get text(): string {
    if (typeof this.detail === "string") return this.detail;
    if (Array.isArray(this.detail))
      return `${this.title}: ${this.detail
        .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).join(".")} ${d.msg ?? ""}`.trim())
        .join("; ")}`;
    return this.title;
  }
}

type Result<T> = { data?: T; error?: unknown; response: Response };

/** The data of a response, or an ApiError thrown with the API's own words. */
export async function unwrap<T>(call: Promise<Result<T>>): Promise<Present<T>> {
  const { data, error, response } = await call;
  if (response.ok) return data as Present<T>;
  const body = (error ?? {}) as { error?: string; detail?: unknown };
  throw new ApiError(response.status, body.error ?? response.statusText, body.detail ?? null);
}

/** multipart/form-data for the upload endpoints, which take files. */
export function formData(fields: Record<string, File | Blob | (File | Blob)[]>, names?: Record<string, string[]>) {
  const fd = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    const files = Array.isArray(value) ? value : [value];
    files.forEach((f, i) => fd.append(key, f, names?.[key]?.[i] ?? (f instanceof File ? f.name : key)));
  }
  return fd;
}

/** A link that downloads a file of an exercise (GET: no header needed, so a plain <a href> works). */
export function fileUrl(exerciseId: string, path: string) {
  return `/api/exercises/${encodeURIComponent(exerciseId)}/files/${path.split("/").map(encodeURIComponent).join("/")}`;
}

/** Download links (GET: a plain <a href> works). Each path is checked against the API's own list. */
const at = <P extends keyof paths>(path: P, params: Record<string, string> = {}) =>
  path.replace(/\{(\w+)\}/g, (_, k: string) => encodeURIComponent(params[k] ?? ""));

export const links = {
  bankReport: (name: "validation_report.xlsx" | "bank.xlsx") => at("/api/bank/reports/{name}", { name }),
  revised: (exercise_id: string) => at("/api/exercises/{exercise_id}/refine/revised.xlsx", { exercise_id }),
  final: (exercise_id: string) => at("/api/exercises/{exercise_id}/refine/final.xlsx", { exercise_id }),
  categoriesCsv: (exercise_id: string) => at("/api/exercises/{exercise_id}/categories/categories.csv", { exercise_id }),
  file: fileUrl,
};
