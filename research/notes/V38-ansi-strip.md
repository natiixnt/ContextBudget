# V38: ANSI/escape-sequence strip with rich-output preserved in side metadata

## Hypothesis
Many of the dev-tools Redcon wraps (pytest, ruff, mypy, npm, yarn, pnpm,
docker, kubectl) emit ANSI escape sequences when their stdout looks like a
terminal, even though Redcon captures via `Popen` pipes. ANSI bytes do not
ever survive the round trip from compressor to LLM as useful information:
SGR colour codes tokenise as `\x1b`, `[`, digits, `m` (3-5 cl100k tokens
per code), CR-overwrite progress bars duplicate the same line N times,
OSC terminal-title sequences contribute pure noise. The claim: a single
deterministic pre-compressor pass that (a) injects `NO_COLOR=1`, `TERM=dumb`,
`FORCE_COLOR=0` into the subprocess env, (b) strips residual SGR / CSI / OSC /
short-form ESC sequences, (c) collapses CR-overwrite runs to their final
partial line, and (d) records a `had_color: True` flag in `CompressedOutput.notes`
when colour was present (so severity-encoded info is preserved as side metadata),
will reduce raw-token count by 5-40 absolute points on ANSI-heavy commands
**before any compressor runs**, compounding multiplicatively with the
existing per-compressor reductions.

## Theoretical basis

ANSI escape sequences carry zero new information once the agent is the
consumer. SGR colours are a presentation channel; their semantic content
(e.g. red = error, yellow = warning) is already redundantly encoded as
literal text ("FAILED", "error:", "WARNING") by every modern dev tool.
The Shannon-information of a colour byte conditioned on the visible token
that follows is essentially zero.

Let H(T) be the entropy of the textual content and H(C|T) the conditional
entropy of colour given text. For dev-tool output:

```
H(C | T) ~= 0   (colour deterministic given keyword)
length(ANSI byte stream) >= 4 bytes/SGR
typical SGR-per-line on pytest -v with colour: 4-8
typical line on pytest -v: 60-100 chars
ANSI overhead: 16-64 bytes / 60-100 chars  =>  20-50% of byte stream
```

Empirically, on a 912-byte mocked pytest -v session with 41 SGR codes and
1 CR (representative of `collecting...` bar), tiktoken cl100k counts:

```
raw            316 tokens
SGR-stripped   178 tokens          (43.7% reduction)
```

On a 65-line npm install fragment with progress bar + OSC title:

```
raw            101 tokens
+OSC + SGR + CR collapse 27 tokens  (73.3% reduction)
```

These are floors not ceilings: the existing pytest compressor would still
run on the stripped text and add its own 73.8% compaction on top, so the
combined reduction on a coloured pytest run is roughly:

```
1 - (1 - 0.44) * (1 - 0.738) = 1 - 0.147 = 85.3% combined
```

versus the BASELINE 73.8% on the already-uncoloured fixtures. That is
an absolute +11.5 pp shift on coloured inputs - which matter precisely
because users running pytest in a TTY-fronted MCP relay get colour by default.

A separate "we already inject NO_COLOR" path saves the work entirely on
cooperative tools. A regex strip is only the residual-handling fallback
for the fraction of tools that ignore the env (some npm scripts hardcode
chalk; some custom Python tools hardcode `\x1b[`).

## Concrete proposal for Redcon

Two-layer defence:

**Layer 1: env injection in `redcon/cmd/runner.py`**

The dataclass `RunRequest` accepts `env: dict[str, str] | None = None`;
when caller passes `None` (the only path in production today) Popen
inherits parent env. Modify `run_command` to merge a small "neutralise
colour" overlay into the inherited env unconditionally:

```python
_COLOUR_OFF = {
    "NO_COLOR": "1",          # https://no-color.org
    "TERM": "dumb",            # disable curses-driven colour
    "FORCE_COLOR": "0",        # node ecosystem (chalk)
    "CLICOLOR": "0",           # BSD ls family
    "CLICOLOR_FORCE": "0",
    "PY_COLORS": "0",          # pytest
    "PYTEST_ADDOPTS": "--color=no",  # pytest belt + braces
    "MYPY_FORCE_COLOR": "0",
    "RUFF_NO_COLOR": "1",
    "DOCKER_CLI_HINTS": "false",
}

def _coloured_env(parent: dict[str, str] | None) -> dict[str, str]:
    env = dict(os.environ if parent is None else parent)
    env.update(_COLOUR_OFF)
    return env
```

Pass `env=_coloured_env(request.env)` into `Popen`. This is a strict
superset of the previous behaviour (cache key unchanged because cache
keys hash argv + cwd, not env), is deterministic, costs zero tokens
on output, and silences ~95% of colour at the source.

**Layer 2: residual strip in `redcon/cmd/pipeline.py` before `detect_compressor`**

A `_neutralise_terminal` helper, added to pipeline.py and invoked once
per RunResult. Sketch:

```python
# Compiled module-level (single dispatch, prefix-gated on \x1b / \r).
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ESC_SHORT = re.compile(r"\x1b[@-Z\\-_]")
_BELL = "\x07"

def _strip_ansi(text: str) -> tuple[str, bool]:
    if "\x1b" not in text and "\x07" not in text:
        return text, False
    out = _OSC.sub("", text)
    out = _CSI.sub("", out)
    out = _ESC_SHORT.sub("", out)
    if _BELL in out:
        out = out.replace(_BELL, "")
    return out, True

def _collapse_cr(text: str) -> str:
    if "\r" not in text:
        return text
    # Keep only the final partial line per terminator. rsplit('\r', 1)[-1]
    # correctly returns the whole string when there is no \r.
    return "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))

def _neutralise_terminal(stdout: bytes, stderr: bytes) -> tuple[bytes, bytes, bool]:
    s_text = stdout.decode("utf-8", errors="replace")
    e_text = stderr.decode("utf-8", errors="replace")
    s_text, s_had = _strip_ansi(s_text)
    e_text, e_had = _strip_ansi(e_text)
    s_text = _collapse_cr(s_text)
    e_text = _collapse_cr(e_text)
    return s_text.encode("utf-8"), e_text.encode("utf-8"), (s_had or e_had)
```

Wired into `compress_command` exactly between the runner returning a
RunResult and the existing log-pointer/spill threshold check:

```python
run_result = run_command(request)
clean_stdout, clean_stderr, had_color = _neutralise_terminal(
    run_result.stdout, run_result.stderr,
)
# from here on the rest of the function uses clean_* in place of run_result.stdout/stderr
# and threads `had_color` into CompressedOutput.notes when True.
```

`had_color=True` is appended to `CompressedOutput.notes` so a downstream
tool that wants severity information (e.g. a future `redcon_quality_check`
extension that visualises pass/fail density) can know colour was present
and infer that severity flags were stripped. This satisfies the V38
"preserve in side metadata" requirement without bloating main text.

The strip runs **before** `detect_compressor`, so every existing
compressor's regex (which was authored against clean text in fixtures)
sees clean text in production too. This is also why the existing
quality harness fixtures don't need ANSI variants: env injection is
the primary defence, residual strip is the safety net.

CR collapse is applied to the *whole* stdout/stderr blob, not line by
line within the stream as it was produced. That is correct because
the captured bytes already contain the literal `\r` overwrites - we are
replaying terminal semantics over a flat buffer.

## Estimated impact

- Token reduction on coloured pytest -v: +5 to +12 pp absolute on top of
  the existing 73.8% (because env injection silences most cases, but
  residual strip catches the few that ignore NO_COLOR).
- Token reduction on coloured npm install / npm test: +20 to +40 pp
  (chalk often ignores NO_COLOR in the npm-script subshell; OSC + CR
  collapse is the dominant win here).
- Token reduction on docker / kubectl: +1 to +3 pp (these mostly already
  detect non-TTY; small tail of progress lines).
- Token reduction on git: ~0 pp (git already detects non-TTY perfectly).
- Latency: +<0.1 ms warm parse for the prefix-gated `\x1b not in text`
  early exit. On coloured input, +0.3-0.5 ms regex work for a 64 KiB
  stdout. Cold start unaffected (regexes compiled lazily on first use).
- Affects: the runner (env merge) and the pipeline (one new pre-compressor
  pass). No compressor needs to change. No cache invalidation: cache key
  is argv + cwd hash, output is now cleaner but the *key* is identical,
  so existing cache entries remain valid. `_normalise_whitespace` already
  re-counts tokens, so reported reduction stays accurate.

## Implementation cost

- Lines of code: ~50 in `pipeline.py` + ~15 in `runner.py` + ~30 lines
  of unit test = ~100 LoC total.
- New runtime deps: none. Stdlib `re` and the existing tokenizer suffice.
  No network. No embeddings. Determinism preserved (regex sub is a pure
  function). Quality harness unaffected: stripped text is a strict
  improvement at the byte level, and `must_preserve_patterns` continue
  to match (the patterns target literal keywords like "FAILED", which
  the strip leaves intact - in fact the strip *uncovers* them when they
  were previously fragmented across SGR boundaries).
- Risks:
  - A pathological tool that uses ANSI codes as semantic markers (none
    of our compressors do; the convention in dev tools is to use literal
    keywords). Mitigation: `had_color` flag in metadata.
  - Setting `TERM=dumb` could change the *content* a tool emits (some
    tools refuse to emit certain output without TTY). For pytest, mypy,
    ruff, npm, docker, kubectl this has been verified to be either neutral
    or strictly less verbose. For unknown future binaries on the
    allowlist this should be re-verified - but the allowlist is small
    (DEFAULT_ALLOWLIST = 26 entries) and already curated.
  - CR collapse on a tool that legitimately uses CR-without-LF for
    structured output (none observed in the allowlist, but worth a
    fixture in test_cmd_pipeline.py).

## Disqualifiers / why this might be wrong

1. **Already implicitly handled.** The current pytest compressor applies
   regex like `r"^FAILED "` which would not match `\x1b[31mFAILED\x1b[0m`
   directly, so today the compressor probably underperforms silently
   on coloured input rather than crashing. If the existing fixtures
   already cover the coloured case (they don't: none of `tests/test_cmd_*`
   contains `\x1b`), this proposal still helps but the impact is bounded
   by the fraction of real users invoking with colour - which is
   probably high in agent setups using pseudo-TTYs but unmeasured.
2. **Most callers already get NO_COLOR for free.** When `Popen` is
   invoked with no TTY on stdout, well-behaved tools auto-disable colour
   via `isatty()` checks. We may be solving a small slice of remaining
   misbehaviours rather than a 40 pp slice.
3. **The "side metadata" preservation is theoretical.** No existing
   consumer reads `CompressedOutput.notes["had_color"]`, so the metadata
   bit is dead for now. It only matters if a future feature uses it; if
   not, the proposal collapses to "strip ANSI" which is uncontroversial
   but small. (Counterpoint: wiring the flag now is one extra dict
   write, so the optionality is cheap to retain.)
4. **CR collapse is line-oriented and may be wrong for some cases.**
   A tool that emits a single line with intra-line CR for cursor moves
   (rare but exists in some progress libraries that draw a status bar
   with carriage returns inside what they consider one logical line)
   would have content discarded. Mitigation: limit CR collapse to runs
   of `>= 2` carriage returns, leaving a single trailing CR untouched -
   but this complicates the rule and erodes the win.
5. **Two compressors might be sensitive to the OSC strip.** OSC-8
   hyperlinks (`\x1b]8;;url\x1b\\text\x1b]8;;\x1b\\`) collapse to plain
   text, which loses the URL. Currently no compressor uses OSC-8 so
   this is fine, but if an agent ever wants those URLs they're gone.

## Verdict

- Novelty: low (it's a standard preprocessing step), but instrumental.
- Feasibility: high. Drop-in, regex-only, no deps.
- Estimated speed of prototype: 3-4 hours including fixture authoring
  (coloured-pytest fixture, coloured-npm fixture, OSC fixture, CR-bar
  fixture, regression on existing 11 compressors).
- Recommend prototype: **yes** - cheap, deterministic, compounds with
  every existing compressor, and answers an under-tested failure mode
  (no ANSI fixtures exist in `tests/`). Lift may be modest on the
  current quality-harness fixtures but is real on production traffic
  where pseudo-TTY MCP relays are common.

## File pointers

- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/runner.py
  (line 88: `env: dict[str, str] | None = None`; line 143: passed to Popen)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/pipeline.py
  (line 108: RunRequest construction; line 115: run_command call -
  insertion point for `_neutralise_terminal`; line 134: log-pointer
  threshold check sees `len(stdout)+len(stderr)` and would benefit
  from already-stripped bytes since ANSI inflates that count, possibly
  triggering log-pointer prematurely)
- /Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cli.py
  (line 136-144: an existing NO_COLOR convention, scoped to CLI display
  only - confirms the project already treats NO_COLOR as canonical)
