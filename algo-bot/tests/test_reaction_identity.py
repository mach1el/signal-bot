"""Regression: structural_zone_id must not fragment on measured-width jitter.

Mirrors the fix in test_confluence_zone.py -
confluence_zone_id was found live to publish two separate TradePlan V7s for
the same resistance level because the width component of its coordinate
bucket happened to straddle a rounding boundary between two detections.
reaction_identity.canonicalize_zone_bucket had the identical width-bucket
component and must not repeat the bug when structural_zone_id falls back to
it (i.e. when no stable source_ids are available).
"""

from __future__ import annotations

import pytest

from app.autotrade.reaction_identity import (
  canonicalize_zone_bucket,
  structural_zone_id,
)


pytestmark = pytest.mark.no_database


def test_canonicalize_zone_bucket_returns_only_a_mid_bucket():
  mid_bucket = canonicalize_zone_bucket(
    4032.9, 4038.35, atr=1.0, pip_size=0.1,
  )
  assert mid_bucket == pytest.approx(4036.0)


def test_structural_zone_id_survives_width_jitter_without_source_ids():
  id_a = structural_zone_id(
    "XAU", "SELL", 4032.9, 4038.35, atr=1.0, pip_size=0.1,
    tags=("supply",),
  )
  id_b = structural_zone_id(
    "XAU", "SELL", 4032.84, 4038.41, atr=1.0, pip_size=0.1,
    tags=("supply",),
  )
  assert id_a == id_b


def test_canonicalize_zone_bucket_survives_ordinary_atr_drift():
  """Mirrors test_confluence_zone.py's ATR-drift regression: this bucket
  mirrors confluence_zone._bucket exactly and had the identical bug - the
  same band bucketing differently as live ATR drifts bar to bar, with
  nothing about the zone's own coordinates changing.
  """
  buckets = {
    canonicalize_zone_bucket(
      4077.088124726152, 4081.535208607181, atr=atr, pip_size=0.1,
    )
    for atr in (0.0, 0.5, 1.0, 1.5, 2.5, 4.0, 5.0, 5.9)
  }
  assert len(buckets) == 1
