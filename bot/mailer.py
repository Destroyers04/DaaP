import logging

import aiohttp

from bot.security import CODE_TTL

log = logging.getLogger("bot.mailer")

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
TIMEOUT = 15  # seconds
ERROR_BODY_LOG_CHARS = 300


class MailError(Exception):
    pass


class Mailer:
    def __init__(self, api_key: str, from_address: str, from_name: str):
        self.api_key = api_key
        self.sender = {"email": from_address, "name": from_name}
        self.session: aiohttp.ClientSession | None = None

    @property
    def dev(self) -> bool:
        return self.api_key == "dev"

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT))

    async def close(self):
        if self.session:
            await self.session.close()

    async def send_code(self, to_email: str, code: str) -> None:
        if self.dev:
            log.warning(f"[DEV MAILER] code: {code}")
            return

        # Plain text only: Brevo adds tracking pixels and rewrites links in HTML emails
        payload = {
            "sender": self.sender,
            "to": [{"email": to_email}],
            "subject": f"DaaP verification code: {code}",
            "textContent": (
                f"Your DaaP verification code is: {code}\n\n"
                f"Enter it in Discord within {CODE_TTL // 60} minutes to verify your account.\n"
                "If you didn't request this, you can ignore this email.\n"
            ),
            "tags": ["verification"],
        }
        headers = {"api-key": self.api_key, "accept": "application/json"}
        try:
            async with self.session.post(BREVO_URL, json=payload, headers=headers) as resp:
                if resp.status == 201:
                    return
                status = resp.status
                body = (await resp.text())[:ERROR_BODY_LOG_CHARS]
        except (aiohttp.ClientError, TimeoutError) as e:
            raise MailError(f"Brevo request failed: {type(e).__name__}") from e

        # Brevo's error text can echo the recipient address, so log it at debug level only
        log.debug(f"Brevo error body: {body}")
        if status in (401, 402):
            # 401 = bad key or unrecognised IP, 402 = out of daily credits. An admin has to act.
            log.error(f"Brevo returned HTTP {status}, check the API key, authorised IPs and credits")
        else:
            log.warning(f"Brevo returned HTTP {status}")
        raise MailError(f"Brevo returned HTTP {status}")
