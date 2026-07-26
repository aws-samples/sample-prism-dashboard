"""Data source CRUD and OAuth callback endpoints."""

import json
import os
from urllib.parse import unquote, urlparse
from urllib.request import Request as _Req, urlopen as _urlopen
from urllib.error import HTTPError, URLError

from db import datasource_table, respond, paginated_scan
from safe_logging import log_error, log_info, log_warning
from audit import audit_log
from validation import sanitize_dict, validate_datasource_body, validate_url
from crypto import encrypt_config, decrypt_config


def _safe_urlopen(req, **kwargs):
    """urlopen wrapper: HTTPS-only AND re-checks the resolved host against
    private/reserved ranges at REQUEST time. The check_ssrf validation at
    body-validation time is not sufficient alone — DNS can change between
    validation and this request (TOCTOU)."""
    from validation import _is_private_or_reserved
    url = req.full_url if hasattr(req, 'full_url') else str(req)
    parsed = urlparse(url)
    if parsed.scheme.lower() != 'https':
        raise ValueError(f"Only HTTPS URLs are allowed, got {parsed.scheme}://")
    if parsed.hostname and _is_private_or_reserved(parsed.hostname):
        raise ValueError("URL resolves to a private or reserved address")
    return _urlopen(req, **kwargs)  # nosec B310


def validate_datasource_auth(ds_type, config):
    """Validate data source credentials by making a lightweight API call."""
    auth_type = config.get("authType", "pat")

    def _fetch_json(url, headers):
        req = _Req(url, headers=headers)
        try:
            with _safe_urlopen(req, timeout=5) as resp:
                body = resp.read().decode()
                if not body.strip():
                    raise Exception("Empty response from server")
                return json.loads(body)
        except HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode()[:200]
            except Exception:  # nosec B110 — best-effort error body read
                error_body = ""
            raise Exception(f"HTTP {e.code}: {error_body or e.reason}")
        except URLError as e:
            raise Exception(f"Cannot reach server: {e.reason}")
        except json.JSONDecodeError:
            raise Exception("Server returned non-JSON response (check URL)")

    try:
        if ds_type == "github" and auth_type == "pat":
            token = config.get("pat", "")
            if not token:
                return None
            result = _fetch_json("https://api.github.com/user", {"Authorization": f"bearer {token}"})
            return {"valid": True, "user": result.get("login", "")}

        if ds_type == "github" and auth_type == "app":
            app_id = config.get("appId", "")
            private_key = config.get("privateKey", "")
            installation_id = config.get("installationId", "")
            if not all([app_id, private_key, installation_id]):
                return None
            try:
                from cryptography.hazmat.primitives import serialization, hashes
                from cryptography.hazmat.primitives.asymmetric import padding
                import time as _time
                import base64 as _b64
                pk = serialization.load_pem_private_key(private_key.encode(), password=None)
                now = int(_time.time())
                payload_dict = {"iat": now - 60, "exp": now + 300, "iss": str(app_id)}
                header = _b64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=")
                body_b = _b64.urlsafe_b64encode(json.dumps(payload_dict).encode()).rstrip(b"=")
                signing_input = header + b"." + body_b
                sig = pk.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
                jwt_token = (signing_input + b"." + _b64.urlsafe_b64encode(sig).rstrip(b"=")).decode()
                result = _fetch_json(
                    f"https://api.github.com/app/installations/{installation_id}",
                    {"Authorization": f"Bearer {jwt_token}", "Accept": "application/vnd.github+json"},
                )
                return {"valid": True, "user": f"App: {result.get('app_slug', app_id)}"}
            except ImportError:
                # cryptography not available in this Lambda — do basic format validation
                pem = private_key.strip()
                if not pem.startswith("-----BEGIN") or "-----END" not in pem:
                    raise Exception("Private key does not look like a valid PEM file")
                if not app_id.isdigit():
                    raise Exception(f"App ID should be numeric, got: {app_id}")
                if not installation_id.isdigit():
                    raise Exception(f"Installation ID should be numeric, got: {installation_id}")
                return {"valid": True, "user": f"App ID {app_id} (PEM format OK, full auth verified on first ETL run)"}

        if ds_type == "claude_api":
            api_key = config.get("apiKey", "")
            if not api_key:
                return None
            # Anthropic Admin API — list workspace members (lightweight, read-only)
            result = _fetch_json(
                "https://api.anthropic.com/v1/organizations/users?limit=1",
                {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            )
            return {"valid": True, "user": "Admin API Key"}

        if ds_type == "gitlab" and auth_type == "pat":
            token = config.get("pat", "")
            url = (config.get("url") or "https://gitlab.com").rstrip("/")
            if not token:
                return None
            result = _fetch_json(f"{url}/api/v4/user", {"PRIVATE-TOKEN": token, "Accept": "application/json"})
            return {"valid": True, "user": result.get("username", "")}

        if ds_type == "asana" and auth_type == "pat":
            token = config.get("pat", "")
            if not token:
                return None
            result = _fetch_json("https://app.asana.com/api/1.0/users/me", {"Authorization": f"Bearer {token}"})
            return {"valid": True, "user": result.get("data", {}).get("email", "")}

        if ds_type == "jira" and auth_type == "pat":
            token = config.get("apiToken", "")
            email = config.get("email", "")
            base_url = (config.get("baseUrl") or "").rstrip("/")
            if not all([token, email, base_url]):
                return None
            import base64
            creds = base64.b64encode(f"{email}:{token}".encode()).decode()
            result = _fetch_json(f"{base_url}/rest/api/3/myself", {"Authorization": f"Basic {creds}"})
            return {"valid": True, "user": result.get("emailAddress", "")}

    except Exception as e:
        log_warning(f"Auth validation failed for {ds_type}: {e}")
        return {"valid": False, "error": "credential validation failed"}

    return None


def handle_datasources(method, path, event, path_params, caller_email=None):
    """Route datasource-related requests. Returns None if not matched."""

    if method == "GET" and path == "/datasources":
        return _list_datasources()

    if method == "POST" and path == "/datasources":
        return _create_datasource(event, caller_email)

    ds_id = path_params.get("dsId")
    if ds_id:
        ds_id = unquote(ds_id)

    if method == "PUT" and path == "/datasources/{dsId}" and ds_id:
        return _update_datasource(event, ds_id, caller_email)

    if method == "POST" and path == "/datasources/{dsId}/oauth-callback" and ds_id:
        return _oauth_callback(event, ds_id, caller_email)

    if method == "POST" and path == "/datasources/{dsId}/test" and ds_id:
        return _test_datasource(ds_id)

    if method == "DELETE" and path == "/datasources/{dsId}" and ds_id:
        return _delete_datasource(ds_id, caller_email)

    return None


def _list_datasources():
    if not datasource_table:
        return respond(200, [])
    all_items = paginated_scan(datasource_table)
    sources = [
        {
            "uuid": ds["uuid"],
            "dataSourceType": ds.get("dataSourceType", ""),
            "name": ds.get("name", ds.get("dataSourceType", "")),
            # OAuth sources (GitLab/Jira/Asana OAuth App) need explicit authorization via callback.
            # GitHub App uses server-to-server JWT — no OAuth needed; always "configured".
            # Detect OAuth sources: authType == 'app' AND config has clientId + clientSecret but NOT privateKey.
            "authStatus": (
                ds.get("config", {}).get("authStatus", "pending")
                if ds.get("config", {}).get("authType") == "app"
                and "clientId" in ds.get("config", {})
                and "privateKey" not in ds.get("config", {})
                else "configured"
            ),
            "config": {
                k: v for k, v in ds.get("config", {}).items()
                if k not in ("pat", "clientSecret", "refreshToken", "apiToken", "privateKey")
            } if isinstance(ds.get("config"), dict) else {},
        }
        for ds in all_items
    ]
    return respond(200, sources)


def _create_datasource(event, caller_email=None):
    if not datasource_table:
        return respond(400, {"message": "DataSource table not configured"})
    import uuid
    body = json.loads(event["body"])
    err = validate_datasource_body(body)
    if err:
        return respond(400, {"message": err})
    if "dataSourceType" not in body:
        return respond(400, {"message": "dataSourceType is required"})
    body = sanitize_dict(body)
    item = {
        "uuid": str(uuid.uuid4()),
        "dataSourceType": body["dataSourceType"],
        "name": body.get("name", body["dataSourceType"]),
        "config": body.get("config", {}),
    }
    validation = validate_datasource_auth(body["dataSourceType"], body.get("config", {}))
    if validation and not validation.get("valid"):
        error_msg = validation.get("error", "unknown error")
        if "Cannot reach server" not in error_msg:
            return respond(400, {"message": "Auth validation failed"})

    # Encrypt sensitive fields before storing
    item["config"] = encrypt_config(item["config"])
    datasource_table.put_item(Item=item)
    audit_log("DATASOURCE_CREATED", caller_email or "unknown", target=item["uuid"], details={"type": item["dataSourceType"], "name": item["name"]})
    return respond(201, {
        "uuid": item["uuid"], "dataSourceType": item["dataSourceType"], "name": item["name"],
        "authValid": validation.get("valid") if validation else None,
        "authUser": validation.get("user") if validation else None,
        "authWarning": "credential validation failed" if validation and not validation.get("valid") else None,
    })


def _update_datasource(event, ds_id, caller_email=None):
    if not datasource_table:
        return respond(400, {"message": "DataSource table not configured"})
    body = json.loads(event["body"])
    err = validate_datasource_body(body)
    if err:
        return respond(400, {"message": err})
    body = sanitize_dict(body)
    existing = datasource_table.get_item(Key={"uuid": ds_id})
    if "Item" not in existing:
        return respond(404, {"message": "Data source not found"})
    item = existing["Item"]
    if "name" in body:
        item["name"] = body["name"]
    if "config" in body:
        validation = validate_datasource_auth(item.get("dataSourceType", ""), body["config"])
        if validation and not validation.get("valid"):
            error_msg = validation.get("error", "unknown error")
            if "Cannot reach server" not in error_msg:
                return respond(400, {"message": "Auth validation failed"})
        # Merge new config into existing, preserving server-managed fields
        # (refreshToken, cachedAccessToken, accessTokenExpiry, authStatus)
        existing_config = item.get("config", {})
        if isinstance(existing_config, dict):
            # Decrypt existing config to get plaintext server-managed fields
            existing_config = decrypt_config(existing_config)
            existing_config.update(body["config"])
            item["config"] = existing_config
        else:
            item["config"] = body["config"]
    # Encrypt sensitive fields before storing
    item["config"] = encrypt_config(item["config"])
    datasource_table.put_item(Item=item)
    audit_log("DATASOURCE_UPDATED", caller_email or "unknown", target=ds_id, details={"type": item.get("dataSourceType", "")})
    return respond(200, {"uuid": item["uuid"], "dataSourceType": item.get("dataSourceType", ""), "name": item.get("name", "")})


def _delete_datasource(ds_id, caller_email=None):
    if not datasource_table:
        return respond(400, {"message": "DataSource table not configured"})
    datasource_table.delete_item(Key={"uuid": ds_id})
    audit_log("DATASOURCE_DELETED", caller_email or "unknown", target=ds_id)
    return respond(200, {"message": "Data source deleted"})


def _test_datasource(ds_id):
    """Test a saved data source's credentials by running validation against stored config."""
    if not datasource_table:
        return respond(400, {"message": "DataSource table not configured"})
    result = datasource_table.get_item(Key={"uuid": ds_id})
    if "Item" not in result:
        return respond(404, {"message": "Data source not found"})
    ds_item = result["Item"]
    ds_type = ds_item.get("dataSourceType", "")
    config = decrypt_config(ds_item.get("config", {}))

    # For OAuth sources, test using the cached access token (or refresh if expired)
    auth_type = config.get("authType", "pat")
    if auth_type == "app" and "clientId" in config and "privateKey" not in config:
        # This is an OAuth source — test with the access token
        access_token = config.get("cachedAccessToken", "")
        if not access_token:
            return respond(200, {"success": False, "message": "No access token cached. Click Authorize to complete the OAuth flow."})
        # Try the cached token
        validation_error = _validate_oauth_token(ds_type, access_token, config)
        if validation_error:
            # Token might be expired — try refreshing
            refresh_token = config.get("refreshToken", "")
            if not refresh_token:
                return respond(200, {"success": False, "message": f"Access token expired and no refresh token stored. Re-authorize the data source."})
            # Attempt refresh
            try:
                new_token = _refresh_oauth_token(ds_type, config)
                if new_token:
                    recheck = _validate_oauth_token(ds_type, new_token, config)
                    if not recheck:
                        return respond(200, {"success": True, "message": "Connection successful (token refreshed)"})
                return respond(200, {"success": False, "message": "Token refresh failed. Re-authorize the data source."})
            except Exception as e:
                return respond(200, {"success": False, "message": f"Token refresh failed: {str(e)[:100]}. Re-authorize the data source."})
        return respond(200, {"success": True, "message": "Connection successful (OAuth token valid)"})

    # For non-OAuth sources, use standard credential validation
    validation = validate_datasource_auth(ds_type, config)
    if validation is None:
        return respond(200, {"success": False, "message": "No validation available for this auth type"})
    if validation.get("valid"):
        return respond(200, {"success": True, "message": f"Connection successful ({validation.get('user', '')})"})
    return respond(200, {"success": False, "message": validation.get("error", "Credential validation failed. Edit the data source and check your credentials.")})


def _oauth_callback(event, ds_id, caller_email=None):
    if not datasource_table:
        return respond(400, {"message": "DataSource table not configured"})
    body = json.loads(event["body"])
    code = body.get("code", "")
    redirect_uri = body.get("redirectUri", "")
    if not code:
        return respond(400, {"message": "code is required"})
    err = validate_url(redirect_uri, "redirectUri")
    if err:
        return respond(400, {"message": err})

    # Validate redirect URI matches the allowed origin (CloudFront domain)
    allowed_origin = os.environ.get("ALLOWED_ORIGIN", "")
    if not allowed_origin:
        return respond(500, {"message": "ALLOWED_ORIGIN not configured"})
    if not redirect_uri.startswith(allowed_origin):
        return respond(400, {"message": "redirectUri must match the application domain"})

    existing = datasource_table.get_item(Key={"uuid": ds_id})
    if "Item" not in existing:
        return respond(404, {"message": "Data source not found"})
    ds_item = existing["Item"]
    config = decrypt_config(ds_item.get("config", {}))
    ds_type = ds_item.get("dataSourceType", "")

    token_url = ""  # nosec B105 — not a password, OAuth endpoint URL
    token_data = {}
    if ds_type == "asana":
        token_url = "https://app.asana.com/-/oauth_token"  # nosec B105
        token_data = {"grant_type": "authorization_code", "client_id": config.get("clientId", ""),
                      "client_secret": config.get("clientSecret", ""), "code": code, "redirect_uri": redirect_uri}
    elif ds_type == "gitlab":
        gitlab_url = (config.get("url") or "https://gitlab.com").rstrip("/")
        token_url = f"{gitlab_url}/oauth/token"
        token_data = {"grant_type": "authorization_code", "client_id": config.get("clientId", ""),
                      "client_secret": config.get("clientSecret", ""), "code": code, "redirect_uri": redirect_uri}
    elif ds_type == "jira":
        token_url = "https://auth.atlassian.com/oauth/token"  # nosec B105
        token_data = {"grant_type": "authorization_code", "client_id": config.get("clientId", ""),
                      "client_secret": config.get("clientSecret", ""), "code": code, "redirect_uri": redirect_uri}

    if not token_url:
        return respond(400, {"message": f"OAuth callback not supported for {ds_type}"})

    try:
        from urllib.parse import urlencode as ue
        if ds_type == "jira":
            req = _Req(token_url, data=json.dumps(token_data).encode(), headers={"Content-Type": "application/json"})
        else:
            req = _Req(token_url, data=ue(token_data).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with _safe_urlopen(req) as resp:
                token_result = json.loads(resp.read().decode())
        except HTTPError as he:
            error_body = ""
            try:
                error_body = he.read().decode()[:500]
            except Exception:  # nosec B110 — best-effort error body read
                error_body = ""
            log_error(f"OAuth token exchange HTTP {he.code} for {ds_type}/{ds_id}: {error_body}")
            return respond(400, {"message": f"OAuth token exchange failed (HTTP {he.code})", "detail": error_body})

        refresh_token = token_result.get("refresh_token", "")
        access_token = token_result.get("access_token", "")
        if not refresh_token:
            return respond(400, {"message": "OAuth provider did not return a refresh token. Authorization not saved."})

        validation_error = _validate_oauth_token(ds_type, access_token, config)
        if validation_error:
            log_warning(f"OAuth token validation failed for {ds_type}/{ds_id}: {validation_error}")
            return respond(400, {"message": "OAuth token validation failed. Authorization not saved."})

        config["refreshToken"] = refresh_token
        config["authStatus"] = "authorized"
        ds_item["config"] = encrypt_config(config)
        datasource_table.put_item(Item=ds_item)
        audit_log("DATASOURCE_OAUTH_AUTHORIZED", caller_email or "unknown", target=ds_id, details={"type": ds_type})
        return respond(200, {"message": "OAuth authorized successfully", "hasRefreshToken": True})
    except Exception as e:
        cid = log_error(f"OAuth token exchange failed for {ds_type}/{ds_id}", exc=e)
        return respond(500, {"message": "OAuth token exchange failed", "correlationId": cid})


def _refresh_oauth_token(ds_type, config):
    """Attempt to refresh an OAuth access token using stored refresh_token. Returns new access token or empty string."""
    from urllib.parse import urlencode
    refresh_token = config.get("refreshToken", "")
    client_id = config.get("clientId", "")
    client_secret = config.get("clientSecret", "")
    if not all([refresh_token, client_id, client_secret]):
        return ""

    token_urls = {
        "gitlab": (config.get("url") or "https://gitlab.com").rstrip("/") + "/oauth/token",
        "jira": "https://auth.atlassian.com/oauth/token",
        "asana": "https://app.asana.com/-/oauth_token",
    }
    token_url = token_urls.get(ds_type, "")
    if not token_url:
        return ""

    token_data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        if "atlassian" in token_url:
            data = json.dumps(token_data).encode()
            content_type = "application/json"
        else:
            data = urlencode(token_data).encode()
            content_type = "application/x-www-form-urlencoded"
        req = _Req(token_url, data=data, headers={"Content-Type": content_type})
        with _safe_urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        return result.get("access_token", "")
    except Exception:
        return ""


def _validate_oauth_token(ds_type, access_token, config):
    """Validate an OAuth access token. Returns error string or None."""
    if not access_token:
        return None
    try:
        if ds_type == "asana":
            req = _Req("https://app.asana.com/api/1.0/users/me", headers={"Authorization": f"Bearer {access_token}"})
            with _safe_urlopen(req) as resp:
                json.loads(resp.read().decode())
        elif ds_type == "gitlab":
            gitlab_url = (config.get("url") or "https://gitlab.com").rstrip("/")
            req = _Req(f"{gitlab_url}/api/v4/user", headers={"Authorization": f"Bearer {access_token}"})
            with _safe_urlopen(req) as resp:
                json.loads(resp.read().decode())
        elif ds_type == "jira":
            req = _Req("https://api.atlassian.com/oauth/token/accessible-resources",
                      headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
            with _safe_urlopen(req) as resp:
                json.loads(resp.read().decode())
    except Exception as ve:
        return str(ve)
    return None
