# V77: Shared-memory IPC for VS Code extension <-> CLI

## Hypothesis
Replace the current "spawn subprocess + capture stdout + JSON.parse" pattern between the
VS Code extension and the `redcon` CLI with a long-lived daemon and a shared-memory ring
buffer for request/response. Predicts that for hot, frequent calls (status bar refresh,
`plan` on save, decoration providers reading `run.json`) the per-call wall-clock drops
because we eliminate (a) Python interpreter cold-start, (b) JSON serialisation/parse on a
multi-MB `run.json`, and (c) repeated stdio kernel buffer copies. Honest expectation:
the win is overwhelmingly from killing process-per-call, not from shared memory itself.
JSON serialisation is almost certainly **not** the bottleneck; CLI startup and the actual
scan/scoring pass dominate.

## Theoretical basis
Current per-call cost (audited from `vscode-redcon/src/redcon.ts::exec`):

```
T_call = T_spawn + T_python_import + T_scan + T_compress + T_serialize_py
       + T_pipe_copy + T_parse_js
```

Empirical orders of magnitude on a medium repo (estimated from BASELINE: lazy-imports
already shaved ~62% of cold-start, so cold-start was non-trivial pre-fix):

| Term | Order |
|---|---|
| `T_spawn` (fork+exec Python) | 30-80 ms |
| `T_python_import` (post lazy-import) | 80-200 ms cold, ~30 ms warm-cache |
| `T_scan + T_compress` | 200-2000 ms (dominant for `pack`) |
| `T_serialize_py` (json.dumps of run.json, ~200 KB-2 MB) | 5-30 ms |
| `T_pipe_copy` (kernel buffer roundtrip) | 1-5 ms |
| `T_parse_js` (JSON.parse) | 5-30 ms |

Serialisation+parse share: roughly `(T_serialize + T_parse) / T_call`. For a 500 ms
`pack` call that's ~10-60 ms / 500 ms = 2-12%. For a fast `doctor --format json` call
(<50 ms of real work) the share rises to maybe 20-40%, but the absolute saving is small.

A daemon model with SHM amortises spawn+import to zero across N calls in a session:

```
T_daemon_call = T_shm_write + T_signal + T_handler + T_shm_read
              + T_scan + T_compress
              ~= T_scan + T_compress + O(100 us)
```

Saving per call vs cold subprocess: `~T_spawn + T_python_import + T_serialize + T_parse`
~= 100-300 ms cold, ~50-90 ms warm. Across a 50-call session that is 5-15 s saved -
real, but bounded.

The shared-memory part specifically saves only the pipe copy and JSON encode/decode
(if we go further and use a binary frame). That is the 10-60 ms slice. Switching from
stdio to SHM while keeping JSON saves nearly nothing (~1-5 ms).

## Concrete proposal for Redcon
Two-layer change. Layer 1 is the entire honest win; layer 2 is the actual V77 vector
and is mostly engineering theatre.

**Layer 1: persistent CLI daemon.**
- New entrypoint `redcon serve --socket .redcon/sock` reusing existing `compress_command`
  and the file-side pipeline. Already half-justified by per-process cache in
  `redcon/cmd/pipeline.py` (cache dies on every call today).
- Extension change in `vscode-redcon/src/redcon.ts`: replace `spawn(...)` per call with
  a persistent `net.Socket` to a Unix domain socket (Windows: named pipe). JSON-RPC 2.0
  framing over newline-delimited JSON. Fall back to subprocess if daemon not running.
- Cache survives between calls -> immediate compounding win, separate from V77.

**Layer 2 (V77 proper): SHM ring buffer for response payloads.**
Used only after Layer 1 is in place. Pseudo-code (Python side):

```python
# redcon/ipc/shm.py (new)
import mmap, struct, posix_ipc
class ShmRing:
    HDR = struct.Struct("<QQQQ")  # head, tail, cap, seq
    def __init__(self, name, cap=8 << 20):
        self.shm = posix_ipc.SharedMemory(name, posix_ipc.O_CREAT, size=cap)
        self.buf = mmap.mmap(self.shm.fd, cap)
    def publish(self, payload: bytes) -> tuple[int, int]:
        # writes payload at tail, returns (offset, length); wakes peer via eventfd
        ...
    def consume(self, offset, length) -> memoryview:
        return memoryview(self.buf)[offset:offset+length]
```

Protocol:
1. Daemon receives JSON-RPC request on socket.
2. Computes response. If `len(payload) > 64 KiB`, writes payload bytes (still JSON,
   or msgpack/cbor) into SHM ring, returns `{"shm": {"name": "...", "off": N, "len": M, "seq": S}}`.
3. Extension reads `(off, len)` from socket, opens the same SHM segment via Node N-API
   addon, decodes in place with `JSON.parse(Buffer.from(view))`. (No native addon =
   no SHM win in Node; this is a hard prerequisite.)

Required new files:
- `redcon/ipc/server.py` - asyncio JSON-RPC dispatcher.
- `redcon/ipc/shm.py` - ring buffer.
- `vscode-redcon/src/ipcClient.ts` - socket + N-API SHM read.
- `vscode-redcon/native/` - C/Rust N-API addon (~300 lines). This is the deal-breaker.

## Estimated impact
- Token reduction: **0**. This vector does not change any compressor output.
- Latency:
  - Layer 1 (daemon, no SHM): -100 to -300 ms cold, -50 to -90 ms warm per call. Real win.
  - Layer 2 (SHM on top): additional -5 to -50 ms only on calls with >100 KB responses
    (`pack` returning a big `run.json`, `benchmark`). Most calls do not move at all.
- Affects: `vscode-redcon/src/redcon.ts`, all 9 wrapper functions; new daemon entrypoint
  in `redcon/cli.py`; opt-in path so non-VS-Code use is unaffected.
- Cache layer: massive practical effect because per-process cache (BASELINE: "per-process
  MutableMapping[str, CompressionReport]") finally lives long enough to hit. But that win
  belongs to Layer 1; SHM contributes nothing to cache hit rate.

## Implementation cost
- Layer 1 (daemon + UDS + JSON-RPC): ~600-900 LOC Python, ~300 LOC TypeScript, ~1 week.
- Layer 2 (SHM ring + N-API addon): +400 LOC Python, +300 LOC C/Rust addon, +200 LOC TS,
  +cross-platform build matrix (macOS/Linux/Windows named pipes + SHM permissions),
  ~3-4 weeks. New runtime deps: `posix_ipc` (Python), a native Node addon shipped per
  platform in the .vsix.
- Risks to determinism: zero - this is transport, not semantics. Cache key derivation
  unchanged.
- Risks to robustness: real - SHM segment leaks on crash; permission/quota issues on
  shared boxes; named-pipe semantics differ on Windows; .vsix size grows from native
  binaries.
- Risks to cold-start budget (BASELINE constraint #5): daemon model regresses *first*
  call (now must spawn daemon then talk to it) but improves all subsequent. Need a
  warm-on-activation handshake that is itself fast.
- Violates no constraint in BASELINE (#1-#7); plain text agent surface is unchanged
  because this is extension <-> CLI internal, not agent-facing.

## Disqualifiers / why this might be wrong
1. **Serialisation is not the bottleneck.** From the back-of-envelope above, JSON
   encode+parse is 2-12% of total time on `pack`. Even if SHM made it free, the user
   would not notice. The honest win is killing process spawn, which a regular daemon
   over a Unix socket already captures - SHM is a rounding-error optimisation on top.
2. **Node + SHM is hostile.** Node has no first-class shared-memory primitive that
   crosses process boundaries (SharedArrayBuffer is intra-process only). Realising the
   SHM win requires shipping a native N-API addon per platform in the VSIX. The
   complexity/binary-size/maintenance cost almost certainly exceeds the ~10-50 ms saved
   on the rare large-response call.
3. **Most extension calls are user-initiated and infrequent.** `redcon.pack`,
   `redcon.plan`, `redcon.benchmark` fire on button press, not in a tight loop. Hot
   calls (status bar refresh, decorations) re-read cached `run.json` from disk; they do
   not spawn the CLI on every redraw. So the call frequency that would amortise SHM
   setup cost is not actually there.
4. **Already partially solved.** BASELINE notes lazy-imports cut cold-start ~62%. The
   remaining cold-start is small enough that a daemon may shave it, but SHM specifically
   targets a slice that is already small.
5. **Determinism + cache key contract.** A daemon makes per-process cache shared across
   calls - good - but if multiple VS Code windows attach to the same daemon, cwd-keyed
   cache must isolate per workspace. Mistake here leaks results across repos.

## Verdict
- Novelty: **low**. Daemonising a CLI and using SHM for big payloads is standard
  systems engineering. Nothing about Redcon's compression/scoring problem is unlocked
  by it. The closest BASELINE-listed open item is V76 ("SQLite WAL persistent cache
  shared across processes") which gets ~80% of the same warm-call benefit with far less
  code and zero native addon.
- Feasibility: **medium**. Layer 1 (daemon) is straightforward (1 week). Layer 2 (SHM)
  is feasible but expensive due to the N-API addon and cross-platform shipping.
- Estimated speed of prototype: Layer 1 ~1 week. Full SHM layer ~3-4 weeks.
- Recommend prototype: **no** for V77 as scoped (SHM specifically). **Conditional yes**
  for the strictly weaker Layer-1 daemon-over-UDS variant, *only* after profiling proves
  process-spawn dominates the hot path on real user sessions. SHM itself is engineering
  theatre unless someone produces a flamegraph showing JSON encode/decode > 30% of a
  hot call - which the available evidence (lazy-import wins, scan/score domination)
  argues against.
