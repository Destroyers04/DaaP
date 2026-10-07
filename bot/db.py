import asyncio
import contextlib

import aiosqlite

from bot import security

# Never edit a migration that has already run on real data. Add a new one instead.
MIGRATIONS = [
    """
    -- One row per Discord user the bot knows about. Future features hang off this table.
    CREATE TABLE users (
        discord_id   INTEGER PRIMARY KEY,
        created_at   INTEGER NOT NULL
    );

    -- Verified = a row exists here. PK gives one email per account, UNIQUE gives one account per email.
    CREATE TABLE verified_emails (
        discord_id   INTEGER PRIMARY KEY REFERENCES users(discord_id) ON DELETE CASCADE,
        email_hash   TEXT    NOT NULL UNIQUE,
        verified_at  INTEGER NOT NULL
    );

    -- At most one pending code per user. Resend replaces the row.
    CREATE TABLE pending_verifications (
        discord_id   INTEGER PRIMARY KEY REFERENCES users(discord_id) ON DELETE CASCADE,
        email_hash   TEXT    NOT NULL,
        code_hash    TEXT    NOT NULL,            -- decoy rows get the hash of a code that was never sent
        masked_email TEXT    NOT NULL,
        sent_at      INTEGER NOT NULL,
        expires_at   INTEGER NOT NULL,
        attempts     INTEGER NOT NULL DEFAULT 0
    );

    -- Every send, real or decoy. Used for rate limits per user, per email and in total.
    CREATE TABLE send_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        discord_id   INTEGER NOT NULL,
        email_hash   TEXT    NOT NULL,
        sent_at      INTEGER NOT NULL,
        real         INTEGER NOT NULL             -- 1 = an email was actually sent, 0 = decoy
    );
    CREATE INDEX send_log_user  ON send_log(discord_id, sent_at);
    CREATE INDEX send_log_email ON send_log(email_hash, sent_at);

    CREATE TABLE verify_settings (
        guild_id           INTEGER PRIMARY KEY,
        role_id            INTEGER NOT NULL,
        ticket_category_id INTEGER,
        admin_role_id      INTEGER
    );

    CREATE TABLE verify_domains (
        guild_id  INTEGER NOT NULL,
        domain    TEXT    NOT NULL,               -- lowercase, no "@"
        PRIMARY KEY (guild_id, domain)
    );

    CREATE TABLE tickets (
        channel_id  INTEGER PRIMARY KEY,
        discord_id  INTEGER NOT NULL,
        opened_at   INTEGER NOT NULL,
        closed_at   INTEGER,                      -- NULL = still open
        closed_by   INTEGER
    );
    CREATE UNIQUE INDEX tickets_one_open ON tickets(discord_id) WHERE closed_at IS NULL;
    """,
    """
    -- Name taken from the email ("Jane Doe"). NULL = the email isn't firstname.lastname
    ALTER TABLE pending_verifications ADD COLUMN nickname TEXT;
    ALTER TABLE verified_emails ADD COLUMN nickname TEXT;
    """,
]

SEND_LOG_KEEP = 7 * security.DAY


class SendBlocked(Exception):
    """reserve_send refused. reason is "cooldown" (wait = seconds left), "limit" or "busy"."""

    def __init__(self, reason: str, wait: int = 0):
        super().__init__(reason)
        self.reason = reason
        self.wait = wait


class Database:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn
        # Every coroutine shares this one connection, so without the lock another coroutine's
        # commit could land in the middle of a transaction
        self._lock = asyncio.Lock()

    @classmethod
    async def connect(cls, path: str) -> "Database":
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")  # OFF by default, and it's per connection
        await conn.execute("PRAGMA journal_mode = WAL")
        db = cls(conn)
        await db.migrate()
        return db

    async def migrate(self):
        (version,) = await self._one("PRAGMA user_version")
        # Not _transaction: executescript commits on its own before it runs
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            await self.conn.executescript(script)
            await self.conn.execute(f"PRAGMA user_version = {i}")
            await self.conn.commit()

    async def close(self):
        await self.conn.close()

    async def _one(self, sql: str, params=()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _all(self, sql: str, params=()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())

    @contextlib.asynccontextmanager
    async def _transaction(self):
        async with self._lock:
            try:
                yield self.conn
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise

    async def _write(self, sql: str, params=()) -> int:
        async with self._transaction() as conn, conn.execute(sql, params) as cur:
            return cur.rowcount

    async def _write_many(self, sql: str, rows: list[tuple]):
        async with self._transaction() as conn:
            await conn.executemany(sql, rows)

    async def ensure_user(self, discord_id: int, now: int):
        await self._write("INSERT OR IGNORE INTO users (discord_id, created_at) VALUES (?, ?)", (discord_id, now))

    async def get_verified(self, discord_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM verified_emails WHERE discord_id = ?", (discord_id,))

    async def email_hash_taken(self, email_hash: str, exclude_id: int) -> bool:
        row = await self._one(
            "SELECT 1 FROM verified_emails WHERE email_hash = ? AND discord_id != ?", (email_hash, exclude_id)
        )
        return row is not None

    async def force_verify(self, discord_id: int, email_hash: str, nickname: str | None, now: int) -> int | None:
        """Returns the other account's ID, and changes nothing, if the email belongs to someone else."""
        async with self._transaction() as conn:
            async with conn.execute("SELECT discord_id FROM verified_emails WHERE email_hash = ?", (email_hash,)) as cur:
                owner = await cur.fetchone()
            if owner and owner["discord_id"] != discord_id:
                return owner["discord_id"]
            await conn.execute("DELETE FROM verified_emails WHERE discord_id = ?", (discord_id,))
            await conn.execute("DELETE FROM pending_verifications WHERE discord_id = ?", (discord_id,))
            await conn.execute(
                "INSERT OR IGNORE INTO users (discord_id, created_at) VALUES (?, ?)", (discord_id, now)
            )
            await conn.execute(
                "INSERT INTO verified_emails (discord_id, email_hash, nickname, verified_at) VALUES (?, ?, ?, ?)",
                (discord_id, email_hash, nickname, now),
            )
        return None

    async def complete_verification(self, discord_id: int, email_hash: str, nickname: str | None, now: int):
        async with self._transaction() as conn:
            await conn.execute(
                "INSERT INTO verified_emails (discord_id, email_hash, nickname, verified_at) VALUES (?, ?, ?, ?)",
                (discord_id, email_hash, nickname, now),
            )
            await conn.execute("DELETE FROM pending_verifications WHERE discord_id = ?", (discord_id,))

    async def delete_verified(self, discord_id: int) -> bool:
        return await self._write("DELETE FROM verified_emails WHERE discord_id = ?", (discord_id,)) > 0

    async def get_pending(self, discord_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM pending_verifications WHERE discord_id = ?", (discord_id,))

    async def set_pending(
        self,
        discord_id: int,
        email_hash: str,
        code_hash: str,
        masked_email: str,
        nickname: str | None,
        sent_at: int,
        expires_at: int,
    ):
        await self._write(
            "INSERT OR REPLACE INTO pending_verifications"
            " (discord_id, email_hash, code_hash, masked_email, nickname, sent_at, expires_at, attempts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (discord_id, email_hash, code_hash, masked_email, nickname, sent_at, expires_at),
        )

    async def delete_pending(self, discord_id: int):
        await self._write("DELETE FROM pending_verifications WHERE discord_id = ?", (discord_id,))

    async def use_attempt(self, discord_id: int, now: int) -> aiosqlite.Row | None:
        # Counted before the code is compared, so codes submitted at the same time can't all slip in
        # under MAX_ATTEMPTS
        sql = (
            "UPDATE pending_verifications SET attempts = attempts + 1"
            " WHERE discord_id = ? AND expires_at > ? AND attempts < ? RETURNING *"
        )
        async with self._transaction() as conn, conn.execute(sql, (discord_id, now, security.MAX_ATTEMPTS)) as cur:
            return await cur.fetchone()

    async def reserve_send(self, discord_id: int, email_hash: str, real: bool, now: int) -> int:
        # One transaction, so submits at the same time can't all pass the limits
        async with self._transaction() as conn:

            async def one(sql: str, params: tuple):
                async with conn.execute(sql, params) as cur:
                    return (await cur.fetchone())[0]

            last = await one("SELECT MAX(sent_at) FROM send_log WHERE discord_id = ?", (discord_id,))
            if last is not None and now - last < security.RESEND_COOLDOWN:
                raise SendBlocked("cooldown", security.RESEND_COOLDOWN - (now - last))
            since_hour = now - security.HOUR
            by_user = await one("SELECT COUNT(*) FROM send_log WHERE discord_id = ? AND sent_at >= ?", (discord_id, since_hour))
            by_email = await one("SELECT COUNT(*) FROM send_log WHERE email_hash = ? AND sent_at >= ?", (email_hash, since_hour))
            if by_user >= security.MAX_SENDS_PER_USER_HOUR or by_email >= security.MAX_SENDS_PER_EMAIL_HOUR:
                raise SendBlocked("limit")
            total = await one("SELECT COUNT(*) FROM send_log WHERE real = 1 AND sent_at >= ?", (now - security.DAY,))
            if total >= security.MAX_SENDS_TOTAL_DAY:
                raise SendBlocked("busy")
            async with conn.execute(
                "INSERT INTO send_log (discord_id, email_hash, sent_at, real) VALUES (?, ?, ?, ?)",
                (discord_id, email_hash, now, int(real)),
            ) as cur:
                return cur.lastrowid

    async def unreserve_send(self, send_id: int):
        await self._write("DELETE FROM send_log WHERE id = ?", (send_id,))

    async def get_settings(self, guild_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM verify_settings WHERE guild_id = ?", (guild_id,))

    async def save_settings(
        self,
        guild_id: int,
        role_id: int,
        ticket_category_id: int | None,
        admin_role_id: int | None,
        domains: list[str],
    ):
        async with self._transaction() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO verify_settings (guild_id, role_id, ticket_category_id, admin_role_id)"
                " VALUES (?, ?, ?, ?)",
                (guild_id, role_id, ticket_category_id, admin_role_id),
            )
            await conn.execute("DELETE FROM verify_domains WHERE guild_id = ?", (guild_id,))
            await conn.executemany(
                "INSERT INTO verify_domains (guild_id, domain) VALUES (?, ?)", [(guild_id, d) for d in domains]
            )

    async def get_domains(self, guild_id: int) -> list[str]:
        rows = await self._all("SELECT domain FROM verify_domains WHERE guild_id = ? ORDER BY domain", (guild_id,))
        return [r["domain"] for r in rows]

    async def add_domains(self, guild_id: int, domains: list[str]):
        await self._write_many(
            "INSERT OR IGNORE INTO verify_domains (guild_id, domain) VALUES (?, ?)", [(guild_id, d) for d in domains]
        )

    async def remove_domains(self, guild_id: int, domains: list[str]):
        await self._write_many(
            "DELETE FROM verify_domains WHERE guild_id = ? AND domain = ?", [(guild_id, d) for d in domains]
        )

    async def open_ticket(self, channel_id: int, discord_id: int, now: int):
        await self._write(
            "INSERT INTO tickets (channel_id, discord_id, opened_at) VALUES (?, ?, ?)", (channel_id, discord_id, now)
        )

    async def get_open_ticket(self, discord_id: int) -> aiosqlite.Row | None:
        return await self._one("SELECT * FROM tickets WHERE discord_id = ? AND closed_at IS NULL", (discord_id,))

    async def close_ticket(self, channel_id: int, closed_by: int | None, now: int):
        await self._write(
            "UPDATE tickets SET closed_at = ?, closed_by = ? WHERE channel_id = ? AND closed_at IS NULL",
            (now, closed_by, channel_id),
        )

    async def cleanup_expired(self, now: int):
        async with self._transaction() as conn:
            await conn.execute("DELETE FROM pending_verifications WHERE expires_at <= ?", (now,))
            await conn.execute("DELETE FROM send_log WHERE sent_at < ?", (now - SEND_LOG_KEEP,))
