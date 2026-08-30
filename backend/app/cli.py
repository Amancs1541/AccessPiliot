"""Console-only administrative commands. Run from backend/ with the venv active:

    python -m app.cli emergency-url [--regenerate]

There is no other CLI convention in this codebase (no manage.py, no scripts/) — this is the first, kept as a
single small argparse-based module (no new dependency) rather than introducing Typer/Click for one command.
"""
from __future__ import annotations

import argparse
import asyncio
import secrets

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models import BreakGlassAccount


async def _emergency_url(regenerate: bool) -> None:
    async with AsyncSessionLocal() as session:
        account = (await session.execute(select(BreakGlassAccount).where(BreakGlassAccount.is_active.is_(True)))).scalars().first()
        if account is None:
            print("No active break-glass account found. Complete the first-time setup wizard before running this command.")
            return
        if regenerate or not account.emergency_path_token:
            account.emergency_path_token = secrets.token_urlsafe(32)
            await session.commit()
        base_url = get_settings().frontend_url.rstrip("/")
        print("Emergency access URL — this is the ONLY time this token is shown; store it securely, share it only with authorized break-glass holders, and never link to it from anywhere in the app:")
        print(f"{base_url}/emergency-access/{account.emergency_path_token}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    emergency_url_parser = subparsers.add_parser("emergency-url", help="Print the hidden Break-Glass emergency access URL, generating one if it doesn't exist yet.")
    emergency_url_parser.add_argument("--regenerate", action="store_true", help="Invalidate the existing URL and generate a new one.")
    args = parser.parse_args()
    if args.command == "emergency-url":
        asyncio.run(_emergency_url(args.regenerate))


if __name__ == "__main__":
    main()
