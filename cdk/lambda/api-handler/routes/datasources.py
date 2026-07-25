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
            "authStatus": ds.get("config", {}).get("authStatus", "pending") if ds.get("config", {}).get("authType") == "app" else "configured",
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
