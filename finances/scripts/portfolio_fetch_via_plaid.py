#!/usr/bin/env python3
"""
Fetches real investment holdings from Plaid (multiple brokerage accounts).

Setup (.env):
    PLAID_CLIENT_ID=...
    PLAID_SECRET=...
    PLAID_ENV=sandbox        # or 'production' — must be set explicitly
    ACCOUNT_Robinhood=access-...
    ACCOUNT_Fidelity=access-...

Any env var starting with ACCOUNT_ is treated as a brokerage connection.

Usage:
    python portfolio_fetch_via_plaid.py
    python portfolio_fetch_via_plaid.py --raw    # also print raw Plaid JSON
"""

import os
import sys
import json
import argparse
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import plaid
    from plaid.api import plaid_api
    from plaid.model.accounts_get_request import AccountsGetRequest
    from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
    from plaid.model.investments_transactions_get_request import InvestmentsTransactionsGetRequest
    PLAID_AVAILABLE = True
except ImportError:
    PLAID_AVAILABLE = False

# ── Configuration ─────────────────────────────────────────────────────────────

PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID", "")
PLAID_SECRET    = os.getenv("PLAID_SECRET", "")
PLAID_ENV       = os.getenv("PLAID_ENV", "sandbox")

ACCOUNTS = {
    key.removeprefix("ACCOUNT_").replace("_", " "): val
    for key, val in os.environ.items()
    if key.startswith("ACCOUNT_") and val.startswith("access-")
}

PLAID_TIMEOUT_SEC    = 30
TRANSACTION_DAYS     = 90
LIABILITY_TYPES      = {"loan", "credit"}
NOTABLE_POSITION_PCT = 10.0
VALID_PLAID_ENVS     = {"sandbox", "production"}


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class Account:
    name: str
    type: str
    subtype: str
    balance: Optional[float]

    @property
    def is_liability(self) -> bool:
        return self.type in LIABILITY_TYPES

    @property
    def signed_balance(self) -> Optional[float]:
        if self.balance is None:
            return None
        return -self.balance if self.is_liability else self.balance


@dataclass
class Holding:
    ticker: str
    name: str
    asset_type: str
    quantity: float
    price: float
    value: float
    cost_basis: Optional[float]

    @property
    def unrealized_gain(self) -> Optional[float]:
        if self.cost_basis is None:
            return None
        return self.value - self.cost_basis

    @property
    def unrealized_gain_pct(self) -> Optional[float]:
        if not self.cost_basis:  # None or 0 — can't compute a percentage of zero
            return None
        return (self.value - self.cost_basis) / self.cost_basis * 100


@dataclass
class Transaction:
    date: str
    type: str
    ticker: str
    quantity: float
    amount: float


@dataclass
class BrokerageData:
    nickname: str
    accounts: list[Account]         = field(default_factory=list)
    holdings: list[Holding]         = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    errors: list[str]               = field(default_factory=list)


# ── Display helpers ───────────────────────────────────────────────────────────

DIVIDER = "─" * 72

def section(title: str) -> None:
    print(f"\n{DIVIDER}\n  {title}\n{DIVIDER}")

def fmt_usd(value: Optional[float], width: int = 12) -> str:
    if value is None:
        return f"{'N/A':>{width}}"
    return f"${value:>{width - 1},.2f}"

def fmt_pct(value: Optional[float], width: int = 6) -> str:
    if value is None:
        return f"{'N/A':>{width}}"
    return f"{value:>{width - 1}.1f}%"


# ── Validation ────────────────────────────────────────────────────────────────

def validate() -> None:
    errors = []
    if not PLAID_AVAILABLE:
        errors.append("plaid-python not installed — run: pip install plaid-python")
    if not PLAID_CLIENT_ID:
        errors.append("PLAID_CLIENT_ID not set")
    if not PLAID_SECRET:
        errors.append("PLAID_SECRET not set")
    if PLAID_ENV not in VALID_PLAID_ENVS:
        errors.append(
            f"PLAID_ENV must be one of {sorted(VALID_PLAID_ENVS)} (got '{PLAID_ENV}'). "
            "Set it explicitly in .env."
        )
    if not ACCOUNTS:
        errors.append(
            "No ACCOUNT_* tokens found — add at least one to .env:\n"
            "    ACCOUNT_Robinhood=access-production-xxxx"
        )
    if not errors:
        return
    print("\n❌  Configuration errors:")
    for e in errors:
        print(f"    • {e}")
    sys.exit(1)


# ── Plaid: client + transport ─────────────────────────────────────────────────

def build_plaid_client():
    env_map = {
        "sandbox":    plaid.Environment.Sandbox,
        "production": plaid.Environment.Production,
    }
    config = plaid.Configuration(
        host=env_map[PLAID_ENV],
        api_key={"clientId": PLAID_CLIENT_ID, "secret": PLAID_SECRET},
    )
    return plaid_api.PlaidApi(plaid.ApiClient(config))


def _try_plaid(fn, request, *, label: str, errors: list[str]):
    """Call a Plaid API function with a timeout, recording errors instead of crashing."""
    try:
        return fn(request, _request_timeout=PLAID_TIMEOUT_SEC)
    except plaid.ApiException as e:
        try:
            msg = json.loads(e.body).get("error_message", str(e))
        except (ValueError, TypeError, AttributeError):
            msg = str(e)
        errors.append(f"{label}: {msg}")
        print(f"    ⚠️  {label}: {msg}")
        return None


# ── Plaid: parsers (pure functions, no I/O) ──────────────────────────────────

def _parse_accounts(resp) -> list[Account]:
    return [
        Account(
            name    = a["name"],
            type    = _enum_str(a["type"]),
            subtype = str(a.get("subtype", "")),
            balance = a["balances"].get("current"),
        )
        for a in resp["accounts"]
    ]


def _parse_holdings(resp) -> list[Holding]:
    sec_map = {s["security_id"]: s for s in resp["securities"]}
    return [_parse_holding(h, sec_map) for h in resp["holdings"]]


def _parse_holding(raw: dict, sec_map: dict) -> Holding:
    sec      = sec_map.get(raw["security_id"], {})
    quantity = raw.get("quantity") or 0
    price    = raw.get("institution_price") or sec.get("close_price") or 0
    return Holding(
        ticker     = sec.get("ticker_symbol") or "—",
        name       = sec.get("name") or "Unknown",
        asset_type = sec.get("type", ""),
        quantity   = quantity,
        price      = price,
        value      = quantity * price,
        cost_basis = raw.get("cost_basis"),
    )


def _parse_transactions(resp) -> list[Transaction]:
    sec_map = {s["security_id"]: s for s in resp.get("securities", [])}
    return [
        Transaction(
            date     = str(t.get("date", "")),
            type     = _enum_str(t.get("type", "")),
            ticker   = sec_map.get(t.get("security_id"), {}).get("ticker_symbol") or "CASH",
            quantity = t.get("quantity") or 0,
            amount   = t.get("amount") or 0,
        )
        for t in resp.get("investment_transactions", [])
    ]


def _enum_str(value) -> str:
    """Plaid returns some fields as enum-like objects; coerce to plain string."""
    return value.value if hasattr(value, "value") else str(value)


# ── Plaid: orchestration ──────────────────────────────────────────────────────

def fetch_brokerage(client, nickname: str, token: str, show_raw: bool) -> BrokerageData:
    print(f"\n  [{nickname}]")
    data = BrokerageData(nickname=nickname)

    resp = _try_plaid(
        client.accounts_get, AccountsGetRequest(access_token=token),
        label="Accounts", errors=data.errors,
    )
    if resp is not None:
        data.accounts = _parse_accounts(resp)
        print(f"    ✅  {len(data.accounts)} account(s)")

    resp = _try_plaid(
        client.investments_holdings_get, InvestmentsHoldingsGetRequest(access_token=token),
        label="Holdings", errors=data.errors,
    )
    if resp is not None:
        if show_raw:
            print(json.dumps(resp.to_dict(), indent=2, default=str))
        data.holdings = _parse_holdings(resp)
        print(f"    ✅  {len(data.holdings)} holding(s)")

    end_date   = date.today()
    start_date = end_date - timedelta(days=TRANSACTION_DAYS)
    resp = _try_plaid(
        client.investments_transactions_get,
        InvestmentsTransactionsGetRequest(access_token=token, start_date=start_date, end_date=end_date),
        label="Transactions", errors=data.errors,
    )
    if resp is not None:
        data.transactions = _parse_transactions(resp)
        print(f"    ✅  {len(data.transactions)} transaction(s) in last {TRANSACTION_DAYS} days")

    return data


# ── Display: balances ─────────────────────────────────────────────────────────

def print_balances(all_data: list[BrokerageData]) -> None:
    section("ACCOUNT BALANCES")
    net_worth = 0.0

    for brokerage in all_data:
        if not brokerage.accounts:
            continue
        print(f"\n  [{brokerage.nickname}]")
        print(f"  {'Account':<35} {'Type':<12} {'Subtype':<16} {'Balance':>12}")
        print("  " + "─" * 78)
        for a in brokerage.accounts:
            if a.signed_balance is not None:
                net_worth += a.signed_balance
            tag = " (liability)" if a.is_liability else ""
            print(f"  {a.name:<35} {a.type:<12} {a.subtype:<16} {fmt_usd(a.balance):>12}{tag}")

    print(f"\n  {'NET WORTH (assets − liabilities)':>65} {fmt_usd(net_worth):>12}")


# ── Display: holdings ─────────────────────────────────────────────────────────

def print_holdings(all_data: list[BrokerageData]) -> None:
    section("COMBINED HOLDINGS")

    rows = [(h, b.nickname) for b in all_data for h in b.holdings]
    if not rows:
        print("  No holdings found.")
        return

    rows.sort(key=lambda x: x[0].value, reverse=True)
    total = sum(h.value for h, _ in rows)

    print(f"  {'Ticker':<8} {'Name':<32} {'Source':<18} {'Qty':>8} {'Price':>12} {'Value':>12} {'%':>6}")
    print("  " + "─" * 100)

    for holding, source in rows:
        share = (holding.value / total * 100) if total else 0
        print(
            f"  {holding.ticker:<8} {holding.name[:31]:<32} {source[:17]:<18}"
            f" {holding.quantity:>8.2f} {fmt_usd(holding.price):>12} {fmt_usd(holding.value):>12} {fmt_pct(share):>6}"
        )

    print("  " + "─" * 100)
    print(f"  {'INVESTMENT VALUE (sum of holdings)':>80} {fmt_usd(total):>12}")
    print(f"\n  Note: this is just investment positions — see NET WORTH above for cash + liabilities.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Portfolio data via Plaid")
    parser.add_argument("--raw", action="store_true", help="Print raw Plaid JSON responses")
    args = parser.parse_args()

    validate()

    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║            Portfolio Analyzer — Plaid            ║")
    print(f"║       {date.today().strftime('%B %d, %Y'):<42}║")
    print("╚══════════════════════════════════════════════════╝")

    section("CONNECTING TO PLAID")
    print(f"  Environment : {PLAID_ENV}")
    print(f"  Accounts    : {', '.join(ACCOUNTS.keys())}")
    try:
        client = build_plaid_client()
        print("  ✅  Plaid client ready")
    except Exception as e:
        sys.exit(f"  ❌  Failed to build Plaid client: {e}")

    section("FETCHING DATA")
    all_data = [
        fetch_brokerage(client, nickname, token, args.raw)
        for nickname, token in ACCOUNTS.items()
    ]

    print_balances(all_data)

    if not any(b.holdings for b in all_data):
        print("\n  ℹ️  No investment holdings found.")
        print("     Add a brokerage (Robinhood, Fidelity, etc.) to see holdings.")
        return

    print_holdings(all_data)


if __name__ == "__main__":
    main()
