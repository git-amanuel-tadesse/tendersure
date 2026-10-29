# packages/core: `tendersure`

Every rule (parser, bank, generate, refine, check, research, llm, privacy),
and `tendersure.flows`: one function per step, plain inputs, a result back,
report lines through the `tendersure` logger.

- Imports: openpyxl, pdfplumber and the standard library. No app, no
  argparse, no web framework, no `print`, no `sys.exit` (`make imports` checks).
- Apps call `tendersure.flows`, never the rule modules.
- Locations come from `tendersure.paths.current()`; tests run on a temporary home.
- `tendersure.testing` holds synthetic workbooks for every party's tests.
