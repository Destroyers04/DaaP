import hashlib
import hmac
import os
import re
import secrets

# Never log an email address or a code. Log Discord IDs only.

HOUR = 3600
DAY = 24 * HOUR

CODE_LENGTH = 6
# Keep this under 15 minutes: Discord only lets the bot edit an ephemeral message for 15 minutes,
# so a longer code would outlive the "Code sent" message and it could never be marked expired
CODE_TTL = 10 * 60
MAX_ATTEMPTS = 5
RESEND_COOLDOWN = 60
MAX_SENDS_PER_USER_HOUR = 5
MAX_SENDS_PER_EMAIL_HOUR = 3  # protects a colleague's inbox from several attacker accounts
MAX_SENDS_TOTAL_DAY = 250  # stay under Brevo's free daily limit (300)
# Decoy sends sleep this long (seconds) instead of calling the mail API. Keep it close to a real send.
DECOY_DELAY = (0.4, 1.2)
MIN_SECRET_LENGTH = 32

EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
DOMAIN_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}")


def _key() -> bytes:
    return os.environ["EMAIL_HASH_SECRET"].encode()


def normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    # jane+1@ and jane+2@ reach the same inbox, so they must count as one email
    return f"{local.split('+', 1)[0]}@{domain}"


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[1]


def hash_email(email: str) -> str:
    return hmac.new(_key(), b"email:" + email.encode(), hashlib.sha256).hexdigest()


def hash_code(discord_id: int, code: str) -> str:
    # Mixing in the user ID means the same code for two users gives two different hashes
    return hmac.new(
        _key(), f"code:{discord_id}:{code}".encode(), hashlib.sha256
    ).hexdigest()


def new_code() -> str:
    return f"{secrets.randbelow(10**CODE_LENGTH):0{CODE_LENGTH}d}"  # secrets, NOT random


def codes_match(stored_hash: str, discord_id: int, entered: str) -> bool:
    return hmac.compare_digest(stored_hash, hash_code(discord_id, entered.strip()))


def mask_email(email: str) -> str:
    local, domain = email.split("@", 1)
    return f"{local[0]}***@{domain}"


def parse_domains(raw: str) -> tuple[list[str], list[str]]:
    good, bad = [], []
    for part in re.split(r"[,\s]+", raw.lower()):
        part = part.strip().lstrip("@")
        if not part:
            continue
        (good if DOMAIN_RE.fullmatch(part) else bad).append(part)
    return list(dict.fromkeys(good)), bad
