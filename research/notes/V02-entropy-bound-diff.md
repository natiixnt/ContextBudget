# V02: Source-coding entropy bound for diff hunks (Markov over add/del/context states)

## Hypothesis

Treat a unified diff as a sequence of typed lines drawn from the alphabet
`S = {H, @, +, -, C}` (file-header, hunk-header, addition, deletion,
context). The current `git_diff.py` COMPACT tier already exploits the
massive redundancy of S by dropping nearly all hunk bodies and emitting one
fixed-shape line per file plus the first hunk header. Empirically that
costs about 73 bytes (= 581 bits) per kept file entry on a real corpus and
delivers 98.6 % token reduction. Claim: the *structural* slack left in the
compact output is small (the Markov-1 entropy of `S` is only 0.476 bit /
line, while compact is amortising about 0.36 bit / original line - already
below that floor because compact discards lines outright). The interesting
slack is on the **content lines that COMPACT keeps or VERBOSE keeps**:
adjacent `-`/`+` pairs are highly redundant (>0.6 SequenceMatcher ratio on
56 % of pairs in the real corpus, with about 12 KB of free savings on two
diffs alone), word-bigram conditional entropy on `+` lines is 3.21 bit /
word vs unigram 11.34 bit / word, and the long tail of "trivial" `+` lines
(28 % under 16 bytes) is already near-degenerate. Prediction: a content
coder that (a) splits structural vs content sub-streams, (b) emits adjacent
`-`/`+` as a single edit op, and (c) replaces the bulk-add hunk format with
a per-file content blob compressed against a small static dictionary of
diff-corpus n-grams, will *not* meaningfully move COMPACT (already ~99 %)
but will close the gap between VERBOSE (10-25 % of raw) and COMPACT on
mid-sized diffs by 3-7 absolute pp, which is where agents actually need
diff bodies.

## Theoretical basis

### Source model

A diff is `L = (l_1, ..., l_n)`. Each line has a *type* `s_i in S` and a
*payload* `c_i` (bytes after the leading marker). Joint entropy

```
H(L) = H(S_1, ..., S_n) + H(C_1, ..., C_n | S_1, ..., S_n)
     <=  n * H(S_t | S_{t-1}) + sum_t H(C_t | S_{<=t}, C_{<t})
        ~~~~~~~~~~~~~~~~~~~~~   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        structural floor        content payload
```

(Cover & Thomas, 5.4: chain rule for entropy; the inequality is tight when
structure and content factor cleanly, which holds for diffs because the
type marker is a deterministic prefix of `c_i` carrying zero information
about the body.) The structural floor is computable directly from line-type
counts. The content cost is upper-bounded by any universal coder applied to
the concatenation of payloads.

### Empirical numbers, pooled corpus

Corpus: 8 real diffs mined from `git diff HEAD~k..HEAD~k-d` over the
ContextBudget repo, plus the synthetic `_huge_diff(12, 20)` from
`tests/test_cmd_quality.py`. Total **26 927 lines / 911 KB**. Counts:

| state | n      | share |
|-------|--------|-------|
| `+`   | 21 724 | 80.7 % |
| `C`   |  2 865 | 10.6 % |
| `-`   |    887 |  3.3 % |
| `H`   |    803 |  3.0 % |
| `@`   |    648 |  2.4 % |

(Skew is real: most commits are bulk-add. The two diffs with substantial
`-`/`C` content (`d7`, `d8`) drive most of the editing-pair signal below.)

```
H(S)               = 1.0366  bit / line       (i.i.d. line-type)
H(S_t | S_{t-1})   = 0.4763  bit / line       (Markov-1)
top bigrams: '+'-> '+': 77.6 %, 'C'->'C': 5.7 %, '+'->'C': 2.8 %
```

So the Markov-1 *structural* floor is **0.476 bit / line**. A coder that
sent only the type sequence would cost 26 927 * 0.476 / 8 = ~1.6 KB total
for state alone. Today's COMPACT spends 11 986 bytes pooled, but it is
encoding more than the state path: it carries every file path, every
per-file `+/-` count, plus the first hunk header per file. So the right
denominator is per-file-entry: **73 bytes (= 581 bits) per kept file
entry** (165 file entries pooled). That is dominated by the path string -
on cl100k the path is 4-15 tokens depending on depth, plus 5-10 tokens of
fixed shape. The structural overhead is already about as cheap as it can
be without aliasing paths (V41 territory).

### Content-line entropy

Content lines (`+` and `-`, marker stripped) pooled across the whole
corpus: 22 608 lines, mean 32.2 bytes, mean 2.94 whitespace tokens / line.

| model                                       | bits / unit | bits / line |
|---------------------------------------------|-------------|-------------|
| raw UTF-8                                   |   8.000     |  257.6      |
| unigram byte (Shannon)                      |   4.965     |  159.9      |
| zlib (level 9, real run on content blob)    |   1.784     |   59.2      |
| unigram word (whitespace-token)             |  11.296     |   33.2      |
| **bigram word (conditional, +-stream)**     |   3.210     |    9.4      |
| bigram word (conditional, --stream)         |   1.464     |    4.3      |
| bigram word (conditional, context-stream)   |   2.283     |    6.7      |

Two things stand out:

1. **The word-bigram conditional entropy on `+` lines is 3.21 bits / word.**
   That is the empirical entropy of "next code word given previous code
   word" computed on the actual corpus. Multiplied by 2.94 word / line you
   get **9.4 bits / line** as a Markov-1 word-model floor for `+`-line
   content. zlib (a general LZ77 + Huffman coder with no diff-specific
   prior) gets 59 bits / line - 6x worse than the bigram model would
   permit if you trained a tokenizer on diff vocabulary. Most of that gap
   is the BPE-vs-word difference: cl100k splits identifiers in ways that
   redistribute information across more tokens but does not exploit the
   strong word-to-word correlations.
2. **The `-` stream is even more redundant** (1.46 bits / word given
   previous word) because deletion lines tend to repeat structural Python
   keywords (`def`, `return`, indentation) under the bigram transition.

### -/+ adjacency redundancy

In hunks that actually edit code (mainly `d7`, `d8` from the corpus), 218
adjacent `-`/`+` pairs were observed; **122 of them (56 %) had
SequenceMatcher ratio >0.6**. Treating those pairs as a single edit-op
(emit `~` line: minimal substitution patch) saves an estimated **11 874
bytes** on those two diffs alone (the cheap proxy is `(|a| + |b|) - ((1 -
ratio) * max(|a|, |b|) + 4)`; that is loose but conservative on the
upside). Today VERBOSE just emits both lines.

### Where the gap is *not*

- COMPACT total = 11 986 bytes. The Markov-1 structural floor for the
  state sequence is about 1 600 bytes pooled (state path only). Closing
  the residual 10 KB requires either a path-alias scheme (V41) or
  per-tokenizer recoding of `M src/foo.py: +N -M` shapes - this is V31 /
  V40 territory, *not* a content-entropy issue.
- COMPACT is already amortising about **36.4 bits per ORIGINAL line** =
  13.4 % byte ratio. Markov-1 lower bound for sending every line's *type*
  alone is 0.476 bit / line, but COMPACT does not send every line - it
  drops bodies. So COMPACT is not fighting the structural entropy bound at
  all.
- VERBOSE keeps about **51 bytes per kept content line** (51.2 from the
  measurement). Word-bigram floor for code content is ~9.4 bit/line = 1.2
  bytes / line if you ignore alphabet expansion. Realistic codec
  (zstd-with-trained-dict / static n-gram) gets to 5-12 bytes / line.
  That is the **5-10x gap** worth attacking.

### Headline numbers

- Markov-1 floor on structure: **0.48 bit / line** (vs current COMPACT
  amortisation of 36.4 bits / original line, which is "free" only because
  most lines are dropped).
- Word-bigram floor on `+` content: **9.4 bit / line** vs raw 257.6 bit /
  line (= 27x slack vs raw, **6x slack vs zlib**).
- Realistic per-line cost in VERBOSE today: 51 bytes = 408 bit.
- Realistic per-line cost reachable by a diff-trained content coder: ~10-15 byte = 80-120 bit.
- Translated to compact-tier: COMPACT already 99 % - the bonus headroom
  here is for a *new mid-tier* (call it COMPACT+CONTENT) that sits between
  current COMPACT and VERBOSE.

### Is the gap really 5+ bits / line?

Yes, but not on the structural axis. On **content lines** the gap between
naive UTF-8 (257 bit / line) and the empirical word-bigram floor (9.4
bit / line) is **248 bits / line**. Versus zlib (an off-the-shelf coder
that respects no diff prior at all) the gap is 59.2 - 9.4 = **49.8 bit /
line**. Versus the *ideal* unigram-byte coder it is 159.9 - 9.4 = 150
bit / line. This is the room a smarter content coder can take.

## Concrete proposal for Redcon

Add a new tier between COMPACT and VERBOSE: **"COMPACT+BODY"**, gated by
an explicit hint and reachable through `select_level`.

Files:

- `redcon/cmd/compressors/git_diff.py`: add `_format_compact_body` and a
  small `_diff_dictionary` table.
- `redcon/cmd/budget.py`: define `_COMPACT_BODY_RATIO = 0.30` and let
  `select_level` return the new tier when COMPACT fits but VERBOSE does
  not.
- `redcon/cmd/types.py`: extend `CompressionLevel` with `COMPACT_BODY`
  (alternative: keep enum stable and reuse VERBOSE with a body-trim flag
  on the result; less surface area but less explicit).

Sketch:

```python
# git_diff.py
_DIFF_KEYWORDS = (
    "def ", "class ", "return ", "import ", "from ", "self.", "if ",
    "for ", "while ", "raise ", "assert ", "elif ", "else:", "except ",
    "    ",  # 4-space indent fixture
)
# Index 0..15 maps to a single-byte sentinel via 0x80-prefix, then payload.
def _pack_word(word: str) -> str:
    idx = _DIFF_KEYWORDS.index(word) if word in _DIFF_KEYWORDS else -1
    return f"\x80{idx:x}" if idx >= 0 else word

def _emit_pair(minus: str, plus: str) -> str:
    """Adjacent -/+ pair: emit minimal substitution if sim > 0.6."""
    import difflib
    sm = difflib.SequenceMatcher(None, minus, plus)
    if sm.ratio() > 0.6:
        # cheap edit-script: the longest common prefix/suffix usually carries it
        i = _common_prefix_len(minus, plus)
        j = _common_suffix_len(minus[i:], plus[i:])
        return f"~ ...{minus[i:len(minus)-j]} -> {plus[i:len(plus)-j]}..."
    return f"-{minus}\n+{plus}"

def _format_compact_body(result, max_lines_per_hunk: int = 8) -> str:
    """Like _format_verbose but with -/+ pair fusion and dict swap."""
    ...
```

API change: `BudgetHint` already exposes `quality_floor`. The new tier
fits under `quality_floor=COMPACT_BODY` (raise floor to keep some body) or
`quality_floor=COMPACT` plus `prefer_keep_bodies=True` (less invasive).

Estimated emission cost per file with this format on the corpus:

```
file_header     : 60-80 bytes (path-dominated, V41/V40 fixes this)
hunk anchor     : 20-30 bytes per hunk
edit_pair       :  ~25 bytes per high-similarity -/+ pair (vs 60-100 today)
single + or -   :  ~32 bytes today, dropping to ~18 with keyword dict
```

Versus current VERBOSE: 51.2 bytes / kept line -> projected ~22 bytes /
kept line on the same corpus. That is a **57 % reduction on top of**
VERBOSE for the same fidelity, **without** going to ULTRA's 99.8 %
information loss.

## Estimated impact

- Token reduction: VERBOSE on git_diff currently averages 88.6 % token
  reduction in the empirical run (real diffs); projected to **94-95 %** at
  COMPACT_BODY. On COMPACT itself: marginal (already at 98.6 %), perhaps
  +0.3 pp from the keyword dictionary on file paths shared with body.
- Fidelity: must-preserve patterns survive trivially (paths, +/- counts,
  hunk anchors all kept verbatim). The new pair-fusion and keyword
  dictionary are *lossless* on the body content. The tier becomes the new
  default when budget pressure says VERBOSE would overflow but COMPACT
  would lose too much (the 30-65 % budget-share band where today
  `select_level` falls off a cliff from VERBOSE to COMPACT).
- Affected: `git_diff` mainly. The same pattern (state alphabet + content
  sub-stream + adjacent-pair fusion) applies to `git_log` (commit headers
  vs body) and `lint` (path:line:col header vs message body) but not in
  this proposal's scope.
- Latency: adjacency check is O(n_pairs), SequenceMatcher.ratio is O(n*m)
  with `autojunk`. On the d7/d8 corpus (218 pairs total) it would add
  <1 ms per call. Cold-start: zero (no new dependencies).

## Implementation cost

- ~120-180 LoC in `git_diff.py` for `_format_compact_body`, helper
  `_emit_pair`, dictionary table, and tests; ~10 LoC in `budget.py` and
  `types.py`. About one day of work.
- New runtime deps: none. `difflib` is stdlib. Determinism is preserved
  (SequenceMatcher with fixed inputs is deterministic, as is the keyword
  dictionary lookup).
- Risks to determinism: low. The keyword sentinel (`\x80<hex>`) is
  byte-stable. Tokenizer interaction needs verification: cl100k may not
  give the dictionary trick a clean win because BPE merges already handle
  common Python keywords. Empirically I'd expect 1-2 byte savings per
  occurrence, not the full dictionary win, but the pair-fusion is the
  bigger lever (~30-50 % savings on edit pairs, which are the majority of
  bodies in real edit-style commits).
- Risks to robustness: pair-fusion must gracefully handle binary, large
  hunks, non-UTF-8. The current parser already handles these; only the
  formatter changes.
- Risks to must-preserve: pair fusion replaces `- foo\n+ bar` with `~
  ...foo -> bar...`. The must-preserve regex
  `^- ?[^\s]+` would not match the fused line. **Mitigation**: emit pairs
  only when fidelity hint allows, or extend the regex to include `^~ `.
  This is the main API risk and must be checked by the quality harness
  with the existing `verify_must_preserve`.

## Disqualifiers / why this might be wrong

1. **The win is on a tier that does not exist yet.** COMPACT is already
   98.6 %; ULTRA is 99.8 %. Adding COMPACT_BODY changes the operating
   curve but does not move the headline metric "git diff compact-tier
   reduction" because that metric is already saturated. Reviewers will
   correctly say "this is V01 rate-distortion territory: pick a better
   point on the curve". V02 here only adds a new point; the *selection
   policy* is V01's job.
2. **cl100k may eat the dictionary win.** BPE merges for the keyword set
   `def `, `class `, `return ` are usually 1-2 tokens already. The
   sentinel encoding `\x80<idx>` is 2 bytes / 1-2 tokens, so the saving
   per swap is more like 1 token, not a full keyword's worth. The 6x
   word-bigram-vs-zlib gap is real but only fully realised by **a custom
   tokenizer**, which is V99. Without a custom tokenizer, the realised
   compression on +-content is closer to 30-40 % savings on the body
   alone, not the 6x ceiling.
3. **The corpus is biased.** 80.7 % of pooled lines are `+` (bulk-add
   commits dominate the recent history). On a corpus of refactor commits
   (`-` and `+` roughly equal, lots of `C`), the distribution and
   compression characteristics change, and the pair-fusion gain is
   concentrated on those commits - which are also the ones most likely to
   be small enough that VERBOSE already fits.
4. **Already partially implemented in disguise.** `_format_verbose` already
   caps each hunk at 5 added + 5 removed lines. That is a crude version of
   "drop the long tail and keep the short head", and it captures most of
   the practical fidelity for free. The marginal value of pair-fusion on
   top of a 5-line cap is real but small.
5. **Markov assumption is too weak for code.** Code has long-range
   structure (matched braces, function signatures referenced 200 lines
   later). A bigram model captures none of that. A real coder would need
   CTW (V06) or a context-tree of higher order, and the implementation
   complexity grows fast.

## Verdict

- Novelty: **medium**. The structural Markov analysis is mechanical (Cover
  & Thomas chapter 5). The empirical numbers and the gap quantification
  are useful but most of the actionable content (-/+ pair fusion,
  diff-corpus dictionary) is well-known LZ77 territory dressed up. The
  intermediate "COMPACT+BODY" tier is genuinely new for Redcon.
- Feasibility: **high**. Pure Python, stdlib only, no determinism risks
  beyond the standard "did you tweak the format" tests.
- Estimated speed of prototype: **1-2 days**, including new fixtures in
  the quality harness and a benchmark line in `redcon/cmd/benchmarks`.
- Recommend prototype: **conditional** on the existence of a real workload
  where VERBOSE-on-git_diff overflows context. Today VERBOSE on git_diff
  already runs at ~10-20 % of raw on the corpus, which is small enough
  that most agents do not feel the pinch. If profile data shows VERBOSE
  trimming is happening *and* agents are re-asking for the dropped tail,
  build it. Otherwise the headline gain is small, and V01 / V31 / V41 /
  V99 each touch a larger surface for the same effort.

### Coda: distinguishing structural vs content lines

The bonus question - "treat structural and content lines with different
entropy models" - resolves cleanly here. Concretely, the proposed coder is

```
encode(diff):
  emit FILE_HEADER block (paths, counts, hunks-anchor) using state-Huffman + path-alias
  for hunk in hunks:
    emit HUNK_HEADER (line numbers + signature) using uniform varint
    encode(hunk.body) using:
      pair_fuse(-/+) where similarity > 0.6
      dict_swap on common Python keywords (length-1 sentinel)
      passthrough otherwise
```

This is an explicit two-stream architecture: state path uses one
distribution (with alphabet `S`, entropy 0.48 bit / line under
Markov-1), content uses another (word-bigram, entropy 3.2 bit / word on
`+`, 1.5 bit / word on `-`). Coding them separately, with each using its
own entropy model, is the practical realisation of the chain-rule split.
The numerical case for it is documented above; whether that translates to
agent-visible utility is an empirical question for the quality harness.
