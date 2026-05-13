# core/twitch_api_client.py
import requests
import logging

logger = logging.getLogger(__name__)

class TwitchApiClient:
    """Минимальный клиент для Helix API – получение ID и логина по токену."""
    
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = "https://api.twitch.tv/helix"
    
    def get_user_from_token(self, access_token: str):
        """
        Возвращает (успех, user_id, display_name, ошибка).
        access_token – чистый токен без префикса 'oauth:'.
        """
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {access_token}"
        }
        try:
            r = requests.get(f"{self.base_url}/users", headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json().get("data", [])
                if data:
                    user = data[0]
                    return True, user["id"], user["login"], None
                else:
                    return False, None, None, "Empty user data"
            else:
                msg = r.json().get("message", r.text)
                return False, None, None, f"API error {r.status_code}: {msg}"
        except Exception as e:
            logger.error(f"Twitch API request failed: {e}")
            return False, None, None, str(e)