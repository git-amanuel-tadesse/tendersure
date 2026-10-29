import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/exercises/$exerciseId/")({
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/exercises/$exerciseId/refine", params });
  },
});
