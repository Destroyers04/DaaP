import asyncio
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
import discord
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
KEEP_LOCAL = 7
# Discord's upload limit for webhooks on a non-boosted server
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def make_backup(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"daap_{datetime.now():%Y%m%d_%H%M%S}.db"
    # Opened read-only so a wrong path fails instead of silently creating an empty database
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return backup_path


async def upload(webhook_url: str, backup_path: Path) -> None:
    async with aiohttp.ClientSession() as session:
        webhook = discord.Webhook.from_url(webhook_url, session=session)
        await webhook.send(
            content=f"Daily backup {datetime.now():%Y-%m-%d %H:%M}",
            file=discord.File(backup_path),
            wait=True,
        )


def prune(backup_dir: Path) -> None:
    for old in sorted(backup_dir.glob("daap_*.db"))[:-KEEP_LOCAL]:
        old.unlink()


def main() -> None:
    load_dotenv(ROOT / ".env")
    webhook_url = os.getenv("BACKUP_WEBHOOK_URL") or sys.exit("BACKUP_WEBHOOK_URL is not set, check your .env file")
    db_path = ROOT / (os.getenv("DATABASE_PATH") or "data/daap.db")
    backup_dir = db_path.parent / "backups"

    backup_path = make_backup(db_path, backup_dir)
    print(f"Backed up {db_path} to {backup_path}")

    size = backup_path.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        sys.exit(f"Backup is {size} bytes, too big for a Discord upload. Kept local copy only.")

    asyncio.run(upload(webhook_url, backup_path))
    print("Uploaded to Discord")
    prune(backup_dir)


if __name__ == "__main__":
    main()
