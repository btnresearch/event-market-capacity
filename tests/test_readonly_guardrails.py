"""Enforces the project's scope constraints against the source itself.

The repository's remit is a read-only measurement. These tests make that a
property the build checks rather than a promise in a README: they parse every
module and fail if anything appears that could place an order, authenticate to a
venue, or forecast an outcome. If a future change legitimately needs one of these
constructs, the test is the place to argue for it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "emc"

# Patterns that would indicate a write path or an authenticated session.
# Regexes rather than substrings so that a read-only endpoint whose name merely
# contains a forbidden word is not flagged: "/markets/{ticker}/orderbook" is a
# public book read, while "/orders" is an order-management path.
FORBIDDEN_PATTERNS = (
    r"api[_-]?key",
    r"secret[_-]?key",
    r"private[_-]?key",
    r"access[_-]?token",
    r"\bbearer\b",
    r"\bauthorization\b",
    r"access-signature",
    r"\b(?:place|create|submit|cancel|post|amend|modify)_order\b",
    r"/orders?(?![a-z])",
    r"/positions?(?![a-z])",
    r"/portfolio",
    r"/fills?(?![a-z])",
    r"\bhmac\b",
    r"\bpkcs1\b",
    r"\bsign_request\b",
    r"\bprivate_?key\b",
)

# Only GET may appear as an HTTP method literal.
FORBIDDEN_METHODS = ("post", "put", "patch", "delete")


def source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_there_are_source_files_to_check():
    """Guards against the scan silently passing because it found nothing."""
    assert len(source_files()) >= 8


@pytest.mark.parametrize("path", source_files(), ids=lambda p: p.name)
def test_no_credential_or_order_constructs(path: Path):
    lowered = path.read_text(encoding="utf-8").lower()
    hits = [pattern for pattern in FORBIDDEN_PATTERNS if re.search(pattern, lowered)]
    assert not hits, f"{path.name} contains out-of-scope construct(s): {hits}"


def test_the_guardrail_patterns_actually_catch_violations():
    """The scan is only meaningful if it would fail on real violations."""
    samples = (
        'headers={"Authorization": f"Bearer {token}"}',
        "client.place_order(ticker, side, count)",
        'url = f"{base}/orders"',
        "signature = hmac.new(secret_key, msg).hexdigest()",
        'requests.get(f"{base}/positions")',
    )
    for sample in samples:
        lowered = sample.lower()
        assert any(
            re.search(pattern, lowered) for pattern in FORBIDDEN_PATTERNS
        ), f"guardrail would not catch: {sample}"


def test_the_guardrail_patterns_do_not_flag_read_only_endpoints():
    """Read paths that merely contain a forbidden word must pass."""
    for sample in (
        "/trade-api/v2/markets/{ticker}/orderbook",
        "book = payload.get('orderbook', payload)",
        "https://clob.polymarket.com/book",
    ):
        lowered = sample.lower()
        hits = [p for p in FORBIDDEN_PATTERNS if re.search(p, lowered)]
        assert not hits, f"guardrail false-positives on {sample}: {hits}"


@pytest.mark.parametrize("path", source_files(), ids=lambda p: p.name)
def test_no_mutating_http_calls(path: Path):
    """No attribute call or string literal names a mutating HTTP verb.

    Catches both ``client.post(...)`` and ``request("POST", ...)``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr.lower() in FORBIDDEN_METHODS:
                offenders.append(f"call to .{node.func.attr}()")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.strip().lower() in FORBIDDEN_METHODS:
                offenders.append(f"literal {node.value!r}")

    assert not offenders, f"{path.name} has mutating HTTP construct(s): {offenders}"


def test_the_only_http_method_used_is_get():
    text = (SRC / "venues" / "base.py").read_text(encoding="utf-8")
    assert '_ALLOWED_METHOD = "GET"' in text


def test_no_forecasting_or_wagering_dependencies():
    """No modelling stack, which is what a prediction model would need.

    The probe measures resting quotes. It does not estimate probabilities, so a
    fitted model appearing here would mean the scope had drifted.
    """
    banned = {
        "sklearn",
        "scikit_learn",
        "torch",
        "tensorflow",
        "keras",
        "statsmodels",
        "xgboost",
        "lightgbm",
        "prophet",
    }
    for path in source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                assert root not in banned, f"{path.name} imports modelling library {root!r}"


def test_pyproject_declares_no_modelling_or_exchange_sdk_dependencies(repo_root: Path):
    text = (repo_root / "pyproject.toml").read_text(encoding="utf-8").lower()
    for banned in ("sklearn", "torch", "tensorflow", "py-clob-client", "kalshi", "ccxt"):
        assert banned not in text, f"pyproject.toml declares out-of-scope dependency {banned!r}"


def test_cli_exposes_no_transacting_subcommand():
    """The CLI surface is the easiest place for scope to creep in."""
    tree = ast.parse((SRC / "cli.py").read_text(encoding="utf-8"))
    subcommands = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_parser"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert subcommands == {"probe", "capture"}


def test_venue_clients_expose_only_read_methods():
    """Every public method on a venue client must be a read."""
    from emc.venues.fixtures import FixtureVenue
    from emc.venues.kalshi import KalshiVenue
    from emc.venues.polymarket import PolymarketVenue

    allowed = {"list_sports_markets", "fetch_snapshot", "snapshots", "venue"}
    for client in (KalshiVenue, PolymarketVenue, FixtureVenue):
        public = {
            name
            for name in vars(client)
            if not name.startswith("_") and name not in {"venue"}
        }
        unexpected = public - allowed
        assert not unexpected, f"{client.__name__} exposes non-read members: {unexpected}"
