/**
 * The app's data: one TanStack Query hook per API call, through the generated client only.
 *
 * Long steps are jobs. Starting one returns the job; useJob polls it until it
 * has finished and then refreshes what the job changed. A page reloaded while
 * a job runs finds it again with useLatestJob.
 */
import { api, formData, type Inputs, type Schemas, unwrap } from "@tendersure/api-client";
import { type QueryKey, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

export type Job = Schemas["Job"];
export type Exercise = Schemas["ExerciseDetail"];
export type Line = Schemas["Line"];
export type Category = Schemas["Category"];
export type Item = Schemas["Item"];
export type Run = Schemas["Run"];

export const keys = {
  settings: ["settings"] as const,
  bank: ["bank"] as const,
  exercises: ["exercises"] as const,
  exercise: (id: string) => ["exercise", id] as const,
  brief: (id: string) => ["brief", id] as const,
  benchmark: (id: string) => ["benchmark", id] as const,
  rules: (id: string) => ["rules", id] as const,
  refine: (id: string) => ["refine", id] as const,
  categories: (id: string) => ["categories", id] as const,
  runs: (id: string) => ["runs", id] as const,
  run: (id: string, n: number) => ["run", id, n] as const,
  job: (id: string) => ["job", id] as const,
  jobs: (exerciseId: string | undefined, kind?: string) => ["jobs", exerciseId ?? "", kind ?? ""] as const,
};

const path = (exercise_id: string) => ({ params: { path: { exercise_id } } });
const multipart = { bodySerializer: (b: unknown) => b as FormData };

// --- settings, bank, exercises ------------------------------------------------------------

export const useSettings = () =>
  useQuery({ queryKey: keys.settings, queryFn: () => unwrap(api.GET("/api/settings")) });

export const useBank = () => useQuery({ queryKey: keys.bank, queryFn: () => unwrap(api.GET("/api/bank")) });

export const useExercises = () =>
  useQuery({ queryKey: keys.exercises, queryFn: () => unwrap(api.GET("/api/exercises")) });

export const useExercise = (id: string) =>
  useQuery({ queryKey: keys.exercise(id), queryFn: () => unwrap(api.GET("/api/exercises/{exercise_id}", path(id))) });

/** The client's brief: one per exercise, read first by every model step (spec 07 B). */
export const useBrief = (id: string) =>
  useQuery({ queryKey: keys.brief(id), queryFn: () => unwrap(api.GET("/api/exercises/{exercise_id}/brief", path(id))) });

export function useSetBrief(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => unwrap(api.PUT("/api/exercises/{exercise_id}/brief", { ...path(id), body: { text } })),
    onSuccess: (b) => qc.setQueryData(keys.brief(id), b),
  });
}

export function useCreateExercise() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Inputs["NewExercise"]) => unwrap(api.POST("/api/exercises", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.exercises }),
  });
}

export function useUpload(exerciseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) =>
      unwrap(api.POST("/api/exercises/{exercise_id}/files", {
        ...path(exerciseId), body: formData({ file }) as never, ...multipart,
      })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.exercise(exerciseId) }),
  });
}

/** Workbooks for the bank; each file's name carries its path inside a picked folder. */
export const useIngest = () =>
  useMutation({
    mutationFn: (files: File[]) =>
      unwrap(api.POST("/api/bank/ingest", {
        body: formData({ files }, { files: files.map((f) => f.webkitRelativePath || f.name) }) as never,
        ...multipart,
      })),
  });

// --- jobs ---------------------------------------------------------------------------------

export const FINISHED = ["done", "failed", "interrupted"];
export const isRunning = (job?: Pick<Job, "status"> | null) => !!job && !FINISHED.includes(job.status);

/** A job, polled while it runs; `refresh` is invalidated once when it has finished. */
export function useJob(jobId: string | undefined, refresh: QueryKey[] = []) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: keys.job(jobId ?? ""),
    queryFn: () => unwrap(api.GET("/api/jobs/{job_id}", { params: { path: { job_id: jobId! } } })),
    enabled: !!jobId,
    refetchInterval: (query) => (isRunning(query.state.data) ? 700 : false),
  });
  const seen = useRef<string | null>(null);
  const status = q.data?.status;
  useEffect(() => {
    if (!jobId || !status || !FINISHED.includes(status) || seen.current === jobId) return;
    seen.current = jobId;
    for (const key of refresh) qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: ["jobs"] });
  }, [jobId, status, qc]); // not `refresh`: a new array every render, read when the job finishes
  return q;
}

/** The newest job of a kind for an exercise: found again after a reload. */
export function useLatestJob(exerciseId: string | undefined, kind: string) {
  return useQuery({
    queryKey: keys.jobs(exerciseId, kind),
    queryFn: async () => {
      const r = await unwrap(api.GET("/api/jobs", { params: { query: { exercise_id: exerciseId, limit: 20 } } }));
      return r.jobs.find((j) => j.kind === kind) ?? null;
    },
  });
}

// --- refinement ----------------------------------------------------------------------------

export const useRefine = (id: string) =>
  useQuery({ queryKey: keys.refine(id), queryFn: () => unwrap(api.GET("/api/exercises/{exercise_id}/refine", path(id))) });

export function useRefineActions(id: string) {
  const qc = useQueryClient();
  const set = (data: Schemas["RefineState"]) => qc.setQueryData(keys.refine(id), data);
  return {
    chooseDraft: useMutation({
      mutationFn: (body: Inputs["ChooseDraft"]) =>
        unwrap(api.POST("/api/exercises/{exercise_id}/refine/draft", { ...path(id), body })),
      onSuccess: set,
    }),
    propose: useMutation({
      mutationFn: (body: Inputs["Propose"]) =>
        unwrap(api.POST("/api/exercises/{exercise_id}/refine/propose", { ...path(id), body })),
    }),
    changeLines: useMutation({
      mutationFn: (changes: Inputs["LineChange"][]) =>
        unwrap(api.PATCH("/api/exercises/{exercise_id}/refine/lines", { ...path(id), body: { changes } })),
      onSuccess: set,
    }),
    importAnswers: useMutation({
      mutationFn: (body: Inputs["RefineImport"]) =>
        unwrap(api.POST("/api/exercises/{exercise_id}/refine/import", { ...path(id), body })),
      onSuccess: (r) => r.applied && qc.invalidateQueries({ queryKey: keys.refine(id) }),
    }),
    apply: useMutation({
      mutationFn: (body: Inputs["RefineApply"]) =>
        unwrap(api.POST("/api/exercises/{exercise_id}/refine/apply", { ...path(id), body })),
    }),
  };
}

// --- categories ------------------------------------------------------------------------------

export const useCategories = (id: string) =>
  useQuery({ queryKey: keys.categories(id), queryFn: () => unwrap(api.GET("/api/exercises/{exercise_id}/categories", path(id))) });

export function useCategoryActions(id: string) {
  const qc = useQueryClient();
  const set = (data: Schemas["Table"]) => qc.setQueryData(keys.categories(id), data);
  return {
    changeRow: useMutation({
      mutationFn: ({ number, ...body }: Inputs["RowChange"] & { number: number }) =>
        unwrap(api.PATCH("/api/exercises/{exercise_id}/categories/{number}", {
          params: { path: { exercise_id: id, number } }, body,
        })),
      onSuccess: set,
    }),
    replace: useMutation({
      mutationFn: (categories: Category[]) =>
        unwrap(api.PUT("/api/exercises/{exercise_id}/categories", { ...path(id), body: { categories } })),
      onSuccess: set,
    }),
    importTable: useMutation({
      mutationFn: (source: string) =>
        unwrap(api.POST("/api/exercises/{exercise_id}/categories/import", { ...path(id), body: { source } })),
      onSuccess: set,
    }),
  };
}

// --- generation ------------------------------------------------------------------------------

export const useRuns = (id: string) =>
  useQuery({ queryKey: keys.runs(id), queryFn: () => unwrap(api.GET("/api/exercises/{exercise_id}/generate", path(id))) });

export const useRun = (id: string, n: number | undefined) =>
  useQuery({
    queryKey: keys.run(id, n ?? 0),
    enabled: !!n,
    queryFn: () =>
      unwrap(api.GET("/api/exercises/{exercise_id}/generate/runs/{number}", { params: { path: { exercise_id: id, number: n! } } })),
  });

/** The client's sample criteria (spec 08): tabs as checked, the group -> tab map. */
export type Benchmark = Schemas["Benchmark"];
export type MapLine = Schemas["MapLine"];

export const useBenchmark = (id: string) =>
  useQuery({ queryKey: keys.benchmark(id), queryFn: () => unwrap(api.GET("/api/exercises/{exercise_id}/benchmark", path(id))) });

export function useBenchmarkActions(id: string) {
  const qc = useQueryClient();
  const set = (data: Benchmark) => qc.setQueryData(keys.benchmark(id), data);
  return {
    choose: useMutation({
      mutationFn: (file: string) => unwrap(api.POST("/api/exercises/{exercise_id}/benchmark", { ...path(id), body: { file } })),
      onSuccess: set,
    }),
    setMap: useMutation({
      mutationFn: (lines: MapLine[]) =>
        unwrap(api.PUT("/api/exercises/{exercise_id}/benchmark/map", { ...path(id), body: { lines } })),
      onSuccess: set,
    }),
    stop: useMutation({
      mutationFn: () => unwrap(api.DELETE("/api/exercises/{exercise_id}/benchmark", path(id))),
      onSuccess: set,
    }),
  };
}

/** The client's rules (spec 12 C): read and saved as a whole. */
export type ClientRule = Schemas["ClientRule"];
export const useClientRules = (id: string) =>
  useQuery({ queryKey: keys.rules(id), queryFn: () => unwrap(api.GET("/api/exercises/{exercise_id}/rules", path(id))) });
export function useSaveClientRules(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rules: ClientRule[]) => unwrap(api.PUT("/api/exercises/{exercise_id}/rules", { ...path(id), body: { rules } })),
    onSuccess: (data) => qc.setQueryData(keys.rules(id), data),
  });
}

/** After the review (spec 11): mark the final run, check an edited copy, add it to the bank. */
export function useFinishActions(id: string) {
  const qc = useQueryClient();
  const at = (n: number) => ({ params: { path: { exercise_id: id, number: n } } });
  return {
    final: useMutation({
      mutationFn: (n: number) => unwrap(api.POST("/api/exercises/{exercise_id}/generate/runs/{number}/final", at(n))),
      onSuccess: () => qc.invalidateQueries({ queryKey: keys.runs(id) }),
    }),
    check: useMutation({
      mutationFn: ({ n, file }: { n: number; file?: string }) =>
        unwrap(api.POST("/api/exercises/{exercise_id}/generate/runs/{number}/check", { ...at(n), body: { file: file || null } })),
    }),
    bank: useMutation({
      mutationFn: ({ n, file }: { n: number; file?: string }) =>
        unwrap(api.POST("/api/exercises/{exercise_id}/generate/runs/{number}/bank", { ...at(n), body: { file: file || null } })),
      onSuccess: () => qc.invalidateQueries({ queryKey: keys.bank }),
    }),
  };
}

export function useGenerateActions(id: string) {
  const qc = useQueryClient();
  return {
    start: useMutation({
      mutationFn: (body: Inputs["Options"]) =>
        unwrap(api.POST("/api/exercises/{exercise_id}/generate", { ...path(id), body })),
      onSuccess: () => qc.invalidateQueries({ queryKey: keys.runs(id) }),
    }),
    answer: useMutation({
      mutationFn: ({ n, answers }: { n: number; answers: Record<string, string> }) =>
        unwrap(api.PATCH("/api/exercises/{exercise_id}/generate/runs/{number}/answers", {
          params: { path: { exercise_id: id, number: n } }, body: { answers },
        })),
      onSuccess: (run) => qc.setQueryData(keys.run(id, run.number), run),
    }),
    apply: useMutation({
      mutationFn: (n: number) =>
        unwrap(api.POST("/api/exercises/{exercise_id}/generate/runs/{number}/apply", {
          params: { path: { exercise_id: id, number: n } },
        })),
      onSuccess: () => qc.invalidateQueries({ queryKey: keys.runs(id) }),
    }),
  };
}
