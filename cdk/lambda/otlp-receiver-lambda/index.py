"""OTLP receiver for codeburn sync (design doc §4.1/§4.2).

POST /v1/traces — OTLP/HTTP JSON only. The API Gateway Cognito authorizer has
already validated the JWT; the caller identity comes from the token's email
claim. Flow (order matters): identify caller → parse OTLP → archive raw batch
to S3 → conditional per-span writes to DynamoDB.

Response contract (verified against codeburn src/sync/push.ts):
- Full success  → 200 {}                                  (codeburn ledgers batch)
- Partial       → 200 {"partialSuccess":{"rejectedSpans":N}}  (codeburn re-sends whole batch)
- Oversized/bad → 400 (codeburn treats as server error, retries next push)
"""

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

TABLE_NAME = os.environ["AI_USAGE_TABLE_NAME"]
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
ARCHIVE_BUCKET = os.environ["ARCHIVE_BUCKET_NAME"]
SPAN_TTL_DAYS = int(os.environ.get("SPAN_TTL_DAYS", "90"))
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", "1048576"))
MAX_SPANS_PER_BATCH = int(os.environ.get("MAX_SPANS_PER_BATCH", "1000"))

table = dynamodb.Table(TABLE_NAME)
USER_TABLE_NAME = os.environ.get("USER_TABLE_NAME", "")
user_table = dynamodb.Table(USER_TABLE_NAME) if USER_TABLE_NAME else None

# Attribute value sanitization: strip control chars, cap length.
_MAX_ATTR_LEN = 512
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_HEX_RE = re.compile(r"^[0-9a-f]{16,32}$")


def _respond(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _clean_str(value, max_len=_MAX_ATTR_LEN):
    if not isinstance(value, str):
        return ""
    return _CONTROL_RE.sub("", value)[:max_len]


def _attr_map(attributes):
    """Flatten OTLP attribute list into a dict of python values."""
    out = {}
    for attr in attributes or []:
        key = attr.get("key")
        val = attr.get("value") or {}
        if not isinstance(key, str):
            continue
        if "stringValue" in val:
            out[key] = _clean_str(val["stringValue"])
        elif "intValue" in val:
            # proto3 int64 JSON mapping: arrives as a string
            try:
                out[key] = int(val["intValue"])
            except (TypeError, ValueError):
                out[key] = 0
        elif "doubleValue" in val:
            try:
                out[key] = float(val["doubleValue"])
            except (TypeError, ValueError):
                out[key] = 0.0
        elif "boolValue" in val:
            out[key] = bool(val["boolValue"])
        elif "arrayValue" in val:
            values = (val["arrayValue"] or {}).get("values") or []
            out[key] = [_clean_str(v.get("stringValue", "")) for v in values if isinstance(v, dict)][:32]
    return out


_cognito = boto3.client("cognito-idp")
_email_cache = {}
_userid_cache = {}


def _resolve_user_id(email):
    """Resolve the JWT email to the platform's stable internal userId.

    Spans are keyed by userId (not email) so an email change never orphans
    usage history. Returns None when the user has no platform record —
    the batch is then rejected (raw payload stays in the S3 archive for
    replay after registration)."""
    if email in _userid_cache:
        return _userid_cache[email]
    if not user_table:
        return email  # table not wired (legacy/test) — fall back to email keys
    try:
        item = user_table.get_item(Key={"email": email}).get("Item")
    except ClientError as e:
        print(f"User lookup failed for span attribution: {e.response['Error']['Code']}")
        return None
    if not item:
        return None
    uid = item.get("userId") or email
    _userid_cache[email] = uid
    return uid


def _extract_email(event):
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    # ID tokens carry `email` directly.
    for key in ("email", "username", "cognito:username"):
        value = claims.get(key, "")
        if isinstance(value, str) and "@" in value:
            return value.strip().lower()
    # Access tokens carry `username` = the pool-internal UUID (email-as-username
    # pools generate a UUID username). Resolve to email via AdminGetUser, cached
    # across warm invocations.
    username = claims.get("username") or claims.get("sub") or ""
    if not username or not USER_POOL_ID:
        return None
    if username in _email_cache:
        return _email_cache[username]
    try:
        resp = _cognito.admin_get_user(UserPoolId=USER_POOL_ID, Username=username)
        email = next((a["Value"] for a in resp.get("UserAttributes", [])
                      if a["Name"] == "email"), "")
        if email and "@" in email:
            email = email.strip().lower()
            _email_cache[username] = email
            return email
    except ClientError as e:
        print(f"AdminGetUser failed for {username}: {e.response['Error']['Code']}")
    return None


def _span_date(start_nano):
    """UTC date (YYYY-MM-DD) for a unix-nano timestamp string."""
    try:
        seconds = int(start_nano) / 1_000_000_000
        return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def handler(event, _context):
    email = _extract_email(event)
    if not email:
        return _respond(401, {"message": "No email claim in token"})
    user_id = _resolve_user_id(email)
    if not user_id:
        return _respond(403, {"message": (
            "User not registered on the platform. Ask an admin to register "
            "your email, then re-push (codeburn sync reset --confirm && "
            "codeburn sync push --since all).")})

    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64
        try:
            body = base64.b64decode(body).decode("utf-8")
        except Exception:
            return _respond(400, {"message": "Invalid body encoding"})

    if len(body.encode("utf-8", errors="ignore")) > MAX_BODY_BYTES:
        return _respond(400, {"message": "Batch too large"})

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return _respond(400, {"message": "Invalid JSON"})

    resource_spans = payload.get("resourceSpans")
    if not isinstance(resource_spans, list):
        return _respond(400, {"message": "Missing resourceSpans"})

    # --- Archive raw batch to S3 BEFORE DDB writes (external contract) ---
    now = datetime.now(tz=timezone.utc)
    archive_key = (
        f"otlp/dt={now.strftime('%Y-%m-%d')}/"
        f"{now.strftime('%H%M%S')}-{uuid.uuid4().hex[:12]}.json"
    )
    try:
        s3.put_object(
            Bucket=ARCHIVE_BUCKET,
            Key=archive_key,
            Body=json.dumps({"userId": user_id, "email": email, "receivedAt": now.isoformat(), "payload": payload}),
            ContentType="application/json",
        )
    except ClientError as e:
        print(f"Archive write failed: {e.response['Error']['Code']}")
        return _respond(500, {"message": "Archive failed"})

    # --- Per-span conditional writes ---
    ttl = int(time.time()) + SPAN_TTL_DAYS * 86400
    accepted = 0
    rejected = 0
    duplicates = 0
    total = 0

    for rs in resource_spans:
        if not isinstance(rs, dict):
            continue
        device_id = ""
        for attr in ((rs.get("resource") or {}).get("attributes") or []):
            if attr.get("key") == "codeburn.device_id":
                device_id = _clean_str((attr.get("value") or {}).get("stringValue", ""), 64)

        for ss in rs.get("scopeSpans") or []:
            if not isinstance(ss, dict):
                continue
            for span in ss.get("spans") or []:
                if not isinstance(span, dict):
                    continue
                total += 1
                if total > MAX_SPANS_PER_BATCH:
                    rejected += 1
                    continue

                span_id = _clean_str(span.get("spanId", ""), 32).lower()
                trace_id = _clean_str(span.get("traceId", ""), 64).lower()
                start_nano = str(span.get("startTimeUnixNano", ""))
                date = _span_date(start_nano)

                if not _HEX_RE.match(span_id) or not date:
                    rejected += 1
                    continue

                attrs = _attr_map(span.get("attributes"))
                provider = _clean_str(str(attrs.get("ai.provider", "")), 64)
                if not provider:
                    rejected += 1
                    continue

                item = {
                    "userId": user_id,
                    "sk": f"SPAN#{start_nano}#{span_id}",
                    "date": date,
                    "tool": provider,
                    "model": _clean_str(str(attrs.get("ai.model", "")), 128),
                    "inputTokens": int(attrs.get("ai.input_tokens", 0) or 0),
                    "outputTokens": int(attrs.get("ai.output_tokens", 0) or 0),
                    # DynamoDB requires Decimal for floats; store cost as string cents-safe decimal
                    "costUsd": str(attrs.get("ai.cost_usd", 0) or 0),
                    "costEstimated": bool(attrs.get("ai.cost_estimated", False)),
                    "project": _clean_str(str(attrs.get("ai.project", "")), 256),
                    "speed": _clean_str(str(attrs.get("ai.speed", "")), 32),
                    "sessionTraceId": trace_id if _HEX_RE.match(trace_id) else "",
                    "deviceId": device_id,
                    "ttl": ttl,
                }
                tools_used = attrs.get("ai.tools")
                if isinstance(tools_used, list) and tools_used:
                    item["toolsUsed"] = tools_used

                try:
                    table.put_item(
                        Item=item,
                        ConditionExpression="attribute_not_exists(sk)",
                    )
                    accepted += 1
                except ClientError as e:
                    if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                        duplicates += 1  # deterministic span IDs: duplicate = already stored
                    else:
                        print(f"Span write failed: {e.response['Error']['Code']}")
                        rejected += 1

    print(json.dumps({
        "userId": user_id, "total": total, "accepted": accepted,
        "duplicates": duplicates, "rejected": rejected, "archiveKey": archive_key,
    }))

    if rejected > 0:
        # codeburn will NOT ledger and re-sends the whole batch next push;
        # conditional writes make the retry idempotent.
        return _respond(200, {"partialSuccess": {"rejectedSpans": rejected}})
    return _respond(200, {})
