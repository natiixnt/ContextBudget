# V100: Causal compression via OS-level tracing - emit only causal-chain lines

## Hypothesis
Most build / test / lint output is **noise emitted by sibling processes** (parallel jobs, transitive recipes, deprecation warnings) that has zero causal relationship to the line that actually caused the failure. If we instrument the subprocess tree at the OS level (strace / dtrace / eBPF), we can build the process DAG, identify the unique pid that emitted the failing chunk (non-zero exit, SIGABRT, write-to-stderr-then-exit), and **emit only output from that pid and its ancestors back to the root**. Sibling-process output is pruned with strong epistemic justification ("this process did not cause the failure"). Predicted reduction: 90%+ on `make -j8` failures and large CI logs, well above current `lint`/`pkg_install`/`docker` compressors which work line-by-line.

## Theoretical basis
Frame the build process as a directed forest `F = (V, E)` rooted at the user-invoked pid `r`. Each node `v` has output bytes `b(v)` and exit code `x(v)`. Define the **failure set** `S = { v : x(v) != 0 }` and the **causal closure** `C = S U ancestors(S)` (any process whose subtree contains a failure). Under the assumption that build orchestrators are append-only (no process modifies a sibling's output), the lines on the causal path are exactly `union(b(v) for v in C)`.

Back-of-envelope. Let `|V| = N` total processes, average output `o` bytes each, failure-tree size `|C| = k`. Reduction ratio is `1 - k/N`. For `make -j8` of LLVM hitting one C++ template error:

```
N ~ 800 (compile + link + codegen units)
k ~ 4   (cc1plus -> g++ -> make -> shell)
ratio = 1 - 4/800 = 99.5%
bytes pruned ~ (N - k) * o = 796 * ~300 B = 239 KiB out of 240 KiB
```

Even adversarial workloads where every TU emits warnings still satisfy `k << N` whenever parallelism `j > 1`. The argument is information-theoretic in spirit: sibling-process bytes are conditionally independent of the failure given the process DAG, so under a Bayes-optimal "explain the failure" coder their entropy contribution to the relevant message is 0.

## Concrete proposal for Redcon
**This is recorded as inspiration, not as a build-out.** A faithful implementation would require:

- New module `redcon/cmd/causal_trace.py` wrapping `strace -f -e trace=execve,exit_group,write -o <fifo>` (Linux), `dtruss` / `dtrace -n 'proc:::exit'` (macOS, root), or eBPF via `bcc.execsnoop` + `exitsnoop` (kernel >= 4.7).
- Streaming parser that builds `pid -> (ppid, argv, exit_code, byte_ranges_into_combined_output)`.
- Post-run pruning pass selecting `C = ancestors(failure_pids)` and slicing the captured stdout/stderr buffer to only those byte ranges before handing off to `detect_compressor`.
- Pipeline integration as a **pre-compressor** stage in `compress_command` (before `detect_compressor`), gated by a `causal_trace=True` flag and falling back silently if `strace` is unavailable.

Pseudo-code:

```python
# redcon/cmd/causal_trace.py  (sketch, not for production)
def run_with_causal_trace(argv, cwd):
    fifo = mkfifo()
    proc = Popen(["strace","-f","-e","trace=execve,exit_group,write",
                  "-o",fifo,"--",*argv], cwd=cwd, ...)
    events = parse_strace_stream(fifo)         # pid,ppid,syscall,args
    out, err = proc.communicate()
    tree = build_pid_tree(events)              # {pid: PidNode}
    fails = {p for p,n in tree.items() if n.exit != 0}
    causal = closure_ancestors(tree, fails)
    return slice_output(events, causal, out, err)  # only bytes from causal pids
```

`compress_command` would then call the existing per-command compressor on the **already-pruned** bytes, compounding multiplicatively with current tier reductions.

## Estimated impact
- Token reduction: **+15 to +40 absolute pp** on top of current tiers for parallel-build failures (`make -jN`, `bazel build`, `cargo build` with many crates, `pytest -n auto`). Negligible win on serial commands (`git diff`, `ls -R`, single-file `pylint`) where `N = 1`.
- Latency: **strace adds 30 - 200% wall-clock overhead** on syscall-heavy workloads (well-documented). eBPF cuts this to <5% but raises the dependency floor.
- Affects: all command-side compressors as a pre-stage. No effect on file-side scorers. Cache-key impact: causal tracing is non-deterministic in scheduling but deterministic in output if we discard timing and only retain the pid DAG topology + exit codes.

## Implementation cost
- Lines of code: ~600 - 1000 for the strace path alone, plus per-platform branches (Linux strace, macOS dtrace, BSD ktrace, Windows ETW = effectively a fourth implementation).
- New runtime deps: hard dep on `strace` binary (not in many minimal containers); `dtrace` requires SIP-disabled macOS or root; eBPF needs `CAP_BPF` / `CAP_PERFMON` / kernel >= 5.8 for unprivileged. None violate "no embeddings / no required network" but **all violate the implicit "no root, no kernel-version assumption"** posture.
- Risks: (a) strace **changes** subprocess behavior (ptrace race conditions, EINTR storms), breaking the determinism contract for the wrapped command. (b) buffered stdout means byte ranges from `write()` syscalls do not align cleanly with line boundaries - we would have to reassemble. (c) container/sandbox environments often forbid `ptrace` (`seccomp` default profile blocks it), so the feature would silently degrade in CI - the most important target.

## Disqualifiers / why this might be wrong
1. **Cross-platform engineering is enormous.** Three OS-specific tracers, root requirement on macOS, kernel-version gates on Linux, Windows entirely missing. The project's positioning is local-first and zero-friction; this regresses both.
2. **Process-tree info is already available without strace** in the two highest-value targets. CI logs from GitHub Actions / GitLab include step boundaries and exit codes natively (-> V68). Kubernetes events carry pod/container parent-child relationships in metadata (-> V67). `make`'s `--output-sync=recurse` and Bazel's `--experimental_ui_max_stdouterr_bytes` already partition output by job. So the **insight** (causal pruning by process structure) is reachable through cheaper signals than syscall tracing.
3. **The hot failure mode is often a single serial process** (one `pytest` run, one `pylint`, one `tsc`). On those, `k = N = 1` and the technique buys nothing. The `make -j8` archetype is a minority of agent-driven invocations.
4. **Determinism breakage.** strace under `-f` perturbs timing enough that flaky tests change outcomes. Redcon's contract is "wrap this command and report it"; we cannot in good faith change the command's behavior to compress its output.
5. **Overlap with V56 (early-kill subprocess).** If we already kill on budget overflow, the marginal value of pruning post-hoc shrinks - the user never paid for the noise in the first place.

## Verdict
- Novelty: **high** - genuinely new dimension (process-DAG-conditioned compression), not present anywhere in BASELINE.md and not reducible to text-level techniques.
- Feasibility: **low** - cross-platform OS tracing, root / capability requirements, sandbox incompatibility, determinism perturbation, and a long tail of edge cases (ptrace race, write-buffer reassembly).
- Estimated speed of prototype: **weeks** for a Linux-only PoC, **months** to reach the cross-platform / sandbox-friendly bar Redcon's other compressors meet.
- Recommend prototype: **no** - skip as a direct build-out. **Capture the insight**: "process-tree causal pruning" is the novel idea. Apply it to V67 (Kubernetes events: parent/owner refs are in the event metadata, no tracing needed - prune events whose owner is a healthy sibling of the failing pod) and V68 (CI annotations / GitHub Actions logs: step IDs and exit codes are structured, build the step-DAG and emit only the failing-step ancestor chain). Both of those reach 80-90% of V100's compression power at 5% of its engineering cost, because the OS already did the bookkeeping for us.
