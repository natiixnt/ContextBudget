# How the Numbers Are Measured

Every savings number redcon publishes is reproducible on this repository with
the open source CLI. This page shows the exact procedure, the latest measured
run, and what the numbers do and do not claim.

## Reproduce it in 60 seconds

```bash
pip install redcon
git clone https://github.com/natiixnt/redcon && cd redcon
redcon pack "add rate limiting to the gateway auth endpoints"
```

Every pack prints its own receipt, no telemetry involved:

```
Budget: input=56117 tokens, saved=963944 tokens, risk=high
Packed 395 files (129 skipped) in 2.1s
```

`run.json` next to it lists every included file with its compression tier, so
the pack is fully auditable: you can diff exactly what the model would see.

## Latest measured run

Default profile, zero configuration, fresh clone of this repository,
five representative development tasks:

| Task | Packed input | Saved | Scanned | Reduction |
| --- | ---: | ---: | ---: | ---: |
| add rate limiting to the gateway auth endpoints | 56,117 | 963,944 | 1,020,061 | 94.5% |
| fix the flaky uvicorn startup in the gateway tests | 51,936 | 763,607 | 815,543 | 93.6% |
| add a new compressor for yaml kubernetes manifests | 59,156 | 973,084 | 1,032,240 | 94.3% |
| speed up the incremental scanner on large monorepos | 46,188 | 752,378 | 798,566 | 94.2% |
| document the license activation flow for pro users | 59,430 | 1,107,719 | 1,167,149 | 94.9% |

The website and README quote **83%**: the pack rate from an earlier, smaller
state of this repository (512k tokens scanned, 88k packed). Repositories
differ, so we keep quoting the lower number and let you measure your own.

## What the terms mean

- **Scanned**: tokens across all files redcon considered for the task
  (packed input plus saved).
- **Packed input**: tokens actually shipped to the model after selection and
  compression.
- **Saved**: scanned minus packed input.
- **Task coverage**: selection ranks every file against the task and includes
  the ones the change touches. Nothing is deleted: the repository stays on
  disk, the agent keeps full tool access, and anything the pack skipped can be
  read on demand. redcon changes what is sent per request, not what exists.
- **Determinism**: no LLM in the loop. The same tree and task produce the
  same pack, which is why the numbers are reproducible at all.
- Token counts use the built-in heuristic estimator by default (the output
  labels it); install `redcon[tokenizers]` for tiktoken-exact counts.

## The max profile (Pro)

The Pro `--compression-profile max` tightens tier thresholds on top of the
default pipeline. Same task, same tree, same day as the table above:

| Profile | Packed input |
| --- | ---: |
| default | 54,145 |
| max | 29,744 |

## Honest limitations

- Numbers above are from redcon's own repository (Python, ~1.2k files,
  including deliberately compressible benchmark fixtures). Typical real
  repositories measure lower; small repositories (under ~50k tokens) have
  little waste to cut.
- redcon optimizes repository context. Tokens spent on conversation history
  and tool output are not touched.
- Reduction percentages depend on repository shape more than on anything
  else. Run one pack on your own repository; that number is the only one
  that matters for you.
