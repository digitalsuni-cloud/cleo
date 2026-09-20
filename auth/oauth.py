import os
import sys
import json
import time
import urllib.parse
import urllib.request
import urllib.error
import base64
import hashlib
import webbrowser
from typing import Optional, Dict

# Use centralized verbose logger
try:
    from cleo_logger import get_logger
    logger = get_logger("oauth")
except ImportError:
    import logging
    logger = logging.getLogger("cleo.oauth")
    logger.setLevel(logging.INFO)

# Known CloudHealth OAuth Client configurations
ANTIGRAVITY_CLIENT_ID     = "https://antigravity.google/oauth/client-metadata.json"
ANTIGRAVITY_REDIRECT_URI  = "https://antigravity.google/oauth-callback"

LOCAL_CLIENT_ID           = "BQf6HFF5XNHyCYDvW6zXN_JnZvNrq4uSQrLkbbG9sbM"
LOCAL_CLIENT_SECRET       = "dfIvQbP6mVn6prSh8pN6kp0gIqaAKhBv-5k-jkSqw2mMg32KfqLAnQv6hUurab0A"
LOCAL_REDIRECT_URI        = "http://127.0.0.1:8080/oauth-callback"

# Default to Cleo's local independent OAuth client (no third-party/Antigravity redirect needed)
DEFAULT_CLIENT_ID         = LOCAL_CLIENT_ID
DEFAULT_CLIENT_SECRET     = LOCAL_CLIENT_SECRET
DEFAULT_REDIRECT_URI      = LOCAL_REDIRECT_URI
DEFAULT_SCOPES            = "mcp:flexorgs:read mcp:query:run mcp:datasets:read mcp:customers:read"
MCP_RESOURCE              = "https://apps.cloudhealthtech.com/mcp"

class OAuth2Helper:
    """
    A dependency-free OAuth 2.0 helper with PKCE & RFC 7591 Dynamic Client Registration.
    Features detailed DEBUG logging for every request, response, and token claim.
    """
    def __init__(self, 
                 client_id: str = "", 
                 auth_url: str = "https://apps.cloudhealthtech.com/oauth2/authorize", 
                 token_url: str = "https://apps.cloudhealthtech.com/oauth2/token", 
                 registration_url: str = "https://apps.cloudhealthtech.com/oauth2/register",
                 redirect_uri: str = "", 
                 scopes: str = DEFAULT_SCOPES, 
                 client_secret: str = "",
                 tokens_file: str = ""):
        self.auth_url = auth_url
        self.token_url = token_url
        self.registration_url = registration_url
        self.client_id = client_id or os.environ.get("CLOUDHEALTH_CLIENT_ID") or DEFAULT_CLIENT_ID
        
        # Determine redirect_uri based on client_id if not explicitly specified
        if not redirect_uri:
            if self.client_id == ANTIGRAVITY_CLIENT_ID:
                self.redirect_uri = ANTIGRAVITY_REDIRECT_URI
            else:
                self.redirect_uri = LOCAL_REDIRECT_URI
        else:
            self.redirect_uri = redirect_uri
            
        self.scopes = scopes
        self.client_secret = client_secret or os.environ.get("CLOUDHEALTH_CLIENT_SECRET") or (
            LOCAL_CLIENT_SECRET if self.client_id == LOCAL_CLIENT_ID else DEFAULT_CLIENT_SECRET
        )
        self.tokens_file = tokens_file or os.path.expanduser("~/.cleo/mcp_oauth_tokens.json")
        self.last_error = ""

        logger.debug(f"[OAuth Init] client_id={self.client_id} redirect_uri={self.redirect_uri}")

    @classmethod
    def register_client(cls, 
                        registration_url: str = "https://apps.cloudhealthtech.com/oauth2/register", 
                        redirect_uris: list = None) -> Optional[Dict]:
        """Dynamically registers a new confidential OAuth client with CloudHealth via RFC 7591."""
        if redirect_uris is None:
            redirect_uris = [
                "http://127.0.0.1:8080/oauth-callback", 
                "http://localhost:8080/oauth-callback"
            ]
        payload = {
            "client_name": "Cleo FinOps Agent",
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
            "scope": DEFAULT_SCOPES
        }
        logger.debug(f"[RFC 7591 Register] POST {registration_url} Payload: {payload}")
        try:
            req = urllib.request.Request(
                registration_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Cleo-FinOps-Agent/1.0"
                }
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                body = resp.read().decode("utf-8")
                logger.debug(f"[RFC 7591 Register] Status: {resp.status}, Response: {body}")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            err = e.read().decode('utf-8', 'ignore') if hasattr(e, 'read') else str(e)
            logger.error(f"[RFC 7591 Register Failed] HTTP {e.code}: {err}")
            return None
        except Exception as e:
            logger.error(f"[RFC 7591 Register Exception] {e}")
            return None

    def _load_token(self) -> Optional[Dict]:
        if os.path.exists(self.tokens_file):
            try:
                with open(self.tokens_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not read tokens file {self.tokens_file}: {e}")
                return None
        return None

    def _save_token(self, mcp_data: Dict):
        os.makedirs(os.path.dirname(self.tokens_file), exist_ok=True)
        with open(self.tokens_file, "w") as f:
            json.dump(mcp_data, f, indent=2)
        os.chmod(self.tokens_file, 0o600)
        # Also sync to legacy path ~/.cleo/oauth_tokens.json for backwards compatibility
        alt_path = os.path.expanduser("~/.cleo/oauth_tokens.json")
        try:
            with open(alt_path, "w") as f:
                json.dump(mcp_data, f, indent=2)
            os.chmod(alt_path, 0o600)
        except Exception:
            pass

    def load_token(self) -> Optional[Dict]:
        return self._load_token()

    def _post_request(self, payload: Dict) -> Optional[Dict]:
        self.last_error = ""
        # Redact secrets in logs
        log_payload = dict(payload)
        if "client_secret" in log_payload:
            log_payload["client_secret"] = log_payload["client_secret"][:6] + "..."
        if "refresh_token" in log_payload:
            log_payload["refresh_token"] = log_payload["refresh_token"][:10] + "..."
        if "code" in log_payload:
            log_payload["code"] = log_payload["code"][:10] + "..."

        logger.debug(f"[OAuth Token Request] POST {self.token_url}")
        logger.debug(f"[OAuth Token Request Payload] {json.dumps(log_payload)}")

        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(self.token_url, data=data)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "Cleo-FinOps-Agent/1.0")
        # Client authentication is handled via payload body (client_secret_post)
        # Avoid sending both body client_secret and Authorization Basic header,
        # which causes Spring Authorization Server to return 401 invalid_client
        
        try:
            with urllib.request.urlopen(req, timeout=15.0) as response:
                resp_body = response.read().decode("utf-8")
                try:
                    log_resp = dict(json.loads(resp_body))
                    for _k in ("access_token", "refresh_token", "id_token"):
                        if _k in log_resp:
                            log_resp[_k] = str(log_resp[_k])[:10] + "..."
                    logger.debug(f"[OAuth Token Response] HTTP {response.status}: {json.dumps(log_resp)}")
                except Exception:
                    logger.debug(f"[OAuth Token Response] HTTP {response.status}: <non-JSON body, {len(resp_body)} bytes>")
                if response.status == 200:
                    return json.loads(resp_body)
                else:
                    self.last_error = f"HTTP {response.status}: {resp_body}"
                    logger.error(f"[OAuth Token Error] {self.last_error}")
                    return None
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8', 'ignore') if hasattr(e, 'read') else str(e)
            self.last_error = f"HTTP {e.code}: {err_body}"
            logger.error(f"[OAuth HTTP Error] Code {e.code}: {err_body}")
            return None
        except urllib.error.URLError as e:
            self.last_error = f"Network error: {e}"
            logger.error(f"[OAuth Network Error] {e}")
            return None
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"[OAuth Exception] {e}")
            return None

    def clear_tokens(self):
        """Clears stored token files from disk."""
        for path in [self.tokens_file, os.path.expanduser("~/.cleo/oauth_tokens.json")]:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    logger.info(f"[OAuth] Removed token file: {path}")
            except Exception as e:
                logger.warning(f"[OAuth] Could not delete {path}: {e}")

    def refresh_token(self, mcp_data: Dict) -> Optional[str]:
        token_info = mcp_data.get("token", {})
        # If token has more than 5 minutes remaining, keep using it
        if time.time() < token_info.get("expires_at", 0) - 300:
            return token_info.get("access_token")
        
        refresh_token_str = token_info.get("refresh_token")
        if not refresh_token_str:
            logger.debug("[OAuth Refresh] No refresh token available in storage")
            return None
            
        logger.info("[OAuth Refresh] Refreshing CloudHealth access token...")
        client_id = mcp_data.get("client_id", self.client_id)
        payload = {
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token_str,
            "client_id":     client_id,
            "resource":      MCP_RESOURCE
        }
        client_sec = self.client_secret or mcp_data.get("client_secret", "")
        if not client_sec and client_id == LOCAL_CLIENT_ID:
            client_sec = LOCAL_CLIENT_SECRET
        if client_sec and client_id != ANTIGRAVITY_CLIENT_ID:
            payload["client_secret"] = client_sec

        td = self._post_request(payload)
        if td and "access_token" in td:
            mcp_data["token"]["access_token"] = td["access_token"]
            if "refresh_token" in td:
                mcp_data["token"]["refresh_token"] = td["refresh_token"]
            mcp_data["token"]["expires_at"] = time.time() + td.get("expires_in", 3600)
            
            tokens = self._load_token() or {}
            tokens[MCP_RESOURCE] = mcp_data
            tokens["token"] = mcp_data["token"]
            tokens["client_id"] = client_id
            self._save_token(tokens)
            logger.info("✅ [OAuth Refresh] Access token refreshed successfully!")
            return td["access_token"]
            
        logger.warning(f"⚠️  [OAuth Refresh] Token refresh failed: {self.last_error}")
        return None

    def exchange_code(self, code: str, verifier: str, storage_key: str = MCP_RESOURCE, 
                      redirect_uri: str = None, client_id: str = None) -> Optional[Dict]:
        """Exchanges authorization code and PKCE verifier for access token."""
        code = code.strip()
        # Auto-extract code if user pasted a full redirect URL
        if "code=" in code:
            parsed = urllib.parse.urlparse(code)
            qs = urllib.parse.parse_qs(parsed.query or parsed.fragment)
            if "code" in qs:
                code = qs["code"][0].strip()

        used_client_id = client_id or self.client_id or LOCAL_CLIENT_ID
        used_redirect_uri = redirect_uri or (
            ANTIGRAVITY_REDIRECT_URI if used_client_id == ANTIGRAVITY_CLIENT_ID else LOCAL_REDIRECT_URI
        )

        logger.info(f"[OAuth Exchange] Starting code exchange (client_id: {used_client_id})...")
        payload = {
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  used_redirect_uri,
            "client_id":     used_client_id,
            "code_verifier": verifier,
            "resource":      MCP_RESOURCE
        }
        sec = self.client_secret
        if not sec and used_client_id == LOCAL_CLIENT_ID:
            sec = LOCAL_CLIENT_SECRET
        if sec and used_client_id != ANTIGRAVITY_CLIENT_ID:
            payload["client_secret"] = sec

        td = self._post_request(payload)
        if td and "access_token" in td:
            access_token = td["access_token"]
            logger.info("✅ [OAuth Exchange] Code exchange successful!")
            
            # Inspect and log decoded JWT claims
            try:
                parts = access_token.split(".")
                if len(parts) >= 2:
                    p = parts[1] + "=" * (-len(parts[1]) % 4)
                    claims = json.loads(base64.urlsafe_b64decode(p).decode())
                    logger.debug(f"[OAuth Token Claims] sub={claims.get('sub')} org={claims.get('org')} "
                                 f"client_id={claims.get('client_id')} scope={claims.get('scope')} "
                                 f"customer_type={claims.get('customer_type')}")
            except Exception as e:
                logger.debug(f"[OAuth Token Parse Warning] {e}")

            mcp_data = {
                "token": {
                    "access_token":  access_token,
                    "refresh_token": td.get("refresh_token", ""),
                    "expires_at":    time.time() + td.get("expires_in", 3600)
                },
                "token_url": self.token_url,
                "client_id": used_client_id,
                "client_secret": LOCAL_CLIENT_SECRET if used_client_id == LOCAL_CLIENT_ID else ""
            }
            tokens = self._load_token() or {}
            tokens[storage_key] = mcp_data
            tokens["token"] = mcp_data["token"]
            tokens["client_id"] = used_client_id
            self._save_token(tokens)
            return mcp_data
            
        logger.error(f"❌ [OAuth Exchange Failed] {self.last_error}")
        return None

    def generate_auth_params(self, client_id: str = None, redirect_uri: str = None) -> tuple[str, str, dict]:
        """Generates PKCE verifier, challenge, state, and full authorization URL."""
        verifier  = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        
        import uuid
        state = str(uuid.uuid4())
        
        cid = client_id or self.client_id
        ruri = redirect_uri or self.redirect_uri

        params = {
            "response_type":         "code",
            "client_id":             cid,
            "redirect_uri":          ruri,
            "scope":                 self.scopes,
            "code_challenge":        challenge,
            "code_challenge_method": "S256",
            "resource":              MCP_RESOURCE,
            "state":                 state
        }
        auth_url = self.auth_url + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        logger.debug(f"[OAuth Auth URL Generated] client_id={cid} redirect_uri={ruri} state={state}")
        return auth_url, verifier, params

    def authenticate(self, storage_key: str = MCP_RESOURCE, use_local_server: bool = True) -> Optional[Dict]:
        """Runs the interactive OAuth 2.0 PKCE flow in terminal."""
        print("\n🔐  CloudHealth Authentication Required\n")
        auth_url, verifier, params = self.generate_auth_params()
        
        print(f"Please open this URL in your browser:\n  {auth_url}\n")
        print("1. Log in and authorize.")
        if "antigravity.google" in self.redirect_uri:
            print("2. You will be redirected to the Antigravity callback page.")
            print("3. Click 'Copy to Clipboard' to copy the authorization code.")
        else:
            print("2. Copy the authorization code from the redirected URL.")
            
        code = input("\nPaste authorization code here: ").strip()
        if not code:
            print("❌ No code provided.")
            return None
            
        return self.exchange_code(code, verifier, storage_key)

# Global default instance
auth_helper = OAuth2Helper()
