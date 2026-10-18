"""LLM client for AI decision making. Calls OpenAI-compatible chat completion API."""
import json
import logging
import time
from decimal import Decimal

import httpx

from .schemas import ModelDecision
from .security import decrypt

log = logging.getLogger('quantdesk.llm')

D = Decimal

# JSON Schema for structured output
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["hold", "order", "strategy"]},
        "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
        "order": {
            "type": ["object", "null"],
            "properties": {
                "market": {"type": "string", "enum": ["us", "cn", "crypto", "poly"]},
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "quantity": {"type": "string"},
                "kind": {"type": "string", "enum": ["market", "limit"]},
                "limit_price": {"type": ["string", "null"]},
                "snapshot_id": {"type": "integer"},
                "strategy_id": {"type": ["integer", "null"]},
            },
            "required": ["market", "symbol", "side", "quantity", "snapshot_id"],
        },
        "strategy": {
            "type": ["object", "null"],
            "properties": {
                "name": {"type": "string"},
                "market": {"type": "string", "enum": ["us", "cn", "crypto", "poly"]},
                "symbols": {"type": "array", "items": {"type": "string"}},
                "template": {"type": "string", "enum": ["trend", "mean_reversion", "probability"]},
                "lookback": {"type": "integer", "minimum": 5, "maximum": 200},
                "threshold": {"type": "number", "minimum": 0.1, "maximum": 50},
                "allocation_pct": {"type": "number", "minimum": 0.1, "maximum": 25},
            },
            "required": ["name", "market", "symbols", "template"],
        },
    },
    "required": ["action", "reason"],
}

SYSTEM_PROMPT = """You are a quantitative trading AI for a paper-trading simulation workbench.
You analyze market data, positions, and risk constraints to make trading decisions.
Rules:
- You can only output: "hold" (no action), "order" (submit a trade), or "strategy" (propose a new strategy).
- All trades are SIMULATED (paper money). No real money is at risk.
- Respect all risk limits and position constraints provided in the context.
- Provide a clear, structured reason for every decision.
- For orders: choose realistic quantities within the allocation_pct and max_order_pct limits.
- For strategies: only propose when you identify a clear pattern opportunity.
- Default to "hold" when uncertain or when risk limits are tight.
- Output valid JSON matching the schema exactly.
"""


def build_prompt(market, snapshots, positions, config, strategies):
    """Build the decision prompt with current market context."""
    whitelist = config.get('whitelist', {}).get(market, [])
    risk = config.get('risk', {}).get(market, {})

    # Format snapshots
    snap_lines = []
    for snap in snapshots:
        data = snap.data or {}
        snap_lines.append(
            f"  {snap.symbol}: price={data.get('price')}, bid={data.get('bid')}, ask={data.get('ask')}, "
            f"size={data.get('size')}, quality={snap.quality if hasattr(snap, 'quality') else data.get('quality', 'real')}, "
            f"event_time={snap.event_time.isoformat() if snap.event_time else 'N/A'}"
        )

    # Format positions
    pos_lines = []
    for p in positions:
        pos_lines.append(
            f"  {p.symbol}: qty={p.quantity}, cost={p.cost}, realized={p.realized}"
        )

    # Format active strategies
    strat_lines = []
    for s in strategies:
        strat_lines.append(f"  {s.name} v{s.version} [{s.status}]")

    prompt = f"""Current market context for {market.upper()}:

## Available Snapshots (latest per symbol)
{chr(10).join(snap_lines) if snap_lines else '  (none available)'}

## Current Positions
{chr(10).join(pos_lines) if pos_lines else '  (no positions)'}

## Active Strategies
{chr(10).join(strat_lines) if strat_lines else '  (none active)'}

## Risk Limits for {market}
- max_order_pct: {risk.get('max_order_pct', 10)}%
- max_position_pct: {risk.get('max_position_pct', 30)}%
- max_exposure_pct: {risk.get('max_exposure_pct', 80)}%
- max_daily_loss_pct: {risk.get('max_daily_loss_pct', 5)}%
- max_drawdown_pct: {risk.get('max_drawdown_pct', 10)}%
- fee_bps: {risk.get('fee_bps', 10)}
- slippage_bps: {risk.get('slippage_bps', 5)}

## Whitelist
{', '.join(whitelist)}

## Instructions
Based on the above data, output your decision as JSON.
If placing an order, use the snapshot_id from the snapshot you're acting on.
Choose quantities that respect the risk limits above.
"""
    return prompt


def call_model(config, secrets, market, prompt, timeout=30):
    """Call the LLM API and return (decision_dict, tokens_used, cost_estimate).

    Returns None on any error (timeout, bad response, network failure).
    Never raises — failures are logged and the caller should default to 'hold'.
    """
    model_cfg = config.get('model', {})
    base_url = model_cfg.get('base_url', 'https://api.openai.com/v1').rstrip('/')
    model_name = model_cfg.get('model', '')
    max_output = model_cfg.get('max_output_tokens', 1000)

    if not model_name:
        log.warning('no model configured, skipping LLM call')
        return None

    # Get API key from secrets
    api_key = ''
    if secrets:
        try:
            decrypted = decrypt(secrets)
            api_key = decrypted.get('model_key', '')
        except Exception:
            log.warning('failed to decrypt model credentials')
            return None

    if not api_key:
        log.warning('no model_key in secrets, skipping LLM call')
        return None

    url = f'{base_url}/chat/completions'
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    payload = {
        'model': model_name,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt},
        ],
        'max_tokens': max_output,
        'temperature': 0.2,
        'response_format': {
            'type': 'json_schema',
            'json_schema': {
                'name': 'decision',
                'schema': DECISION_SCHEMA,
                'strict': True,
            },
        },
    }

    start = time.time()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        log.warning(f'LLM call timed out after {timeout}s')
        return None
    except httpx.HTTPStatusError as e:
        log.warning(f'LLM API error: {e.response.status_code}')
        return None
    except Exception as e:
        log.warning(f'LLM call failed: {type(e).__name__}: {e}')
        return None

    elapsed = time.time() - start
    log.info(f'LLM call completed in {elapsed:.1f}s')

    # Parse response
    try:
        choice = data['choices'][0]
        content = choice['message']['content']
        result = json.loads(content)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        log.warning(f'failed to parse LLM response: {e}')
        return None

    # Token usage
    usage = data.get('usage', {})
    tokens = usage.get('total_tokens', 0)
    input_tokens = usage.get('prompt_tokens', 0)
    output_tokens = usage.get('completion_tokens', 0)

    # Cost estimate
    input_rate = D(str(model_cfg.get('input_cost_million', 0) or 0))
    output_rate = D(str(model_cfg.get('output_cost_million', 0) or 0))
    cost = input_rate * D(str(input_tokens)) / D('1000000') + output_rate * D(str(output_tokens)) / D('1000000')

    return result, tokens, cost


def make_decision(config, secrets, market, snapshots, positions, strategies):
    """High-level: build prompt, call model, validate output.

    Returns (ModelDecision | None, tokens, cost).
    On any failure, returns (None, 0, 0).
    """
    prompt = build_prompt(market, snapshots, positions, config, strategies)
    result = call_model(config, secrets, market, prompt)

    if result is None:
        return None, 0, D(0)

    decision_dict, tokens, cost = result

    try:
        decision = ModelDecision.model_validate(decision_dict)
        return decision, tokens, cost
    except Exception as e:
        log.warning(f'LLM output failed schema validation: {e}')
        return None, tokens, cost
