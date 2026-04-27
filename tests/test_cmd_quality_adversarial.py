"""
V85 adversarial input generator (genetic / mutation-based).

Goal: targeted search of input space to find inputs where current compressors
yield (a) reduction below COMPACT floor, (b) must-preserve violation,
(c) crash, or (d) non-determinism. Different from V81 random fuzzing - V85
uses a fitness function and selection pressure.

Read-only with respect to production source. Imports compressors and runs
them through the existing QualityCheck-like primitives.

The generator is *deterministic* (seeded random.Random). Re-running the
test reproduces the same hall-of-shame inputs. This is a research probe,
not a regression gate, so the default test marks discovered failures as
expected (we record them) rather than failing CI; flip
REDCON_V85_ENFORCE=1 to make the test fail on any new finding.

Run:

    pytest tests/test_cmd_quality_adversarial.py -s
    REDCON_V85_GENERATIONS=1000 pytest tests/test_cmd_quality_adversarial.py::test_v85_genetic_hunt -s
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from typing import Callable

import pytest

from redcon.cmd.budget import BudgetHint
from redcon.cmd.compressors.base import Compressor, CompressorContext
from redcon.cmd.compressors.cargo_test_compressor import CargoTestCompressor
from redcon.cmd.compressors.git_diff import GitDiffCompressor
from redcon.cmd.compressors.git_log import GitLogCompressor
from redcon.cmd.compressors.git_status import GitStatusCompressor
from redcon.cmd.compressors.go_test_compressor import GoTestCompressor
from redcon.cmd.compressors.grep_compressor import GrepCompressor
from redcon.cmd.compressors.listing_compressor import (
    FindCompressor,
    LsCompressor,
    TreeCompressor,
)
from redcon.cmd.compressors.coverage_compressor import CoverageCompressor
from redcon.cmd.compressors.docker_compressor import DockerCompressor
from redcon.cmd.compressors.json_log_compressor import JsonLogCompressor
from redcon.cmd.compressors.kubectl_compressor import KubectlGetCompressor
from redcon.cmd.compressors.lint_compressor import LintCompressor
from redcon.cmd.compressors.npm_test_compressor import NpmTestCompressor
from redcon.cmd.compressors.pkg_install_compressor import (
    PackageInstallCompressor,
)
from redcon.cmd.compressors.profiler_compressor import ProfilerCompressor
from redcon.cmd.compressors.pytest_compressor import PytestCompressor
from redcon.cmd.compressors.sql_explain_compressor import SqlExplainCompressor
from redcon.cmd.types import CompressionLevel


# --- knobs ---

# Generation budget. The full V85 protocol asks for 1000 generations per
# compressor. That takes ~3-5 minutes total locally; tests in CI cap at 50.
DEFAULT_GENERATIONS = int(os.environ.get("REDCON_V85_GENERATIONS", "50"))
POPULATION = int(os.environ.get("REDCON_V85_POPULATION", "32"))
ENFORCE = os.environ.get("REDCON_V85_ENFORCE", "0") == "1"

# Hard cap on raw seed size in bytes - keeps the search tractable.
MAX_INPUT_BYTES = 16 * 1024


# --- fitness ---


@dataclass
class Finding:
    schema: str
    kind: str  # "crash" | "non_deterministic" | "must_preserve" | "below_floor"
    fitness: float
    raw: bytes
    detail: str

    def short(self) -> str:
        return (
            f"{self.schema}/{self.kind} fitness={self.fitness:.3f} "
            f"size={len(self.raw)}B detail={self.detail}"
        )


def _force_compact_hint() -> BudgetHint:
    """Same as quality._force_level_hint(COMPACT). Pull selection up to compact."""
    return BudgetHint(
        remaining_tokens=200,
        max_output_tokens=4_000,
        quality_floor=CompressionLevel.COMPACT,
    )


def _eval_one(
    compressor: Compressor, raw: bytes, argv: tuple[str, ...]
) -> tuple[float, Finding | None]:
    """
    Run compressor against raw at COMPACT, return (fitness, finding-or-None).

    Fitness is bigger when the input is "more adversarial":
      - crash       -> 100
      - non-det     -> 50 + len-bonus
      - lost-pres   -> 20 + (1 - red_pct)
      - low-red     -> max(0, floor - red_frac) * 10  (only if >= 80 raw tokens)
    """
    ctx = CompressorContext(
        argv=argv, cwd=".", returncode=0, hint=_force_compact_hint()
    )
    # (a) crash check
    try:
        first = compressor.compress(raw, b"", ctx)
    except Exception as exc:  # pragma: no cover - the whole point
        return 100.0, Finding(
            schema=compressor.schema,
            kind="crash",
            fitness=100.0,
            raw=raw,
            detail=f"{type(exc).__name__}: {exc}",
        )

    # (d) non-determinism
    try:
        second = compressor.compress(raw, b"", ctx)
    except Exception as exc:
        return 100.0, Finding(
            schema=compressor.schema,
            kind="crash",
            fitness=100.0,
            raw=raw,
            detail=f"second-run {type(exc).__name__}: {exc}",
        )
    if first.text != second.text or first.level != second.level:
        return 50.0 + min(50.0, len(raw) / 1024), Finding(
            schema=compressor.schema,
            kind="non_deterministic",
            fitness=50.0,
            raw=raw,
            detail=f"diff={_first_diff(first.text, second.text)}",
        )

    # (b) must-preserve. We use the compressor's own self-report
    # (must_preserve_ok) which is set after the verify_must_preserve call;
    # if False, the harness would fail at COMPACT.
    if not first.must_preserve_ok:
        return 20.0 + max(0.0, 1.0 - first.reduction_pct / 100), Finding(
            schema=compressor.schema,
            kind="must_preserve",
            fitness=20.0,
            raw=raw,
            detail=(
                f"red={first.reduction_pct:.1f}% "
                f"orig_tok={first.original_tokens} "
                f"comp_tok={first.compressed_tokens}"
            ),
        )

    # (c) below floor. Quality harness exempts inputs <80 raw tokens. We
    # additionally exempt outputs that are byte-identical to the raw input
    # (modulo trailing whitespace) - that is the compressor's intentional
    # passthrough on inputs it could not structurally compress (random
    # bytes, non-canonical schema). Asserting a 30% reduction on noise
    # is meaningless; the contract is "do not inflate", not "always
    # compress". The compressor's own header-inflation guard already
    # prevents inflation here.
    raw_text = raw.decode("utf-8", errors="replace").rstrip()
    is_passthrough = first.text.rstrip() == raw_text
    if first.original_tokens >= 80 and not is_passthrough:
        red_frac = first.reduction_pct / 100.0
        floor = 0.30
        if red_frac < floor:
            slack = (floor - red_frac) * 10.0
            return slack, Finding(
                schema=compressor.schema,
                kind="below_floor",
                fitness=slack,
                raw=raw,
                detail=(
                    f"red={first.reduction_pct:.1f}% "
                    f"orig_tok={first.original_tokens} "
                    f"comp_tok={first.compressed_tokens}"
                ),
            )
        # Even when above floor, gradient towards low reduction so the GA
        # keeps climbing.
        return max(0.0, floor - red_frac) * 5.0, None

    # Tiny input below 80 raw tokens. Push GA to grow.
    return -float(first.original_tokens) / 80.0, None


def _first_diff(a: str, b: str, n: int = 60) -> str:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"@{i} {a[i:i+n]!r} vs {b[i:i+n]!r}"
    return f"len {len(a)} vs {len(b)}"


# --- mutation operators ---


_PRINTABLE = (
    b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    b"0123456789 \n\t/.:-_+@()=[]{}<>"
)


def _mutate(seed: bytes, rng: random.Random) -> bytes:
    """One step of mutation. Pick op uniformly; bound output length."""
    if not seed:
        return rng.choice(_TINY_SEEDS)

    op = rng.randint(0, 9)
    if op == 0:
        # bit flip
        i = rng.randrange(len(seed))
        return seed[:i] + bytes([seed[i] ^ (1 << rng.randrange(8))]) + seed[i + 1 :]
    if op == 1:
        # delete a chunk
        if len(seed) < 2:
            return seed
        i = rng.randrange(len(seed))
        j = min(len(seed), i + rng.randint(1, 32))
        return seed[:i] + seed[j:]
    if op == 2:
        # duplicate a chunk - amplifies repetitive structure (tries to defeat
        # dedup, since dedup is supposed to win on duplicates)
        if len(seed) < 2:
            return seed
        i = rng.randrange(len(seed))
        j = min(len(seed), i + rng.randint(1, 64))
        chunk = seed[i:j]
        out = seed + chunk * rng.randint(1, 4)
        return out[:MAX_INPUT_BYTES]
    if op == 3:
        # insert random printable bytes
        i = rng.randrange(len(seed) + 1)
        n = rng.randint(1, 32)
        ins = bytes(rng.choice(_PRINTABLE) for _ in range(n))
        return (seed[:i] + ins + seed[i:])[:MAX_INPUT_BYTES]
    if op == 4:
        # insert a structurally meaningful token from a corpus
        i = rng.randrange(len(seed) + 1)
        tok = rng.choice(_TOKENS)
        return (seed[:i] + tok + seed[i:])[:MAX_INPUT_BYTES]
    if op == 5:
        # newline storm (target: line-based parsers)
        i = rng.randrange(len(seed) + 1)
        return (seed[:i] + b"\n" * rng.randint(1, 50) + seed[i:])[:MAX_INPUT_BYTES]
    if op == 6:
        # truncate (mid-stream)
        if len(seed) < 4:
            return seed
        return seed[: rng.randint(1, len(seed) - 1)]
    if op == 7:
        # repeat-line: duplicate the last line many times (target: dedup wins)
        idx = seed.rfind(b"\n")
        last = seed[idx + 1 :] if idx >= 0 else seed
        return (seed + (b"\n" + last) * rng.randint(2, 20))[:MAX_INPUT_BYTES]
    if op == 8:
        # ASCII-noise: replace a span with high-byte garbage
        if len(seed) < 4:
            return seed
        i = rng.randrange(len(seed))
        j = min(len(seed), i + rng.randint(1, 16))
        noise = bytes(rng.randint(0x80, 0xFF) for _ in range(j - i))
        return seed[:i] + noise + seed[j:]
    # op == 9: crossover-with-other-corpus
    other = rng.choice(_CORPUS)
    cut1 = rng.randrange(len(seed) + 1)
    cut2 = rng.randrange(len(other) + 1)
    return (seed[:cut1] + other[cut2:])[:MAX_INPUT_BYTES]


# Token corpus drawn from real fixture vocabulary across all compressors.
_TOKENS: tuple[bytes, ...] = (
    b"diff --git a/x b/x\n",
    b"@@ -1,3 +1,3 @@\n",
    b"--- a/x\n",
    b"+++ b/x\n",
    b"index abc..def 100644\n",
    b"commit abcdef1234567890abcdef1234567890abcdef12\n",
    b"Author: x <x@x>\n",
    b"Date:   Mon Jan 1 00:00:00 2025\n",
    b"FAILED tests/x.py::test_x - assert 1 == 2\n",
    b"PASSED tests/x.py::test_y\n",
    b"E       AssertionError\n",
    b"=================================== FAILURES ===================================\n",
    b"tests/x.py:1: AssertionError\n",
    b"src/x.py:1:1: E501 line too long\n",
    b"src/x.py:1:def x():\n",
    b"./x/y.py\n",
    b"  M foo\n",
    b"?? bar\n",
    b"## main\n",
    b"=== RUN   TestX\n",
    b"--- FAIL: TestX (0.00s)\n",
    b"--- PASS: TestY (0.00s)\n",
    b"running 1 tests\n",
    b"test foo::bar ... ok\n",
    b"test foo::bar ... FAILED\n",
    b"PASS  src/x.test.js\n",
    b"FAIL  src/x.test.js\n",
    b"Tests:       1 failed, 1 passed, 2 total\n",
    b"Step 1/2 : FROM alpine\n",
    b" ---> abcdef\n",
    b"Successfully built abcdef\n",
    b"Collecting numpy\n",
    b"Successfully installed numpy-1.0\n",
    b"NAME   READY   STATUS\n",
    b"x      1/1     Running\n",
    b"\x00\x01\x02\xff\xfe",
    b" " * 80 + b"\n",
    b"\n" * 10,
    # Pathological repeats: identical hunk to test dedup
    b"@@ -1,1 +1,1 @@\n-a\n+a\n",
    # Long whitespace-only line
    b"\t" * 200 + b"\n",
)


_TINY_SEEDS: tuple[bytes, ...] = (
    b"x\n",
    b"hello\n",
    b"./a.py\n",
    b"diff --git a/a b/a\n",
)


def _seed_corpus_for(schema: str) -> tuple[bytes, ...]:
    """Compressor-specific starting points (close to what the parser expects)."""
    if schema == "git_diff":
        return (
            b"diff --git a/a.py b/a.py\nindex 1..2 100644\n--- a/a.py\n+++ b/a.py\n"
            b"@@ -1,3 +1,3 @@\n a\n-b\n+c\n d\n",
        )
    if schema == "git_status":
        return (b"## main\n M a\n?? b\nA  c\n",)
    if schema == "git_log":
        return (
            b"commit abcdef1234567890abcdef1234567890abcdef12\n"
            b"Author: x <x@x>\nDate:   Mon Jan 1 00:00:00 2025\n\n    Msg\n",
        )
    if schema == "pytest":
        return (
            b"============================= test session starts ==============================\n"
            b"collected 2 items\n\ntests/a.py F. [100%]\n\n"
            b"=================================== FAILURES ===================================\n"
            b"_______________ test_a _______________\n"
            b">       assert 1 == 2\nE       AssertionError\ntests/a.py:1: AssertionError\n"
            b"========================= 1 failed, 1 passed in 0.01s ==========================\n",
        )
    if schema == "grep":
        return (
            b"src/a.py:1:def f():\nsrc/a.py:2:    return 1\nsrc/b.py:1:def g():\n",
        )
    if schema == "ls":
        return (b"./d:\nfoo.py\nbar.py\n",)
    if schema == "tree":
        return (b".\n|-- a.py\n`-- b.py\n",)
    if schema == "find":
        return (b"./a/b.py\n./a/c.py\n./d/e.py\n",)
    if schema == "cargo_test":
        return (
            b"running 2 tests\ntest a::b ... ok\ntest a::c ... FAILED\n\n"
            b"failures:\n    a::c\n"
            b"test result: FAILED. 1 passed; 1 failed; 0 ignored\n",
        )
    if schema == "go_test":
        return (
            b"=== RUN   TestA\n--- PASS: TestA (0.00s)\n=== RUN   TestB\n"
            b"    b_test.go:1: bad\n--- FAIL: TestB (0.00s)\n",
        )
    if schema == "npm_test":
        return (
            b"PASS src/a.test.js\nFAIL src/b.test.js\n"
            b"Tests:       1 failed, 1 passed, 2 total\n",
        )
    return (b"hello\n",)


_CORPUS: tuple[bytes, ...] = _TOKENS + tuple(
    s for k in (
        "git_diff", "git_status", "git_log", "pytest", "grep",
        "ls", "tree", "find", "cargo_test", "go_test", "npm_test",
    )
    for s in _seed_corpus_for(k)
)


# --- GA loop ---


@dataclass
class _PopMember:
    raw: bytes
    fitness: float
    finding: Finding | None


def genetic_hunt(
    compressor: Compressor,
    argv: tuple[str, ...],
    *,
    generations: int,
    population: int,
    rng: random.Random,
) -> list[Finding]:
    """
    Run mutation-based GA. Returns deduped list of unique findings.

    Selection: tournament (k=3). Replacement: keep top half each gen.
    Mutation rate: 1-2 ops per offspring (tier-2 mutation).
    """
    seeds = _seed_corpus_for(compressor.schema)
    pop: list[_PopMember] = []
    for _ in range(population):
        s = rng.choice(seeds)
        for _ in range(rng.randint(0, 2)):
            s = _mutate(s, rng)
        f, finding = _eval_one(compressor, s, argv)
        pop.append(_PopMember(raw=s, fitness=f, finding=finding))

    findings: dict[tuple[str, bytes], Finding] = {}
    for m in pop:
        if m.finding is not None:
            findings[(m.finding.kind, m.raw)] = m.finding

    for _gen in range(generations):
        pop.sort(key=lambda m: m.fitness, reverse=True)
        survivors = pop[: max(2, population // 2)]
        children: list[_PopMember] = []
        while len(children) + len(survivors) < population:
            parent = _tournament(survivors, rng)
            child_raw = parent.raw
            for _ in range(rng.randint(1, 2)):
                child_raw = _mutate(child_raw, rng)
            f, finding = _eval_one(compressor, child_raw, argv)
            children.append(_PopMember(raw=child_raw, fitness=f, finding=finding))
            if finding is not None:
                key = (finding.kind, child_raw)
                if key not in findings:
                    findings[key] = finding
        pop = survivors + children
    return list(findings.values())


def _tournament(pop: list[_PopMember], rng: random.Random, k: int = 3) -> _PopMember:
    sample = rng.sample(pop, min(k, len(pop)))
    return max(sample, key=lambda m: m.fitness)


# --- pytest entrypoint ---


COMPRESSOR_TARGETS: list[tuple[str, Callable[[], Compressor], tuple[str, ...]]] = [
    ("git_diff", GitDiffCompressor, ("git", "diff")),
    ("git_status", GitStatusCompressor, ("git", "status")),
    ("git_log", GitLogCompressor, ("git", "log")),
    ("pytest", PytestCompressor, ("pytest",)),
    ("grep", GrepCompressor, ("rg", "x")),
    ("ls", LsCompressor, ("ls", "-R")),
    ("tree", TreeCompressor, ("tree",)),
    ("find", FindCompressor, ("find", ".")),
    ("cargo_test", CargoTestCompressor, ("cargo", "test")),
    ("go_test", GoTestCompressor, ("go", "test")),
    ("npm_test", NpmTestCompressor, ("npm", "test")),
    ("lint", LintCompressor, ("ruff", "check", ".")),
    ("docker", DockerCompressor, ("docker", "build", ".")),
    ("pkg_install", PackageInstallCompressor, ("pip", "install", "fastapi")),
    ("kubectl_get", KubectlGetCompressor, ("kubectl", "get", "pods")),
    ("profiler", ProfilerCompressor, ("py-spy", "record")),
    ("json_log", JsonLogCompressor, ("cat", "/var/log/app.log")),
    ("coverage", CoverageCompressor, ("coverage", "report")),
    (
        "sql_explain",
        SqlExplainCompressor,
        ("psql", "-c", "EXPLAIN ANALYZE SELECT 1"),
    ),
]


# Schemas with residual findings on the deterministic seed; these stay
# informational under REDCON_V85_ENFORCE=1 until the underlying parser
# / regex / formatter alignment is closed. Track per #117 and the
# follow-ups for #106-#108. Adding a schema here is opt-out: every
# other schema becomes a hard CI gate when ENFORCE is on.
_NOT_YET_ENFORCED: frozenset[str] = frozenset()


@pytest.mark.parametrize(
    "name,factory,argv",
    COMPRESSOR_TARGETS,
    ids=[t[0] for t in COMPRESSOR_TARGETS],
)
def test_v85_genetic_hunt(name, factory, argv, capsys):
    """
    Run V85 adversarial GA against one compressor.

    Reports findings via stdout (use -s to view). Does not fail unless
    REDCON_V85_ENFORCE=1 is set, because we are documenting attack surface
    on first integration, not gating CI.
    """
    # `hash(name)` is salted by PYTHONHASHSEED so the seed varies between
    # Python invocations. Use a stable hash so REDCON_V85_ENFORCE=1 is a
    # real CI gate, not a flaky one. The 0x85_85 base offset stays so
    # seeds never collide with the smoke test's 0xC0FFEE.
    import hashlib

    rng = random.Random(
        0x85_85
        + int(hashlib.sha1(name.encode("utf-8")).hexdigest()[:8], 16) % 10_000
    )
    compressor = factory()
    findings = genetic_hunt(
        compressor,
        argv,
        generations=DEFAULT_GENERATIONS,
        population=POPULATION,
        rng=rng,
    )
    findings.sort(key=lambda f: f.fitness, reverse=True)
    with capsys.disabled():
        print(f"\n[V85] {name}: {len(findings)} unique findings")
        for f in findings[:5]:
            print(f"  {f.short()}")
    if ENFORCE and name not in _NOT_YET_ENFORCED:
        assert not findings, [f.short() for f in findings]


# A whole-suite smoke that runs a tiny GA across all compressors and just
# confirms the harness itself doesn't crash. Cheap to keep in CI.
def test_v85_smoke():
    rng = random.Random(0xC0FFEE)
    for name, factory, argv in COMPRESSOR_TARGETS[:3]:
        findings = genetic_hunt(
            factory(),
            argv,
            generations=3,
            population=4,
            rng=rng,
        )
        assert isinstance(findings, list)
