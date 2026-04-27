# V44: Deep-link references (file:line) so agent re-fetches instead of receiving content

## Hypothesis

Several Redcon compressors today inline source content into their compressed
output: pytest emits failure tracebacks containing `>` snippet lines and
multi-line `E ` messages; lint VERBOSE emits per-issue message previews;
grep/rg COMPACT emits the actual matching line text; git_diff VERBOSE emits
added/removed hunk lines. We claim that for the subset of bytes that are
*verbatim source* (not derived diagnostic prose), replacing the body with
a deterministic deep-link `redcon://path:line[:col][+lines]` and exposing
a small `redcon_view` MCP tool that resolves it on demand is a net token
win whenever the agent's empirical body-fetch rate `f` falls below a
crossover threshold derived below. The trade is **token-now vs tool-call-
later**: we save the body's tokens unconditionally; we pay the link's
tokens unconditionally; and we pay the body's tokens *plus* the
round-trip framing only when the agent actually fetches.

The thesis is *not* "always replace content with links" but "for content
whose carrier is the filesystem (still on disk, line-addressable, won't
mutate within the turn) the body is recoverable, so its only legitimate
in-band cost is the cases the agent reads it, not the cases it skims".
This composes with V09 (selective re-fetch markers): V09 *signals*
"this candidate may need detail"; V44 *implements* one delivery mode
for that detail by replacing it with a resolvable link.

## Theoretical basis

Treat the agent as a noisy receiver and the compressor output as a
codeword over a two-channel system: (i) the compressed payload, and
(ii) a side-channel of follow-up tool calls that can resolve symbolic
references at known cost. This is the **lazy-evaluation / call-by-need**
analog of the Reisner-Plotkin demand-driven semantics, applied to LLM
context.

Let, per content carrier `i` (one snippet block, one diff hunk, one
match line):

```
B_i = tokens of the inline body
L_i = tokens of the deep-link replacement
F_i = round-trip framing tokens of one fetch (request + result preamble)
f_i = probability the agent actually fetches body i, in [0, 1]
```

Inlining body costs `B_i` deterministically.
Linking body costs `L_i + f_i * (B_i + F_i)` in expectation.

The link wins iff:

```
B_i  >  L_i + f_i * (B_i + F_i)
=> f_i  <  (B_i - L_i) / (B_i + F_i)         ... (1)
```

This is the **crossover fetch rate** `f*_i`. Below it, V44 saves tokens
in expectation. Aggregate over `n` carriers in one compressed output:

```
ExpectedSave = sum_i  (B_i - L_i) - f_i * (B_i + F_i)        ... (2)
```

Plug in concrete numbers from real Redcon outputs (cl100k):

- pytest snippet line, e.g. `>       assert calc(7) == 8`: B_snip ~= 9
  tokens. A whole 8-line snippet block is B_block ~= 60 tokens.
  A deep-link `redcon/cmd/x.py:42+8` ~= 6 tokens (fewer with prefix
  dedup against the FAIL header that already prints the path).
- lint VERBOSE per-issue body `[E711] comparison to None should be
  'is None'` ~= 12 tokens; deep-link with code retained
  `redcon/x.py:8:24 E711` ~= 7 tokens.
- grep match text (avg from ripgrep corpus on this repo) ~= 14 tokens;
  deep-link `redcon/x.py:42` ~= 4 tokens.
- git_diff hunk body, COMPACT already drops; VERBOSE shows up to 5
  added/5 removed: B_hunk ~= 60-160 tokens, deep-link ~= 6 tokens.
- F_i (round-trip framing for one MCP `redcon_view` call): the JSON
  envelope, _meta.redcon block, schema, level, token-count fields are
  ~25 tokens of fixed framing per response.

Crossover values from (1):

| carrier | B_i | L_i | F_i | f*_i |
|---|---|---|---|---|
| pytest snippet (8-line block) | 60 | 6 | 25 | (60-6)/(60+25) = 0.635 |
| lint message line | 12 | 7 | 25 | (12-7)/(12+25) = 0.135 |
| grep match line | 14 | 4 | 25 | (14-4)/(14+25) = 0.256 |
| diff hunk body (verbose) | 100 | 6 | 25 | (100-6)/(100+25) = 0.752 |

Interpretation:

- **pytest snippets and diff hunks**: agent must fetch >63% of the
  time before linking is worse. Empirically agents look at full
  failure context maybe 30-50% of the time when the failure is in a
  test they're actively writing, much lower when triaging a long
  test list. V44 wins comfortably for these.
- **lint messages**: crossover is 13.5% - very low. Lint messages
  are short and self-describing; replacing them with a link only
  pays off if the agent almost never opens them, which contradicts
  why they were in the output. **V44 likely loses on lint.**
- **grep matches**: crossover is 26%. Borderline. The match line is
  the *whole point* of grep output for the agent; replacing it with
  a path:line forces the agent to fetch to do anything. **V44 likely
  loses on grep at COMPACT** (the whole purpose of grep). It might
  win at ULTRA where we already collapse to counts.

Two-scenario simulation (worst/best case) on a representative
COMPACT output mix - one pytest run with 6 failures (avg 4-line
snippet each, so 6 carriers x ~30 tokens = 180 body tokens):

```
Today (no V44):           180 body tokens inline
V44 best case (f=0):      6 * 6 = 36 link tokens.   Save 144.
V44 worst case (f=1):     36 + 6 * (30 + 25) = 366. Lose 186.
Crossover (per (1)):      f* = (30 - 6) / (30 + 25) = 0.436
```

So pytest snippets: V44 saves tokens whenever the empirical body-fetch
rate per snippet is **below ~44%**, loses when above. The per-call
worst case is bounded by the existing snippet cap (8 lines per
failure already).

A second-order term: V44 also adds **latency** of one extra MCP
round trip per fetched body. At ~50-200 ms per round trip on a
local stdio MCP transport, fetching N bodies is a serial cost
unless the client batches. V09's `refetch_candidates` array is the
batching-friendly form; V44 should ride on it.

## Concrete proposal for Redcon

Three pieces, all opt-in:

**1. Deep-link grammar (read-only, deterministic)**

```
redcon://<path>[:<line>][:<col>][+<lines>][@<sha>]
```

- `path` is the canonical relative path produced by Redcon's existing
  path normaliser. No URL encoding needed for typical code paths;
  fall back to percent-encoding for non-ASCII or `:` in filenames.
- `line` and `col` are 1-indexed.
- `+<lines>` is an inclusive line span requested *by the link emitter*
  to default the resolver. The agent can override.
- `@<sha>` (optional) pins to a git blob hash. Lets the resolver
  detect a stale link if the file changed mid-session. Costs ~10
  tokens; default off.

The link has **zero rendering surprises** (uses scheme + colon, both
single-token in cl100k for ASCII). At cl100k, `redcon://` is one
multi-char token; the path follows the same merges grep already
exploits. Expected size 4-7 tokens for typical repo paths.

**2. New MCP tool `redcon_view`**

Sketched call (the resolver):

```python
# redcon/mcp/tools.py (sketch only - do NOT modify production)

def tool_view(
    *,
    target: str,                      # "redcon://path:line+lines"
    lines: int | None = None,         # override link's span
    context_before: int = 0,
    context_after: int = 0,
) -> dict:
    parsed = parse_deep_link(target)  # returns Path, line, span, sha
    if parsed.sha is not None:
        if blob_sha(parsed.path) != parsed.sha:
            return {"error": "stale_link", "current_sha": blob_sha(parsed.path)}
    body = read_lines(parsed.path,
                      start=parsed.line - context_before,
                      end=(parsed.line + (lines or parsed.span)) + context_after)
    return {
        "path": str(parsed.path),
        "line": parsed.line,
        "lines": body,                # already line-clipped
        "_meta": _meta_block("redcon_view"),
    }
```

Determinism: same path + same on-disk content + same range -> same
output. SHA pin makes that explicit when the caller cares.
Cold-start cost: zero - it's a `Path.read_text` plus slicing.

**3. Per-compressor link-mode toggle**

Expose `BudgetHint.link_mode: Literal["off","auto","always"]` (default
`"off"` until measured). When `auto`, a compressor whose canonical
typed result has line-addressable carriers swaps body for link iff
the carrier passes the per-carrier crossover guard (Eq. 1) using
*conservative defaults* `f_i = 0.3`, `F_i = 25`. No randomness; pure
threshold check on `B_i, L_i`.

Files that would change in a real implementation (NOT done here):

- `redcon/cmd/compressors/test_format.py`: `_format_compact` and
  `_format_verbose` get a `link_mode` parameter; snippet block
  becomes `redcon://path:line+8` when `link_mode == "auto"` and the
  carrier passes the guard.
- `redcon/cmd/compressors/lint_compressor.py`: VERBOSE emits the
  `path:line:col` it already has; in `link_mode == "auto"` the
  message body is suppressed when `B - L > f_default * (B + F)`.
  Almost no lint messages clear that bar; expect link mode to be a
  near-no-op here, which is the correct outcome.
- `redcon/cmd/compressors/git_diff.py`: VERBOSE hunk-body block
  becomes `redcon://path:hunk_new_start+new_lines`. Strong save.
- `redcon/cmd/compressors/grep_compressor.py`: COMPACT *keeps* the
  match text by default (V44 loses there per the table). Provide
  an explicit `link_mode == "always"` for callers who really want
  link-only output.
- `redcon/mcp/tools.py`: register `redcon_view`. No protocol bump:
  it's a new tool name, MCP discovers it.

Pseudo-code for the per-carrier guard, central:

```python
# redcon/cmd/links.py (new module)
F_DEFAULT = 25            # MCP framing tokens per response
F_DEFAULT_FETCH = 0.3     # conservative default agent body-fetch rate

def link_wins(body_tokens: int, link_tokens: int,
              f: float = F_DEFAULT_FETCH, F: int = F_DEFAULT) -> bool:
    if body_tokens <= link_tokens:
        return False
    crossover = (body_tokens - link_tokens) / (body_tokens + F)
    return f < crossover

def to_link(path: str, line: int, span: int = 1, sha: str | None = None) -> str:
    parts = ["redcon://", path, ":", str(line)]
    if span > 1:
        parts += ["+", str(span)]
    if sha is not None:
        parts += ["@", sha[:10]]
    return "".join(parts)
```

Sketch of one-line client usage that the link convention enables:

```
redcon_view target="redcon://redcon/cmd/pipeline.py:42+8" lines=8
```

## Estimated impact

- **Token reduction (best case, f=0)**: pytest COMPACT median saves
  ~30-40% of remaining body tokens after current compaction (~5-10 pp
  on top of the 73.8% baseline). git_diff VERBOSE saves
  ~15-25 pp. Grep COMPACT no change in `auto` mode; `always`
  saves ~5 pp at the cost of forcing fetches. Lint VERBOSE: ~0,
  by design.
- **Token reduction (worst case, f=1)**: net *loss* of `n * F` per
  output where `n` is carrier count. Bounded; for pytest at 6
  failures, n*F = 150 tokens, less than the typical body savings.
- **Crossover**: per Eq. (1), per-carrier `f*` ranges 0.13 (lint)
  to 0.75 (diff hunks). Agent traces will determine actual
  per-carrier f.
- **Latency**: +1 MCP round trip per fetched body. For pytest with
  one followed-up failure, that's ~50-200 ms. For diff verbose where
  the agent often wants to see one specific hunk, this is ~1
  round trip, acceptable.
- **Affects**: pytest, cargo_test, npm_test, go_test (all share
  test_format.py), lint, grep, git_diff. Other compressors'
  outputs are not source-line carriers (git_status, git_log,
  docker, pkg_install, kubectl, ls/find).

## Implementation cost

- ~250 LOC: link grammar + parser + emitter (~80), `redcon_view`
  MCP tool (~70), per-compressor link-mode plumbing (~50), tests/
  fixtures/quality-harness updates (~50).
- No new runtime deps. No network. Determinism preserved (slicing
  a file at fixed line range is deterministic; SHA pin is
  optional).
- Cache key: `link_mode` becomes part of the canonicalised hint
  fingerprint, strict superset of the current key (BASELINE
  constraint #6).
- Quality harness: must-preserve patterns may currently match the
  body literal; replacing with a link breaks that. Mitigation: when
  `link_mode != "off"`, `must_preserve_patterns` becomes the *path*
  carrier, not the body content. This narrows the guarantee but is
  honest about the new mode (the body is recoverable on demand).
- Risks to determinism: zero, given fixed thresholds and no
  randomness.
- Risks to robustness: stale-file races between emit time and
  view time. SHA-pin opt-in mitigates; without it the agent may
  receive content that doesn't match the link's intent. For
  Redcon's typical "single agent turn over a working tree" use
  case, this race is rare but real.
- Risks to must-preserve: the body is no longer transmitted at
  COMPACT; if the test harness asserts a body literal survives, it
  fails. Spec change required.

## Disqualifiers / why this might be wrong

1. **Empirical fetch rate may exceed crossover.** If real agents
   actually do open >50% of pytest snippets they see (because the
   snippet *is* what the agent uses to write the fix), V44 net-
   loses on the most carrier-rich compressor. This needs a real
   recorded-trace measurement before flipping default; otherwise
   we're optimising against a synthetic prior.
2. **Latency is real and compounds.** A single agent turn that
   fetches 5 bodies serially adds ~500 ms-1 s end-to-end. On a
   token budget that's "free" but on user perception it isn't. The
   token savings only matter inside the model's context; the user
   pays for latency outside it. This trade may not be the right
   one even if (2) is positive.
3. **Already partially exists.** The log-pointer tier in
   `pipeline.py::_spill_to_log` is V44 with coarse granularity
   (whole command output -> file pointer + tail). V44 just
   re-applies the same idea at carrier granularity. The argument
   for V44 is therefore "extend an existing pattern", which is a
   weaker novelty claim than "introduce a new mechanism". Map:
   log-pointer = link to spill log; V44 = link to source file.
4. **Coupling to filesystem state.** Deep-links assume the file
   on disk hasn't changed since the carrier was emitted. For
   pytest output, the test ran against a specific file revision;
   if the agent edits the file before fetching, the link resolves
   to *post-edit* content, which is wrong. SHA-pin fixes this but
   costs tokens; without it, V44 silently lies in this race. This
   is a class of bug the current inline-body design literally
   cannot have.
5. **Self-fulfilling fetch rate.** Making the link *available* may
   *induce* fetches the agent would have skipped under inline
   bodies (because the inline body wasn't worth re-asking for, but
   a link is cheap to act on). This is the same agent-bias risk
   that V09 has and V94 (self-instructing prompts) addresses.
6. **MCP tool surface inflation.** `redcon_view` is the seventh
   redcon_* tool. Each new tool increases prompt-side tool-list
   tokens for every call into Redcon, *whether or not the tool is
   used*. At ~30 tokens for a tool's name + description + schema,
   adding `redcon_view` costs every Redcon-using session ~30
   tokens regardless. Net win only kicks in once V44 has saved
   30+ body tokens in that session.

## Verdict

- Novelty: **medium**. The mechanism (replace content with a
  resolvable reference) is standard lazy evaluation / call-by-need.
  Novel for Redcon as a per-carrier mode rather than a whole-output
  mode (the latter exists as log-pointer). Composes with V09 cleanly.
- Feasibility: **high**. The infrastructure is straightforward; the
  hard part is the empirical fetch-rate calibration that decides
  whether to ship.
- Estimated speed of prototype: **3-5 days** for grammar + view
  tool + one compressor (pytest) behind `link_mode="auto"`, plus a
  recorded-trace replay harness to measure crossover f in practice.
  **2-3 weeks** to land across all six affected compressors with
  proper quality-harness rework.
- Recommend prototype: **conditional-on** instrumenting an agent
  trace corpus first. If observed `f` per carrier class is below
  the per-carrier crossover for at least three of {pytest snippet,
  diff hunk, grep match}, build it. If `f` is consistently above
  ~0.6 across carriers, do not - V44 then is a slow-down with no
  token win. The right way to ship V44 is with V09 already in
  place: V09's `refetch_candidates` becomes the natural batched
  call site, and V44 becomes its rendering format.
