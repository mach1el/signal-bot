"""Static-analysis entry points invoked indirectly at runtime."""

from app.analysis.detectors import build_default_detectors, live_detector_report
from app.analysis.m1_trigger import evaluate_m1_trigger
from app.analysis.market_map_delivery import render_current_market_map
from app.analysis.scanner import scanner_loop


build_default_detectors
evaluate_m1_trigger
live_detector_report
render_current_market_map
scanner_loop
