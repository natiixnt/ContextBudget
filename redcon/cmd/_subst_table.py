"""
Tokenizer-aware substitution table for compact/ultra tier outputs.

Each entry is (orig, repl, scope, note). `scope` is None for "any schema",
or a frozenset of schema strings to gate the rewrite to specific compressors.
The pipeline applies entries in order, accepting each candidate only when
estimate_tokens shrinks - so the table is monotone-safe by construction
even though some pairs would inflate in isolation (e.g. ', ' after a digit).

cl100k-calibrated; gains documented in research/notes/V31-multi-token-subst.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Sub:
    orig: str
    repl: str
    scope: frozenset[str] | None
    note: str


SUBST_TABLE: tuple[Sub, ...] = (
    Sub("Traceback (most recent call last):", "TB:", None, "py-traceback"),
    Sub(
        "Installing collected packages: ",
        "Pkgs: ",
        frozenset({"pkg_install"}),
        "pip-installed-list",
    ),
    Sub("AssertionError: assert ", "AE: ", None, "py-error-assert"),
    Sub("AttributeError: ", "AttrE: ", None, "py-attr-error"),
    Sub(
        "diff --git summary:",
        "git diff:",
        frozenset({"git_diff"}),
        "diff-header",
    ),
    Sub(
        "Successfully built ",
        "OK ",
        frozenset({"docker"}),
        "docker-success",
    ),
    Sub(
        " > 100 characters)",
        ">100)",
        frozenset({"lint"}),
        "ruff-E501-tail",
    ),
    Sub(
        " > 100 characters",
        ">100",
        frozenset({"lint"}),
        "ruff-E501-tail2",
    ),
    Sub(
        "DEPRECATION:",
        "DEP:",
        frozenset({"pkg_install"}),
        "pip-deprecation",
    ),
    Sub(" line too long ", " ll ", frozenset({"lint"}), "ruff-E501"),
    Sub(" matches in ", " m/", frozenset({"grep"}), "grep-header"),
    Sub("kubectl get ", "kg ", frozenset({"kubectl_get"}), "kubectl-prefix"),
    Sub("Collecting ", "C ", frozenset({"pkg_install"}), "pip-collect"),
    Sub("more hunks", "more h", frozenset({"git_diff"}), "diff-overflow"),
    Sub("AssertionError", "AE", None, "py-error"),
    Sub("grep: no matches", "g:0", frozenset({"grep"}), "grep-empty"),
    Sub(" (binary)", " bin", frozenset({"git_diff"}), "diff-bin"),
    Sub(", behind ", " -", frozenset({"git_status"}), "status-behind"),
    Sub(" failed, ", " F/", None, "test-summary"),
    Sub("ahead ", "+", frozenset({"git_status"}), "status-ahead"),
    Sub("grep: ", "g:", frozenset({"grep"}), "grep-prefix"),
    Sub("Step ", "S", frozenset({"docker"}), "docker-step"),
    Sub("log: ", "log:", frozenset({"git_log"}), "log-prefix"),
    Sub(" -> ", "->", None, "arrow"),
)
