"""Canonical-shaped configuration fixtures for Phase 2I-A.1 tests.

Production code post-Phase 2I-A.1 reads
``runtime_config.<domain>.<subdomain>.<field>``. These helpers build
``SimpleNamespace`` trees mirroring that shape so tests can inject overrides
without the retired ``project_runtime_config`` bridge.

These helpers are strictly test-only. Production code MUST NOT import them.
``canonical_ns_from_flat`` is marked for Phase 2I-B removal once every flat
fixture is rewritten with explicit canonical overrides.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Mapping


def _ns(**fields: Any) -> SimpleNamespace:
  return SimpleNamespace(**fields)


def _dict_to_ns(obj: Any) -> Any:
  if isinstance(obj, dict):
    return _ns(**{k: _dict_to_ns(v) for k, v in obj.items()})
  if isinstance(obj, (list, tuple)):
    return type(obj)(_dict_to_ns(item) for item in obj)
  return obj


def _set_path_dict(tree: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
  node = tree
  for part in path[:-1]:
    node = node.setdefault(part, {})
  node[path[-1]] = value


def _to_plain_dict(obj: Any) -> Any:
  if hasattr(obj, "model_dump"):
    return _to_plain_dict(obj.model_dump())
  if isinstance(obj, dict):
    return {key: _to_plain_dict(value) for key, value in obj.items()}
  if isinstance(obj, (list, tuple)):
    return type(obj)(_to_plain_dict(item) for item in obj)
  return obj


def _runtime_config_plain_tree() -> dict[str, Any]:
  """Materialize ``runtime_config`` leaves into a plain mutable dict tree."""
  from app.configuration.generated.legacy_access import DIRECT_LEGACY_PATHS
  from app.core.config import runtime_config

  if hasattr(runtime_config, "model_dump"):
    dumped = _to_plain_dict(runtime_config.model_dump())
    if isinstance(dumped, dict):
      return dumped

  tree: dict[str, Any] = {}
  for _name, path in DIRECT_LEGACY_PATHS.items():
    try:
      node: Any = runtime_config
      for part in path:
        node = getattr(node, part)
    except Exception:
      continue
    _set_path_dict(tree, tuple(path), node)
  return tree


def canonical_ns_from_flat(flat: Any) -> SimpleNamespace:
  """TEST-ONLY adapter: project flat Settings-like attrs into nested shape.

  When ``runtime_config`` is importable (normal pytest), starts from a full
  plain dump of the live config so unspecified sibling paths keep production
  values, then overlays every present legacy attribute via
  ``DIRECT_LEGACY_PATHS``. Marked for Phase 2I-B removal.
  """
  from app.configuration.generated.legacy_access import DIRECT_LEGACY_PATHS

  try:
    tree = _runtime_config_plain_tree()
  except Exception:
    tree = {}

  for name, path in DIRECT_LEGACY_PATHS.items():
    if not hasattr(flat, name):
      continue
    try:
      value = getattr(flat, name)
    except Exception:
      continue
    _set_path_dict(tree, tuple(path), value)
  return _dict_to_ns(tree)


def execution_cfg(**overrides: Any) -> SimpleNamespace:
  """Config shape for execution_policy / protective_stop / trade_plan_builder.

  Builds from the live ``runtime_config`` tree when available so unspecified
  fields keep production defaults, then applies legacy-name overrides.
  """
  cleaned = dict(overrides)
  if "auto_trade_opposing_zone_push_enabled" in cleaned:
    cleaned["auto_trade_stop_push_beyond_zone"] = cleaned.pop(
      "auto_trade_opposing_zone_push_enabled",
    )
  cleaned.pop("auto_trade_opposing_zone_buffer_atr", None)
  return canonical_ns_from_flat(SimpleNamespace(**cleaned))


def map_strategy_cfg(**overrides: Any) -> SimpleNamespace:
  """Config shape read by ``app.autotrade.map_strategy``."""
  cleaned = dict(overrides)
  if "auto_trade_targets_pips" in cleaned and "auto_trade_tp_pips" not in cleaned:
    cleaned["auto_trade_tp_pips"] = cleaned.pop("auto_trade_targets_pips")
  else:
    cleaned.pop("auto_trade_targets_pips", None)
  cleaned.pop("pip_size", None)
  # Ensure required map defaults exist even when runtime_config is thin.
  defaults = {
    "auto_trade_mapped_zone_enabled": True,
    "auto_trade_max_entry_distance_pips": 10,
    "auto_trade_strategy_match_max_age_seconds": 420,
    "auto_trade_tp_pips": "30,60,90,120,200",
    "auto_trade_map_zone_min_width_atr": 0.15,
    "auto_trade_map_zone_min_width_abs": 1.0,
    "auto_trade_map_counter_bias_enabled": True,
    "auto_trade_map_counter_bias_min_score": 6.0,
    "auto_trade_map_counter_bias_min_confluence": 2,
    "auto_trade_map_track_distance_atr": 8.0,
    "auto_trade_map_execute_distance_atr": 1.5,
    "auto_trade_map_execute_tolerance_pips": 0.0,
    "auto_trade_map_execute_tolerance_atr": 0.0,
    "auto_trade_map_reaction_lookback_bars": 5,
    "auto_trade_map_thesis_lock_enabled": True,
    "auto_trade_map_reaction_rearm_bars": 3,
    "auto_trade_map_reaction_rearm_atr": 0.50,
    "auto_trade_map_max_entry_drift_atr": 0.40,
    "auto_trade_allow_counter_bias": False,
    "atr_length": 14,
    "proximal_band_atr": 0.5,
  }
  defaults.update(cleaned)
  return canonical_ns_from_flat(SimpleNamespace(**defaults))


def _build_from_legacy_map(
  legacy_map: dict[str, tuple[str, ...]],
  defaults: dict[str, Any],
  overrides: Mapping[str, Any] | None = None,
) -> SimpleNamespace:
  values = dict(defaults)
  if overrides:
    values.update(dict(overrides))
  tree: dict[str, Any] = {}
  for legacy, path in legacy_map.items():
    _set_path_dict(tree, path, values[legacy])
  return _dict_to_ns(tree)


def scale_context_cfg(**overrides: Any) -> SimpleNamespace:
  """Config shape read by ``build_auto_scale_context``."""
  legacy_map = {
    "atr_length": ("analysis", "atr", "length"),
    "swing_fractal_n": ("analysis", "swings", "fractal_size"),
    "zigzag_pct": ("analysis", "swings", "zigzag", "pct"),
    "zigzag_atr_mult": ("analysis", "swings", "zigzag", "atr_mult"),
    "displacement_atr_mult": ("analysis", "displacement", "atr_mult"),
    "momentum_body_frac": ("analysis", "momentum", "body_frac"),
  }
  defaults = {
    "atr_length": 14,
    "swing_fractal_n": 2,
    "zigzag_pct": 0.0,
    "zigzag_atr_mult": 1.0,
    "displacement_atr_mult": 1.5,
    "momentum_body_frac": 0.6,
  }
  return _build_from_legacy_map(legacy_map, defaults, overrides)


def trend_cfg(overrides: Mapping[str, Any] | None = None) -> SimpleNamespace:
  """Config shape read by ``classify_regime`` / trend gate helpers."""
  legacy_map = {
    "atr_length": ("analysis", "atr", "length"),
    "swing_fractal_n": ("analysis", "swings", "fractal_size"),
    "zigzag_pct": ("analysis", "swings", "zigzag", "pct"),
    "zigzag_atr_mult": ("analysis", "swings", "zigzag", "atr_mult"),
    "displacement_atr_mult": ("analysis", "displacement", "atr_mult"),
    "momentum_body_frac": ("analysis", "momentum", "body_frac"),
    "trend_min_bos": ("strategies", "trend", "minimum_bos"),
    "trend_min_height_atr": ("strategies", "trend", "min_height_atr"),
    "auto_trade_allow_counter_bias": ("actionability", "counter_bias", "allowed"),
    "trend_atr_expansion": ("strategies", "trend", "atr_expansion_multiplier"),
    "trend_regime_direction_enabled": ("execution", "regime", "direction_enabled"),
    "trend_regime_direction_lookback": ("execution", "regime", "direction_lookback"),
    "trend_regime_min_directional_swings": (
      "execution", "regime", "min_directional_swings",
    ),
    "trend_regime_min_displacement_atr": (
      "execution", "regime", "min_displacement_atr",
    ),
    "auto_trade_measurements_tp_min_spacing_atr": (
      "analysis", "measurements", "tp_min_spacing_atr",
    ),
    "trend_breakout_max_age_bars": ("strategies", "trend", "breakout_max_age_bars"),
    "trend_breakout_accept_bars": ("strategies", "trend", "breakout_accept_bars"),
    "trend_breakout_min_room_pips": ("strategies", "trend", "breakout_min_room_pips"),
    "reactions_max_atr": ("analysis", "reactions", "max_atr"),
    "trend_allow_chase": ("strategies", "trend", "allow_chase"),
    "trend_level_buffer_atr": ("strategies", "trend", "level_buffer_atr"),
    "trend_atr_baseline_bars": ("strategies", "trend", "atr_baseline_bars"),
  }
  defaults = {
    "atr_length": 14,
    "swing_fractal_n": 2,
    "zigzag_pct": 0.0,
    "zigzag_atr_mult": 1.0,
    "displacement_atr_mult": 1.5,
    "momentum_body_frac": 0.6,
    "trend_min_bos": 2,
    "trend_min_height_atr": 2.0,
    "auto_trade_allow_counter_bias": False,
    "trend_atr_expansion": 1.2,
    "trend_regime_direction_enabled": True,
    "trend_regime_direction_lookback": 60,
    "trend_regime_min_directional_swings": 2,
    "trend_regime_min_displacement_atr": 1.0,
    "auto_trade_measurements_tp_min_spacing_atr": 0.75,
    "trend_breakout_max_age_bars": 6,
    "trend_breakout_accept_bars": 2,
    "trend_breakout_min_room_pips": 20,
    "reactions_max_atr": 3.0,
    "trend_allow_chase": True,
    "trend_level_buffer_atr": 0.2,
    "trend_atr_baseline_bars": 100,
  }
  return _build_from_legacy_map(legacy_map, defaults, overrides)


def market_map_cfg(**overrides: Any) -> SimpleNamespace:
  """Config shape read by ``build_map`` / ``render_market_map``."""
  legacy_map = {
    "map_max_per_side": ("analysis", "market_map", "max_per_side"),
    "map_major_score": ("analysis", "market_map", "major_score"),
    "map_max_touches": ("analysis", "market_map", "max_touches"),
    "map_min_zone_score": ("analysis", "market_map", "min_zone_score"),
    "map_min_level_touches": ("analysis", "market_map", "min_level_touches"),
    "map_max_distance_atr": ("analysis", "market_map", "max_distance_atr"),
    "map_band_max_atr": ("analysis", "market_map", "band_max_atr"),
    "map_min_per_side": ("analysis", "market_map", "min_per_side"),
    "map_fallback_radius": ("analysis", "market_map", "fallback_radius_price"),
    "map_scalp_radius": ("analysis", "market_map", "scalp_radius_price"),
    "map_change_min": ("analysis", "market_map", "change_min"),
    "round_step": ("analysis", "levels", "round_step"),
    "range_scalp_min_touches": (
      "strategies", "range_reversion", "range_edge", "min_touches",
    ),
    "range_scalp_min_width_atr": (
      "strategies", "range_reversion", "range_edge", "min_width_atr",
    ),
    "range_scalp_max_width_atr": (
      "strategies", "range_reversion", "range_edge", "max_width_atr",
    ),
    "range_scalp_min_room_atr": (
      "strategies", "range_reversion", "range_edge", "min_room_atr",
    ),
    "range_scalp_break_closes": (
      "strategies", "range_reversion", "range_edge", "break_closes",
    ),
    "scanner_exec_tf": ("market_data", "scanner", "execution_timeframe"),
    "proximal_band_atr": ("actionability", "gates", "proximal_band_atr"),
    "session_asia_start": ("market_data", "sessions", "asia_start"),
    "session_london_start": ("market_data", "sessions", "london_start"),
    "session_ny_start": ("market_data", "sessions", "ny_start"),
  }
  defaults = {
    "map_max_per_side": 4,
    "map_major_score": 12.0,
    "map_max_touches": 2,
    "map_min_zone_score": 6.0,
    "map_min_level_touches": 4,
    "map_max_distance_atr": 15.0,
    "map_band_max_atr": 2.0,
    "map_min_per_side": 2,
    "map_fallback_radius": 30.0,
    "map_scalp_radius": 15.0,
    "map_change_min": 1.0,
    "round_step": 5.0,
    "range_scalp_min_touches": 3,
    "range_scalp_min_width_atr": 1.2,
    "range_scalp_max_width_atr": 6.0,
    "range_scalp_min_room_atr": 1.0,
    "range_scalp_break_closes": 2,
    "scanner_exec_tf": "M5",
    "proximal_band_atr": 0.5,
    "session_asia_start": 22,
    "session_london_start": 7,
    "session_ny_start": 13,
  }
  return _build_from_legacy_map(legacy_map, defaults, overrides)


def scalp_ranges_cfg(**overrides: Any) -> SimpleNamespace:
  """Config shape read by ``build_scalp_structure``."""
  legacy_map = {
    "range_scalp_lookback": (
      "strategies", "range_reversion", "range_edge", "lookback",
    ),
    "range_scalp_cluster_atr": (
      "strategies", "range_reversion", "range_edge", "cluster_atr",
    ),
    "range_scalp_cluster_min_abs": (
      "strategies", "range_reversion", "range_edge", "cluster_min_abs",
    ),
    "range_scalp_min_touches": (
      "strategies", "range_reversion", "range_edge", "min_touches",
    ),
    "range_scalp_min_wick_frac": (
      "strategies", "range_reversion", "range_edge", "min_wick_frac",
    ),
    "range_scalp_entry_tol_atr": (
      "strategies", "range_reversion", "range_edge", "entry_tol_atr",
    ),
    "range_scalp_max_edge_width_atr": (
      "strategies", "range_reversion", "range_edge", "max_edge_width_atr",
    ),
    "range_scalp_min_width_atr": (
      "strategies", "range_reversion", "range_edge", "min_width_atr",
    ),
    "range_scalp_max_width_atr": (
      "strategies", "range_reversion", "range_edge", "max_width_atr",
    ),
    "range_scalp_min_room_atr": (
      "strategies", "range_reversion", "range_edge", "min_room_atr",
    ),
    "range_scalp_min_inside_closes": (
      "strategies", "range_reversion", "range_edge", "min_inside_closes",
    ),
    "range_scalp_break_closes": (
      "strategies", "range_reversion", "range_edge", "break_closes",
    ),
    "scalp_barrier_fallback_enabled": (
      "strategies", "scalp", "scalp_barrier_fallback_enabled",
    ),
    "scalp_barrier_fallback_min_confirmations": (
      "strategies", "scalp", "scalp_barrier_fallback_min_confirmations",
    ),
    "scalp_range_provisional_enabled": (
      "strategies", "scalp", "scalp_range_provisional_enabled",
    ),
    "scalp_post_impulse_range_enabled": (
      "strategies", "scalp", "scalp_post_impulse_range_enabled",
    ),
    "round_step": ("analysis", "levels", "round_step"),
  }
  defaults = {
    "range_scalp_lookback": 36,
    "range_scalp_cluster_atr": 0.20,
    "range_scalp_cluster_min_abs": 0.0,
    "range_scalp_min_touches": 3,
    "range_scalp_min_wick_frac": 0.35,
    "range_scalp_entry_tol_atr": 0.15,
    "range_scalp_max_edge_width_atr": 0.75,
    "range_scalp_min_width_atr": 1.2,
    "range_scalp_max_width_atr": 6.0,
    "range_scalp_min_room_atr": 1.0,
    "range_scalp_min_inside_closes": 3,
    "range_scalp_break_closes": 2,
    "scalp_barrier_fallback_enabled": True,
    "scalp_barrier_fallback_min_confirmations": 1,
    "scalp_range_provisional_enabled": True,
    "scalp_post_impulse_range_enabled": True,
    "round_step": 5.0,
  }
  return _build_from_legacy_map(legacy_map, defaults, overrides)


def actionability_cfg(**overrides: Any) -> SimpleNamespace:
  """Config shape read by ``resolve_actionability``."""
  legacy_map = {
    "contested_corridor_gap_atr": (
      "actionability", "contested_corridor", "gap_atr",
    ),
    "auto_trade_allow_counter_bias": ("actionability", "counter_bias", "allowed"),
    "auto_trade_structural_guard_mode": (
      "actionability", "structural_guard", "guard_mode",
    ),
    "auto_trade_opposing_barrier_atr": (
      "actionability", "target_room", "barrier_buffer_atr",
    ),
    "scanner_actionability_gate_enabled": (
      "actionability", "scanner_gates", "actionability_gate_enabled",
    ),
    "key_level_role_ambiguity_gate_enabled": (
      "actionability", "key_level_role", "enabled",
    ),
    "auto_trade_displacement_override_lookback_bars": (
      "execution", "policy", "displacement_override_lookback_bars",
    ),
    "auto_trade_execution_cost_pips": (
      "execution", "policy", "execution_cost_pips",
    ),
    "auto_trade_min_capped_target_pips": (
      "actionability", "target_room", "minimum_capped_target_pips",
    ),
    "breakout_accept_bars": ("analysis", "breakout", "accept_bars"),
  }
  defaults = {
    "contested_corridor_gap_atr": 0.5,
    "auto_trade_allow_counter_bias": True,
    "auto_trade_structural_guard_mode": "balanced",
    "auto_trade_opposing_barrier_atr": 0.5,
    "scanner_actionability_gate_enabled": True,
    "key_level_role_ambiguity_gate_enabled": True,
    "auto_trade_displacement_override_lookback_bars": 0,
    "auto_trade_execution_cost_pips": 1.0,
    "auto_trade_min_capped_target_pips": 15.0,
    "breakout_accept_bars": 2,
  }
  return _build_from_legacy_map(legacy_map, defaults, overrides)
