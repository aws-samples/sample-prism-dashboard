"""codeburn data source plugin (design doc §4.3).

Built-in source (Kiro pattern — no credentials). Reads deduplicated OTLP spans
from AiUsageTable (written by the OTLP receiver) and rolls them into fixed
totals plus dynamic per-provider and per-model keys:

    Fixed:    aiTokensIn, aiTokensOut, aiCost, aiSessions
    Dynamic:  aiTokensIn_<provider>, aiTokensOut_<provider>, aiCost_<provider>
    Model:    aiModelTokensIn_<model>, aiModelTokensOut_<model>, aiModelCost_<model>

Model keys use the aiModel* prefix (NOT aiCost_/aiTokensIn_/aiTokensOut_) so
apply_ai_blending's per-tool prefix sums never double-count them — they are a
parallel dimension of the same spans, always codeburn-estimated (authoritative
provider overrides apply to tool keys only, so model totals may diverge from
blended tool totals).

Dynamic-key guards (ai.provider and ai.model are client-controlled and become
DynamoDB attribute names): sanitize to [a-z0-9-] (providers max 32 chars,
models max 48 with "." mapped to "-"), cap distinct values per user-day at 20
each — anything else buckets into "other". The raw table keeps the original
strings verbatim.
"""

import os
import re
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

from datasources import BaseDataSource, DataSourceType

dynamodb = boto3.resource("dynamodb")
_table_name = os.environ.get("AI_USAGE_TABLE_NAME", "")
ai_usage_table = dynamodb.Table(_table_name) if _table_name else None

_PROVIDER_RE = re.compile(r"[^a-z0-9-]")
_MAX_PROVIDERS_PER_DAY = 20

# Alias normalization only where two provider names are one product (§4.2)
_PROVIDER_ALIASES = {"cursor-agent": "cursor"}


def sanitize_provider(raw: str) -> str:
    """Sanitize a client-controlled provider string for use in attribute names."""
    if not isinstance(raw, str) or not raw:
        return "other"
    p = _PROVIDER_RE.sub("", raw.strip().lower())[:32]
    if not p:
        return "other"
    return _PROVIDER_ALIASES.get(p, p)


def sanitize_model(raw: str) -> str:
    """Sanitize a client-controlled model string for use in attribute names.

    Version dots become dashes so common names stay readable
    ("gpt-4.1" -> "gpt-4-1", "claude-opus-4.6" -> "claude-opus-4-6").
    """
    if not isinstance(raw, str) or not raw:
        return "other"
    m = _PROVIDER_RE.sub("", raw.strip().lower().replace(".", "-"))[:48]
    return m or "other"


def _day_nano_bounds(date_str: str):
    """Return (startNano, endNano) strings for a UTC date."""
    from datetime import datetime, timezone, timedelta
    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start = int(day.timestamp()) * 1_000_000_000
    end = int((day + timedelta(days=1)).timestamp()) * 1_000_000_000 - 1
    return str(start), str(end)


class CodeburnDataSource(BaseDataSource):
    source_type = DataSourceType.CODEBURN

    def fetch_activity(self, date_str: str) -> dict:
        if ai_usage_table is None:
            return {}
        start_nano, end_nano = _day_nano_bounds(date_str)
        try:
            items = []
            kwargs = {
                "KeyConditionExpression": Key("userId").eq(self.user.get("userId") or self.email)
                & Key("sk").between(f"SPAN#{start_nano}", f"SPAN#{end_nano}#zzzz"),
            }
            while True:
                result = ai_usage_table.query(**kwargs)
                items.extend(result.get("Items", []))
                lek = result.get("LastEvaluatedKey")
                if not lek:
                    break
                kwargs["ExclusiveStartKey"] = lek
        except Exception as e:
            print(f"Error fetching codeburn spans for {self.email} on {date_str}: {e}")
            return {}

        if not items:
            return {}

        per_provider = {}
        per_model = {}
        sessions = set()
        for item in items:
            provider = sanitize_provider(item.get("tool", ""))
            if provider not in per_provider and len(per_provider) >= _MAX_PROVIDERS_PER_DAY:
                provider = "other"  # cardinality cap overflow
            bucket = per_provider.setdefault(
                provider, {"tokensIn": 0, "tokensOut": 0, "cost": 0.0}
            )
            tokens_in = int(item.get("inputTokens", 0) or 0)
            tokens_out = int(item.get("outputTokens", 0) or 0)
            try:
                cost = float(item.get("costUsd", 0) or 0)
            except (TypeError, ValueError):
                cost = 0.0
            bucket["tokensIn"] += tokens_in
            bucket["tokensOut"] += tokens_out
            bucket["cost"] += cost

            model = sanitize_model(item.get("model", ""))
            if model not in per_model and len(per_model) >= _MAX_PROVIDERS_PER_DAY:
                model = "other"  # cardinality cap overflow
            mbucket = per_model.setdefault(
                model, {"tokensIn": 0, "tokensOut": 0, "cost": 0.0}
            )
            mbucket["tokensIn"] += tokens_in
            mbucket["tokensOut"] += tokens_out
            mbucket["cost"] += cost

            trace = item.get("sessionTraceId", "")
            if trace:
                sessions.add(trace)

        return {"perProvider": per_provider, "perModel": per_model, "sessions": len(sessions)}

    def get_aggregation_fields(self, activity: dict) -> dict:
        per_provider = activity.get("perProvider", {})
        fields = {
            "aiTokensIn": Decimal(0),
            "aiTokensOut": Decimal(0),
            "aiCost": Decimal(0),
            "aiSessions": Decimal(str(activity.get("sessions", 0))),
        }
        for provider, bucket in per_provider.items():
            tin = Decimal(str(bucket["tokensIn"]))
            tout = Decimal(str(bucket["tokensOut"]))
            cost = Decimal(str(round(bucket["cost"], 6)))
            fields[f"aiTokensIn_{provider}"] = tin
            fields[f"aiTokensOut_{provider}"] = tout
            fields[f"aiCost_{provider}"] = cost
            fields[f"aiSource_{provider}"] = "estimated"
            fields["aiTokensIn"] += tin
            fields["aiTokensOut"] += tout
            fields["aiCost"] += cost
        # Model dimension — parallel rollup of the same spans, deliberately NOT
        # added to the fixed totals (the provider loop above already counted
        # them) and NOT using the aiCost_/aiTokensIn_/aiTokensOut_ prefixes
        # that apply_ai_blending sums per tool.
        for model, bucket in activity.get("perModel", {}).items():
            fields[f"aiModelTokensIn_{model}"] = Decimal(str(bucket["tokensIn"]))
            fields[f"aiModelTokensOut_{model}"] = Decimal(str(bucket["tokensOut"]))
            fields[f"aiModelCost_{model}"] = Decimal(str(round(bucket["cost"], 6)))
        return fields
