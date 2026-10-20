"""Onboard the demo sellers A, B, C as Airwallex sandbox connected accounts.

Writes their account ids to data/sandbox_sellers.json, which `scripts/demo.py --live` and
the API (USE_FAKE_AIRWALLEX=false) read. The file is saved after each seller, so a failed
run resumes with only the missing sellers. --force backs the file up and starts over.
"""

import argparse
import sys
from datetime import UTC, datetime

from reserveguard.airwallex.client import AirwallexError
from reserveguard.airwallex.rest import RestAirwallexClient
from reserveguard.airwallex.sandbox import SandboxAdmin
from reserveguard.config import Settings
from reserveguard.scenario import (
    DEMO_SELLERS,
    SANDBOX_SELLERS_PATH,
    load_seller_accounts,
    onboard_demo_sellers,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="onboard new sellers anyway")
    args = parser.parse_args()

    path = SANDBOX_SELLERS_PATH
    if args.force and path.exists():
        backup = path.with_suffix(f".{datetime.now(UTC):%Y%m%d%H%M%S}.bak")
        path.replace(backup)
        print(f"Moved previous sellers to {backup}")
    elif path.exists() and _is_complete():
        print(f"{path} already has every seller; use --force to onboard new ones.")
        return 0

    admin = SandboxAdmin(RestAirwallexClient(Settings()))
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    try:
        accounts = onboard_demo_sellers(admin, path, stamp)
    except AirwallexError as exc:
        print(f"Onboarding failed: {exc}", file=sys.stderr)
        if path.exists():
            print(f"Sellers created so far are saved in {path}; re-run to resume.")
        if "cannot onboard" in str(exc):
            print(
                "Your sandbox needs connected accounts and platform payments enabled; "
                "email devhelp@airwallex.com with your sandbox email and Client ID.",
                file=sys.stderr,
            )
        return 1

    for seller_id, account_id in accounts.items():
        print(f"Seller {seller_id}: {account_id}")
    print(f"Saved to {path}")
    return 0


def _is_complete() -> bool:
    try:
        return len(load_seller_accounts(SANDBOX_SELLERS_PATH)) == len(DEMO_SELLERS)
    except ValueError:
        return False


if __name__ == "__main__":
    sys.exit(main())
