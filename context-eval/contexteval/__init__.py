"""context-eval: an open, reproducible benchmark for context-selection tools.

Given a repository and a natural-language task, a context-selection tool
decides which files an AI coding agent should see. context-eval measures
how good that decision is:

- tasks come from the repository's own git history (a commit is a task,
  the files that commit modified are the ground truth),
- every tool is evaluated at the same token budget,
- coverage = how much of the ground truth landed in the selected context,
- tokens per coverage point = what each point of coverage costs.

The harness is tool-agnostic: adapters wrap redcon, aider's repo map,
and simple baselines, and new adapters are a single function.
"""

from contexteval.runner import evaluate
from contexteval.tasks import Task, extract_tasks

__all__ = ["Task", "extract_tasks", "evaluate"]
