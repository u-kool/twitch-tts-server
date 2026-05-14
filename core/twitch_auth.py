import http.server
import threading
import urllib.parse
import webbrowser

import requests


DEFAULT_SCOPES = [
    "chat:read", "chat:edit",
    "channel:read:subscriptions", "channel:read:redemptions",
    "bits:read", "channel:read:hype_train",
    "moderator:read:followers",
    "channel:manage:redemptions",
]


class TwitchAuth:
    def __init__(self, client_id: str, client_secret: str,
                 redirect_uri: str = "http://localhost:3000/redirect/",
                 oauth_port: int = 3000):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.oauth_port = oauth_port

    def get_auth_url(self):
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(DEFAULT_SCOPES),
            "force_verify": "true",
        }
        return "https://id.twitch.tv/oauth2/authorize?" + urllib.parse.urlencode(params)

    def exchange_code_for_token(self, code):
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }
        r = requests.post("https://id.twitch.tv/oauth2/token", data=data, timeout=15)
        r.raise_for_status()
        token_data = r.json()
        return token_data["access_token"], token_data.get("refresh_token")

    def refresh_access_token(self, refresh_token):
        if not refresh_token:
            return None, None
        try:
            r = requests.post(
                "https://id.twitch.tv/oauth2/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                timeout=15,
            )
            if r.status_code != 200:
                return None, None
            token_data = r.json()
            return token_data["access_token"], token_data.get("refresh_token", refresh_token)
        except requests.RequestException:
            return None, None

    def get_user_from_token(self, access_token):
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {access_token}",
        }
        r = requests.get("https://api.twitch.tv/helix/users", headers=headers, timeout=10)
        r.raise_for_status()
        users = r.json()["data"]
        if not users:
            raise Exception("Не удалось получить данные пользователя")
        return users[0]["id"], users[0]["login"]

    def perform_full_oauth(self):
        handler = self._make_handler()
        server = http.server.HTTPServer(("localhost", self.oauth_port), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        webbrowser.open(self.get_auth_url())
        if not handler.event.wait(timeout=120):
            server.shutdown()
            return None, None, None, None
        server.shutdown()

        if handler.error:
            return None, None, None, None

        try:
            access_token, refresh_token = self.exchange_code_for_token(handler.code)
            user_id, login = self.get_user_from_token(access_token)
            return access_token, user_id, login, refresh_token
        except Exception:
            return None, None, None, None

    def _make_handler(self):
        class OAuthHandler(http.server.BaseHTTPRequestHandler):
            code = None
            error = None
            event = threading.Event()

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                if "code" in query:
                    OAuthHandler.code = query["code"][0]
                    self.send_response(200)
                    self.send_header("Content-type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        "<html><body><h2>✅ Успешная авторизация!</h2>"
                        "<p>Можно закрыть окно.</p></body></html>".encode()
                    )
                    OAuthHandler.event.set()
                elif "error" in query:
                    OAuthHandler.error = query["error"][0]
                    self.send_response(400)
                    self.send_header("Content-type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        f"<html><body><h2>❌ Ошибка: {OAuthHandler.error}</h2>"
                        f"</body></html>".encode()
                    )
                    OAuthHandler.event.set()
                else:
                    self.send_response(400)
                    self.end_headers()

            def log_message(self, format, *args):
                pass

        return OAuthHandler
