# Research Index - 100 Vectors

Each researcher takes ONE vector. Read `BASELINE.md` first. Output a single file at `research/notes/V<NN>-<slug>.md` using the template at the bottom.

## Theme A. Information-theoretic foundations
- V01: Rate-distortion theory for code outputs - derive R(D) curve, pick operating point per compressor
- V02: Source-coding entropy bound for diff hunks (Markov over add/del/context states)
- V03: Universal source coder for code+prose mixture, prove upper bound on remaining slack
- V04: Kolmogorov-complexity proxy via 5-codec ensemble min-length, use as compressor floor
- V05: Asymmetric numeral systems (ANS) for ULTRA-tier serialisation of structured outputs
- V06: Context-tree weighting (CTW) line predictor for compressors with strong local Markov structure
- V07: Algorithmic mutual information between consecutive tool calls in same agent turn
- V08: MDL-based symbol/snippet selection for file packing (compete vs current heuristic scorer)
- V09: Channel-coding analogy - selective re-fetch protocol when receiver uncertainty high
- V10: Information-bottleneck objective for compressor parameter tuning per command

## Theme B. Semantic / structural compression beyond AST
- V11: Provenance-aware patch DAG with shared-subgraph contraction across hunks of multi-file diff
- V12: Semantic-equivalence canonical form (alpha-rename, sort imports) before tokenisation
- V13: CST-level template extraction across files - emit template once, parameter slots inline
- V14: Type-driven literal collapsing - replace verbose literals with `:T` markers when T determines structure
- V15: CFG summary - replace function bodies with signature plus calls plus return-shape only
- V16: Differential-vs-baseline test report - "pass set delta vs last green run"
- V17: Symbolic execution path equivalence classes for trace compression
- V18: Data-flow taint slicing for task-relevant lines only
- V19: AST-diff representation for code-mod tasks (preserve only edit ops)
- V20: Bigraph (imports x file roles) adjacency-list delta vs prior run

## Theme C. Agent-aware predictive selection
- V21: Speculative decoding analog - emit "expected next call" so agent skips tool round trip
- V22: Triple-scorer consensus filter - keep only files all three deterministic scorers agree on
- V23: Bayesian budget allocation - allocate tokens proportional to expected entropy decrease
- V24: Multi-armed bandit across compression levels using "did agent then ask for full" signal
- V25: Markov chain over MCP call sequences, prefetch likely-next compressed views
- V26: Replay-value tracker - files re-read in same session ranked higher
- V27: Negative-space pruning - drop file kinds the agent never reads in this user/repo
- V28: Call-graph-conditioned scoring - reachability from keyword-matching files
- V29: Set-cover atomic-fact decomposition - rank files by which task facts they cover
- V30: Turn-budget amortisation across an agent session

## Theme D. Tokenizer-exact optimisation
- V31: Multi-token-string substitution table per tokenizer family (replace common spans with shorter equivalents)
- V32: Token-boundary-aware whitespace inserter - empirically aligned to cl100k merges
- V33: Unicode NFKC collapsing to dedup near-duplicate glyphs into single tokens
- V34: Numeric formatting tuned for tokenizer (scientific/hex when shorter)
- V35: Dynamic tokenizer detection from caller, swap dictionary
- V36: Cross-tokenizer Rosetta - output that tokenises identically across cl100k/o200k/llama-3
- V37: Greedy multi-candidate phrasing - try N rewrites, keep min-token version
- V38: ANSI/escape-sequence strip with rich-output preserved in side metadata
- V39: Trailing-zero / padding analysis on counts and timings
- V40: Path canonicalisation - choose shortest relative path representation per tokenizer

## Theme E. Cross-call dictionary / reference (BIG breakthrough surface)
- V41: Stable session-scoped 4-char alias for files/symbols, persist across calls
- V42: Hash-keyed shared dictionary (server-side) returned to agent once, referenced thereafter
- V43: RAG-style hot store - in-memory K-V of prior-turn facts, replace repeats with `{ref:#42}`
- V44: Deep-link references (file:line) so agent re-fetches instead of receiving content
- V45: Bloom filter "you already saw this" so agent skips fetch
- V46: Merkle-tree path summarisation - send root, agent expands selectively
- V47: Snapshot delta vs prior `redcon_run` of same command on same repo
- V48: Cross-tool dedup - pytest path + grep path returns single canonical entry
- V49: Persistent symbol cards once per session, then only diffs against them
- V50: Server-pushed pre-keyed cache - client requests by hash, gets full

## Theme F. Streaming / online algorithms
- V51: Reservoir sampling stratified by file for test failures over N
- V52: HyperLogLog++ for distinct counts in grep results
- V53: T-digest for log-line latency distribution summarisation
- V54: Priority queue with exponential decay for streaming diff hunks
- V55: Online clustering of similar test-failure messages -> failure templates
- V56: Early-kill subprocess when output exceeds budget signal
- V57: Anytime-algorithm pipeline - emit best-so-far compressed output on interrupt
- V58: Adaptive sampling rate driven by running info-entropy estimate
- V59: Budget-aware backpressure to subprocess (PIPE pause)
- V60: Online dedup via rolling hash shingles

## Theme G. New compressor classes
- V61: SQL EXPLAIN ANALYZE compressor (Postgres + MySQL)
- V62: Linter (eslint/rubocop/pylint) compressor with rule-frequency table
- V63: Bundle stats compressor (webpack/esbuild tree-shake report)
- V64: Stack-trace deduplication and frame-template extraction
- V65: JSON-log compressor - mine schema, transmit table-shaped
- V66: HTTP access log compressor (NCSA/combined)
- V67: Kubernetes events stream compressor (group by reason+object)
- V68: CI annotations / GitHub Actions log compressor
- V69: Coverage report compressor (delta vs main branch)
- V70: Profiler output compressor (flamegraph DAG -> top-K paths)

## Theme H. Cache and perf architecture
- V71: Content-defined chunking (FastCDC) for cache key under near-duplicate argv
- V72: SIMD-accelerated regex via pyhyperscan or rust extension
- V73: Zero-copy bytes parsing with memoryview throughout
- V74: mmap-backed spill log writes
- V75: Tokenizer-free byte-level estimator with empirical calibration table
- V76: SQLite WAL persistent cache shared across processes
- V77: Shared-memory IPC for VS Code extension <-> CLI
- V78: Pre-compiled regex globals audit - find misses
- V79: Compile-time-generated parsers (PEG to static dispatch table)
- V80: Lazy-deserialise cached entries

## Theme I. Quality / verification
- V81: Hypothesis-style property-based fuzzing for must-preserve invariants
- V82: Differential testing - golden corpus byte-for-byte across implementations
- V83: KL-divergence between line-distribution before/after compression
- V84: Round-trip lossless flag - skip compression when raw fits in budget
- V85: Adversarial input generator - hunt regressions in current compressors
- V86: Mutation testing on regex patterns themselves
- V87: Auto-explored quality vs reduction Pareto curve per command
- V88: Self-supervised re-feed eval - "anything missing?" probe
- V89: Coverage-guided fuzzing of regex parsers
- V90: BNF formal grammar validation for output reversibility

## Theme J. Wildcards / breakthrough candidates
- V91: Predictive closure - bundle the next-line lookups the agent will need
- V92: Differential-privacy style global info budget across a session
- V93: Proof-carrying compression - attach hash certifying invariant set preserved
- V94: Self-instructing prompt format - tell the model when NOT to ask for expansion
- V95: Cross-LLM meta-cache - one compressed output reused across agents/models
- V96: CFG-discovery - detect context-free grammars in command output, switch to structural representation
- V97: Active-learning loop - agent labels what it actually used, tune pattern weights
- V98: Markov blanket of task - minimal d-separating file set
- V99: Custom BPE tokenizer trained on Redcon output corpus, ship as tokenizer pack
- V100: Causal compression via OS-level tracing (strace/dtrace) - emit only causal-chain lines

## Output template (mandatory)

Every researcher writes ONE file: `research/notes/V<NN>-<slug>.md`. Format:

```
# V<NN>: <title>

## Hypothesis
One paragraph. What is the new claim or technique? What does it predict?

## Theoretical basis
Cite the math, theorem, or prior work. If novel, state it formally.
A back-of-envelope derivation (>= 3 lines of math) is required.

## Concrete proposal for Redcon
What changes in `redcon/cmd/...` or `redcon/scorers/...` etc.
Name the files. Sketch the API. 5-15 lines of pseudo-code.

## Estimated impact
- Token reduction: <delta in absolute pp on which compressor(s)>
- Latency: <plus/minus on cold + warm parse>
- Affects which existing compressors / scorers / cache layers

## Implementation cost
- Lines of code (rough)
- New runtime deps (and whether they break "no required network / no embeddings" rule)
- Risks to determinism, robustness, must-preserve guarantees

## Disqualifiers / why this might be wrong
At least three reasons this idea could fail or be already-implemented in disguise.

## Verdict
- Novelty: low | medium | high | breakthrough
- Feasibility: low | medium | high
- Estimated speed of prototype: <hours / days / weeks>
- Recommend prototype: yes | no | conditional-on-X
```

If the answer is "this is already done in BASELINE.md", say so explicitly and mark Novelty: low. We learn the boundary, that's still useful.
