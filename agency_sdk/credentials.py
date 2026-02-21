import jwt
import requests


class CredentialsSupplier:
    def __init__(self, auth_base_url: str, client_id: str, client_secret: str):
        self.auth_base_url = auth_base_url.rstrip('/')
        self.client_id = client_id
        self.client_secret = client_secret
        self._cached_token = None

    def bearer_token(self, org_id: str = "") -> str:
        """Get OAuth2 bearer token using client credentials flow."""
        if self._cached_token and not self._is_token_expired():
            return self._cached_token

        token_url = f"{self.auth_base_url}"

        form_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }

        response = requests.post(token_url, data=form_data)
        response.raise_for_status()

        token_data = response.json()
        self._cached_token = token_data["access_token"]
        return self._cached_token

    def _is_token_expired(self) -> bool:
        """Check if the cached token is expired."""
        if not self._cached_token:
            return True

        try:
            decoded = jwt.decode(self._cached_token, options={"verify_signature": False})
            import time
            return decoded.get('exp', 0) <= time.time()
        except:
            return True
