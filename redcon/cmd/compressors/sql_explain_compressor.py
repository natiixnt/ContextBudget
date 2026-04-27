"""
SQL EXPLAIN ANALYZE compressor (V61).

Parses Postgres EXPLAIN ANALYZE and MySQL EXPLAIN FORMAT=TREE plans.
The body of these outputs is tree-shaped: each node has an indentation
depth keyed off `' -> '`, an operator name, an optional relation, plus
two parenthesised triplets `(cost=A..B rows=R width=W)` and `(actual
time=T1..T2 rows=R' loops=L)`.

Self-time per node is derived as `actual_ms - sum(child.actual_ms *
child.loops)` so we can rank by *node-local* cost rather than cumulative
time. Top 3 by self-time plus scan-flag histogram plus a small set of
smell warnings (Seq Scan on >1000 rows, top-N heapsort spilling) gives
the agent a query-tuning answer in <50 tokens.

Detection: argv head is psql/mysql/mysqlsh/mariadb OR any argv token
begins with EXPLAIN (the inline `-c "EXPLAIN ANALYZE ..."` form).
"""

from __future__ import annotations

import re

from redcon.cmd.budget import select_level
from redcon.cmd.compressors.base import (
    Compressor,
    CompressorContext,
    verify_must_preserve,
)
from redcon.cmd.types import (
    CompressedOutput,
    CompressionLevel,
    ExplainNode,
    ExplainResult,
)
from redcon.cmd._tokens_lite import estimate_tokens


# --- regexes ---


# Postgres node line. The leading "->" is the parent arrow; the root
# Limit/etc has no arrow but matches the same rest. We capture indentation
# to determine depth (4 spaces per level in coverage's default indent).
_PG_NODE = re.compile(
    r"^(?P<indent>\s*)(?:->\s+)?"
    r"(?P<op>[A-Z][A-Za-z][\w \-/]+?)"
    r"(?:\s+(?:on|using)\s+(?P<rel>\S+))?"
    r"\s*\(cost=[\d.]+\.\.(?P<cost_total>[\d.]+)"
    r"\s+rows=(?P<rows_est>\d+)(?:\s+width=\d+)?\)"
    r"(?:\s*\(actual time=[\d.]+\.\.(?P<actual_ms>[\d.]+)"
    r"\s+rows=(?P<rows_actual>\d+)\s+loops=(?P<loops>\d+)\))?\s*$"
)

# MySQL FORMAT=TREE node line. Each node opens with "-> Op" then optional
# (cost=N rows=M) (actual time=T rows=R loops=L). Indentation is two
# spaces per level.
_MYSQL_TREE_NODE = re.compile(
    r"^(?P<indent>\s*)->\s+"
    r"(?P<op>[A-Z][A-Za-z][\w \-/]+?)"
    r"(?:\s+(?:on|using)\s+(?P<rel>\S+))?"
    r"\s*\(cost=(?P<cost_total>[\d.]+)\s+rows=(?P<rows_est>\d+)\)"
    r"(?:\s*\(actual time=(?P<actual_ms>[\d.]+)"
    r"\s+rows=(?P<rows_actual>\d+)\s+loops=(?P<loops>\d+)\))?\s*$"
)

_PLAN_TIME = re.compile(r"^\s*Planning Time:\s+(?P<ms>[\d.]+)\s+ms\s*$")
_EXEC_TIME = re.compile(r"^\s*Execution Time:\s+(?P<ms>[\d.]+)\s+ms\s*$")
_DETAIL_PREFIXES = (
    "Sort Key:",
    "Sort Method:",
    "Hash Cond:",
    "Index Cond:",
    "Filter:",
    "Join Filter:",
    "Recheck Cond:",
    "Group Key:",
    "Merge Cond:",
)
_TIME_TIME = re.compile(r"^\s*Time:\s+(?P<ms>[\d.]+)\s+ms\s*$")  # mysql

_SEQ_SCAN_ROWS_THRESHOLD = 1000
_SLOWEST_K = 3
_DETAIL_CLIP = 80


class SqlExplainCompressor:
    schema = "sql_explain"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        # Patterns extended at compress time once we know the slowest
        # nodes / smell set.
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if not argv:
            return False
        head = argv[0].rsplit("/", 1)[-1]
        if head in {"psql", "mysql", "mysqlsh", "mariadb"}:
            return True
        for token in argv[1:]:
            if token.upper().startswith("EXPLAIN"):
                return True
        return False

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        if not text.strip() and raw_stderr:
            text = raw_stderr.decode("utf-8", errors="replace")
        result = parse_explain(text)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        compressed_tokens = estimate_tokens(formatted)
        patterns = _must_preserve_for(result, level)
        preserved = verify_must_preserve(formatted, patterns, text)
        return CompressedOutput(
            text=formatted,
            level=level,
            schema=self.schema,
            original_tokens=raw_tokens,
            compressed_tokens=compressed_tokens,
            must_preserve_ok=preserved,
            truncated=False,
            notes=ctx.notes,
        )


def parse_explain(text: str) -> ExplainResult:
    dialect = _sniff_dialect(text)
    node_re = _MYSQL_TREE_NODE if dialect == "mysql_tree" else _PG_NODE
    nodes: list[ExplainNode] = []
    plan_time_ms: float | None = None
    exec_time_ms: float | None = None

    pending_detail: list[str] = []
    last_node_idx: int | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        # Match plan-time / execution-time first; they are agent-relevant.
        plan_match = _PLAN_TIME.match(line)
        if plan_match:
            try:
                plan_time_ms = float(plan_match.group("ms"))
            except ValueError:
                pass
            continue
        exec_match = _EXEC_TIME.match(line)
        if exec_match:
            try:
                exec_time_ms = float(exec_match.group("ms"))
            except ValueError:
                pass
            continue
        time_match = _TIME_TIME.match(line)
        if time_match:
            try:
                exec_time_ms = float(time_match.group("ms"))
            except ValueError:
                pass
            continue

        # Detail lines (Filter:, Index Cond:, Sort Key:, ...) attach to
        # the most recently parsed node.
        stripped = line.strip()
        if last_node_idx is not None and any(
            stripped.startswith(prefix) for prefix in _DETAIL_PREFIXES
        ):
            pending_detail.append(stripped)
            continue

        node_match = node_re.match(line)
        if not node_match:
            continue

        # Flush previous node's detail before starting a new node.
        if pending_detail and last_node_idx is not None:
            existing = nodes[last_node_idx]
            joined = "; ".join(pending_detail)[:_DETAIL_CLIP * 2]
            nodes[last_node_idx] = _replace(existing, detail=joined)
            pending_detail = []

        depth = _depth_from_indent(node_match.group("indent"))
        op = node_match.group("op").strip()
        relation = node_match.group("rel")
        cost_total = _safe_float(node_match.group("cost_total"))
        rows_est = _safe_int(node_match.group("rows_est"))
        actual_ms = _safe_float(node_match.groupdict().get("actual_ms"))
        rows_actual = _safe_int(node_match.groupdict().get("rows_actual"))
        loops = _safe_int(node_match.groupdict().get("loops"))

        node = ExplainNode(
            op=op,
            relation=relation,
            cost_total=cost_total,
            rows_est=rows_est,
            actual_ms=actual_ms,
            self_ms=None,
            rows_actual=rows_actual,
            loops=loops,
            detail=None,
            depth=depth,
        )
        nodes.append(node)
        last_node_idx = len(nodes) - 1

    if pending_detail and last_node_idx is not None:
        existing = nodes[last_node_idx]
        joined = "; ".join(pending_detail)[:_DETAIL_CLIP * 2]
        nodes[last_node_idx] = _replace(existing, detail=joined)

    nodes = _derive_self_ms(nodes)

    root = nodes[0] if nodes else None
    slowest = _slowest_nodes(nodes, _SLOWEST_K)
    flags = _flag_counts(nodes)
    warnings = _warnings_for(nodes)

    return ExplainResult(
        dialect=dialect,
        nodes=tuple(nodes),
        plan_time_ms=plan_time_ms,
        exec_time_ms=exec_time_ms,
        root=root,
        slowest=tuple(slowest),
        flag_counts=tuple(flags),
        warnings=tuple(warnings),
    )


def _replace(node: ExplainNode, **kwargs) -> ExplainNode:
    """Return a copy of node with field overrides; frozen dataclass utility."""
    fields = {
        "op": node.op,
        "relation": node.relation,
        "cost_total": node.cost_total,
        "rows_est": node.rows_est,
        "actual_ms": node.actual_ms,
        "self_ms": node.self_ms,
        "rows_actual": node.rows_actual,
        "loops": node.loops,
        "detail": node.detail,
        "depth": node.depth,
    }
    fields.update(kwargs)
    return ExplainNode(**fields)


def _sniff_dialect(text: str) -> str:
    """Return 'mysql_tree' when the format-tree shape dominates, else 'postgres'."""
    sample = text[:2000]
    if "EXPLAIN ANALYZE" in sample.upper() and "(actual time=" in sample:
        return "postgres"
    if "Planning Time:" in sample or "Execution Time:" in sample:
        return "postgres"
    if "(cost=" in sample and "(actual time=" in sample and "rows=" in sample:
        # Either dialect; pick by cost-shape: PG uses A..B, MySQL is a single
        # number.
        if re.search(r"cost=[\d.]+\.\.[\d.]+", sample):
            return "postgres"
    return "mysql_tree"


def _depth_from_indent(indent: str) -> int:
    """Translate leading whitespace to a tree depth.

    Postgres uses ~3-6 spaces per level (variable), MySQL TREE uses 2
    consistently. Floor-divide by 2 - it preserves relative ordering for
    self-time derivation, which is the only thing depth is used for.
    """
    return len(indent) // 2


def _derive_self_ms(nodes: list[ExplainNode]) -> list[ExplainNode]:
    """Compute per-node self_ms using the indentation-keyed parent map."""
    if not nodes:
        return nodes
    # Walk in reverse depth order so each parent has already aggregated
    # its descendants. Use a depth stack to find each node's children.
    children: dict[int, list[int]] = {i: [] for i in range(len(nodes))}
    stack: list[int] = []
    for idx, node in enumerate(nodes):
        while stack and nodes[stack[-1]].depth >= node.depth:
            stack.pop()
        if stack:
            children[stack[-1]].append(idx)
        stack.append(idx)

    out: list[ExplainNode] = []
    for idx, node in enumerate(nodes):
        if node.actual_ms is None:
            out.append(_replace(node, self_ms=None))
            continue
        child_total = 0.0
        for child_idx in children[idx]:
            child = nodes[child_idx]
            if child.actual_ms is None:
                continue
            loops = child.loops if child.loops and child.loops > 0 else 1
            child_total += child.actual_ms * loops
        loops = node.loops if node.loops and node.loops > 0 else 1
        own = node.actual_ms * loops
        self_ms = max(0.0, own - child_total)
        out.append(_replace(node, self_ms=self_ms))
    return out


def _slowest_nodes(nodes: list[ExplainNode], k: int) -> list[ExplainNode]:
    rated = [n for n in nodes if n.self_ms is not None]
    rated.sort(
        key=lambda n: (
            -(n.self_ms or 0.0),
            n.depth,
            n.op,
            n.relation or "",
        )
    )
    return rated[:k]


def _flag_counts(nodes: list[ExplainNode]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for node in nodes:
        canon = _canon_op(node.op)
        counts[canon] = counts.get(canon, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _canon_op(op: str) -> str:
    lowered = op.strip().lower().replace(" ", "_")
    # Compact the verbose Postgres nested-loop / merge form.
    if lowered.startswith("seq_scan"):
        return "seq_scan"
    if lowered.startswith("index_scan"):
        return "index_scan"
    if lowered.startswith("index_only_scan"):
        return "index_only_scan"
    if lowered.startswith("hash_join"):
        return "hash_join"
    if lowered.startswith("nested_loop"):
        return "nested_loop"
    if lowered.startswith("merge_join"):
        return "merge_join"
    return lowered


def _warnings_for(nodes: list[ExplainNode]) -> list[str]:
    out: list[str] = []
    for node in nodes:
        canon = _canon_op(node.op)
        if canon == "seq_scan" and node.rows_actual is not None and node.rows_actual >= _SEQ_SCAN_ROWS_THRESHOLD:
            relation = node.relation or "<unknown>"
            out.append(f"Seq Scan on {relation} ({node.rows_actual} rows)")
        if node.detail and "top-N heapsort" in node.detail and "spill" in node.detail.lower():
            out.append(f"top-N heapsort spilled at {node.op}")
    return out


def _format(result: ExplainResult, level: CompressionLevel) -> str:
    if not result.root:
        return f"sql_explain ({result.dialect}): no plan parsed"

    if level == CompressionLevel.ULTRA:
        return _format_ultra(result)
    if level == CompressionLevel.COMPACT:
        return _format_compact(result)
    return _format_verbose(result)


def _format_ultra(result: ExplainResult) -> str:
    root = result.root
    parts = [f"sql_explain.{result.dialect}: root={root.op}"]
    if root.cost_total is not None:
        parts.append(f"cost={root.cost_total:.2f}")
    if result.exec_time_ms is not None:
        parts.append(f"exec={result.exec_time_ms:.1f}ms")
    if result.slowest:
        slow = result.slowest[0]
        relation = f"({slow.relation})" if slow.relation else ""
        ms = f"{slow.self_ms:.1f}ms" if slow.self_ms is not None else "-"
        parts.append(f"slowest={slow.op}{relation},{ms}")
    seq = next((c for k, c in result.flag_counts if k == "seq_scan"), 0)
    if seq:
        parts.append(f"seq_scans={seq}")
    return " ".join(parts)


def _format_compact(result: ExplainResult) -> str:
    root = result.root
    lines = [f"sql_explain.{result.dialect}"]
    head = f"root: {root.op}"
    if root.relation:
        head += f" on {root.relation}"
    if root.cost_total is not None:
        head += f", total_cost={root.cost_total:.2f}"
    if result.exec_time_ms is not None:
        head += f", actual_time={result.exec_time_ms:.2f}ms"
    lines.append(head)
    if result.plan_time_ms is not None or result.exec_time_ms is not None:
        plan = (
            f"plan_time={result.plan_time_ms:.2f}ms"
            if result.plan_time_ms is not None
            else ""
        )
        exec_ = (
            f"exec_time={result.exec_time_ms:.2f}ms"
            if result.exec_time_ms is not None
            else ""
        )
        rows = f" rows={root.rows_actual}" if root.rows_actual is not None else ""
        lines.append(" ".join(filter(None, [plan, exec_])) + rows)
    if result.slowest:
        lines.append("slowest:")
        for node in result.slowest:
            relation = f" {node.relation}" if node.relation else ""
            ms = f"{node.self_ms:.2f}ms" if node.self_ms is not None else "-"
            rows = (
                f" rows={node.rows_actual}" if node.rows_actual is not None else ""
            )
            detail = ""
            if node.detail:
                detail = f" {_clip(node.detail, _DETAIL_CLIP)}"
            lines.append(f"- {node.op}{relation}: {ms}{rows}{detail}")
    if result.flag_counts:
        flags = " ".join(f"{k}={v}" for k, v in result.flag_counts)
        lines.append(f"flags: {flags}")
    for warn in result.warnings:
        lines.append(f"warn: {warn}")
    return "\n".join(lines)


def _format_verbose(result: ExplainResult) -> str:
    lines = [_format_compact(result), "---"]
    for node in result.nodes:
        ms = f"{node.actual_ms:.2f}ms" if node.actual_ms is not None else "-"
        rows = (
            f" rows={node.rows_actual}" if node.rows_actual is not None else ""
        )
        relation = f" {node.relation}" if node.relation else ""
        prefix = "  " * node.depth + "->"
        lines.append(f"{prefix} {node.op}{relation}: {ms}{rows}")
        if node.detail:
            lines.append(f"    {_clip(node.detail, _DETAIL_CLIP * 2)}")
    return "\n".join(lines)


def _must_preserve_for(
    result: ExplainResult, level: CompressionLevel
) -> tuple[str, ...]:
    if level == CompressionLevel.ULTRA or not result.root:
        return ()
    patterns: list[str] = []
    if result.root.cost_total is not None:
        patterns.append(re.escape(f"{result.root.cost_total:.2f}"))
    if result.slowest:
        patterns.append(re.escape(result.slowest[0].op))
    has_seq_scan = any(c[0] == "seq_scan" for c in result.flag_counts)
    if has_seq_scan and any("Seq Scan" in w for w in result.warnings):
        patterns.append(r"Seq Scan")
    return tuple(patterns)


def _safe_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
