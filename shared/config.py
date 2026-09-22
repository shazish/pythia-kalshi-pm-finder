"""Shared pipeline configuration: defaults < YAML < environment < caller."""
import os
import math
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

SCANNER_DEFAULTS = {
    "price_threshold": 90,          # cents — primary filter (high-confidence only)
    "deep_scan_threshold": 80,      # cents — secondary daily scan (broader net)
    "spread_max": 3,                # max bid-ask spread in cents
    "min_volume": 50,               # minimum volume as secondary signal
    "deep_spread_min_volume": 200,  # higher volume floor for wide-spread (spread > spread_max) markets in deep scan
    "max_ask_price": 95,             # cents — upper ceiling; ask ≥96 can't clear 3% edge after fees
    "price_change_threshold": 3,    # cents — meaningful change vs cache
    "max_pages": 20,                # max event pages per full scan (2,000 events)
    "incremental_max_pages": 5,     # max market pages per incremental scan (500 markets)
    "cache_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/market_cache.json"),
    "candidates_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/candidates.json"),
    # Categories where "obvious outcome" markets exist
    "scan_categories": ["Politics", "Economics", "Entertainment", "Weather", "World", "Elections", "Health", "Finance"],
    # Volume anomaly: flag when implied $ on the opposite (longshot) side exceeds this
    "volume_anomaly_threshold": 5000,
}

POLYMARKET_DEFAULTS = {
    "price_threshold":       85,    # cents — same as Kalshi primary
    "deep_scan_threshold":   80,    # cents
    "spread_max":            5,     # cents — regular (high-confidence) scans
    "anomaly_spread_max":   10,    # cents — anomaly scan; 20-79c markets have wider spreads
    "min_volume":            1000,  # USDC — higher floor than Kalshi contracts
    "price_change_threshold": 3,    # cents
    "max_ask_price":         95,    # cents — upper ceiling; ask ≥96 can't clear 3% edge after fees
    "max_pages":             30,    # events pages per full scan
    "scan_categories": ["Politics", "Economics", "Entertainment", "World", "Science", "Health", "Finance"],
    "cache_file":      os.path.expanduser("~/.hermes/kalshi-tracker/cache/pm_cache.json"),
    "candidates_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/pm_candidates.json"),
    "volume_anomaly_threshold": 5000,   # USDC — same logic as Kalshi
}

ANOMALY_DEFAULTS = {
    "min_price": 20,                      # ignore markets below 20c (too speculative)
    "max_price": 79,                      # don't duplicate ScannerAgent (80c+ is its job)
    "min_implied_hc_dollars": 10000,      # $10k+ on high-confidence side to qualify
    "min_volume": 500,                    # raw volume floor
    "max_spread": 10,                     # wider spread allowed than primary scanner
    "max_pages": 20,
    "min_hc_ratio": 1.0,               # minimum HC-to-opposite implied dollar ratio; overridden to 1.5 in pythia-main
    "scan_categories": ["Politics", "Economics", "Entertainment", "Weather", "World", "Elections", "Health", "Finance"],
    "cache_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/anomaly_cache.json"),
    "candidates_file": os.path.expanduser("~/.hermes/kalshi-tracker/cache/anomaly_candidates.json"),
}

OPPORTUNITY_DEFAULTS = {
    "min_edge_after_fees": 0.03,     # 3% minimum edge to notify (baseline for 30-day market)
    "min_edge_annualized": 0.15,     # 15% annualized edge minimum — time-adjusted threshold
    "max_bankroll_pct": 0.05,        # max 5% of bankroll per opportunity
    "default_bankroll": 1000.0,      # default bankroll in dollars
    "fee_rate": 0.015,               # ~1.5% average Kalshi fee (quadratic model on profits)
    "pm_fee_rate": 0.005,            # ~0.5% effective Polymarket fee (maker ~0%, taker ~1-1.5%, blended)
    "pm_fee_rates_by_category": {    # Polymarket taker fees by category (maker = 0%)
        "Politics": 0.010,           # 1.0%
        "Economics": 0.015,          # 1.5%
        "Entertainment": 0.010,      # ~1.0% (culture/mentions blended)
        "World": 0.010,              # ~1.0% (geopolitics/politics)
        "Science": 0.010,            # ~1.0%
        "Sports": 0.0075,            # 0.75%
        "Crypto": 0.018,             # 1.8%
        "Finance": 0.010,            # 1.0%
        "Tech": 0.010,               # 1.0%
        "Weather": 0.0125,           # 1.25%
    },
    "dashboard_log": os.path.expanduser("~/.hermes/kalshi-tracker/logs/opportunities.jsonl"),
    "notified_cache": os.path.expanduser("~/.hermes/kalshi-tracker/cache/notified.json"),
    "max_market_age_seconds": 300,  # refresh quotes older than five minutes
    "notify_ttl_hours": 168,         # 7 days before re-notifying same market
}

BACKTEST_DEFAULTS = {
    "sample_size": 50,              # minimum markets to evaluate
    "min_precision": 0.95,          # minimum acceptable precision for CERTAIN
    "results_dir": os.path.expanduser("~/.hermes/kalshi-tracker/backtests"),
}

POLYMARKET_DEFAULTS.update(anomaly_min_price=20, anomaly_max_price=79, min_implied_hc_dollars=10000)
SCANNER_DEFAULTS["price_threshold"] = 85
ANOMALY_DEFAULTS["min_hc_ratio"] = 1.5
DEFAULTS = {
    **SCANNER_DEFAULTS, **OPPORTUNITY_DEFAULTS,
    "recency_days": 14, "backtest_sample_size": 50, "backtest_min_precision": 0.95,
    "cache_dir": "cache", "log_dir": "logs", "backtest_dir": "backtests",
}
PATH_PARENTS = {
    "cache_file": ("cache_dir", "market_cache.json"),
    "candidates_file": ("cache_dir", "candidates.json"),
    "classified_file": ("cache_dir", "classified.json"),
    "notified_cache": ("cache_dir", "notified.json"),
    "pm_cache_file": ("cache_dir", "pm_cache.json"),
    "pm_candidates_file": ("cache_dir", "pm_candidates.json"),
    "anomaly_cache_file": ("cache_dir", "anomaly_cache.json"),
    "anomaly_candidates_file": ("cache_dir", "anomaly_candidates.json"),
    "dashboard_log": ("log_dir", "opportunities.jsonl"),
}
for prefix, defaults in (("pm", POLYMARKET_DEFAULTS), ("anomaly", ANOMALY_DEFAULTS)):
    for key, value in defaults.items():
        DEFAULTS[f"{prefix}_{key}"] = value
for key, (parent, filename) in PATH_PARENTS.items():
    DEFAULTS[key] = str(Path(DEFAULTS[parent]) / filename)
PATH_KEYS = set(PATH_PARENTS) | {"cache_dir", "log_dir", "backtest_dir"}


def _validate(key, value, default):
    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Configuration {key} must be numeric")
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Configuration {key} must be finite and non-negative")
        if isinstance(default, int) and not isinstance(value, int):
            raise ValueError(f"Configuration {key} must be an integer")
        if any(word in key for word in ("fee_rate", "bankroll_pct", "min_precision")) and value > 1:
            raise ValueError(f"Configuration {key} must be between 0 and 1")
    elif not isinstance(value, type(default)):
        raise ValueError(f"Configuration {key} must be {type(default).__name__}")
    if key in PATH_KEYS and not value.strip():
        raise ValueError(f"Configuration {key} must be a non-empty path")


def load_config(overrides=None, *, config_path=None):
    """Load fresh settings. Relative paths use the YAML directory, independent of cwd."""
    explicit = config_path or os.environ.get("KALSHI_CONFIG_FILE")
    path = (Path(explicit).expanduser() if explicit else ROOT / "config.yaml").resolve()
    values = {}
    if explicit or path.exists():
        with path.open(encoding="utf-8") as stream:
            values = yaml.safe_load(stream)
        if values is None:
            values = {}
        if not isinstance(values, dict):
            raise ValueError(f"Configuration {path} must contain a mapping")
    result = dict(DEFAULTS)
    env = {key: (os.environ["KALSHI_" + key.upper()] if isinstance(default, str)
                 else yaml.safe_load(os.environ["KALSHI_" + key.upper()]))
           for key, default in DEFAULTS.items() if "KALSHI_" + key.upper() in os.environ}
    for layer in (values, env, overrides or {}):
        for key, value in layer.items():
            if key not in DEFAULTS:
                raise ValueError(f"Unknown configuration key: {key}")
            _validate(key, value, DEFAULTS[key])
        result.update(layer)
        for key, (parent, filename) in PATH_PARENTS.items():
            if parent in layer and key not in layer:
                result[key] = str(Path(result[parent]) / filename)
    for key in PATH_KEYS:
        value = Path(result[key]).expanduser()
        result[key] = str(value if value.is_absolute() else path.parent / value)
    return result


def component_config(component, overrides=None):
    cfg = load_config()
    if component == "scanner":
        result = {key: cfg[key] for key in SCANNER_DEFAULTS}
    elif component == "opportunity":
        result = {key: cfg[key] for key in OPPORTUNITY_DEFAULTS}
    elif component in ("polymarket", "anomaly"):
        prefix, defaults = (("pm", POLYMARKET_DEFAULTS) if component == "polymarket"
                            else ("anomaly", ANOMALY_DEFAULTS))
        result = {key: cfg[f"{prefix}_{key}"] for key in defaults}
    elif component == "backtest":
        result = dict(sample_size=cfg["backtest_sample_size"],
                      min_precision=cfg["backtest_min_precision"], results_dir=cfg["backtest_dir"])
    else:
        raise ValueError(f"Unknown component: {component}")
    for key, value in (overrides or {}).items():
        if key in result:
            _validate(key, value, result[key])
        result[key] = value
    for key in result:
        if key in PATH_KEYS or key == "results_dir":
            path = Path(result[key]).expanduser()
            result[key] = str(path if path.is_absolute() else ROOT / path)
    return result


def run_cache():
    """Keep the active run's environment/pointer precedence over the cache fallback."""
    cfg = load_config()
    if "KALSHI_CACHE_DIR" in os.environ:
        return Path(cfg["cache_dir"])
    pointer = Path(cfg["log_dir"]) / ".current_run"
    if pointer.exists():
        run = Path(cfg["log_dir"]) / pointer.read_text().strip()
        if run.is_dir():
            return run
    return Path(cfg["cache_dir"])


def artifact_path(key):
    """Configured standalone file, or standard filename inside an active run."""
    cfg = load_config()
    active = run_cache()
    if "KALSHI_CACHE_DIR" in os.environ or active != Path(cfg["cache_dir"]):
        return active / PATH_PARENTS[key][1]
    return Path(cfg[key])
