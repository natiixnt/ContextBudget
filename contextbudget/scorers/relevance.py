from __future__ import annotations

from contextbudget.core.text import task_keywords
from contextbudget.schemas.models import FileRecord, RankedFile

_SIGNAL_FILES = {
    "readme.md": 0.5,
    "contributing.md": 0.4,
    "package.json": 0.3,
    "pyproject.toml": 0.3,
    "requirements.txt": 0.3,
    "dockerfile": 0.2,
}


def score_files(task: str, files: list[FileRecord]) -> list[RankedFile]:
    keywords = task_keywords(task)
    ranked: list[RankedFile] = []

    for record in files:
        path_lower = record.path.lower()
        preview_lower = record.content_preview.lower()
        score = 0.0
        reasons: list[str] = []

        for keyword in keywords:
            path_hits = path_lower.count(keyword)
            preview_hits = preview_lower.count(keyword)
            if path_hits:
                score += 2.0 * path_hits
                reasons.append(f"path contains '{keyword}'")
            if preview_hits:
                score += min(4.0, 0.25 * preview_hits)
                reasons.append(f"content mentions '{keyword}'")

        name = record.path.rsplit("/", 1)[-1]
        if name in _SIGNAL_FILES:
            score += _SIGNAL_FILES[name]
            reasons.append(f"signal file {name}")

        if record.extension in {".py", ".ts", ".tsx", ".js", ".go", ".rs", ".java"}:
            score += 0.35

        if "test" in path_lower:
            score += 0.25
            reasons.append("test proximity")

        if record.line_count > 500:
            score -= 0.2

        if score > 0:
            deduped_reasons = list(dict.fromkeys(reasons))
            ranked.append(RankedFile(file=record, score=round(score, 3), reasons=deduped_reasons[:4]))

    ranked.sort(key=lambda item: (-item.score, item.file.path))
    return ranked
