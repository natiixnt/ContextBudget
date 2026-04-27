# V67: Kubernetes events stream compressor (group by reason+object)

## Hypothesis

`kubectl get events --all-namespaces` (and `--watch`, and the standard
`kubectl describe` events footer) emit a long table where the
overwhelming majority of rows are *repetitions of the same (reason,
involvedObject.kind, involvedObject.name) tuple* with a counter and a
nearly-identical message. Existing `KubectlGetCompressor`
(`redcon/cmd/compressors/kubectl_compressor.py`) treats events as just
another resource list: header detection, per-row "name status extras"
emission, capped at 40 rows, and a "+N more" tail. This is wrong for
events because (a) the `NAME` of an event is a synthetic
`<involvedObject>.<hex>` string the agent never wants to read, (b) the
useful payload is `(REASON, OBJECT-KIND, MESSAGE)` not the row itself,
and (c) Kubernetes already sends a `COUNT` column carrying the
in-cluster aggregation - the compressor is currently re-emitting per
occurrence what the API server has already deduplicated. The claim is
that grouping by `(reason, involvedObject.kind, involvedObject.name)`
and emitting one templated line per group with a sample message, first
and last seen timestamps, and the sum of `COUNT`, yields a 70-90%
compact-tier reduction on realistic event streams, comparable to
`grep_compressor` (76.9%) and beating the generic `kubectl get` path
which hovers near 33% on weakly-redundant lists.

## Theoretical basis

Kubernetes event streams have a strong empirical Markov structure on
the (reason, kind) pair. Empirically, on a node with a CrashLoopBackOff
pod, you get a sequence like

```
BackOff           Pod   nginx-7d  Back-off restarting failed container
Unhealthy         Pod   nginx-7d  Liveness probe failed: HTTP probe failed
Pulled            Pod   nginx-7d  Container image "nginx:1.21" already present on machine
Created           Pod   nginx-7d  Created container nginx
Started           Pod   nginx-7d  Started container nginx
BackOff           Pod   nginx-7d  Back-off restarting failed container
Unhealthy         Pod   nginx-7d  Liveness probe failed: HTTP probe failed
... (50x more)
```

If we model each row as a triple T = (reason, kind, message-template)
drawn from a finite alphabet, the empirical entropy H(T) is bounded
above by the number of *distinct triples* seen, not by the row count.
A k-row stream with d distinct triples has

    H(stream) <= log2(d!) + sum_i n_i * log2(n_i / k)   bits

(the first term is the ordering, the second is the multinomial type
class). For typical CrashLoopBackOff or NodePressure traces, d is in
the single digits while k can be 200+. The lossless lower bound on
*content* (ignoring ordering, which an agent does not need beyond
"first/last seen") is therefore

    bits_lossless ~= d * (avg_template_len * 4)   # rough cl100k bytes-to-bits

For d = 7, avg_template_len = 60 chars, we get ~1.7 kbits ~= 425
tokens. The raw 200-row table is roughly 200 * (60 + 30 + 12) ~= 20 KB
~= 5000 tokens. Theoretical reduction is 1 - 425/5000 = **91.5%**. So
the structural ceiling for an events-grouping compressor is on the
order of git diff (97%), not the 33% we currently achieve on `kubectl
get`.

The argument that grouping is *correct* (not just compact) reduces to:
the API server's `Event.count`, `firstTimestamp`, `lastTimestamp`
fields are exactly the sufficient statistics for a Poisson rate
estimator over (reason, kind, name) - they preserve all information an
SRE agent uses ("how often, since when, last when, what triggered").
The MESSAGE field's variation across occurrences of one (reason, kind,
name) tuple is dominated by floating-point ages and pointers
("started 0.341s ago" vs "started 0.412s ago"); a *templated* sample
that masks numbers and IPs collapses these into one line losslessly
modulo agent-irrelevant noise.

## Concrete proposal for Redcon

Three edits, no new file required:

1. **Detection.** In `redcon/cmd/compressors/kubectl_compressor.py`,
   recognise the events shape *inside* `KubectlGetCompressor.compress`
   based on `_kind_from_argv(ctx.argv)` matching `events` / `event` /
   `ev`, OR (more robustly) on the column header containing
   `{"REASON", "OBJECT", "MESSAGE"}` or the modern
   `{"LAST SEEN", "TYPE", "REASON", "OBJECT", "MESSAGE"}`. The header
   sniff is preferable because agents often invoke `kubectl describe`
   which has the same events block at the bottom.

2. **New parser** `parse_kubectl_events(text) -> KubeEventsResult`.
   Reuse the existing column-offset machinery; do not write a new
   tabular parser. The output dataclass added to
   `redcon/cmd/types.py`:

   ```python
   @dataclass(frozen=True, slots=True)
   class KubeEventGroup:
       reason: str
       kind: str            # e.g. "Pod"
       name: str            # e.g. "nginx-7d"
       namespace: str | None
       count: int           # sum across rows in group
       first_seen: str | None
       last_seen: str | None
       sample_message: str  # template-masked, <= 80 chars
       severity: str        # Normal / Warning, picked up from TYPE col
   ```

3. **Formatter** `_format_events(result, level)` replacing the generic
   path when `result` is `KubeEventsResult`:

   ```python
   def _format_events(res, level):
       head = f"kubectl events: {res.total_count} occurrences in {len(res.groups)} groups"
       warn = sum(1 for g in res.groups if g.severity == "Warning")
       if warn:
           head += f" ({warn} Warning)"
       if level == CompressionLevel.ULTRA:
           # emit only Warning groups, top-K by count
           top = [g for g in res.groups if g.severity == "Warning"][:8]
           lines = [head] + [f"!{g.reason}/{g.kind}/{g.name} x{g.count}" for g in top]
           return "\n".join(lines)
       lines = [head]
       limit = len(res.groups) if level == CompressionLevel.VERBOSE else 30
       for g in res.groups[:limit]:
           ns = f"{g.namespace}/" if g.namespace else ""
           sev = "!" if g.severity == "Warning" else "-"
           lines.append(
               f"{sev} {g.reason} {g.kind}/{ns}{g.name} x{g.count} "
               f"[{g.first_seen}..{g.last_seen}] {g.sample_message}"
           )
       if len(res.groups) > limit:
           lines.append(f"+{len(res.groups) - limit} more groups")
       return "\n".join(lines)
   ```

   Message templating (cheap, deterministic): replace numeric runs of
   length >= 2 with `N`, replace IPv4/IPv6 dotted-quads with `IP`,
   replace `0x[0-9a-f]+` with `HEX`, replace UID-shaped strings
   (`[0-9a-f]{8}-[0-9a-f]{4}-...`) with `UID`. After masking, group
   messages within (reason, kind, name) by template; pick the longest
   distinct template as the sample (agent-readable, tie-break on first
   occurrence for determinism).

4. **must\_preserve\_patterns.** Currently empty. Add Warning event
   reasons (`FailedScheduling`, `Unhealthy`, `BackOff`, `OOMKilled`,
   `Evicted`, `FailedMount`, `NodeNotReady`) so the harness verifies
   any of these surviving COMPACT. ULTRA exempt by convention.

5. **Cache.** No change. Output schema becomes `kubectl_events`
   (distinct from `kubectl_get`) so `_meta.redcon.schema` discrimination
   on the MCP side works.

## Estimated impact

- Token reduction: estimated **75-90% at COMPACT, 95%+ at ULTRA** on
  `kubectl get events` outputs of 100+ rows. Quantification on a
  hand-rolled 200-row CrashLoopBackOff fixture (worked through above):
  raw ~5000 tokens, grouped 7-template output ~425 tokens, **91.5%
  reduction**. On `kubectl get pods` (which the same compressor
  already handles): zero change, the dispatch is by header sniff.
- Latency: a single extra dict-lookup per row in the parse loop
  (O(rows)). On a 1000-row events stream: a few hundred microseconds,
  far below subprocess overhead. Cold start unchanged - same module,
  no new import.
- Affects: `kubectl_compressor.py`, `types.py` (one new dataclass),
  golden fixtures for kubectl tests (must add an events fixture; none
  exists today). Cache key unaffected. `_meta.redcon.schema` gains a
  new value `kubectl_events`.

## Implementation cost

- ~120 LOC: 30 LOC for `parse_kubectl_events`, 25 LOC for
  `_format_events`, 15 LOC for `_template_mask`, ~10 LOC for the
  dispatch fork in `compress`, ~10 LOC for the new dataclass, ~30 LOC
  of tests including a fixture and a determinism check.
- No new runtime deps. All stdlib (`re` already imported).
- Risk to determinism: nil. Grouping uses a `dict` keyed on a
  deterministic tuple; group ordering is by (-count, reason, kind,
  name) - a total order. Sample-message tiebreak is "longest
  template, then first occurrence" - both deterministic.
- Risk to robustness fuzz: the template masker must be linear-time on
  pathological input; the regex `\d{2,}` is fine but `[0-9a-f]{8}-`
  with backtracking could be coaxed into quadratic form. Fix: anchor
  the UID regex with explicit length classes `{8}-{4}-{4}-{4}-{12}`,
  no `+` or `*`. The 5000-newlines and binary-garbage cases are
  already covered by the parent `KubectlGetCompressor.compress` path
  before we reach events handling.
- Risk to must\_preserve: adding patterns is conservative. The
  Warning-reason set is small and well-defined; on COMPACT we always
  emit Warning groups before truncation, so the patterns hold for any
  realistic input that contains them.

## Disqualifiers / why this might be wrong

1. **Already done in disguise.** The existing
   `KubectlGetCompressor._format` takes the first 40 resources and
   dumps "name status extras", and at ULTRA emits only the count line.
   On a homogeneous events table where all 200 rows fit in 40 lines'
   worth of tokens, the difference between "+160 more" and a 7-line
   group view is real but bounded. Counter: realistic event streams
   are exactly *not* homogeneous - they mix Normal Pulled/Started with
   Warning BackOff/Unhealthy, and the agent needs the Warning subset
   prioritised, which the 40-row truncation will not guarantee.
2. **`-o yaml` and `-o json` are the agent's escape hatch.** A
   power-user agent that wants events *will* ask for JSON, and the
   current pipeline routes JSON through the byte path with no
   compression. So the table form might be a strawman. Counter: in
   practice agents copy whatever was in the user's terminal, which is
   the default table form; the JSON escape hatch is theoretical.
   Still, V67 should declare it does **not** apply to `-o json/yaml`
   and let those fall back. The detector already keys on header
   tokens, which JSON does not produce - safe by construction.
3. **`COUNT` column is being deprecated.** Modern Kubernetes (>=1.19)
   uses `events.k8s.io/v1` where the semantic is one Event object per
   occurrence (no count), and aggregation is done client-side with
   `LAST SEEN` deltas. So the "API already aggregated" claim is
   weakening. Counter: this *strengthens* V67, not weakens it - if the
   server stops aggregating, the client side (us) becomes the only
   place the dedup happens. Without V67 the agent eats the full
   stream.
4. **`kubectl describe` events block is a different shape.**
   `describe` indents the events table, has a different header
   (`Type Reason Age From Message`), and embeds it after a key/value
   block. We claimed the same compressor handles both; that requires
   either dispatching `describe` to a different compressor (and adding
   `_is_kubectl_describe` to `registry.py`) or making the events
   detector tolerant of leading whitespace and arbitrary preceding
   lines. The first is cleaner but is more work; the second is fragile.
5. **Determinism of sample-message selection.** "Longest template,
   tie-break on first occurrence" requires the parser to remember
   *insertion order*, which `dict` preserves in CPython 3.7+ but is a
   language guarantee, not a Redcon-private one. Fine; just call it
   out.
6. **Quality floor at COMPACT.** On a 30-row events fixture, raw
   tokens ~700, grouped output ~120, reduction = 83% (passes the 30%
   floor easily). On a 5-row, 5-distinct events fixture, raw ~120
   tokens, grouped output ~110 tokens, reduction = 8% - **fails the
   30% COMPACT floor**. Fix: the existing "raw < 80 tokens skips
   floor" rule (BASELINE quality harness, paragraph 3) catches the
   degenerate case at <= 80 tokens but not at 81-150. We'd need to
   either widen that gate for `kubectl_events` or accept that the
   compressor falls back to the existing `kubectl_get` formatter when
   `len(groups) >= 0.8 * len(rows)` (no aggregation worth doing). The
   second is simple and safe.

## Verdict

- Novelty: **medium**. The technique (group-by + sample-message
  templating) is straightforward; the *gap in the current
  implementation* is real and the reduction estimate is in the same
  weight class as the strongest existing compressors. Below
  "breakthrough" because it does not move the needle across multiple
  compressors - it lifts one specific subcommand from a weak ~33% to
  a strong ~85%.
- Feasibility: **high**. Stdlib only, ~120 LOC, no architectural
  changes, fits the existing `KubectlGetCompressor` cleanly, schema
  discriminator already supported by the `_meta.redcon` convention.
- Estimated speed of prototype: **half a day** including a fixture, a
  determinism test, and the `must_preserve_patterns` extension. Plus
  ~2 hours if we also add the `kubectl describe` dispatcher (point 4
  in disqualifiers).
- Recommend prototype: **yes**, with the 0.8-aggregation-ratio
  fallback baked in from the start to keep the COMPACT quality floor
  honest on small event sets.
