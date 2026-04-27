# V80: Lazy-deserialise cached entries

## Hypothesis
On a `redcon_run` cache hit, the entire `CompressionReport` (and the nested `CompressedOutput`, plus its `text` payload, plus `notes`, plus the `CommandCacheKey` tuple) is materialised by the cache backend before the caller has even decided which fields it needs. For the in-process `MutableMapping[str, CompressionReport]` baseline this is free (the Python object is already alive). But for any out-of-process backend - SQLite-WAL (V76), Redis, or a file-backed shelve - the hit pays a fixed deserialisation cost (pickle / JSON / msgpack) proportional to `len(text)`. The hypothesis: store cache entries as raw bytes plus a small header containing already-decoded scalar metadata (digest, level, schema, original_tokens, compressed_tokens, must_preserve_ok, truncated, returncode, duration) and defer deserialising `text` and `notes` until a property accessor reads them. For VERBOSE long outputs (10-50 KiB text) this collapses the hit cost from "decode N KiB + materialise dataclass" to "decode 200 B header + return proxy".

## Theoretical basis
This is the standard lazy-decoding pattern from columnar/row-store databases (Parquet / FlatBuffers / Cap'n Proto) and from Python's own `email.message_from_binary_file` (header parsing is eager, body bytes lazy). The model:
```
T_eager = T_io(N + H) + T_decode(N) + T_alloc(N)        # current path
T_lazy  = T_io(H)     + T_decode(H) + T_alloc(H) + p * (T_io(N) + T_decode(N))
```
where `H` is header size (~150-300 bytes for the metadata dataclass), `N` is `len(text) + len(notes_blob)`, and `p` is the probability that the caller actually reads `text` on this hit (always 1 if the hit is consumed, less than 1 only if the hit is e.g. probed for `cache_hit` and discarded - which is not a real Redcon flow).

Substituting `p = 1`:
```
T_lazy - T_eager = T_io(H) - T_io(N + H)
                 ~ -T_io(N)         (header reads dominate trivially)
```
The savings asymptote to zero as `p -> 1`. The only regime where lazy decode wins is when `p < 1` OR when decode-time dominates IO (large `N`, fast disk, slow pickle). On SQLite with `text BLOB` columns and `pickle.loads`, decode cost on a 30 KiB string is roughly 50-100 us; SQLite row read is ~30-80 us. So `T_decode(N) ~ T_io(N)` and the lazy split is at best 2x on the no-text-access branch and zero on the text-access branch. **Therefore: lazy deserialise has a ceiling of "tens of microseconds per hit" and only on backends that aren't yet in tree.**

For the *current* `MutableMapping[str, CompressionReport]` baseline (`_DEFAULT_CACHE` at `pipeline.py:57`), the cache holds Python objects directly. There is no serialisation at all on put/get. `_with_cache_hit` (pipeline.py:214) constructs a fresh `CompressionReport` only to flip `cache_hit=True`; this is one slot-dataclass allocation, not a deserialisation. Lazy decoding has nothing to optimise here.

## Concrete proposal for Redcon

Files involved (proposed; no production source modified by this note):
- `redcon/cache/backends.py` - introduce a `LazyReport` proxy class wrapping (header_dict, payload_bytes, payload_codec).
- `redcon/cmd/pipeline.py::_with_cache_hit` - accept `LazyReport | CompressionReport`; force materialisation only on `.output.text` access.
- `redcon/cmd/types.py::CompressedOutput` - if we want to keep dataclass immutability, expose `text` via `__getattr__` on a sibling proxy class instead of monkey-patching the existing `slots=True` frozen dataclass. (This is the awkward bit.)

Sketch:
```python
@dataclass(frozen=True, slots=True)
class LazyReport:
    header: dict        # digest, level, schema, *_tokens, returncode, duration
    payload: bytes      # zstd/raw of (text, notes)
    codec: str          # "raw" | "zstd" | "pickle"
    cache_key: CommandCacheKey
    cache_hit: bool = True

    @cached_property
    def output(self) -> CompressedOutput:
        text, notes = _decode(self.payload, self.codec)
        return CompressedOutput(
            text=text,
            level=CompressionLevel(self.header["level"]),
            schema=self.header["schema"],
            original_tokens=self.header["original_tokens"],
            compressed_tokens=self.header["compressed_tokens"],
            must_preserve_ok=self.header["must_preserve_ok"],
            truncated=self.header["truncated"],
            notes=tuple(notes),
        )
```
SQLite schema (only relevant under V76):
```
CREATE TABLE cmd_cache (
    digest TEXT PRIMARY KEY,
    header BLOB NOT NULL,    -- ~200 B msgpack
    payload BLOB NOT NULL,   -- text + notes, zstd-compressed
    codec TEXT NOT NULL,
    inserted_at INTEGER NOT NULL
);
```
Header read alone is one row-fetch with `SELECT header FROM cmd_cache WHERE digest = ?` returning ~200 B; payload is read on first `.output` touch via `SELECT payload, codec FROM cmd_cache WHERE digest = ?`. Two round-trips on the cold-payload branch is acceptable because `p < 1` is the entire premise.

## Estimated impact
- Token reduction: zero. This is a latency/IO play, not a compression play. Vector picked the wrong theme axis.
- Latency: in-process cache - **zero benefit** (no serialisation in the first place). SQLite-WAL backend (when V76 ships) - estimated 50-150 us saved per cache hit on VERBOSE entries that are probed but not consumed (rare). On consumed hits, savings are 0-30 us (decode amortised across two SQL calls instead of one). Redis backend - similar; saves the network round-trip of fetching `text` if the caller only looked at `cache_hit`.
- Affects which existing layers: only `redcon/cache/backends.py` and any backend that crosses a serialisation boundary. The pipeline-level `_DEFAULT_CACHE: dict[str, CompressionReport]` is untouched. Quality harness, must-preserve checks, determinism: untouched.

## Implementation cost
- Lines of code: ~80 LOC for `LazyReport` proxy and codec, ~40 LOC of backend changes per backend that opts in.
- New runtime deps: optional `zstandard` for header/payload split (otherwise pickle alone, which is in stdlib). No network, no embeddings.
- Risks:
  1. Determinism: codec must be byte-deterministic. Pickle is not (dict ordering, set ordering); msgpack with sorted keys is. Adopt msgpack-with-sort-keys or a hand-rolled JSON-with-sorted-keys.
  2. Frozen-dataclass interaction: `CompressedOutput` is `frozen=True, slots=True`. A `LazyReport.output` `cached_property` therefore cannot live on the existing dataclass; it must be a sibling proxy. Callers that today do `report.output.text` must either work transparently (proxy implements `__getattr__`) or be updated.
  3. The optimisation is invisible until V76 (or another out-of-process backend) lands. Ship-without-driver means you write code that exercises only its degenerate branch in CI.

## Disqualifiers / why this might be wrong
1. **The default cache backend has no deserialisation step.** `_DEFAULT_CACHE: MutableMapping[str, CompressionReport]` (pipeline.py:57) holds live Python objects keyed by digest. `cache.get(...)` returns the original `CompressionReport`. There is nothing to defer. The vector premise (deserialisation is a fixed cost) does not apply to the in-tree implementation.
2. **The savings asymptote to zero on the consume-text branch.** Every realistic Redcon caller eventually formats `report.output.text` into the agent's tool result; `p` (probability text is read) is essentially 1. Lazy decode wins only when `p < 1`, which it rarely is.
3. **V76 (SQLite-WAL persistent cache) is not yet implemented.** V80's only meaningful target backend is theoretical. Building V80 first means building scaffolding for a backend that doesn't exist; sequencing should be V76 -> measure deserialise cost on real traces -> decide if V80 is needed. Almost certainly the answer is "msgpack is fast enough, no need for laziness".
4. **Already partly subsumed by `_with_cache_hit`.** The current cache hit path (pipeline.py:103-105, 214-223) returns the cached report essentially free. The only allocation is the `_with_cache_hit` rebuild, which exists solely to flip `cache_hit=True` on a frozen dataclass. A simpler optimisation - mutate a single field via `dataclasses.replace` once and stash both versions, or carry `cache_hit` outside the report - would erase the only per-hit allocation in the in-process path. That's a 5-line change with a measurable benefit; V80 is a 200-line scaffold with no measurable benefit until V76 ships.
5. **Lazy semantics complicate the MCP `_meta.redcon` block** (BASELINE.md mentions it). That block reads `level`, schema, token counts, `cache_hit` - all header fields, fine for lazy. But it also runs through the response-emission path which inspects `text` length. Touching `text` materialises the lazy payload anyway. So in MCP-tool flow, `p = 1` again.

## Verdict
- Novelty: **low**. Lazy decode is textbook, and within Redcon's actual code path it has nothing to bite on: the per-process `MutableMapping` cache holds live objects with zero serialisation overhead. The optimisation only becomes legible against an out-of-process backend that does not yet exist. Cite **V76 (SQLite WAL persistent cache shared across processes)** as the load-bearing dependency: V80 is V76's final 5% optimisation, not a standalone vector.
- Feasibility: high *if* V76 is in tree; medium otherwise (you would be writing tests for behaviour that only triggers in a future backend).
- Estimated speed of prototype: ~1 day on top of V76, ~3 days standalone (because you would have to build a stub serialising backend just to demonstrate the lazy split has any effect at all).
- Recommend prototype: **no, not as an independent effort.** Roll into V76 as an optional codec/laziness decision made *after* V76's first benchmark numbers are in. If the V76 deserialise step shows >100 us median on VERBOSE entries, revisit V80. Until then, the dominant in-process win is killing the `_with_cache_hit` rebuild (one allocation per hit), which is unrelated to lazy decoding.
