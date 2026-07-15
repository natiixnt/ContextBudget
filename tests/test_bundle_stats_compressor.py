"""Behavioral tests for the bundle-stats compressor.

Feeds a representative webpack ``stats.json`` and an esbuild metafile through
``BundleStatsCompressor`` and checks that the summary keeps the top assets and
modules (with sizes) while cutting tokens versus the raw JSON.
"""

from __future__ import annotations

import json

from redcon.cmd.budget import BudgetHint
from redcon.cmd.compressors.base import CompressorContext
from redcon.cmd.compressors.bundle_stats_compressor import BundleStatsCompressor


def _ctx(argv: tuple[str, ...]) -> CompressorContext:
    # A generous budget so the formatter emits the full (verbose) summary; the
    # raw JSON still dwarfs it, so the reduction assertions hold.
    return CompressorContext(
        argv=argv,
        cwd=".",
        returncode=0,
        hint=BudgetHint(remaining_tokens=100_000, max_output_tokens=10_000),
    )


def _webpack_stats(n_modules: int = 60) -> bytes:
    modules = [
        {
            "id": i,
            "identifier": f"/abs/project/node_modules/pkg{i:02d}/dist/index.js",
            "name": f"./node_modules/pkg{i:02d}/index.js",
            "size": 1000 + i * 137,
            "chunks": [0],
            "reasons": [{"moduleName": "./src/index.js", "type": "harmony side effect"}],
        }
        for i in range(n_modules)
    ]
    stats = {
        "assets": [
            {"name": "main.js", "size": 250_000, "chunks": [0], "emitted": True},
            {"name": "vendor.js", "size": 900_000, "chunks": [1], "emitted": True},
        ],
        "modules": modules,
        "warnings": ["asset size limit: vendor.js exceeds the recommended limit"],
        "errors": [],
        "time": 4200,
    }
    return json.dumps(stats).encode("utf-8")


def _esbuild_metafile(n_inputs: int = 60) -> bytes:
    inputs = {
        f"src/module{i:02d}.ts": {"bytes": 500 + i * 11, "imports": []} for i in range(n_inputs)
    }
    meta = {
        "inputs": inputs,
        "outputs": {
            "dist/main.js": {
                "bytes": 320_000,
                "inputs": {
                    f"src/module{i:02d}.ts": {"bytesInOutput": 400 + i * 7} for i in range(n_inputs)
                },
                "imports": [],
                "exports": [],
            }
        },
    }
    return json.dumps(meta).encode("utf-8")


def test_webpack_stats_summarized_and_reduced() -> None:
    raw = _webpack_stats()
    out = BundleStatsCompressor().compress(raw, b"", _ctx(("webpack", "--json")))

    # Real reduction versus the raw JSON.
    assert out.original_tokens > 0
    assert out.compressed_tokens < out.original_tokens

    text = out.text
    assert "webpack" in text  # the tool is named
    assert "vendor.js" in text and "main.js" in text  # both assets kept
    assert "pkg" in text  # top modules listed
    # The largest asset is surfaced first.
    assert text.index("vendor.js") < text.index("main.js")
    # Asset names are must-preserve patterns and the formatter emitted them.
    assert out.must_preserve_ok is True


def test_esbuild_metafile_summarized_and_reduced() -> None:
    raw = _esbuild_metafile()
    out = BundleStatsCompressor().compress(raw, b"", _ctx(("esbuild", "--analyze")))

    assert out.original_tokens > 0
    assert out.compressed_tokens < out.original_tokens

    text = out.text
    assert "esbuild" in text
    assert "dist/main.js" in text  # the output bundle is named
    assert "src/module" in text  # input modules listed
    assert out.must_preserve_ok is True


def test_compressor_matches_bundle_argv() -> None:
    comp = BundleStatsCompressor()
    assert comp.matches(("webpack", "--json")) is True
    assert comp.matches(("git", "status")) is False


def test_non_json_output_does_not_crash_or_inflate() -> None:
    # Adversarial: plain text is not bundle stats; the compressor must not crash
    # and must not blow the output up.
    raw = b"just some build log line\nanother line\n"
    out = BundleStatsCompressor().compress(raw, b"", _ctx(("webpack", "--json")))
    assert out.compressed_tokens <= out.original_tokens
