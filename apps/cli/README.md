# apps/cli: the `tendersure` command

Arguments in, `tendersure.flows` out: the flows' lines are printed, and failures
become exit codes (`check` exits 1 on errors). Imports `tendersure.flows` only,
and never the API.
