# Copyright (c) 2026 Natalia Szczepanik. Licensed under FSL-1.1-MIT (see LICENSE).

"""Named compression profiles.

A profile is a preset of tier-threshold overrides applied on top of the
``[compression]`` settings. The ``max`` profile tightens every threshold so
more files land in cheaper representations; measured on this repository it
packed ~21% fewer input tokens than the default profile at the same budget,
top-files count, and reported quality risk.

``max`` is a Pro feature (``compression.max``). When it is requested without
an active license the run falls back to the default profile with a one-line
warning - free behaviour never changes and nothing errors.
"""

from __future__ import annotations

from dataclasses import replace

from redcon.config import CompressionSettings
from redcon.entitlements import Entitlement

PROFILE_DEFAULT = "default"
PROFILE_MAX = "max"

FEATURE_MAX_COMPRESSION = "compression.max"

# The max preset. Applying the profile overrides exactly these keys; users who
# want to keep hand-tuned values for them should stay on the default profile.
MAX_PROFILE_OVERRIDES: dict[str, int | float] = {
    "full_file_threshold_tokens": 100,
    "snippet_hit_limit": 4,
    "snippet_total_line_limit": 80,
    "snippet_fallback_lines": 40,
    "summary_preview_lines": 4,
    "adaptive_line_budget_max_factor": 2.0,
    "max_degradation_rounds": 2,
}


def resolve_compression_profile(
    settings: CompressionSettings, entitlement: Entitlement
) -> tuple[CompressionSettings, str, str]:
    """Resolve the requested profile into effective settings.

    Returns ``(effective_settings, applied_profile, note)``. ``note`` is
    non-empty only when the request could not be honored (unknown profile
    name, or ``max`` without a Pro license) and is meant to be surfaced as a
    single warning line. The input ``settings`` object is never mutated.
    """
    requested = (settings.profile or "").strip().lower()
    if requested in ("", PROFILE_DEFAULT):
        return settings, PROFILE_DEFAULT, ""
    if requested != PROFILE_MAX:
        return (
            replace(settings, profile=PROFILE_DEFAULT),
            PROFILE_DEFAULT,
            f"unknown compression profile {requested!r} - using the default profile",
        )
    if not entitlement.has(FEATURE_MAX_COMPRESSION):
        return (
            replace(settings, profile=PROFILE_DEFAULT),
            PROFILE_DEFAULT,
            "compression profile 'max' is a Pro feature - using the default profile "
            "(activate with: redcon license --activate KEY)",
        )
    return replace(settings, **MAX_PROFILE_OVERRIDES), PROFILE_MAX, ""
