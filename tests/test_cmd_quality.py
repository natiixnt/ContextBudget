"""
Quality regression harness for every registered compressor.

The case corpus lives in redcon.cmd.quality_cases (inside the package,
because the cmd-quality / cmd-bench CLI commands run it from the
installed wheel too); this file is just the pytest harness around it.
"""

from __future__ import annotations

import pytest

from redcon.cmd.quality import run_quality_check
from redcon.cmd.quality_cases import CASES


@pytest.mark.parametrize(
    "name,compressor,raw_stdout,raw_stderr,argv",
    CASES,
    ids=[c[0] for c in CASES],
)
def test_quality_check(
    name: str,
    compressor,
    raw_stdout: bytes,
    raw_stderr: bytes,
    argv: tuple[str, ...],
):
    check = run_quality_check(
        compressor,
        raw_stdout=raw_stdout,
        raw_stderr=raw_stderr,
        argv=argv,
    )
    failures = check.failures()
    assert not failures, "\n".join(failures)


def test_harness_detects_inflation_regression():
    """A compressor whose verbose level inflates by >10% must fail the gate."""
    from redcon.cmd.types import CompressedOutput, CompressionLevel

    class InflatingCompressor:
        schema = "fake_inflate"
        must_preserve_patterns = ()

        def matches(self, argv):
            return False

        def compress(self, stdout, stderr, ctx):
            text = stdout.decode("utf-8", errors="replace")
            inflated = text + "\n" + ("padding\n" * 200)
            from redcon.core.tokens import estimate_tokens

            return CompressedOutput(
                text=inflated,
                level=CompressionLevel.VERBOSE,
                schema=self.schema,
                original_tokens=estimate_tokens(text),
                compressed_tokens=estimate_tokens(inflated),
                must_preserve_ok=True,
                truncated=False,
            )

    big = ("input " * 200).encode()
    check = run_quality_check(
        InflatingCompressor(),
        raw_stdout=big,
        argv=("fake",),
    )
    failures = check.failures()
    # We expect at least one threshold violation across the three levels.
    assert any("below floor" in f for f in failures)
