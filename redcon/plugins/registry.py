from __future__ import annotations

"""Explicit plugin registry and config-driven resolution helpers."""

from dataclasses import asdict, dataclass, field
import importlib
from typing import Any, Mapping

from redcon.config import RedconConfig, PluginRegistrationSettings
from redcon.plugins.api import CompressorPlugin, ScorerPlugin, TokenEstimatorPlugin
from redcon.plugins.builtins import register_builtin_plugins
from redcon.schemas.models import TokenEstimatorReport


class PluginResolutionError(ValueError):
    """Raised when plugin registration or resolution fails."""


@dataclass(frozen=True, slots=True)
class ResolvedPlugins:
    """Selected plugins and their bound configuration options."""

    scorer: ScorerPlugin
    compressor: CompressorPlugin
    token_estimator: TokenEstimatorPlugin
    scorer_options: dict[str, Any] = field(default_factory=dict)
    compressor_options: dict[str, Any] = field(default_factory=dict)
    token_estimator_options: dict[str, Any] = field(default_factory=dict)
    token_estimator_report: dict[str, Any] = field(default_factory=dict)

    def estimate_tokens(self, text: str) -> int:
        """Estimate tokens using the selected token estimator."""

        return int(
            self.token_estimator.estimate(
                text=text,
                options=self.token_estimator_options,
            )
        )

    def plan_implementations(self) -> dict[str, str]:
        """Implementations recorded in plan artifacts."""

        return {
            "scorer": self.scorer.name,
            "token_estimator": self.token_estimator.name,
        }

    def pack_implementations(self) -> dict[str, str]:
        """Implementations recorded in pack and benchmark artifacts."""

        return {
            "scorer": self.scorer.name,
            "compressor": self.compressor.name,
            "token_estimator": self.token_estimator.name,
        }


class PluginRegistry:
    """Registry for scorer, compressor, and token-estimator plugins."""

    def __init__(self) -> None:
        self._scorers: dict[str, ScorerPlugin] = {}
        self._compressors: dict[str, CompressorPlugin] = {}
        self._token_estimators: dict[str, TokenEstimatorPlugin] = {}
        self._scorer_options: dict[str, dict[str, Any]] = {}
        self._compressor_options: dict[str, dict[str, Any]] = {}
        self._token_estimator_options: dict[str, dict[str, Any]] = {}

    def register_scorer(
        self,
        plugin: ScorerPlugin,
        *,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        self._register(
            store=self._scorers,
            option_store=self._scorer_options,
            plugin=plugin,
            options=options,
            kind="scorer",
        )

    def register_compressor(
        self,
        plugin: CompressorPlugin,
        *,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        self._register(
            store=self._compressors,
            option_store=self._compressor_options,
            plugin=plugin,
            options=options,
            kind="compressor",
        )

    def register_token_estimator(
        self,
        plugin: TokenEstimatorPlugin,
        *,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        self._register(
            store=self._token_estimators,
            option_store=self._token_estimator_options,
            plugin=plugin,
            options=options,
            kind="token_estimator",
        )

    def register_from_config(self, registration: PluginRegistrationSettings) -> None:
        """Import and register a plugin object declared in configuration."""

        plugin_object = _import_plugin_target(registration.target)
        options = dict(registration.options)
        if isinstance(plugin_object, ScorerPlugin):
            self.register_scorer(plugin_object, options=options)
            return
        if isinstance(plugin_object, CompressorPlugin):
            self.register_compressor(plugin_object, options=options)
            return
        if isinstance(plugin_object, TokenEstimatorPlugin):
            self.register_token_estimator(plugin_object, options=options)
            return
        raise PluginResolutionError(
            "Unsupported plugin target "
            f"{registration.target!r}. Expected a ScorerPlugin, CompressorPlugin, or TokenEstimatorPlugin."
        )

    def resolve(
        self,
        *,
        scorer_name: str,
        compressor_name: str,
        token_estimator_name: str,
    ) -> ResolvedPlugins:
        """Resolve configured plugin names into bound implementations."""

        scorer = self._resolve(self._scorers, scorer_name, kind="scorer")
        compressor = self._resolve(self._compressors, compressor_name, kind="compressor")
        token_estimator = self._resolve(self._token_estimators, token_estimator_name, kind="token_estimator")
        return ResolvedPlugins(
            scorer=scorer,
            compressor=compressor,
            token_estimator=token_estimator,
            scorer_options=dict(self._scorer_options.get(scorer.name, {})),
            compressor_options=dict(self._compressor_options.get(compressor.name, {})),
            token_estimator_options=dict(self._token_estimator_options.get(token_estimator.name, {})),
        )

    def _register(
        self,
        *,
        store: dict[str, Any],
        option_store: dict[str, dict[str, Any]],
        plugin,
        options: Mapping[str, Any] | None,
        kind: str,
    ) -> None:
        name = str(plugin.name).strip()
        if not name:
            raise PluginResolutionError(f"{kind} plugin name must not be empty.")
        if name in store:
            raise PluginResolutionError(f"Duplicate {kind} plugin registration for {name!r}.")
        store[name] = plugin
        option_store[name] = dict(options or {})

    @staticmethod
    def _resolve(store: dict[str, Any], name: str, *, kind: str):
        if name in store:
            return store[name]
        available = ", ".join(sorted(store)) or "<none>"
        raise PluginResolutionError(f"Unknown {kind} plugin {name!r}. Available: {available}.")


def _import_plugin_target(target: str):
    module_name, separator, attr_path = str(target).partition(":")
    if not module_name or not separator or not attr_path:
        raise PluginResolutionError(
            f"Plugin target {target!r} must use the format 'package.module:attribute'."
        )
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise PluginResolutionError(f"Could not import plugin module {module_name!r}.") from exc

    current = module
    for attribute in attr_path.split("."):
        if not hasattr(current, attribute):
            raise PluginResolutionError(f"Plugin target {target!r} has no attribute {attribute!r}.")
        current = getattr(current, attribute)
    return current


def build_plugin_registry(config: RedconConfig) -> PluginRegistry:
    """Build a plugin registry with built-ins and config-registered plugins."""

    registry = PluginRegistry()
    register_builtin_plugins(registry, token_settings=config.tokens)
    for registration in config.plugins.registrations:
        registry.register_from_config(registration)
    return registry


def resolve_plugins(config: RedconConfig) -> ResolvedPlugins:
    """Resolve configured scorer, compressor, and token-estimator plugins."""

    registry = build_plugin_registry(config)
    resolved = registry.resolve(
        scorer_name=config.plugins.scorer,
        compressor_name=config.plugins.compressor,
        token_estimator_name=config.plugins.token_estimator,
    )
    report = _describe_token_estimator(
        plugin=resolved.token_estimator,
        options=resolved.token_estimator_options,
    )
    return ResolvedPlugins(
        scorer=resolved.scorer,
        compressor=resolved.compressor,
        token_estimator=resolved.token_estimator,
        scorer_options=resolved.scorer_options,
        compressor_options=resolved.compressor_options,
        token_estimator_options=resolved.token_estimator_options,
        token_estimator_report=asdict(report),
    )


def _describe_token_estimator(
    *,
    plugin: TokenEstimatorPlugin,
    options: Mapping[str, Any],
) -> TokenEstimatorReport:
    if plugin.describe is not None:
        return plugin.describe(options=options)
    return TokenEstimatorReport(
        selected_backend=plugin.name,
        effective_backend=plugin.name,
        uncertainty="custom",
        available=True,
        fallback_used=False,
        fallback_reason="",
        notes=["Custom estimator plugin; accuracy depends on the plugin implementation."],
    )
