from __future__ import annotations

"""Repository context graph visualizer.

Builds a dependency graph over scanned repository files, annotates nodes with
token usage and inclusion frequency, and exports the result as JSON.  An
optional self-contained HTML file provides an interactive visualization.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from contextbudget.config import ContextBudgetConfig
from contextbudget.core.tokens import estimate_tokens
from contextbudget.schemas.models import FileRecord, normalize_repo
from contextbudget.scorers.import_graph import ImportGraph, build_import_graph
from contextbudget.stages.workflow import run_scan_stage


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class GraphNode:
    """A single file node in the repository context graph."""

    id: str                  # relative path used as stable identifier
    label: str               # short filename for display
    estimated_tokens: int    # heuristic token count for file content
    inclusion_count: int     # times seen in pack history (0 if no history)
    inclusion_rate: float    # fraction of runs where file was included
    extension: str           # file extension (e.g. ".py")
    is_entrypoint: bool      # detected as a repo entrypoint
    in_degree: int           # number of files that import this file
    out_degree: int          # number of files this file imports


@dataclass(slots=True)
class GraphEdge:
    """A directed import/dependency edge between two file nodes."""

    source: str   # node id of the importing file
    target: str   # node id of the imported file


@dataclass(slots=True)
class VisualizeStats:
    """Summary statistics for the repository graph."""

    total_nodes: int
    total_edges: int
    total_estimated_tokens: int
    max_tokens_per_file: int
    files_with_heatmap_data: int
    top_token_files: list[str]   # top-5 by token count
    most_imported_files: list[str]  # top-5 by in_degree


@dataclass(slots=True)
class VisualizeReport:
    """Complete serializable output of the visualize command."""

    command: str
    generated_at: str
    repo: str
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    stats: VisualizeStats = field(
        default_factory=lambda: VisualizeStats(0, 0, 0, 0, 0, [], [])
    )


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

_ENTRYPOINT_NAMES: set[str] = {
    "main.py", "__main__.py", "index.ts", "index.tsx", "index.js",
    "index.jsx", "app.py", "app.ts", "app.js", "server.py", "server.ts",
    "server.js", "manage.py", "cli.py",
}


def _relative_path(record: FileRecord) -> str:
    """Return the best relative path string for a FileRecord."""
    return record.relative_path or record.path


def _load_heatmap_stats(history: Sequence[str | Path]) -> dict[str, tuple[int, float]]:
    """Parse pack run JSON artifacts and return {path: (inclusion_count, rate)}."""
    from contextbudget.core.heatmap import build_heatmap_report, heatmap_as_dict

    if not history:
        return {}
    try:
        report = build_heatmap_report(history_inputs=history)
        data = heatmap_as_dict(report)
    except ValueError:
        return {}

    result: dict[str, tuple[int, float]] = {}
    for file_stat in data.get("files", []):
        path = str(file_stat.get("path", "") or "")
        if not path:
            continue
        count = int(file_stat.get("inclusion_count", 0) or 0)
        rate = float(file_stat.get("inclusion_rate", 0.0) or 0.0)
        result[path] = (count, rate)
    return result


def build_repo_graph(
    repo: str | Path,
    config: ContextBudgetConfig,
    *,
    history: Sequence[str | Path] | None = None,
) -> VisualizeReport:
    """Scan *repo*, build a dependency graph, and annotate nodes with token data.

    Args:
        repo: Repository root path.
        config: Loaded ContextBudget configuration.
        history: Optional list of pack run JSON files or directories to derive
                 inclusion frequency from.  When omitted all inclusion counts
                 are zero.

    Returns:
        A :class:`VisualizeReport` ready for JSON serialisation or HTML export.
    """
    repo_path = normalize_repo(repo)

    # 1. Scan files
    records: list[FileRecord] = run_scan_stage(repo_path, config)

    # 2. Build import graph
    graph: ImportGraph = build_import_graph(records, entrypoint_filenames=_ENTRYPOINT_NAMES)

    # 3. Load optional heatmap history
    heatmap: dict[str, tuple[int, float]] = _load_heatmap_stats(list(history or []))

    # 4. Estimate tokens per file
    token_map: dict[str, int] = {}
    for record in records:
        rel = _relative_path(record)
        text = record.content_preview or ""
        if record.absolute_path:
            try:
                full_text = Path(record.absolute_path).read_text(encoding="utf-8", errors="ignore")
                token_map[record.path] = estimate_tokens(full_text)
            except OSError:
                token_map[record.path] = estimate_tokens(text)
        else:
            token_map[record.path] = estimate_tokens(text)

    # 5. Build node list
    entrypoints_set = graph.entrypoints
    nodes: list[GraphNode] = []
    for record in records:
        rel = _relative_path(record)
        path_key = record.path
        heat = heatmap.get(rel) or heatmap.get(path_key)
        inclusion_count = heat[0] if heat else 0
        inclusion_rate = heat[1] if heat else 0.0

        node = GraphNode(
            id=rel,
            label=rel.rsplit("/", 1)[-1],
            estimated_tokens=token_map.get(path_key, 0),
            inclusion_count=inclusion_count,
            inclusion_rate=round(inclusion_rate, 4),
            extension=record.extension or "",
            is_entrypoint=path_key in entrypoints_set,
            in_degree=len(graph.incoming.get(path_key, set())),
            out_degree=len(graph.outgoing.get(path_key, set())),
        )
        nodes.append(node)

    # 6. Build edge list (use relative paths as IDs)
    path_to_rel: dict[str, str] = {r.path: _relative_path(r) for r in records}
    edges: list[GraphEdge] = []
    for source_abs, targets in graph.outgoing.items():
        source_id = path_to_rel.get(source_abs, source_abs)
        for target_abs in sorted(targets):
            target_id = path_to_rel.get(target_abs, target_abs)
            edges.append(GraphEdge(source=source_id, target=target_id))

    # 7. Compute summary stats
    total_tokens = sum(n.estimated_tokens for n in nodes)
    max_tokens = max((n.estimated_tokens for n in nodes), default=0)
    top_token_files = [
        n.id for n in sorted(nodes, key=lambda x: -x.estimated_tokens)[:5]
    ]
    most_imported = [
        n.id for n in sorted(nodes, key=lambda x: -x.in_degree)[:5]
    ]

    stats = VisualizeStats(
        total_nodes=len(nodes),
        total_edges=len(edges),
        total_estimated_tokens=total_tokens,
        max_tokens_per_file=max_tokens,
        files_with_heatmap_data=sum(1 for n in nodes if n.inclusion_count > 0),
        top_token_files=top_token_files,
        most_imported_files=most_imported,
    )

    return VisualizeReport(
        command="visualize",
        generated_at=datetime.now(timezone.utc).isoformat(),
        repo=str(repo_path),
        nodes=nodes,
        edges=edges,
        stats=stats,
    )


def visualize_as_dict(report: VisualizeReport) -> dict[str, Any]:
    """Convert a :class:`VisualizeReport` to a JSON-serialisable dictionary."""
    return asdict(report)


# ---------------------------------------------------------------------------
# HTML visualization
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ContextBudget Graph - {repo}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#0f1117;color:#e2e8f0;display:flex;height:100vh;overflow:hidden}}
#sidebar{{width:280px;min-width:200px;background:#1a1f2e;border-right:1px solid #2d3748;display:flex;flex-direction:column;padding:12px;gap:10px;overflow-y:auto}}
#sidebar h1{{font-size:13px;font-weight:700;color:#63b3ed;text-transform:uppercase;letter-spacing:.05em}}
#repo-label{{font-size:11px;color:#718096;word-break:break-all}}
#search{{background:#2d3748;border:1px solid #4a5568;color:#e2e8f0;border-radius:6px;padding:6px 10px;font-size:12px;width:100%}}
#search::placeholder{{color:#718096}}
#stats{{font-size:11px;color:#a0aec0;line-height:1.7}}
#stats b{{color:#e2e8f0}}
#legend{{font-size:11px;color:#a0aec0}}
.legend-row{{display:flex;align-items:center;gap:6px;margin-top:4px}}
.legend-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
#detail{{background:#2d3748;border-radius:8px;padding:10px;font-size:11px;color:#a0aec0;line-height:1.8;display:none}}
#detail b{{color:#e2e8f0;display:block;margin-bottom:4px;font-size:12px;word-break:break-all}}
#canvas-wrap{{flex:1;position:relative;overflow:hidden}}
canvas{{display:block;width:100%;height:100%}}
#tooltip{{position:absolute;background:#2d3748;border:1px solid #4a5568;border-radius:6px;padding:8px 10px;font-size:11px;color:#e2e8f0;pointer-events:none;display:none;max-width:240px;line-height:1.7;z-index:10}}
#controls{{position:absolute;bottom:12px;right:12px;display:flex;gap:6px}}
.btn{{background:#2d3748;border:1px solid #4a5568;color:#e2e8f0;border-radius:6px;padding:5px 10px;font-size:11px;cursor:pointer}}
.btn:hover{{background:#4a5568}}
</style>
</head>
<body>
<div id="sidebar">
  <h1>ContextBudget</h1>
  <div id="repo-label">{repo}</div>
  <input id="search" type="text" placeholder="Filter nodes…" autocomplete="off">
  <div id="stats">
    <div>Nodes: <b id="s-nodes">0</b></div>
    <div>Edges: <b id="s-edges">0</b></div>
    <div>Total tokens: <b id="s-tokens">0</b></div>
    <div>Files w/ history: <b id="s-heat">0</b></div>
  </div>
  <div id="legend">
    <div style="font-weight:600;margin-bottom:4px">Extension</div>
  </div>
  <div id="detail"></div>
</div>
<div id="canvas-wrap">
  <canvas id="canvas"></canvas>
  <div id="tooltip"></div>
  <div id="controls">
    <button class="btn" id="btn-reset">Reset view</button>
    <button class="btn" id="btn-fit">Fit all</button>
  </div>
</div>
<script>
const RAW = {graph_json};

// ---- colour palette ----
const EXT_COLORS = {{
  ".py":"#63b3ed",".ts":"#68d391",".tsx":"#68d391",".js":"#f6e05e",
  ".jsx":"#f6e05e",".mjs":"#f6e05e",".go":"#76e4f7",".rs":"#fc8181",
  ".java":"#fbd38d",".kt":"#b794f4",".rb":"#f687b3",".cs":"#90cdf4",
  ".cpp":"#fc8181",".c":"#fbd38d",".md":"#a0aec0",".toml":"#a0aec0",
  ".json":"#a0aec0",".yaml":"#a0aec0",".yml":"#a0aec0",".sh":"#68d391",
}};
function extColor(ext){{ return EXT_COLORS[ext] || "#718096"; }}

// ---- build lookup maps ----
const nodeById = {{}};
RAW.nodes.forEach(n => nodeById[n.id] = n);

// ---- physics state ----
const W = () => canvas.width, H = () => canvas.height;
let nodes = [], edges = [];
let transform = {{x:0, y:0, scale:1}};
let drag = null, panStart = null;
let hoveredNode = null, selectedNode = null;
let filterText = "";

function initPhysics() {{
  const maxTok = Math.max(1, ...RAW.nodes.map(n => n.estimated_tokens));
  nodes = RAW.nodes.map(n => ({{
    ...n,
    x: (Math.random() - 0.5) * 600,
    y: (Math.random() - 0.5) * 600,
    vx: 0, vy: 0,
    r: 5 + 14 * Math.sqrt(n.estimated_tokens / maxTok),
    color: extColor(n.extension),
  }}));

  const idxById = {{}};
  nodes.forEach((n,i) => idxById[n.id] = i);
  edges = RAW.edges
    .map(e => ({{ src: idxById[e.source], tgt: idxById[e.target] }}))
    .filter(e => e.src !== undefined && e.tgt !== undefined);
}}

// ---- force simulation ----
const REPULSION = 3000, SPRING_LEN = 80, SPRING_K = 0.05, DAMPING = 0.85;
let simRunning = true, simTick = 0;

function simulate() {{
  if (!simRunning) return;
  const n = nodes.length;

  // repulsion (Barnes-Hut approximation skipped for simplicity - O(n²) capped)
  const CAP = Math.min(n, 300);
  for (let i = 0; i < CAP; i++) {{
    for (let j = i + 1; j < CAP; j++) {{
      const a = nodes[i], b = nodes[j];
      let dx = b.x - a.x, dy = b.y - a.y;
      const dist2 = dx*dx + dy*dy + 1;
      const f = REPULSION / dist2;
      const inv = Math.sqrt(dist2);
      dx /= inv; dy /= inv;
      a.vx -= f * dx; a.vy -= f * dy;
      b.vx += f * dx; b.vy += f * dy;
    }}
  }}

  // spring forces
  edges.forEach(e => {{
    const a = nodes[e.src], b = nodes[e.tgt];
    const dx = b.x - a.x, dy = b.y - a.y;
    const dist = Math.sqrt(dx*dx + dy*dy) || 1;
    const f = (dist - SPRING_LEN) * SPRING_K;
    const fx = f * dx / dist, fy = f * dy / dist;
    a.vx += fx; a.vy += fy;
    b.vx -= fx; b.vy -= fy;
  }});

  // gravity toward centre
  nodes.forEach(nd => {{
    nd.vx -= nd.x * 0.003;
    nd.vy -= nd.y * 0.003;
  }});

  // integrate
  nodes.forEach(nd => {{
    if (nd === drag) return;
    nd.vx *= DAMPING; nd.vy *= DAMPING;
    nd.x += nd.vx; nd.y += nd.vy;
  }});

  simTick++;
  if (simTick > 300) simRunning = false;
}}

// ---- render ----
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");

function worldToScreen(x, y) {{
  return [x * transform.scale + transform.x, y * transform.scale + transform.y];
}}
function screenToWorld(sx, sy) {{
  return [(sx - transform.x) / transform.scale, (sy - transform.y) / transform.scale];
}}

function visibleNodes() {{
  if (!filterText) return nodes;
  return nodes.filter(n => n.id.toLowerCase().includes(filterText));
}}

function draw() {{
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  canvas.width = W * dpr; canvas.height = H * dpr;
  ctx.scale(dpr, dpr);

  ctx.fillStyle = "#0f1117";
  ctx.fillRect(0, 0, W, H);

  const visible = new Set(visibleNodes().map(n => n.id));

  // draw edges
  ctx.lineWidth = 0.7;
  edges.forEach(e => {{
    const a = nodes[e.src], b = nodes[e.tgt];
    if (!visible.has(a.id) || !visible.has(b.id)) return;
    const [ax, ay] = worldToScreen(a.x, a.y);
    const [bx, by] = worldToScreen(b.x, b.y);
    ctx.beginPath();
    ctx.strokeStyle = "rgba(74,85,104,0.55)";
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.stroke();
  }});

  // draw nodes
  nodes.forEach(nd => {{
    if (!visible.has(nd.id)) return;
    const [sx, sy] = worldToScreen(nd.x, nd.y);
    const r = nd.r * transform.scale;
    if (sx + r < 0 || sx - r > W || sy + r < 0 || sy - r > H) return;

    const alpha = filterText ? (visible.has(nd.id) ? 1 : 0.15) : 1;
    ctx.globalAlpha = alpha;

    // glow for high inclusion rate
    if (nd.inclusion_rate > 0) {{
      ctx.shadowColor = nd.color;
      ctx.shadowBlur = 6 * nd.inclusion_rate * transform.scale;
    }}

    ctx.beginPath();
    ctx.arc(sx, sy, Math.max(r, 2), 0, Math.PI * 2);
    ctx.fillStyle = nd === selectedNode ? "#fff" : (nd === hoveredNode ? "#edf2f7" : nd.color);
    ctx.fill();

    // entrypoint ring
    if (nd.is_entrypoint) {{
      ctx.strokeStyle = "#f6e05e";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }}
    ctx.shadowBlur = 0;
    ctx.globalAlpha = 1;

    // label for large / selected nodes
    if (r > 8 || nd === selectedNode || nd === hoveredNode) {{
      const fs = Math.min(11, Math.max(8, r * 0.9));
      ctx.font = `${{fs}}px system-ui,sans-serif`;
      ctx.fillStyle = "#e2e8f0";
      ctx.textAlign = "center";
      ctx.fillText(nd.label, sx, sy + r + fs + 1);
    }}
  }});
}}

// ---- stats panel ----
function updateStats() {{
  document.getElementById("s-nodes").textContent = RAW.stats.total_nodes;
  document.getElementById("s-edges").textContent = RAW.stats.total_edges;
  document.getElementById("s-tokens").textContent = RAW.stats.total_estimated_tokens.toLocaleString();
  document.getElementById("s-heat").textContent = RAW.stats.files_with_heatmap_data;
}}

// ---- legend ----
function buildLegend() {{
  const seen = {{}};
  RAW.nodes.forEach(n => {{ if (n.extension) seen[n.extension] = extColor(n.extension); }});
  const legend = document.getElementById("legend");
  Object.entries(seen).sort().forEach(([ext, color]) => {{
    const row = document.createElement("div");
    row.className = "legend-row";
    row.innerHTML = `<span class="legend-dot" style="background:${{color}}"></span><span>${{ext}}</span>`;
    legend.appendChild(row);
  }});
}}

// ---- detail panel ----
function showDetail(nd) {{
  const el = document.getElementById("detail");
  if (!nd) {{ el.style.display = "none"; return; }}
  el.style.display = "block";
  el.innerHTML = `
    <b>${{nd.id}}</b>
    Tokens: ${{nd.estimated_tokens.toLocaleString()}}<br>
    Extension: ${{nd.extension || "-"}}<br>
    Imports: ${{nd.out_degree}}<br>
    Imported by: ${{nd.in_degree}}<br>
    Inclusions: ${{nd.inclusion_count}} (${{(nd.inclusion_rate * 100).toFixed(1)}}%)<br>
    Entrypoint: ${{nd.is_entrypoint ? "yes" : "no"}}
  `;
}}

// ---- tooltip ----
const tooltip = document.getElementById("tooltip");
function showTooltip(nd, px, py) {{
  if (!nd) {{ tooltip.style.display = "none"; return; }}
  tooltip.style.display = "block";
  tooltip.style.left = (px + 14) + "px";
  tooltip.style.top = (py - 10) + "px";
  tooltip.innerHTML = `
    <b style="color:${{nd.color}}">${{nd.id}}</b><br>
    ${{nd.estimated_tokens.toLocaleString()}} tokens &bull;
    ${{nd.in_degree}} imported by &bull;
    ${{nd.out_degree}} imports<br>
    Inclusion: ${{(nd.inclusion_rate * 100).toFixed(1)}}%
  `;
}}

// ---- hit test ----
function hitTest(sx, sy) {{
  const [wx, wy] = screenToWorld(sx, sy);
  let best = null, bestDist = Infinity;
  visibleNodes().forEach(nd => {{
    const dx = nd.x - wx, dy = nd.y - wy;
    const dist = Math.sqrt(dx*dx + dy*dy);
    if (dist < nd.r / transform.scale + 4 && dist < bestDist) {{
      best = nd; bestDist = dist;
    }}
  }});
  return best;
}}

// ---- fit all ----
function fitAll() {{
  if (!nodes.length) return;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  nodes.forEach(nd => {{
    minX = Math.min(minX, nd.x - nd.r);
    maxX = Math.max(maxX, nd.x + nd.r);
    minY = Math.min(minY, nd.y - nd.r);
    maxY = Math.max(maxY, nd.y + nd.r);
  }});
  const W = canvas.clientWidth, H = canvas.clientHeight;
  const pad = 40;
  const scaleX = (W - pad * 2) / (maxX - minX || 1);
  const scaleY = (H - pad * 2) / (maxY - minY || 1);
  transform.scale = Math.min(scaleX, scaleY, 2);
  transform.x = W / 2 - ((minX + maxX) / 2) * transform.scale;
  transform.y = H / 2 - ((minY + maxY) / 2) * transform.scale;
}}

// ---- events ----
canvas.addEventListener("mousedown", e => {{
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const nd = hitTest(sx, sy);
  if (nd) {{
    drag = nd;
    selectedNode = nd;
    showDetail(nd);
  }} else {{
    panStart = {{sx, sy, tx: transform.x, ty: transform.y}};
    selectedNode = null;
    showDetail(null);
  }}
}});

canvas.addEventListener("mousemove", e => {{
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  if (drag) {{
    const [wx, wy] = screenToWorld(sx, sy);
    drag.x = wx; drag.y = wy;
    drag.vx = 0; drag.vy = 0;
    simRunning = true; simTick = 0;
  }} else if (panStart) {{
    transform.x = panStart.tx + (sx - panStart.sx);
    transform.y = panStart.ty + (sy - panStart.sy);
  }} else {{
    const nd = hitTest(sx, sy);
    hoveredNode = nd;
    showTooltip(nd, sx, sy);
    canvas.style.cursor = nd ? "pointer" : "default";
  }}
}});

window.addEventListener("mouseup", () => {{ drag = null; panStart = null; }});

canvas.addEventListener("wheel", e => {{
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const factor = e.deltaY < 0 ? 1.12 : 0.89;
  const [wx, wy] = screenToWorld(sx, sy);
  transform.scale = Math.max(0.05, Math.min(10, transform.scale * factor));
  transform.x = sx - wx * transform.scale;
  transform.y = sy - wy * transform.scale;
}}, {{passive: false}});

document.getElementById("search").addEventListener("input", e => {{
  filterText = e.target.value.trim().toLowerCase();
}});

document.getElementById("btn-reset").addEventListener("click", () => {{
  transform = {{x: canvas.clientWidth/2, y: canvas.clientHeight/2, scale: 1}};
}});

document.getElementById("btn-fit").addEventListener("click", fitAll);

// ---- animation loop ----
function loop() {{
  simulate();
  draw();
  requestAnimationFrame(loop);
}}

// ---- init ----
initPhysics();
updateStats();
buildLegend();
setTimeout(fitAll, 800);
loop();
</script>
</body>
</html>
"""


def render_graph_html(report: VisualizeReport) -> str:
    """Render a self-contained HTML visualization for *report*.

    The output file has no external dependencies and works offline.
    """
    graph_data = visualize_as_dict(report)
    # Embed graph data as an inline JSON literal
    graph_json = json.dumps(graph_data, separators=(",", ":"))
    return _HTML_TEMPLATE.format(
        repo=report.repo,
        graph_json=graph_json,
    )
