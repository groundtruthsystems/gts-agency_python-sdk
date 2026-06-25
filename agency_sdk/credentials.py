import time

import jwt
import requests


class CredentialsSupplier:
    def __init__(
        self,
        auth_base_url: str,
        client_id: str,
        client_secret: str,
        *,
        refresh_buffer: float = 30.0,
    ):
        self.auth_base_url = auth_base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        # Refresh this many seconds before the real expiry so a token is never
        # used mid-flight. Matters for the observability OTLP per-request auth
        # hook, which re-reads this token on every export in a long-running
        # process.
        self.refresh_buffer = refresh_buffer
        self._cached_token: str | None = None

    def bearer_token(self) -> str:
        """Get OAuth2 bearer token using client credentials flow."""
        if self._cached_token and not self._is_token_expired():
            return self._cached_token

        form_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        response = requests.post(self.auth_base_url, data=form_data, timeout=30)
        response.raise_for_status()

        token_data = response.json()
        token: str = token_data["access_token"]
        self._cached_token = token
        return token

    def _is_token_expired(self) -> bool:
        """Check if the cached token is expired."""
        if not self._cached_token:
            return True

        try:
            decoded = jwt.decode(self._cached_token, options={"verify_signature": False})
            return bool(decoded.get("exp", 0) <= time.time() + self.refresh_buffer)
        except Exception:
            return True
