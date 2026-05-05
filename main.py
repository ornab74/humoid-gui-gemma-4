
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np
import pandas as pd

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except Exception as exc:
    raise RuntimeError("Tkinter is required.") from exc

try:
    import customtkinter as ctk
except Exception as exc:
    raise RuntimeError("customtkinter is required.") from exc

try:
    import jwt
except Exception:
    jwt = None

try:
    import bleach
except Exception:
    bleach = None

try:
    import psutil
except Exception:
    psutil = None

try:
    import pennylane as qml
except Exception:
    qml = None

try:
    import litert_lm
except Exception:
    litert_lm = None

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except Exception as exc:
    raise RuntimeError("cryptography is required.") from exc


APP_NAME = "Entropic Coinbase Quantum Intelligence"
SETTINGS_PATH = Path("main_eth_perp_quantum_settings.json")
PRODUCT_CACHE_DB_PATH = Path("coinbase_product_cache.sqlite3")
MODEL_REVISION = "7fa1d78473894f7e736a21d920c3aa80f950c0db"
MODEL_REPO = f"https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm/resolve/{MODEL_REVISION}/"
MODEL_FILE = "gemma-4-E2B-it.litertlm"
EXPECTED_MODEL_SHA256 = "ab7838cdfc8f77e54d8ca45eadceb20452d9f01e4bfade03e5dce27911b27e42"
MODELS_DIR = Path("models")
LITERT_CACHE_DIR = Path(".litert_lm_cache")
DEFAULT_MODEL_PATH = str(MODELS_DIR / MODEL_FILE)
# Gemma 4 LiteRT community builds commonly expose a 4096-token text context.
# Keep input safely below that boundary so market packets do not crash generation.
LITERT_CONTEXT_TOKENS = 4096
LITERT_SAFE_INPUT_TOKENS = 1700
LITERT_SAFE_OUTPUT_TOKENS = 768
LITERT_REPLY_CHUNK_TOKENS = 512
LITERT_MAX_REPLY_CHUNKS = 6
LITERT_OVERLAP_CHARS = 950
LITERT_CONTINUE_MARKER = "[[CONTINUE]]"
LITERT_CHAR_BUDGET = 3600
# Keep heavy LiteRT inference outside Tkinter's process. Some LiteRT wheels
# hold the GIL during generation, which makes Tkinter look frozen even when
# called from a Python thread.
LITERT_SUBPROCESS_TIMEOUT_SECONDS = 240
LITERT_WORKER_FLAG = "--litert-text-worker"
COINBASE_EXCHANGE_CANDLES = "https://api.exchange.coinbase.com/products/{product_id}/candles"
COINBASE_PUBLIC_ADVANCED_CANDLES = "https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}/candles"
COINBASE_AUTH_ADVANCED_CANDLES = "https://api.coinbase.com/api/v3/brokerage/products/{product_id}/candles"
COINBASE_AUTH_ADVANCED_TICKER = "https://api.coinbase.com/api/v3/brokerage/products/{product_id}/ticker"
COINBASE_PUBLIC_ADVANCED_TICKER = "https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}/ticker"
COINBASE_AUTH_ADVANCED_PRODUCT = "https://api.coinbase.com/api/v3/brokerage/products/{product_id}"
COINBASE_PUBLIC_ADVANCED_PRODUCT = "https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}"
COINBASE_AUTH_ADVANCED_PRODUCT_BOOK = "https://api.coinbase.com/api/v3/brokerage/product_book"
COINBASE_PUBLIC_ADVANCED_PRODUCT_BOOK = "https://api.coinbase.com/api/v3/brokerage/market/product_book"
COINBASE_AUTH_ADVANCED_BEST_BID_ASK = "https://api.coinbase.com/api/v3/brokerage/best_bid_ask"
COINBASE_PUBLIC_ADVANCED_BEST_BID_ASK = "https://api.coinbase.com/api/v3/brokerage/market/best_bid_ask"
COINBASE_PUBLIC_PRODUCTS = "https://api.coinbase.com/api/v3/brokerage/market/products"
COINBASE_AUTH_PRODUCTS_PATH = "/products"
COINBASE_ADVANCED_BASE = "https://api.coinbase.com/api/v3/brokerage"
COINBASE_INTX_CANDLES = "https://api.international.coinbase.com/api/v1/instruments/{instrument}/candles"
NETWORK_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=10.0)
EASTERN_TZ = "US/Eastern"
EMA_SPANS = (8, 13, 21, 34, 55, 89, 144, 233)
PRIMARY_EMA_SPANS = (8, 13, 21, 34, 55, 89)
MAX_IMAGE_BYTES = 20 * 1024 * 1024
ALLOWED_NATIVE_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SAFE_PRODUCT_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{1,80}$")
ADVANCED_GRANULARITY_MAP = {
    1: "ONE_MINUTE",
    5: "FIVE_MINUTE",
    15: "FIFTEEN_MINUTE",
    30: "THIRTY_MINUTE",
    60: "ONE_HOUR",
    120: "TWO_HOUR",
    240: "FOUR_HOUR",
    360: "SIX_HOUR",
    1440: "ONE_DAY",
}
INTX_GRANULARITY_MAP = {
    1: "ONE_MINUTE",
    5: "FIVE_MINUTE",
    15: "FIFTEEN_MINUTE",
    30: "THIRTY_MINUTE",
    60: "ONE_HOUR",
    120: "TWO_HOUR",
    360: "SIX_HOUR",
    1440: "ONE_DAY",
}

TIMEFRAME_BUTTONS: Tuple[Tuple[str, int], ...] = (
    ("1m", 1), ("5m", 5), ("15m", 15), ("30m", 30),
    ("1h", 60), ("2h", 120), ("4h", 240), ("6h", 360),
    ("12h", 720), ("1D", 1440), ("1W", 10080), ("1M", 43200),
)
SUPPORTED_TIMEFRAME_MINUTES = tuple(minutes for _, minutes in TIMEFRAME_BUTTONS)
RESAMPLE_TIMEFRAME_MAP: Dict[int, Tuple[int, str, int]] = {
    # Some Coinbase products expose sparse or inconsistent candles for non-core granularities.
    # Build these from reliable smaller candles so chart buttons do not blank out.
    30: (15, "30min", 2),
    120: (60, "2h", 2),
    240: (60, "4h", 4),
    720: (360, "12h", 2),
    10080: (1440, "W", 7),
    43200: (1440, "ME", 31),
}
TIMEFRAME_LABEL_BY_MINUTE: Dict[int, str] = {minutes: label for label, minutes in TIMEFRAME_BUTTONS}
LEGACY_SECRET_AAD = b"coinbase-secret-bundle-v4"
VAULT_SECRET_AAD = b"coinbase-secret-bundle-v5"
VAULT_MASTER_KEY_AAD = b"coinbase-secret-master-key-v2"
WRAPPED_KEY_PBKDF2_ITERATIONS = 350_000
MIN_VAULT_PASSWORD_LENGTH = 10

MODELS_DIR.mkdir(parents=True, exist_ok=True)
LITERT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")

THEME = {
    "window": "#030406",
    "canvas": "#06080c",
    "panel": "#0b1119",
    "card": "#111a26",
    "card_soft": "#152131",
    "line": "#233246",
    "line_soft": "#182231",
    "text": "#f5f7fb",
    "muted": "#93a3bb",
    "accent": "#ffd60a",
    "accent_2": "#9ad7ff",
    "accent_3": "#89f0d2",
    # Dark gold keeps the selected tab readable with white text.
    "tab_selected": "#5a4700",
    "tab_selected_hover": "#735b00",
    "warning": "#ffcf66",
    "danger": "#ff657d",
    "success": "#8dffc7",
    "candle_up": "#ffd60a",
    "candle_down": "#ffffff",
    "ema_fast": "#ffe45c",
    "ema_fast2": "#ffef8e",
    "ema_mid": "#fff7cc",
    "ema_slow": "#cfd6ff",
    "ema_slow2": "#a7b8ff",
    "basis": "#8ad4ff",
    "flow": "#89f0d2",
    "flow_2": "#6dc9ff",
    "entropy": "#ffef8e",
    "quantum": "#f6f3ff",
    "risk": "#ff8fa3",
}

DEFAULT_PROMPT_PACK: Dict[str, Any] = {
    "system": """You are Gemma 4 inside an advanced ETH-PERP derivatives intelligence cockpit.

Your job is not to hype, soothe, or pretend certainty.
You are a bounded market-structure, derivatives, and execution-architecture analyst.

Always separate:
- evidence directly present in the packet
- inference from that evidence
- contradictions that weaken the packet
- risk or ambiguity that blocks action
- explicit reasons to stay flat

Primary lenses:
- multi-timeframe trend and EMA ribbon structure
- perp-vs-spot basis and basis z-state
- crowding and forced-flow proxies
- liquidation ladder behavior
- volatility compression and expansion
- inventory imbalance and passive absorption
- entropic drift and shock transition
- RGB / Pennylane auxiliary sensor surface
- retrieved analog memory states from this session only
- product identity, contract expiry, and instrument eligibility
- candle freshness, endpoint quality, and reference-market validity

Hard rules:
- Never promise profit.
- Never imply certainty.
- Never confuse a proxy with ground truth.
- If the packet is weak, say to stay flat.
- If the packet is contradictory, say exactly what is contradictory.
- If the market is too entropic for monetization, say the regime should not be traded.
- Treat maker and carry ideas as conditional research rails, not automatic trades.
- For oil, commodity, or expiring futures products, verify contract identity, expiry, liquidity, and data quality before discussing direction.
- If product identity, reference market, candle freshness, or liquidity quality is uncertain, downgrade confidence or return no-view.

Required output:
1. Thesis
2. Long case
3. Short case
4. Flat / wait case
5. Income-generation research rail
6. Invalidations
7. Risk box
8. Confidence
9. What would most likely make you change your mind""",
    "vision_system": """You are Gemma 4 visual derivatives analyst mode.

Read the chart image like a professional ETH-PERP discretionary trader.
Explicitly comment on:
- sweeps
- failed breaks
- EMA ribbon inflections
- compression or expansion
- possible ladder magnets
- absorption vs impulsive aggression
- basis or regime contradictions between image and text packet
- whether the visual evidence confirms or weakens the model packet

Do not exaggerate confidence.""",
    "debate_request": """Run an internal five-lens debate and reveal only the synthesis.
Lens A: trend continuation.
Lens B: basis stretch / mean reversion.
Lens C: liquidation sweep.
Lens D: convexity breakout.
Lens E: passive-income / maker rail.
Return the best stance and the strongest reason to stay flat.""",
    "execution_request": """Convert the packet into an execution architecture:
- trigger
- invalidation
- size logic
- scale ladder
- hedge idea
- no-trade criteria
- cancel conditions""",
    "risk_officer_request": """Audit for:
- crowding
- hidden convexity
- fragile basis
- entropy shock
- overfit logic
- false precision
- thin evidence
- reasons to reduce size or stay flat""",
    "theory_notes": {
        "COHERENCE_FIELD": "Alignment across trend, structure, and multiple timeframes.",
        "LIQUIDITY_SWEEP": "Stop-run and reclaim/reject behavior.",
        "COMPRESSION_BREAKOUT": "Stored range energy and expansion probability.",
        "REFLEXIVE_ACCELERATION": "Trend + momentum + participation feedback.",
        "ENTROPIC_DRIFT": "Low-entropy directional glide versus shock transition.",
        "FUNDING_PRESSURE_TENSOR": "Crowded carry / directional crowding proxy using basis behavior.",
        "BASIS_DISLOCATION_PULSE": "Perp-vs-spot stretch and reflexive unwind pressure.",
        "LIQUIDATION_LADDER_MAGNETISM": "Forced-flow pull into obvious stop zones.",
        "INVENTORY_IMBALANCE_RESONANCE": "Aggressor/passive imbalance and absorption tension.",
        "CONVEXITY_TRAP_GRADIENT": "Compression + crowding + asymmetry creating trap-prone breakouts.",
        "TERM_STRUCTURE_SHEAR": "Lower-timeframe and higher-timeframe disagreement.",
        "MAKER_ABSORPTION_EDGE": "Whether passive liquidity is absorbing without immediate failure.",
        "FUNDING_REFLEX_INVERSION": "Risk that crowded carry flips into reversal pressure.",
        "ENTROPIC_CARRY_HARVEST": "Bounded participation in orderly regimes where carry-like behavior may be monetizable.",
        "REGIME_HANDOFF_ARBITRAGE": "Transition edge when lower-timeframe impulse tries to hand off into higher-timeframe acceptance."
    },
    "quick_prompts": {
        "Full Research": "Produce a full ETH-PERP research memo from the current packet.",
        "Basis Map": "Focus on perp-vs-spot basis, crowding, and mean-reversion risk.",
        "Ladder Sweep": "Map liquidation ladders, stop pools, and the best sweep path.",
        "Income Rail": "Design a bounded income-generation research rail with explicit no-trade conditions.",
        "Risk Audit": "Audit the packet like a paranoid derivatives risk officer.",
        "Contrarian View": "Argue the strongest contrarian case against the dominant signal.",
        "Entropy Map": "Explain whether the regime is monetizable or too entropic.",
        "Long vs Short": "Give the strongest long and strongest short case side by side.",
        "Maker Rail": "Propose a selective maker rail only if passive quoting is defensible.",
        "Futures Roll Map": "Map the contract expiry, basis, roll risk, liquidity state, and safest comparison market for the selected futures product.",
        "Oil Futures Read": "Analyze the selected oil futures product using contract structure, volume/liquidity, trend, volatility, and invalidation-first risk framing.",
        "Regime Classifier": "Classify the current market into one regime, explain why, list contradictions, and provide a no-trade trigger.",
        "Data Quality Audit": "Audit the packet for stale candles, missing products, bad symbols, liquidity gaps, and source disagreements before producing any market view.",
        "Decision Gate": "Return PASS, WATCH, or REJECT using evidence, contradictions, invalidations, liquidity, and execution risk.",
        "Stay Flat": "Make the best disciplined case for staying flat."
    },
    "prompt_library": {
        "CORE_SYSTEM_PROMPTS": {
            "ORACLE_SYNTHESIS_SYSTEM": """You are the synthesis engine for an ETH-PERP derivatives cockpit.
Turn live packet data into a bounded decision memo.
Never hide contradictions.
If evidence is thin, downgrade confidence aggressively.
Prefer disciplined waiting over forced action.""",
            "RISK_GOVERNOR_SYSTEM": """You are the internal risk governor.
Your mission is to shrink size, remove false certainty, and stop the system from confusing pattern recognition with durable edge.
Every recommendation must include a reason it could fail.""",
            "INCOME_GENERATION_SYSTEM": """You are the income-generation research layer for ETH-PERP.
You are allowed to think in terms of carry, maker execution, volatility harvesting, regime handoff, and bounded directional overlays.
You are not allowed to imply guaranteed yield.
You must define the regime where the rail should be inactive.""",
            "VISUAL_CONFIRMATION_SYSTEM": """You are the visual confirmation layer.
Treat the chart as evidence.
Compare the image to the structured packet and identify what the image confirms, weakens, or directly contradicts.""",
            "QUANTUM_SENSOR_INTERPRETER": """You interpret the RGB / Pennylane / entropic surface only as auxiliary context.
Use it to gate confidence and detect stress or coherence.
Never let it overpower direct market structure."""
        },
        "ADVANCED_MARKET_RESEARCH_PROMPTS": {
            "MULTI_TIMEFRAME_DECISION_MEMO": """From the current ETH-PERP packet, write a decision memo that reconciles 5m, 15m, and 60m structure.
State where the shorter timeframe is leading, where the higher timeframe is vetoing, and what signal would create clean alignment.""",
            "BASIS_PRESSURE_ATTRIBUTION": """Explain the current basis state in plain English and in trader language.
Separate normal directional carry from unstable basis dislocation.
State whether the basis supports trend continuation, fade, or no-trade.""",
            "LIQUIDATION_PATH_MAPPING": """Infer the most likely liquidation path.
Describe where trapped longs and trapped shorts likely sit.
State which sweep path is more attractive to the market and what reclaim would invalidate it.""",
            "CONVEXITY_AND_TRAP_REPORT": """Analyze the packet for convexity traps.
Decide whether breakout traders or fade traders are more vulnerable.
Describe how a false break would likely look in price, flow, and basis.""",
            "ENTROPIC_MONETIZATION_AUDIT": """Judge whether the regime is monetizable.
Use entropy, volatility, ribbon spread, basis behavior, and time-frame alignment.
Output one of: MONETIZABLE, CONDITIONAL, or TOO ENTROPIC.""",
            "INVENTORY_IMBALANCE_FORENSICS": """Study passive vs aggressive behavior proxies.
Explain whether the tape looks like absorption, exhaustion, or impulsive continuation.
Say which side appears to be defending inventory.""",
            "TERM_STRUCTURE_HANDOFF_ANALYSIS": """Analyze whether the lower timeframe impulse is successfully handing off into higher timeframe acceptance.
If the handoff fails, say who gets trapped next.""",
            "CONTRADICTION_HUNTER": """Hunt contradictions inside the packet.
List every major reason the dominant signal could be wrong.
Then decide whether those contradictions are fatal, manageable, or ignorable.""",
            "ANALOG_MEMORY_COMPARATOR": """Compare the current packet to the retrieved in-session analogs.
Use memory only as analogy, not proof.
Explain whether the analogs support persistence, mean reversion, or caution.""",
            "SIGNAL_DECAY_FORECAST": """Assume the current edge is decaying.
Describe the most likely path by which the signal loses validity over the next few bars."""
        },
        "ADVANCED_INCOME_GENERATION_PROMPTS": {
            "ENTROPIC_CARRY_HARVEST_DESIGN": """Design a bounded entropic carry harvest rail for ETH-PERP.
Focus on orderly regimes only.
Define entry filter, hold filter, unwind filter, and hard no-trade states.""",
            "MAKER_ABSORPTION_RAIL": """Design a selective maker-execution rail.
Only allow passive participation when absorption evidence is present and entropy is controlled.
Specify when maker behavior becomes dangerous.""",
            "BASIS_FADE_SCALP_RAIL": """Design a short-horizon basis fade rail.
Use basis z-state, sweep behavior, and reclaim/reject logic.
Be explicit about when stretched basis should NOT be faded.""",
            "REGIME_HANDOFF_SCALP_RAIL": """Design a regime-handoff scalp rail that exploits transfer from lower-timeframe impulse into higher-timeframe acceptance.
State the confirmation sequence required before size can be added.""",
            "ASYMMETRIC_LONG_SHORT_INCOME_STACK": """Build an income-oriented framework that combines bounded directional trades, maker behavior, and carry logic.
Do not overtrade.
State when the stack should shrink to zero exposure.""",
            "VOL_COMPRESSION_RELEASE_PLAN": """Design a plan for monetizing volatility compression release in ETH-PERP.
State how to avoid getting trapped by the first fake break.""",
            "FUNDING_REFLEX_INVERSION_PLAN": """Design a rail for when crowded carry begins to invert.
Separate clean inversion setups from random noise and chop.""",
            "MEAN_REVERSION_WHEN_NOT_TO": """Explain when mean reversion looks attractive but should still be avoided because the regime is transitioning into trend.""",
            "PASSIVE_EDGE_SURVIVAL_GUIDE": """Write a survival guide for passive quoting in ETH-PERP.
Define the signs that passive edge is real versus imaginary.""",
            "INCOME_GENERATION_RISK_CAPSULE": """Return a compact risk capsule for advanced income generation systems:
edge source, fragility source, kill switch, max participation logic, and what invalidates the whole idea."""
        },
        "ADVANCED_EXECUTION_PROMPTS": {
            "LONG_EXECUTION_ARCHITECT": """Given the live packet, build the best possible long execution architecture.
Include trigger, confirmation, invalidation, scaling, hedge idea, cancel conditions, and the single biggest reason to avoid the long.""",
            "SHORT_EXECUTION_ARCHITECT": """Given the live packet, build the best possible short execution architecture.
Include trigger, confirmation, invalidation, scaling, hedge idea, cancel conditions, and the single biggest reason to avoid the short.""",
            "FLAT_DISCIPLINE_ARCHITECT": """Build a flat-discipline plan.
State why staying flat is rational, what signal would wake the system up, and what should still be ignored even if price becomes exciting.""",
            "SIZE_AND_FRAGILITY_ENGINE": """Translate confidence, fragility, entropy, and crowding into size logic.
Do not return a full Kelly style sizing answer.
Return a conservative bounded sizing ladder.""",
            "STOP_AND_INVALIDATION_ENGINE": """Design invalidation first, then entry.
Explain the difference between informational invalidation and pain-based exit.""",
            "HEDGE_AND_OFFSET_ENGINE": """Propose bounded hedge ideas appropriate for ETH-PERP research.
Explain what the hedge protects against and what it cannot protect against.""",
            "TRAP_AVOIDANCE_ENGINE": """Describe the single most likely trap for the currently dominant signal and how execution logic should adapt.""",
            "LIQUIDATION_RECLAIM_ENGINE": """Design an execution plan that waits for a sweep and reclaim instead of chasing momentum.""",
            "LADDERED_SCALE_ENGINE": """Create a laddered scale-in / scale-out blueprint that stays conditional and reduces exposure when evidence weakens.""",
            "POST_TRADE_AUTOPSY_TEMPLATE": """Produce a post-trade autopsy template specific to ETH-PERP trend, basis, and sweep trades."""
        },
        "ADVANCED_VISUAL_PROMPTS": {
            "RIBBON_FORENSICS": """Read the chart image and diagnose the EMA ribbon.
State whether the ribbon shows trend health, compression, loss of structure, or a trap transition.""",
            "SWEEP_AND_RECLAIM_VISUAL": """Use the chart image to identify likely sweep and reclaim events.
Explain which sweep looks real and which one looks cosmetic.""",
            "FLOW_AND_ABSORPTION_VISUAL": """Infer whether the move looks absorbed, exhausted, or impulsively sponsored.
Point to specific parts of the image that support the claim.""",
            "BASIS_AND_PRICE_CONTRADICTION_VISUAL": """Compare the packet narrative against the chart image.
If the chart undermines the packet, say so clearly.""",
            "MULTI_PANEL_VISUAL_SYNTHESIS": """Use all chart panels together: price, basis, flow, and quantum surface.
Return the cleanest integrated read possible without exaggerating confidence.""",
            "TRAP_DETECTION_VISUAL": """Identify whether the current image favors a breakout trap, a fade trap, or a stay-flat regime.""",
            "VISUAL_INVALIDATION_MAP": """Mark the most important visual invalidation points a discretionary trader should respect.""",
            "IMAGE_TO_EXECUTION_BRIDGE": """Translate the visual read into an execution posture: engage, wait, fade, or stand down.""",
            "VISUAL_ENTROPY_DIAGNOSIS": """Use the image to decide whether the regime is visually orderly or visually entropic.""",
            "VISUAL_MEMORY_COMPARISON": """Compare the current chart image to the in-session memory narrative and say whether the image agrees."""
        },
        "ADVANCED_FUTURES_AND_COMMODITY_PROMPTS": {
            "CONTRACT_IDENTITY_RESOLVER": """First identify the selected product as spot, perpetual, expiring futures, or unsupported.
Use product_id, display name, base asset, quote asset, expiry, contract size, price increment, and status when present.
If fields are missing, say exactly what is missing and downgrade confidence before discussing direction.""",
            "OIL_FUTURES_STRUCTURE_MEMO": """Analyze the selected oil futures contract as a derivatives instrument, not as spot oil.
Separate: contract identity, expiry/roll context, session liquidity, candle quality, volatility regime, directional structure, and execution hazards.
Never infer physical oil exposure unless the packet explicitly proves it.""",
            "FUTURES_ROLL_AND_EXPIRY_RISK": """Explain how expiry proximity, roll behavior, and declining liquidity could distort the signal.
State whether the selected contract is suitable for analysis, suitable only for observation, or too thin/stale to use.
Include what continuous or reference market should be checked before acting.""",
            "CROSS_ASSET_CONTEXT_CHECK": """Compare the selected product against its most relevant reference market when available.
For crypto futures, compare against spot/perp reference.
For oil futures, compare against the closest liquid oil reference if supplied; otherwise state that cross-asset confirmation is unavailable.
Do not invent macro context from missing data.""",
            "LIQUIDITY_AND_SLIPPAGE_FORENSICS": """Audit liquidity quality using volume, candle gaps, wick behavior, stale bars, and abrupt spread-like movement proxies.
Decide whether market orders, maker orders, or no execution are most defensible.
If liquidity is poor, make that the main conclusion.""",
            "EXPIRING_CONTRACT_NO_TRADE_FILTER": """Build a hard no-trade filter for expiring contracts.
Reject setups with stale candles, thin volume, large discontinuities, missing reference price, near-expiry distortion, or contradictory higher timeframe structure.
Return only the conditions that would make the contract analyzable again."""
        },
        "ADVANCED_DECISION_ENGINE_PROMPTS": {
            "EVIDENCE_WEIGHTED_DECISION_GATE": """Return exactly one of: PASS, WATCH, or REJECT.
Base the decision on direct packet evidence only.
Score trend, structure, flow, volatility, basis/reference agreement, liquidity quality, and data quality from -2 to +2.
Then state the single strongest reason not to act.""",
            "CONFIDENCE_CALIBRATION_ENGINE": """Calibrate confidence from evidence quality rather than signal strength.
Penalize stale data, missing reference markets, single-timeframe agreement, high entropy, thin volume, and symbol ambiguity.
Return confidence as LOW, MEDIUM, or HIGH with one sentence justifying the calibration.""",
            "CONTRADICTION_PRIORITY_SORTER": """List contradictions in priority order.
For each contradiction, say whether it is fatal, cautionary, or cosmetic.
If any fatal contradiction exists, the final stance must be flat or reject.""",
            "INVALIDATION_FIRST_THESIS": """Start with what would prove the thesis wrong.
Only after invalidation is clear, describe the long case, short case, and flat case.
Do not recommend action unless invalidation can be stated from observable market data.""",
            "REGIME_STATE_CLASSIFIER": """Classify the current state as one of: TREND, RANGE, COMPRESSION, EXPANSION, REVERSAL_RISK, LIQUIDITY_SWEEP, STALE_OR_THIN, or UNUSABLE_DATA.
Explain the classification using packet fields only, then state what regime transition would matter most next.""",
            "MULTI_AGENT_MARKET_COUNCIL": """Run five internal reviewers and reveal only the consensus.
Reviewer 1: trend follower. Reviewer 2: mean reversion trader. Reviewer 3: liquidity hunter. Reviewer 4: risk officer. Reviewer 5: data quality auditor.
Consensus must include the best trade thesis and the best reason to do nothing."""
        },
        "ADVANCED_DATA_QUALITY_PROMPTS": {
            "SYMBOL_AND_ENDPOINT_AUDIT": """Audit product_id, endpoint source, candle granularity, timestamp freshness, and returned row count.
Flag symbol normalization issues, unsupported products, and endpoint mismatches.
Do not analyze direction until the data source is judged usable.""",
            "CANDLE_INTEGRITY_AUDIT": """Inspect the candle packet for missing rows, flatlined prices, impossible OHLC relationships, zero/negative volume, timestamp gaps, and outlier jumps.
Return PASS, DEGRADED, or FAIL.
If DEGRADED or FAIL, explain what analysis is still safe, if any.""",
            "REFERENCE_MARKET_AUDIT": """Evaluate whether the reference product is appropriate.
For a futures product, the reference must be economically related, liquid, and time-aligned.
If no valid reference exists, disable basis claims and say so plainly.""",
            "STALE_DATA_KILL_SWITCH": """Check whether the latest candle is stale relative to the selected timeframe.
If stale, return a kill-switch warning before any market interpretation.
Do not allow confident language when data is stale."""
        },
        "ADVANCED_RISK_AND_GOVERNANCE_PROMPTS": {
            "AUTONOMY_PRE_FLIGHT_CHECK": """Before any autonomous or paper action, verify data quality, product validity, account mode, live_trading flag, confidence threshold, cooldown, notional size, leverage, and margin type.
Return BLOCKED unless every condition is explicitly satisfied.""",
            "ORDER_SAFETY_REVIEW": """Review the proposed order like a risk control system.
Check product validity, side, size, leverage, margin mode, preview availability, liquidity suitability, and invalidation logic.
Return APPROVE_PAPER_ONLY, REQUIRE_MANUAL_REVIEW, or BLOCK.""",
            "LEVERAGE_FRAGILITY_AUDIT": """Explain how leverage changes the failure mode of the setup.
Prefer smaller notional and lower leverage when volatility, entropy, liquidity risk, or symbol uncertainty rises.
Never present leverage as improving edge.""",
            "LOSS_BOUNDARY_DECLARATION": """State the loss boundary before any upside case.
Include maximum acceptable thesis damage, market condition that invalidates the setup, and reason the system should stop re-entering after invalidation.""",
            "FALSE_PRECISION_REMOVER": """Remove unjustified precision from the analysis.
Replace exact claims that are not supported by the packet with ranges, conditional statements, or flat/no-view conclusions."""
        },
        "ADVANCED_OUTPUT_FORMATS": {
            "COMPACT_OPERATOR_BRIEF": """Return a compact operator brief with these headings only:
State, Data Quality, Regime, Dominant Risk, Long Trigger, Short Trigger, Flat Trigger, Kill Switch, Confidence.""",
            "FULL_DERIVATIVES_MEMO": """Return a full derivatives memo with these headings:
Product Identity, Data Quality, Market Structure, Cross-Market Context, Liquidity, Volatility, Long Case, Short Case, Flat Case, Execution Hazards, Invalidations, Confidence.""",
            "JSON_DECISION_PACKET": """Return valid JSON only with keys: product_id, data_quality, regime, signal, confidence, long_case, short_case, flat_case, invalidations, kill_switches, action_gate.
Do not include markdown around the JSON.""",
            "RED_TEAM_REPORT": """Return only reasons the current thesis may be wrong.
Group them into data problems, market structure problems, liquidity problems, execution problems, and model-overfit problems."""
        },
        "CHAINED_MACROS": {
            "FULL_STACK_RESEARCH_CHAIN": """Use this sequence:
1. MULTI_TIMEFRAME_DECISION_MEMO
2. BASIS_PRESSURE_ATTRIBUTION
3. LIQUIDATION_PATH_MAPPING
4. CONTRADICTION_HUNTER
5. ENTROPIC_MONETIZATION_AUDIT
Then return a final synthesis.""",
            "INCOME_STACK_CHAIN": """Use this sequence:
1. ENTROPIC_CARRY_HARVEST_DESIGN
2. MAKER_ABSORPTION_RAIL
3. BASIS_FADE_SCALP_RAIL
4. INCOME_GENERATION_RISK_CAPSULE
Then return only the best bounded income-generation rail.""",
            "VISUAL_EXECUTION_CHAIN": """Use this sequence:
1. MULTI_PANEL_VISUAL_SYNTHESIS
2. TRAP_DETECTION_VISUAL
3. IMAGE_TO_EXECUTION_BRIDGE
Then return the cleanest action posture.""",
            "RISK_FIRST_CHAIN": """Use this sequence:
1. CONTRADICTION_HUNTER
2. RISK_GOVERNOR_SYSTEM
3. SIZE_AND_FRAGILITY_ENGINE
Then answer with the most conservative high-quality stance.""",
            "STAY_FLAT_CHAIN": """Use this sequence:
1. ENTROPIC_MONETIZATION_AUDIT
2. FLAT_DISCIPLINE_ARCHITECT
3. VISUAL_ENTROPY_DIAGNOSIS
Then explain why patience is edge if that is what the packet says."""
        }
    }
}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "product_id": "ETH-PERP",
    "reference_product_id": "ETH-USD",
    "refresh_seconds": 25,
    "timeframe_minutes": 15,
    "secondary_minutes": 5,
    "tertiary_minutes": 60,
    "bars": 320,
    "model_path": DEFAULT_MODEL_PATH,
    "model_temperature": 0.20,
    "model_top_p": 0.90,
    "model_max_tokens": 1024,
    "use_visual_llm": True,
    "paper_trading_enabled": True,
    "live_trading_enabled": False,
    "autonomy_enabled": True,
    "autonomy_min_confidence": 0.70,
    "autonomy_cooldown_seconds": 420,
    "default_order_quote_size": 25.0,
    "default_order_leverage": 3.0,
    "margin_type": "CROSS",
    "jwt_algorithm": "ES256",
    "chart_show_ribbon_cloud": True,
    "chart_show_quantum_panel": True,
    "chart_show_ema233": True,
    "side_tray_hidden": False,
    "rag_memory_enabled": True,
    "rag_memory_limit": 120,
    "pennylane_sensor_enabled": True,
    "prompt_pack": copy.deepcopy(DEFAULT_PROMPT_PACK),
    "secrets": None,
    "vault_security": {
        "enabled": False,
        "mode": "missing",
        "prompt_on_startup": True,
        "startup_setup_popup_seen": False,
        "rotation_count": 0,
        "last_rotated_at": "",
        "last_unlocked_at": "",
        "last_updated_at": "",
        "last_reason": "",
        "unlock_count": 0,
        "created_at": "",
        "audit_log": [],
    },
}


def recursive_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = copy.deepcopy(base)
        for key, value in override.items():
            merged[key] = recursive_merge(base.get(key), value) if key in base else copy.deepcopy(value)
        return merged
    return copy.deepcopy(base if override is None else override)


def merge_prompt_pack(incoming: Any) -> Dict[str, Any]:
    if not isinstance(incoming, dict):
        return copy.deepcopy(DEFAULT_PROMPT_PACK)
    merged = recursive_merge(DEFAULT_PROMPT_PACK, incoming)
    return merged if isinstance(merged, dict) else copy.deepcopy(DEFAULT_PROMPT_PACK)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def human_money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "—"


def human_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "—"


def human_size(num_bytes: int) -> str:
    value = float(max(0, int(num_bytes)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{int(num_bytes)} B"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_structured_text(value: Any, *, max_chars: int = 20000) -> str:
    text = CONTROL_CHARS_RE.sub(" ", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


def sanitize_ui_text(value: Any, *, max_chars: int = 240) -> str:
    """Strip control chars and markup before a value is displayed in the UI."""
    text = sanitize_structured_text(value, max_chars=max_chars)
    if bleach is not None:
        text = bleach.clean(text, tags=[], attributes={}, strip=True)
    return text.strip()[:max_chars]


def sanitize_product_id(value: Any, *, default: str = "ETH-USD") -> str:
    """Coinbase product ids are safe ASCII symbols. Reject/normalize everything else."""
    text = sanitize_ui_text(value, max_chars=96).upper().replace("/", "-").replace(" ", "-")
    text = re.sub(r"[^A-Z0-9._-]", "", text).strip(".-_")
    text = re.sub(r"^(N[A-Z]{1,3}-\d{1,2}[A-Z]{3}\d{2}-CDE)(?:ETH|BTC|USD|USDC)$", r"\1", text)
    return text if SAFE_PRODUCT_ID_RE.match(text) else default


def safe_product_label(product: Dict[str, Any]) -> str:
    pid = sanitize_product_id(product.get("product_id"), default="ETH-USD")
    future_details = product.get("future_product_details") if isinstance(product.get("future_product_details"), dict) else {}
    perpetual_details = future_details.get("perpetual_details") if isinstance(future_details.get("perpetual_details"), dict) else {}

    display = sanitize_ui_text(
        product.get("display_name_overwrite")
        or product.get("display_name")
        or future_details.get("display_name")
        or product.get("alias")
        or pid,
        max_chars=96,
    )

    def compact_market_tag(value: Any) -> str:
        tag = sanitize_ui_text(value or "", max_chars=64).upper()
        for prefix in (
            "FUTURES_UNDERLYING_TYPE_",
            "FUTURES_ASSET_TYPE_",
            "UNKNOWN_",
        ):
            if tag.startswith(prefix):
                tag = tag[len(prefix):]
        tag = tag.replace("_", " ").strip()
        return tag.title() if tag else ""

    ptype = sanitize_ui_text(product.get("product_type") or "", max_chars=24)
    venue = sanitize_ui_text(product.get("product_venue") or future_details.get("venue") or "", max_chars=32)
    underlying = compact_market_tag(
        future_details.get("futures_asset_type")
        or perpetual_details.get("underlying_type")
        or future_details.get("underlying_type")
        or ""
    )
    suffix = " · ".join(part for part in (ptype, venue, underlying) if part)
    return f"{pid} — {display}" + (f" ({suffix})" if suffix else "")

def resolve_native_image_path(image_path: Optional[str | Path]) -> Optional[Path]:
    if not image_path:
        return None
    try:
        path = Path(image_path).expanduser().resolve(strict=True)
    except Exception:
        return None
    if not path.is_file():
        return None
    if path.suffix.lower() not in ALLOWED_NATIVE_IMAGE_EXTENSIONS:
        return None
    size = path.stat().st_size
    if size <= 0 or size > MAX_IMAGE_BYTES:
        return None
    return path


def download_model_httpx(
    url: str,
    dest: Path,
    *,
    expected_sha: Optional[str] = None,
    progress_callback: Optional[Any] = None,
) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with httpx.stream("GET", url, follow_redirects=True, timeout=NETWORK_TIMEOUT) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with dest.open("wb") as handle:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress_callback:
                    progress_callback(done, total)
    sha = digest.hexdigest()
    if expected_sha and sha.lower() != expected_sha.lower():
        try:
            dest.unlink()
        except Exception:
            pass
        raise ValueError(f"SHA256 mismatch. Expected {expected_sha}, got {sha}.")
    return sha


def hex_to_rgb(value: str) -> Tuple[int, int, int]:
    text = str(value).strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"Expected a 6-digit hex color, got {value!r}")
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb: Tuple[float, float, float]) -> str:
    r, g, b = (max(0, min(255, int(round(channel)))) for channel in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def blend_hex(foreground: str, background: str, alpha: float) -> str:
    fg = hex_to_rgb(foreground)
    bg = hex_to_rgb(background)
    weight = clamp(alpha, 0.0, 1.0)
    return rgb_to_hex(tuple(bg[idx] + (fg[idx] - bg[idx]) * weight for idx in range(3)))


def mask_secret_multiline(value: str, *, min_stars: int = 8, max_stars: int = 36) -> str:
    text = sanitize_structured_text(value, max_chars=32000)
    if not text:
        return ""
    masked_lines: List[str] = []
    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            masked_lines.append("")
            continue
        masked_lines.append("*" * max(min_stars, min(max_stars, len(clean))))
    return "\n".join(masked_lines) or ("*" * min_stars)


def format_chart_time(value: Any) -> str:
    try:
        return pd.Timestamp(value).strftime("%m-%d %H:%M")
    except Exception:
        return str(value)


def compact_json(value: Any, max_chars: int = 3200) -> str:
    try:
        text = json.dumps(value, indent=2, default=str)
    except Exception:
        text = str(value)
    return text if len(text) <= max_chars else text[: max_chars - 16] + "\n... [truncated]"


def hist_entropy(values: np.ndarray, bins: int = 7) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 4:
        return 0.5
    hist, _ = np.histogram(arr, bins=bins)
    total = float(hist.sum())
    if total <= 0:
        return 0.5
    probs = hist / total
    probs = probs[probs > 0]
    if probs.size == 0:
        return 0.5
    entropy = -np.sum(probs * np.log(probs))
    return clamp(float(entropy / math.log(bins)), 0.0, 1.0)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def derive_key(passphrase: str, salt: bytes, *, iterations: int = WRAPPED_KEY_PBKDF2_ITERATIONS) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=int(iterations))
    return kdf.derive(passphrase.encode("utf-8"))


def vault_bundle_mode(bundle: Any) -> str:
    if not isinstance(bundle, dict):
        return "missing"
    if bundle.get("version") == "wrapped_master_v2" and isinstance(bundle.get("wrapped_key"), dict) and isinstance(bundle.get("payload"), dict):
        return "wrapped_master_v2"
    if all(key in bundle for key in ("salt_b64", "nonce_b64", "ciphertext_b64")):
        return "legacy_direct_v1"
    return "unknown"


def wrap_master_key_for_passphrase(passphrase: str, master_key: bytes) -> Dict[str, Any]:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    wrapping_key = derive_key(passphrase, salt, iterations=WRAPPED_KEY_PBKDF2_ITERATIONS)
    ciphertext = AESGCM(wrapping_key).encrypt(nonce, master_key, VAULT_MASTER_KEY_AAD)
    return {
        "version": "wrapped_master_v2",
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": WRAPPED_KEY_PBKDF2_ITERATIONS,
        "salt_b64": base64.b64encode(salt).decode("utf-8"),
        "nonce_b64": base64.b64encode(nonce).decode("utf-8"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("utf-8"),
    }


def unwrap_master_key_with_passphrase(bundle: Dict[str, Any], passphrase: str) -> bytes:
    salt = base64.b64decode(bundle["salt_b64"])
    nonce = base64.b64decode(bundle["nonce_b64"])
    ciphertext = base64.b64decode(bundle["ciphertext_b64"])
    iterations = int(bundle.get("iterations") or WRAPPED_KEY_PBKDF2_ITERATIONS)
    wrapping_key = derive_key(passphrase, salt, iterations=iterations)
    return AESGCM(wrapping_key).decrypt(nonce, ciphertext, VAULT_MASTER_KEY_AAD)


def encrypt_secret_payload(
    payload: Dict[str, Any],
    passphrase: Optional[str] = None,
    *,
    master_key: Optional[bytes] = None,
    wrapped_key_bundle: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], bytes, str]:
    clean_payload = payload if isinstance(payload, dict) else {}
    active_master_key = master_key or os.urandom(32)
    active_wrapped_key = wrapped_key_bundle
    if active_wrapped_key is None:
        if not passphrase:
            raise ValueError("A vault password is required to seal a new wrapped vault.")
        active_wrapped_key = wrap_master_key_for_passphrase(passphrase, active_master_key)
    nonce = os.urandom(12)
    plaintext = json.dumps(clean_payload, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(active_master_key).encrypt(nonce, plaintext, VAULT_SECRET_AAD)
    return (
        {
            "version": "wrapped_master_v2",
            "cipher": "AES-GCM",
            "wrapped_key": active_wrapped_key,
            "payload": {
                "nonce_b64": base64.b64encode(nonce).decode("utf-8"),
                "ciphertext_b64": base64.b64encode(ciphertext).decode("utf-8"),
            },
        },
        active_master_key,
        "wrapped_master_v2",
    )


def decrypt_secret_payload(payload: Dict[str, Any], passphrase: str) -> Tuple[Dict[str, Any], Optional[bytes], str]:
    mode = vault_bundle_mode(payload)
    if mode == "wrapped_master_v2":
        master_key = unwrap_master_key_with_passphrase(payload["wrapped_key"], passphrase)
        nonce = base64.b64decode(payload["payload"]["nonce_b64"])
        ciphertext = base64.b64decode(payload["payload"]["ciphertext_b64"])
        plaintext = AESGCM(master_key).decrypt(nonce, ciphertext, VAULT_SECRET_AAD)
        loaded = json.loads(plaintext.decode("utf-8"))
        return (loaded if isinstance(loaded, dict) else {}), master_key, mode
    if mode == "legacy_direct_v1":
        salt = base64.b64decode(payload["salt_b64"])
        nonce = base64.b64decode(payload["nonce_b64"])
        ciphertext = base64.b64decode(payload["ciphertext_b64"])
        key = derive_key(passphrase, salt, iterations=WRAPPED_KEY_PBKDF2_ITERATIONS)
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, LEGACY_SECRET_AAD)
        loaded = json.loads(plaintext.decode("utf-8"))
        return (loaded if isinstance(loaded, dict) else {}), None, mode
    raise ValueError("The stored secret bundle is missing or uses an unsupported vault format.")


class SettingsManager:
    def __init__(self, path: Path, defaults: Dict[str, Any]):
        self.path = path
        self.defaults = copy.deepcopy(defaults)
        self.data = copy.deepcopy(defaults)
        self.load()

    def load(self) -> Dict[str, Any]:
        merged = copy.deepcopy(self.defaults)
        if self.path.exists():
            try:
                incoming = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(incoming, dict):
                    merged = recursive_merge(merged, incoming)
            except Exception:
                pass
        merged["prompt_pack"] = merge_prompt_pack(merged.get("prompt_pack"))
        merged["vault_security"] = recursive_merge(self.defaults.get("vault_security", {}), merged.get("vault_security") or {})
        self.data = merged
        return self.data

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def load_secrets(self, passphrase: str) -> Dict[str, Any]:
        bundle = self.data.get("secrets")
        if not bundle:
            return {}
        secrets, _, _ = decrypt_secret_payload(bundle, passphrase)
        return secrets

    def vault_security(self) -> Dict[str, Any]:
        return recursive_merge(self.defaults.get("vault_security", {}), self.data.get("vault_security") or {})

    def vault_mode(self) -> str:
        return vault_bundle_mode(self.data.get("secrets"))

    def _update_vault_security(self, *, mode: str, reason: str, rotation_increment: int = 0, unlocked: bool = False) -> Dict[str, Any]:
        state = self.vault_security()
        now = utc_now_iso()
        state["enabled"] = bool(self.data.get("secrets"))
        state["mode"] = mode
        state["last_reason"] = reason
        state["last_updated_at"] = now
        if not state.get("created_at"):
            state["created_at"] = now
        if rotation_increment > 0:
            state["rotation_count"] = max(0, int(safe_float(state.get("rotation_count", 0)))) + int(rotation_increment)
            state["last_rotated_at"] = now
        if unlocked:
            state["last_unlocked_at"] = now
            state["unlock_count"] = max(0, int(safe_float(state.get("unlock_count", 0)))) + 1
        audit = list(state.get("audit_log") or [])
        audit.insert(0, {"timestamp": now, "reason": reason, "mode": mode})
        state["audit_log"] = audit[:8]
        self.data["vault_security"] = state
        return state

    def save_secrets(
        self,
        secrets: Dict[str, Any],
        passphrase: Optional[str] = None,
        *,
        master_key: Optional[bytes] = None,
        reason: str = "refresh_seal",
    ) -> Tuple[Dict[str, Any], bytes, str]:
        current_bundle = self.data.get("secrets")
        current_mode = self.vault_mode()
        wrapped_key_bundle = None
        if master_key is not None and current_mode == "wrapped_master_v2" and isinstance(current_bundle, dict) and reason not in {"password_rotation", "legacy_upgrade", "initial_seal"}:
            wrapped_key_bundle = current_bundle.get("wrapped_key")
        bundle, active_master_key, mode = encrypt_secret_payload(
            secrets,
            passphrase,
            master_key=master_key,
            wrapped_key_bundle=wrapped_key_bundle,
        )
        self.data["secrets"] = bundle
        self._update_vault_security(
            mode=mode,
            reason=reason,
            rotation_increment=1 if reason in {"password_rotation", "legacy_upgrade"} else 0,
            unlocked=bool(active_master_key),
        )
        self.save()
        return secrets, active_master_key, mode

    def unlock_secret_bundle(self, passphrase: str) -> Tuple[Dict[str, Any], Optional[bytes], str]:
        bundle = self.data.get("secrets")
        if not bundle:
            return {}, None, "missing"
        return decrypt_secret_payload(bundle, passphrase)

    def migrate_legacy_secrets(self, passphrase: str) -> Tuple[Dict[str, Any], bytes, str]:
        secrets, _, mode = self.unlock_secret_bundle(passphrase)
        if mode != "legacy_direct_v1":
            raise ValueError("The saved secret bundle does not need a legacy migration.")
        return self.save_secrets(secrets, passphrase, reason="legacy_upgrade")

    def rotate_secret_passphrase(self, current_passphrase: str, new_passphrase: str) -> Tuple[Dict[str, Any], bytes, str]:
        secrets, _, mode = self.unlock_secret_bundle(current_passphrase)
        reason = "legacy_upgrade" if mode == "legacy_direct_v1" else "password_rotation"
        return self.save_secrets(secrets, new_passphrase, reason=reason)

    def note_vault_unlock(self, mode: str) -> None:
        self._update_vault_security(mode=mode, reason="unlock", unlocked=True)
        self.save()

    def note_startup_setup_popup_seen(self) -> None:
        state = self.vault_security()
        if state.get("startup_setup_popup_seen"):
            return
        state["startup_setup_popup_seen"] = True
        self.data["vault_security"] = state
        self.save()


class PromptPackManager:
    def __init__(self, settings: SettingsManager):
        self.settings = settings
        self.data = merge_prompt_pack(self.settings.data.get("prompt_pack"))

    def save(self) -> None:
        self.settings.data["prompt_pack"] = merge_prompt_pack(self.data)
        self.settings.save()


class EncryptedProductStore:
    """SQLite cache with per-row AES-GCM encrypted product payloads."""

    def __init__(self, path: Path = PRODUCT_CACHE_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS products (
                    product_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    product_type TEXT NOT NULL DEFAULT '',
                    venue TEXT NOT NULL DEFAULT '',
                    fetched_at TEXT NOT NULL,
                    nonce_b64 TEXT NOT NULL,
                    ciphertext_b64 TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_products_label ON products(label)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_products_type ON products(product_type)")

    @staticmethod
    def _cache_key(master_key: bytes) -> bytes:
        return hashlib.sha256(b"coinbase-product-cache-v1" + bytes(master_key)).digest()

    def save_products(self, products: List[Dict[str, Any]], master_key: bytes) -> None:
        if not master_key:
            return
        aes = AESGCM(self._cache_key(master_key))
        now = utc_now_iso()
        rows = []
        for product in products:
            if not isinstance(product, dict):
                continue
            pid = sanitize_product_id(product.get("product_id"), default="")
            if not pid:
                continue
            label = safe_product_label(product)
            ptype = sanitize_ui_text(product.get("product_type") or "", max_chars=24)
            venue = sanitize_ui_text(product.get("product_venue") or product.get("future_product_details", {}).get("venue") or "", max_chars=32)
            nonce = os.urandom(12)
            plaintext = json.dumps(product, separators=(",", ":"), default=str).encode("utf-8")
            ciphertext = aes.encrypt(nonce, plaintext, pid.encode("utf-8"))
            rows.append((pid, label, ptype, venue, now, base64.b64encode(nonce).decode("utf-8"), base64.b64encode(ciphertext).decode("utf-8")))
        if not rows:
            return
        with sqlite3.connect(self.path) as conn:
            conn.executemany(
                """
                INSERT INTO products(product_id, label, product_type, venue, fetched_at, nonce_b64, ciphertext_b64)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET
                    label=excluded.label, product_type=excluded.product_type, venue=excluded.venue,
                    fetched_at=excluded.fetched_at, nonce_b64=excluded.nonce_b64, ciphertext_b64=excluded.ciphertext_b64
                """,
                rows,
            )

    def load_index(self) -> List[Dict[str, str]]:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT product_id, label, product_type, venue, fetched_at FROM products ORDER BY product_type DESC, product_id ASC"
            ).fetchall()
        return [
            {"product_id": row[0], "label": row[1], "product_type": row[2], "venue": row[3], "fetched_at": row[4]}
            for row in rows
        ]


class CoinbaseAdvancedClient:
    def __init__(self, settings: SettingsManager):
        self.settings = settings
        self.http = httpx.Client(timeout=NETWORK_TIMEOUT)
        self.secrets: Dict[str, Any] = {}
        self.last_public_error = ""
        self.product_metadata_by_id: Dict[str, Dict[str, Any]] = {}
        self._auth_disabled_error = ""

    def set_secrets(self, secrets: Dict[str, Any]) -> None:
        self.secrets = secrets or {}
        self._auth_disabled_error = ""

    @staticmethod
    def _candidate_product_ids(product_id: str) -> List[str]:
        base = sanitize_product_id(product_id, default="")
        if not base:
            return []
        candidates = [base]
        if base.endswith("-PERP") and not base.endswith("-PERP-INTX"):
            candidates.append(f"{base}-INTX")
        if base.endswith("-PERP-INTX"):
            candidates.append(base.removesuffix("-INTX"))
        if base.endswith("-USDC"):
            # Coinbase Advanced Trade docs note many -USDC products share market data with the -USD product.
            candidates.append(base.removesuffix("-USDC") + "-USD")
        if base.endswith("-USD"):
            candidates.append(base.removesuffix("-USD") + "-USDC")
        seen: List[str] = []
        for candidate in candidates:
            candidate = sanitize_product_id(candidate, default="")
            if candidate and candidate not in seen:
                seen.append(candidate)
        return seen

    def _product_meta(self, product_id: str) -> Dict[str, Any]:
        clean = sanitize_product_id(product_id, default="")
        return self.product_metadata_by_id.get(clean, {}) if clean else {}

    def _is_futures_product(self, product_id: str) -> bool:
        clean = sanitize_product_id(product_id, default="")
        meta = self._product_meta(clean)
        if str(meta.get("product_type") or "").upper() == "FUTURE":
            return True
        if isinstance(meta.get("future_product_details"), dict) and meta.get("future_product_details"):
            return True
        return bool(re.match(r"^[A-Z]{2,4}-\d{1,2}[A-Z]{3}\d{2}-CDE$", clean))

    @staticmethod
    def _intx_candidates(product_id: str) -> List[str]:
        base = sanitize_product_id(product_id, default="")
        if not base or "-PERP" not in base:
            return []
        candidates = [base.removesuffix("-INTX"), base]
        seen: List[str] = []
        for candidate in candidates:
            if candidate and candidate not in seen:
                seen.append(candidate)
        return seen

    @staticmethod
    def _format_request_error(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            response = exc.response
            body = ""
            try:
                body = " ".join(response.text.split())
            except Exception:
                body = ""
            if len(body) > 240:
                body = f"{body[:237]}..."
            detail = f"{response.status_code} {response.reason_phrase} for {response.request.url}"
            if response.status_code == 401 and ("unrecognized" in body.lower() or "api key" in body.lower()):
                return (
                    f"{detail} :: Coinbase rejected the API key name. Use the CDP JSON field named 'name' "
                    "(usually organizations/{org}/apiKeys/{key}) with the matching privateKey, not only the short key id or secret."
                )
            return f"{detail} :: {body}" if body else detail
        return str(exc)

    @staticmethod
    def _iso_utc(ts: int) -> str:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _candles_to_frame(raw: Any, *, source: str, bars: int) -> pd.DataFrame:
        if isinstance(raw, dict):
            if source == "advanced":
                raw = raw.get("candles", [])
            elif source == "intx":
                raw = raw.get("aggregations", [])
            else:
                raw = raw.get("candles", raw.get("aggregations", []))
        if not raw:
            return pd.DataFrame()
        if source == "advanced":
            df = pd.DataFrame(raw).rename(
                columns={"start": "time", "low": "Low", "high": "High", "open": "Open", "close": "Close", "volume": "Volume"}
            )
        elif source == "intx":
            df = pd.DataFrame(raw).rename(
                columns={"start": "time", "low": "Low", "high": "High", "open": "Open", "close": "Close", "volume": "Volume"}
            )
        else:
            df = pd.DataFrame(raw, columns=["time", "Low", "High", "Open", "Close", "Volume"])
        if "time" not in df:
            return pd.DataFrame()
        if source == "intx":
            df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce").dt.tz_convert(EASTERN_TZ)
        else:
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True, errors="coerce").dt.tz_convert(EASTERN_TZ)
        df = df.dropna(subset=["time"])
        df = df.sort_values("time").set_index("time")
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[["Open", "High", "Low", "Close", "Volume"]].dropna(how="any").tail(bars)

    @staticmethod
    def _resample_candles(df: pd.DataFrame, rule: str, bars: int) -> pd.DataFrame:
        if df.empty:
            return df
        try:
            resampled = df.resample(rule).agg(
                {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
            )
            return resampled.dropna(how="any").tail(max(1, int(bars)))
        except Exception:
            return pd.DataFrame()



    @staticmethod
    def _extract_prices_from_payload(raw: Any) -> Tuple[List[float], float]:
        """Extract usable prices from Coinbase ticker/book/product/best-bid-ask shapes."""
        prices: List[float] = []
        volume = 0.0

        def add_price(value: Any) -> None:
            val = pd.to_numeric(value, errors="coerce")
            if not pd.isna(val):
                prices.append(float(val))

        def walk(value: Any, depth: int = 0) -> None:
            nonlocal volume
            if depth > 4:
                return
            if isinstance(value, dict):
                for key in (
                    "price", "mid_market_price", "mark_price", "index_price", "last_price",
                    "best_bid", "best_ask", "bid", "ask", "pricebook_mid_market_price",
                    "base_increment", "quote_increment",
                ):
                    if key in value:
                        add_price(value.get(key))
                for key in ("volume", "volume_24h", "quote_volume", "base_volume"):
                    val = pd.to_numeric(value.get(key), errors="coerce")
                    if not pd.isna(val):
                        volume = max(volume, float(val))
                # Product book commonly uses pricebook: {bids: [{price,size}], asks:[...]}
                for side_key in ("bids", "asks"):
                    levels = value.get(side_key)
                    if isinstance(levels, list) and levels:
                        level = levels[0]
                        if isinstance(level, dict):
                            add_price(level.get("price"))
                        elif isinstance(level, (list, tuple)) and level:
                            add_price(level[0])
                for nested_key in ("pricebook", "product", "product_details", "best_bid_ask", "future_product_details"):
                    if nested_key in value:
                        walk(value.get(nested_key), depth + 1)
                for key, val in value.items():
                    if key in {"products", "pricebooks", "best_bid_asks"}:
                        walk(val, depth + 1)
            elif isinstance(value, list):
                for item in value[:8]:
                    walk(item, depth + 1)

        walk(raw)
        # de-duplicate while preserving order
        clean: List[float] = []
        for price in prices:
            if math.isfinite(price) and price > 0 and price not in clean:
                clean.append(price)
        return clean, volume

    @staticmethod
    def _quote_to_frame(raw: Any, *, minutes: int, bars: int) -> pd.DataFrame:
        prices, volume = CoinbaseAdvancedClient._extract_prices_from_payload(raw)
        if not prices:
            return pd.DataFrame()
        px = prices[0]
        low = min(prices)
        high = max(prices)
        now = pd.Timestamp.now(tz="UTC").floor(f"{max(1, int(minutes))}min").tz_convert(EASTERN_TZ)
        return pd.DataFrame(
            {"Open": [px], "High": [max(high, px)], "Low": [min(low, px)], "Close": [px], "Volume": [volume]},
            index=[now],
        )

    @staticmethod
    def _ticker_to_frame(raw: Any, *, minutes: int, bars: int) -> pd.DataFrame:
        """Build OHLCV from Advanced Trade market trades, quote, book, or product snapshot.

        Some valid Coinbase Advanced/CFM futures pages exist in the UI but return
        an empty REST candle array for a selected history window. In that case,
        keep the product alive in the UI by falling back to official Advanced
        market-data endpoints: ticker/trades, best-bid-ask, product book, and
        product detail. Trade-derived bars are preferred; quote-derived bars are
        marked by low/zero volume and should be treated as degraded.
        """
        if not isinstance(raw, dict):
            return pd.DataFrame()
        trades = raw.get("trades") if isinstance(raw.get("trades"), list) else []
        rows: List[Dict[str, Any]] = []
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            ts = pd.to_datetime(trade.get("time"), utc=True, errors="coerce")
            price = pd.to_numeric(trade.get("price"), errors="coerce")
            size = pd.to_numeric(trade.get("size"), errors="coerce")
            if pd.isna(ts) or pd.isna(price):
                continue
            rows.append({"time": ts, "price": float(price), "size": 0.0 if pd.isna(size) else float(size)})
        if rows:
            tdf = pd.DataFrame(rows).sort_values("time").set_index("time")
            rule = f"{max(1, int(minutes))}min"
            out = tdf.resample(rule).agg({"price": ["first", "max", "min", "last"], "size": "sum"})
            out.columns = ["Open", "High", "Low", "Close", "Volume"]
            out = out.dropna(how="any")
            out.index = out.index.tz_convert(EASTERN_TZ)
            return out[["Open", "High", "Low", "Close", "Volume"]].tail(max(1, int(bars)))

        return CoinbaseAdvancedClient._quote_to_frame(raw, minutes=minutes, bars=bars)

    def _advanced_marketdata_fallback(self, product_id: str, minutes: int, bars: int, start_ts: int, end_ts: int) -> pd.DataFrame:
        """Fallback for valid Advanced/CFM products whose candle endpoint is empty.

        This intentionally uses only Coinbase Advanced Trade endpoints and the
        user's existing CDP JWT key. It does not require or mention any separate
        CDE/FairX key.
        """
        candidate = sanitize_product_id(product_id, default="")
        if not candidate:
            return pd.DataFrame()

        limit = min(1000, max(50, int(bars) * 4))
        attempts: List[Tuple[str, str, Dict[str, Any], bool, str]] = []

        # Prefer authenticated current market trades without a start/end window:
        # thin futures often have no trades in the exact requested candle window,
        # but current ticker/book still proves the product is live.
        if self.secrets and jwt is not None and not self._auth_disabled_error:
            attempts.extend([
                (COINBASE_AUTH_ADVANCED_TICKER.format(product_id=candidate), f"/products/{candidate}/ticker", {"limit": limit}, True, "auth ticker"),
                (COINBASE_AUTH_ADVANCED_TICKER.format(product_id=candidate), f"/products/{candidate}/ticker", {"limit": limit, "start": str(int(start_ts)), "end": str(int(end_ts))}, True, "auth ticker window"),
                (COINBASE_AUTH_ADVANCED_BEST_BID_ASK, "/best_bid_ask", {"product_ids": candidate}, True, "auth best bid/ask"),
                (COINBASE_AUTH_ADVANCED_PRODUCT_BOOK, "/product_book", {"product_id": candidate, "limit": 1}, True, "auth product book"),
                (COINBASE_AUTH_ADVANCED_PRODUCT.format(product_id=candidate), f"/products/{candidate}", {}, True, "auth product"),
            ])

        attempts.extend([
            (COINBASE_PUBLIC_ADVANCED_TICKER.format(product_id=candidate), f"/market/products/{candidate}/ticker", {"limit": limit}, False, "public ticker"),
            (COINBASE_PUBLIC_ADVANCED_TICKER.format(product_id=candidate), f"/market/products/{candidate}/ticker", {"limit": limit, "start": str(int(start_ts)), "end": str(int(end_ts))}, False, "public ticker window"),
            (COINBASE_PUBLIC_ADVANCED_BEST_BID_ASK, "/market/best_bid_ask", {"product_ids": candidate}, False, "public best bid/ask"),
            (COINBASE_PUBLIC_ADVANCED_PRODUCT_BOOK, "/market/product_book", {"product_id": candidate, "limit": 1}, False, "public product book"),
            (COINBASE_PUBLIC_ADVANCED_PRODUCT.format(product_id=candidate), f"/market/products/{candidate}", {}, False, "public product"),
        ])

        for url, path, params, is_auth, _label in attempts:
            try:
                headers = {"Cache-Control": "no-cache"}
                if is_auth:
                    headers["Authorization"] = f"Bearer {self._jwt_token('GET', path)}"
                r = self.http.get(url, params=params, headers=headers)
                r.raise_for_status()
                payload = r.json()
                df = self._ticker_to_frame(payload, minutes=minutes, bars=bars)
                if not df.empty:
                    # Hydrate product metadata opportunistically from product endpoint responses.
                    if isinstance(payload, dict):
                        product_payload = payload.get("product") if isinstance(payload.get("product"), dict) else payload
                        pid = sanitize_product_id(product_payload.get("product_id") if isinstance(product_payload, dict) else "", default="")
                        if pid:
                            self.product_metadata_by_id[pid] = product_payload
                    return df
            except Exception:
                continue
        return pd.DataFrame()

    # Backward-compatible name used by older patched blocks.
    def _advanced_ticker_fallback(self, product_id: str, minutes: int, bars: int, start_ts: int, end_ts: int) -> pd.DataFrame:
        return self._advanced_marketdata_fallback(product_id, minutes, bars, start_ts, end_ts)

    def _validate_or_discover_advanced_product(self, product_id: str) -> Tuple[List[str], List[str]]:
        """Return exact/nearby Coinbase product ids, using product metadata when available.

        Typed or pasted expiring futures symbols can be stale or normalized
        differently than the trade UI. Before declaring an INVALID_ARGUMENT fatal,
        try exact product lookup and then use already-loaded product metadata to
        offer nearby unexpired products with the same root.
        """
        requested = sanitize_product_id(product_id, default="")
        errors: List[str] = []
        candidates = self._candidate_product_ids(requested)

        if requested and requested.endswith("-CDE"):
            root = requested.split("-", 1)[0]
            # Add nearby products from the product cache/list, e.g. GOL-* or NOL-*.
            for pid, meta in sorted(self.product_metadata_by_id.items()):
                clean = sanitize_product_id(pid, default="")
                if clean and clean.startswith(root + "-") and clean.endswith("-CDE") and clean not in candidates:
                    candidates.append(clean)

        # Exact product lookup validates UI-linked futures and also hydrates metadata.
        for candidate in list(candidates):
            for url, path, is_auth in (
                (COINBASE_AUTH_ADVANCED_PRODUCT.format(product_id=candidate), f"/products/{candidate}", True),
                (COINBASE_PUBLIC_ADVANCED_PRODUCT.format(product_id=candidate), f"/market/products/{candidate}", False),
            ):
                if is_auth and not (self.secrets and jwt is not None and not self._auth_disabled_error):
                    continue
                try:
                    headers = {"Cache-Control": "no-cache"}
                    if is_auth:
                        headers["Authorization"] = f"Bearer {self._jwt_token('GET', path)}"
                    r = self.http.get(url, headers=headers)
                    r.raise_for_status()
                    payload = r.json()
                    product_payload = payload.get("product") if isinstance(payload.get("product"), dict) else payload
                    pid = sanitize_product_id(product_payload.get("product_id") if isinstance(product_payload, dict) else candidate, default="")
                    if pid:
                        self.product_metadata_by_id[pid] = product_payload if isinstance(product_payload, dict) else {"product_id": pid}
                        if pid not in candidates:
                            candidates.insert(0, pid)
                    break
                except Exception as exc:
                    msg = self._format_request_error(exc)
                    if "INVALID_ARGUMENT" not in msg and "404" not in msg:
                        errors.append(f"product lookup failed for {candidate}: {msg}")
                    continue

        # De-dupe after metadata additions.
        seen: List[str] = []
        for candidate in candidates:
            clean = sanitize_product_id(candidate, default="")
            if clean and clean not in seen:
                seen.append(clean)
        return seen, errors

    def public_candles(self, product_id: str, minutes: int, bars: int) -> pd.DataFrame:
        requested_minutes = max(1, int(minutes))
        if requested_minutes in RESAMPLE_TIMEFRAME_MAP:
            base_minutes, rule, multiplier = RESAMPLE_TIMEFRAME_MAP[requested_minutes]
            base_bars = min(350, max(25, int(bars) * int(multiplier)))
            base_df = self.public_candles(product_id, base_minutes, base_bars)
            base_error = self.last_public_error
            out = self._resample_candles(base_df, rule, bars)
            if not out.empty:
                self.last_public_error = ""
                return out
            if base_df.empty:
                self.last_public_error = f"Could not build {requested_minutes}m candles for {product_id}: base {base_minutes}m candles failed. {base_error}".strip()
            else:
                self.last_public_error = f"Could not locally resample {base_minutes}m candles into {requested_minutes}m candles for {product_id}."
            return pd.DataFrame()
        advanced_granularity = ADVANCED_GRANULARITY_MAP.get(requested_minutes)
        intx_granularity = INTX_GRANULARITY_MAP.get(requested_minutes)
        granularity = max(60, requested_minutes * 60)
        end_ts = int(time.time())
        intx_limit = min(350, max(25, int(bars)))
        advanced_limit = min(350, max(25, int(bars)))
        exchange_limit = min(300, max(25, int(bars)))
        intx_start_ts = end_ts - granularity * intx_limit
        advanced_start_ts = end_ts - granularity * advanced_limit
        exchange_start_ts = end_ts - granularity * exchange_limit
        errors: List[str] = []
        requested_product = sanitize_product_id(product_id, default="")
        is_futures = self._is_futures_product(requested_product)
        if is_futures or requested_product.endswith("-CDE"):
            candidates, product_lookup_errors = self._validate_or_discover_advanced_product(requested_product)
            errors.extend(product_lookup_errors[:2])
        else:
            candidates = self._candidate_product_ids(requested_product)

        if intx_granularity and "-PERP" in requested_product:
            for candidate in self._intx_candidates(requested_product):
                try:
                    r = self.http.get(
                        COINBASE_INTX_CANDLES.format(instrument=candidate),
                        params={
                            "granularity": intx_granularity,
                            "start": self._iso_utc(intx_start_ts),
                            "end": self._iso_utc(end_ts),
                        },
                        headers={"Cache-Control": "no-cache"},
                    )
                    r.raise_for_status()
                    df = self._candles_to_frame(r.json(), source="intx", bars=bars)
                    if not df.empty:
                        self.last_public_error = ""
                        return df
                    errors.append(f"intx candles returned 0 rows for {candidate}")
                except Exception as exc:
                    errors.append(f"intx candles failed for {candidate}: {self._format_request_error(exc)}")

        if advanced_granularity:
            for candidate in candidates:
                if self.secrets and jwt is not None and not self._auth_disabled_error:
                    try:
                        path = f"/products/{candidate}/candles"
                        token = self._jwt_token("GET", path)
                        r = self.http.get(
                            COINBASE_AUTH_ADVANCED_CANDLES.format(product_id=candidate),
                            params={
                                "granularity": advanced_granularity,
                                "start": str(advanced_start_ts),
                                "end": str(end_ts),
                                "limit": advanced_limit,
                            },
                            headers={"Cache-Control": "no-cache", "Authorization": f"Bearer {token}"},
                        )
                        r.raise_for_status()
                        df = self._candles_to_frame(r.json(), source="advanced", bars=bars)
                        if not df.empty:
                            self.last_public_error = ""
                            return df
                        errors.append(f"advanced auth candles returned 0 rows for {candidate}")
                        if is_futures:
                            df = self._advanced_ticker_fallback(candidate, requested_minutes, bars, advanced_start_ts, end_ts)
                            if not df.empty:
                                self.last_public_error = (
                                    f"{candidate}: candle endpoint returned 0 rows; using Advanced ticker/trades fallback. "
                                    "Treat candle history as degraded for thin CFM futures."
                                )
                                return df
                    except Exception as exc:
                        msg = self._format_request_error(exc)
                        if self._looks_like_private_key_error(exc):
                            self._auth_disabled_error = self._friendly_private_key_error()
                            errors.append(f"advanced auth candles skipped: {self._auth_disabled_error}")
                        else:
                            errors.append(f"advanced auth candles failed for {candidate}: {msg}")
                            if is_futures and ("INVALID_ARGUMENT" in msg or "product_id argument is invalid" in msg):
                                df = self._advanced_marketdata_fallback(candidate, requested_minutes, bars, advanced_start_ts, end_ts)
                                if not df.empty:
                                    self.last_public_error = (
                                        f"{candidate}: candle endpoint rejected/returned no history; using Advanced market-data fallback "
                                        "(ticker/book/product). Treat candle history as degraded."
                                    )
                                    return df
                elif self.secrets and self._auth_disabled_error:
                    errors.append(f"advanced auth candles skipped: {self._auth_disabled_error}")
                elif self.secrets and jwt is None:
                    errors.append("advanced auth candles skipped: PyJWT is not installed. Run: pip install PyJWT")

                try:
                    r = self.http.get(
                        COINBASE_PUBLIC_ADVANCED_CANDLES.format(product_id=candidate),
                        params={
                            "granularity": advanced_granularity,
                            "start": str(advanced_start_ts),
                            "end": str(end_ts),
                            "limit": advanced_limit,
                        },
                        headers={"Cache-Control": "no-cache"},
                    )
                    r.raise_for_status()
                    df = self._candles_to_frame(r.json(), source="advanced", bars=bars)
                    if not df.empty:
                        self.last_public_error = ""
                        return df
                    errors.append(f"advanced public candles returned 0 rows for {candidate}")
                    if is_futures:
                        df = self._advanced_ticker_fallback(candidate, requested_minutes, bars, advanced_start_ts, end_ts)
                        if not df.empty:
                            self.last_public_error = (
                                f"{candidate}: candle endpoint returned 0 rows; using Advanced ticker/trades fallback. "
                                "Treat candle history as degraded for thin CFM futures."
                            )
                            return df
                except Exception as exc:
                    msg = self._format_request_error(exc)
                    errors.append(f"advanced public candles failed for {candidate}: {msg}")
                    if is_futures and ("INVALID_ARGUMENT" in msg or "product_id argument is invalid" in msg):
                        df = self._advanced_marketdata_fallback(candidate, requested_minutes, bars, advanced_start_ts, end_ts)
                        if not df.empty:
                            self.last_public_error = (
                                f"{candidate}: public candle endpoint rejected/returned no history; using Advanced market-data fallback "
                                "(ticker/book/product). Treat candle history as degraded."
                            )
                            return df

        # Coinbase Exchange API does not know Advanced Trade CFM futures symbols, so skip that noisy 404 path.
        # For USDC-quoted products, still allow the USD market-data candidate through Exchange
        # (example: ARB-USDC -> ARB-USD), but do not hit Exchange with the USDC candidate itself.
        if not is_futures:
            for candidate in candidates:
                if candidate.endswith("-USDC"):
                    errors.append(f"exchange candles skipped for {candidate}: trying USD market-data candidates instead")
                    continue
                try:
                    r = self.http.get(
                        COINBASE_EXCHANGE_CANDLES.format(product_id=candidate),
                        params={"granularity": granularity, "start": exchange_start_ts, "end": end_ts},
                        headers={"Cache-Control": "no-cache"},
                    )
                    r.raise_for_status()
                    df = self._candles_to_frame(r.json(), source="exchange", bars=bars)
                    if not df.empty:
                        self.last_public_error = ""
                        return df
                    errors.append(f"exchange candles returned 0 rows for {candidate}")
                except Exception as exc:
                    errors.append(f"exchange candles failed for {candidate}: {self._format_request_error(exc)}")

                try:
                    r = self.http.get(
                        COINBASE_EXCHANGE_CANDLES.format(product_id=candidate),
                        params={"granularity": granularity},
                        headers={"Cache-Control": "no-cache"},
                    )
                    r.raise_for_status()
                    df = self._candles_to_frame(r.json(), source="exchange", bars=bars)
                    if not df.empty:
                        self.last_public_error = ""
                        return df
                    errors.append(f"exchange latest candles returned 0 rows for {candidate}")
                except Exception as exc:
                    errors.append(f"exchange latest candles failed for {candidate}: {self._format_request_error(exc)}")
        else:
            errors.append(
                f"exchange candles skipped for {requested_product}: CFM futures use Coinbase Advanced Trade API, "
                "not Coinbase Exchange. No separate CDE/FairX API key is required; use the CDP/Advanced JWT key from Coinbase API settings."
            )

        # Keep the UI readable: de-duplicate, cap repeated PyJWT warnings, and avoid multi-screen exception spam.
        compact_errors: List[str] = []
        for err in errors:
            if err not in compact_errors:
                compact_errors.append(err)
        if is_futures and jwt is None and self.secrets:
            compact_errors.insert(0, "Install PyJWT to use authenticated Coinbase futures candles: pip install PyJWT")
        self.last_public_error = "; ".join(compact_errors[:5]) if compact_errors else f"no candle data returned for {requested_product}"
        return pd.DataFrame()

    @staticmethod
    def _json_obj_from_text(value: Any) -> Dict[str, Any]:
        text = str(value or "").strip()
        if not text:
            return {}
        try:
            loaded = json.loads(text)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _extract_api_key_name(value: Any) -> str:
        """Return the exact CDP API key name expected in JWT iss/sub/kid."""
        text = sanitize_structured_text(value or "", max_chars=20000).strip()
        obj = CoinbaseAdvancedClient._json_obj_from_text(text)
        if obj:
            for key in ("name", "apiKeyName", "api_key_name", "keyName", "key_name"):
                candidate = sanitize_structured_text(obj.get(key) or "", max_chars=5000).strip()
                if candidate:
                    return candidate
            # Last resort only: older exports/tools may call it id. Coinbase usually wants full 'name'.
            candidate = sanitize_structured_text(obj.get("id") or obj.get("keyId") or obj.get("key_id") or "", max_chars=5000).strip()
            if candidate:
                return candidate
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()
        return text

    @staticmethod
    def _normalize_private_key_input(value: Any) -> str:
        """Accept Coinbase CDP private keys pasted as JSON, PEM, or escaped one-line PEM.

        Coinbase often displays the key as a JSON string like:
        -----BEGIN EC PRIVATE KEY-----\n...==\n-----END EC PRIVATE KEY-----\n

        Tk text boxes and JSON exports can preserve the two characters \ and n instead
        of real newlines. Normalize that form into a PEM that PyJWT/cryptography can load.
        """
        text = str(value or "").strip()
        obj = CoinbaseAdvancedClient._json_obj_from_text(text)
        if obj:
            for key in ("privateKey", "private_key", "pem", "secret"):
                candidate = obj.get(key)
                if candidate:
                    text = str(candidate).strip()
                    break
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()
        text = (
            text.replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\u000a", "\n")
            .replace("\u000d", "\n")
        )
        text = CONTROL_CHARS_RE.sub(" ", text).strip()
        for marker in ("EC PRIVATE KEY", "PRIVATE KEY"):
            begin = f"-----BEGIN {marker}-----"
            end = f"-----END {marker}-----"
            if begin in text and end in text:
                body = text.split(begin, 1)[1].split(end, 1)[0]
                compact_body = re.sub(r"[^A-Za-z0-9+/=]", "", body)
                if compact_body:
                    wrapped_body = "\n".join(compact_body[i : i + 64] for i in range(0, len(compact_body), 64))
                    return f"{begin}\n{wrapped_body}\n{end}\n"
        return text

    @staticmethod
    def _validate_private_key_pem(value: str) -> Tuple[bool, str]:
        pem = CoinbaseAdvancedClient._normalize_private_key_input(value)
        if not pem:
            return False, "Private key is empty."
        if "-----BEGIN" not in pem or "-----END" not in pem:
            return False, "Private key is not a PEM block. Paste the full BEGIN/END EC PRIVATE KEY text or the JSON privateKey field."
        try:
            serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
            return True, "Private key PEM loaded successfully."
        except TypeError:
            return False, "Private key appears encrypted with a passphrase; Coinbase CDP JWT signing needs the unencrypted privateKey export."
        except Exception as exc:
            return False, f"Private key PEM could not be loaded after newline repair: {exc}"

    @staticmethod
    def extract_coinbase_credentials(api_key_input: Any, private_key_input: Any) -> Tuple[str, str, List[str]]:
        """Accept separate fields or a pasted CDP JSON key and return (api_key_name, private_key_pem, notes)."""
        notes: List[str] = []
        api_text = str(api_key_input or "").strip()
        key_text = str(private_key_input or "").strip()
        api_obj = CoinbaseAdvancedClient._json_obj_from_text(api_text)
        key_obj = CoinbaseAdvancedClient._json_obj_from_text(key_text)
        combined = api_obj or key_obj
        api_key = CoinbaseAdvancedClient._extract_api_key_name(api_text)
        private_key = CoinbaseAdvancedClient._normalize_private_key_input(key_text)
        if combined:
            from_json_api = CoinbaseAdvancedClient._extract_api_key_name(json.dumps(combined))
            from_json_key = CoinbaseAdvancedClient._normalize_private_key_input(json.dumps(combined))
            if from_json_api and (not api_key or api_obj):
                api_key = from_json_api
            if from_json_key and (not private_key or key_obj):
                private_key = from_json_key
            notes.append("Detected Coinbase CDP JSON key export and extracted name/privateKey fields.")
        if api_key and not api_key.startswith("organizations/"):
            notes.append("API key does not look like the full CDP name organizations/{org}/apiKeys/{key}; Coinbase may return 401 key unrecognized.")
        return api_key.strip(), private_key.strip(), notes

    @staticmethod
    def _looks_like_private_key_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "could not deserialize key data" in msg
            or "unsupported key type" in msg
            or ("unsupported" in msg and "openssl" in msg)
            or "invalid key" in msg
        )

    @staticmethod
    def _friendly_private_key_error() -> str:
        return (
            "Coinbase private key format is invalid. Paste the full EC private key PEM exactly as exported, "
            "including BEGIN/END lines. Escaped newline markers are OK; the app will repair them. "
            "Do not paste a Coinbase API secret/password or public key."
        )

    def _jwt_token(self, method: str, path: str) -> str:
        if jwt is None:
            raise RuntimeError("PyJWT is required.")
        api_key, private_key, _ = self.extract_coinbase_credentials(
            self.secrets.get("coinbase_api_key") or "",
            self.secrets.get("coinbase_private_key") or "",
        )
        algorithm = sanitize_structured_text(self.settings.data.get("jwt_algorithm", "ES256"), max_chars=24).strip() or "ES256"
        if not api_key or not private_key:
            raise RuntimeError("Coinbase credentials are required.")

        # Coinbase Advanced REST JWTs must be signed against the full v3 brokerage
        # request URI, e.g. "GET api.coinbase.com/api/v3/brokerage/accounts".
        # The app passes short Advanced paths such as "/products/.../candles", so
        # normalize them here before building the token.
        clean_path = str(path or "").strip()
        if not clean_path.startswith("/"):
            clean_path = f"/{clean_path}"
        if not clean_path.startswith("/api/v3/brokerage"):
            clean_path = f"/api/v3/brokerage{clean_path}"
        signed_uri = f"{method.upper()} api.coinbase.com{clean_path}"

        try:
            signing_key = serialization.load_pem_private_key(private_key.encode("utf-8"), password=None)
        except Exception as exc:
            if self._looks_like_private_key_error(exc):
                raise RuntimeError(self._friendly_private_key_error()) from exc
            raise

        now = int(time.time())
        payload = {
            "sub": api_key,
            "iss": "cdp",
            "nbf": now,
            "exp": now + 120,
            "uri": signed_uri,
        }
        try:
            token = jwt.encode(
                payload,
                signing_key,
                algorithm=algorithm,
                headers={"kid": api_key, "nonce": uuid.uuid4().hex},
            )
        except Exception as exc:
            if self._looks_like_private_key_error(exc):
                raise RuntimeError(self._friendly_private_key_error()) from exc
            raise
        return token if isinstance(token, str) else token.decode("utf-8")

    def _private_request(self, method: str, path: str, json_body: Optional[dict] = None) -> dict:
        token = self._jwt_token(method, path)
        r = self.http.request(
            method,
            f"{COINBASE_ADVANCED_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=json_body,
        )
        r.raise_for_status()
        return r.json()

    def list_products(self, *, master_key: Optional[bytes] = None, cache: Optional[EncryptedProductStore] = None) -> List[Dict[str, Any]]:
        """Load Coinbase products, including CFM futures across commodity, stock, ETF, and index underlyings."""
        products_by_id: Dict[str, Dict[str, Any]] = {}
        errors: List[str] = []

        futures_underlying_types = (
            "FUTURES_UNDERLYING_TYPE_SPOT",
            "FUTURES_UNDERLYING_TYPE_INDEX",
            "FUTURES_UNDERLYING_TYPE_EQUITY",
            "FUTURES_UNDERLYING_TYPE_EQUITY_INDEX",
            "FUTURES_UNDERLYING_TYPE_EQUITY_ETF",
            "FUTURES_UNDERLYING_TYPE_PREIPO",
            "FUTURES_UNDERLYING_TYPE_COMMOD",
            "FUTURES_UNDERLYING_TYPE_COMMOD_ETF",
            "FUTURES_UNDERLYING_TYPE_COMMOD_INDEX",
            "FUTURES_UNDERLYING_TYPE_ADR",
            "FUTURES_UNDERLYING_TYPE_FOREIGN_EQUITY",
            "FUTURES_UNDERLYING_TYPE_OTC",
        )

        def add_many(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                pid = sanitize_product_id(item.get("product_id"), default="")
                if pid:
                    item["product_id"] = pid
                    products_by_id[pid] = item

        def fetch_paginated_products(*, url: str, base_params: Dict[str, Any], auth: bool, label: str) -> None:
            cursor = None
            while True:
                request_params = dict(base_params)
                if cursor:
                    request_params["cursor"] = cursor
                try:
                    headers = {"Cache-Control": "no-cache"}
                    if auth:
                        token = self._jwt_token("GET", COINBASE_AUTH_PRODUCTS_PATH)
                        headers["Authorization"] = f"Bearer {token}"
                    r = self.http.get(url, params=request_params, headers=headers)
                    r.raise_for_status()
                    payload = r.json()
                    add_many(payload.get("products", []))
                    pagination = payload.get("pagination") or {}
                    cursor = pagination.get("next_cursor") if pagination.get("has_next") else None
                    if not cursor:
                        break
                except Exception as exc:
                    errors.append(f"{label} products failed for {request_params}: {self._format_request_error(exc)}")
                    break

        core_queries: List[Dict[str, Any]] = [
            {"limit": 250},
            {"limit": 250, "product_type": "SPOT"},
            {"limit": 250, "product_type": "FUTURE", "contract_expiry_type": "EXPIRING", "expiring_contract_status": "STATUS_UNEXPIRED"},
            {"limit": 250, "product_type": "FUTURE", "contract_expiry_type": "PERPETUAL"},
        ]
        futures_underlying_queries: List[Dict[str, Any]] = [
            {
                "limit": 250,
                "product_type": "FUTURE",
                "futures_underlying_type": underlying,
                "expiring_contract_status": "STATUS_UNEXPIRED",
            }
            for underlying in futures_underlying_types
        ]

        if self.secrets:
            auth_queries = [{"limit": 250, "get_all_products": "true"}] + core_queries + futures_underlying_queries
            for params in auth_queries:
                fetch_paginated_products(
                    url=f"{COINBASE_ADVANCED_BASE}{COINBASE_AUTH_PRODUCTS_PATH}",
                    base_params=params,
                    auth=True,
                    label="auth",
                )

        public_queries = core_queries + futures_underlying_queries
        for params in public_queries:
            fetch_paginated_products(
                url=COINBASE_PUBLIC_PRODUCTS,
                base_params=params,
                auth=False,
                label="public",
            )

        # Keep a known oil futures symbol visible even before a successful authenticated refresh.
        if "NOL-18MAY26-CDE" not in products_by_id:
            products_by_id["NOL-18MAY26-CDE"] = {
                "product_id": "NOL-18MAY26-CDE",
                "display_name": "nano Crude Oil Futures 18 MAY 26",
                "product_type": "FUTURE",
                "product_venue": "CFM",
                "future_product_details": {
                    "contract_code": "NOL",
                    "contract_expiry_name": "18 MAY 26",
                    "futures_asset_type": "FUTURES_ASSET_TYPE_COMMOD",
                    "non_crypto": True,
                },
            }

        def sort_key(product: Dict[str, Any]) -> Tuple[int, str, str]:
            ptype = str(product.get("product_type") or "").upper()
            details = product.get("future_product_details") if isinstance(product.get("future_product_details"), dict) else {}
            futures_tag = str(
                details.get("futures_asset_type")
                or (details.get("perpetual_details") or {}).get("underlying_type")
                or ""
            ).upper()
            is_non_crypto_future = ptype == "FUTURE" and (
                bool(details.get("non_crypto"))
                or any(tag in futures_tag for tag in ("COMMOD", "EQUITY", "INDEX", "ETF", "ADR", "PREIPO", "OTC"))
            )
            priority = 0 if is_non_crypto_future else 1 if ptype == "FUTURE" else 2
            return (priority, str(product.get("display_name") or product.get("product_id") or ""), str(product.get("product_id") or ""))

        products = sorted(products_by_id.values(), key=sort_key)
        self.product_metadata_by_id = {sanitize_product_id(p.get("product_id"), default=""): p for p in products if sanitize_product_id(p.get("product_id"), default="")}
        if products and cache is not None and master_key:
            cache.save_products(products, master_key)
        self.last_public_error = "; ".join(errors[:4]) if errors and not products else ""
        return products
    def preview_market_order(self, side: str, quote_size: str, product_id: str, leverage: Optional[str], margin_type: Optional[str]) -> dict:
        payload = {
            "product_id": product_id,
            "side": side.upper(),
            "order_configuration": {"market_market_ioc": {"quote_size": str(quote_size), "rfq_disabled": True}},
        }
        if leverage:
            payload["leverage"] = str(leverage)
        if margin_type:
            payload["margin_type"] = str(margin_type).upper()
        return self._private_request("POST", "/orders/preview", payload)

    def place_market_order(self, side: str, quote_size: str, product_id: str, leverage: Optional[str], margin_type: Optional[str], preview_id: Optional[str]) -> dict:
        payload = {
            "client_order_id": str(uuid.uuid4()),
            "product_id": product_id,
            "side": side.upper(),
            "order_configuration": {"market_market_ioc": {"quote_size": str(quote_size), "rfq_disabled": True}},
        }
        if leverage:
            payload["leverage"] = str(leverage)
        if margin_type:
            payload["margin_type"] = str(margin_type).upper()
        if preview_id:
            payload["preview_id"] = str(preview_id)
        return self._private_request("POST", "/orders", payload)


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for span in EMA_SPANS:
        out[f"EMA{span}"] = out["Close"].ewm(span=span, adjust=False).mean()

    ribbon_cols = [f"EMA{s}" for s in PRIMARY_EMA_SPANS]
    out["RIBBON_LOW"] = out[ribbon_cols].min(axis=1)
    out["RIBBON_HIGH"] = out[ribbon_cols].max(axis=1)
    out["RIBBON_SPREAD_PCT"] = ((out["RIBBON_HIGH"] - out["RIBBON_LOW"]) / out["Close"].replace(0, np.nan)).fillna(0.0)

    delta = out["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    out["RSI14"] = (100 - (100 / (1 + rs))).fillna(50)

    ema12 = out["Close"].ewm(span=12, adjust=False).mean()
    ema26 = out["Close"].ewm(span=26, adjust=False).mean()
    out["MACD"] = ema12 - ema26
    out["MACD_SIGNAL"] = out["MACD"].ewm(span=9, adjust=False).mean()
    out["MACD_HIST"] = out["MACD"] - out["MACD_SIGNAL"]

    prev_close = out["Close"].shift(1)
    tr = pd.concat(
        [out["High"] - out["Low"], (out["High"] - prev_close).abs(), (out["Low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    out["ATR14"] = tr.rolling(14).mean()
    out["ATR_PCT"] = (out["ATR14"] / out["Close"]).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    out["RET_1"] = out["Close"].pct_change().replace([np.inf, -np.inf], np.nan)
    out["RET_STD_20"] = out["RET_1"].rolling(20).std().fillna(0.0)
    out["ROLLING_ENTROPY_24"] = out["RET_1"].rolling(24).apply(lambda values: hist_entropy(np.asarray(values, dtype=float), bins=7), raw=True).fillna(0.5)

    out["VOL_MA20"] = out["Volume"].rolling(20).mean()
    out["VOL_Z"] = ((out["Volume"] - out["VOL_MA20"]) / out["Volume"].rolling(20).std().replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out["VWMA20"] = ((out["Close"] * out["Volume"]).rolling(20).sum() / out["Volume"].rolling(20).sum().replace(0, np.nan)).bfill().fillna(out["Close"])

    body = (out["Close"] - out["Open"]).abs()
    full_range = (out["High"] - out["Low"]).replace(0, np.nan)
    upper_wick = out["High"] - out[["Close", "Open"]].max(axis=1)
    lower_wick = out[["Close", "Open"]].min(axis=1) - out["Low"]
    out["BODY_TO_RANGE"] = (body / full_range).fillna(0.0)
    out["WICK_IMBALANCE"] = ((lower_wick - upper_wick) / full_range).fillna(0.0)
    out["CLV"] = (((out["Close"] - out["Low"]) - (out["High"] - out["Close"])) / full_range).fillna(0.0)
    out["SIGNED_FLOW"] = out["Volume"] * np.sign(out["Close"].diff().fillna(0.0)) * out["CLV"]
    out["FLOW_IMPULSE"] = out["SIGNED_FLOW"].rolling(5).sum().fillna(0.0)
    out["FLOW_Z"] = ((out["FLOW_IMPULSE"] - out["FLOW_IMPULSE"].rolling(20).mean()) / out["FLOW_IMPULSE"].rolling(20).std().replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out["LIQ_HIGH_20"] = out["High"].rolling(20).max().shift(1)
    out["LIQ_LOW_20"] = out["Low"].rolling(20).min().shift(1)
    out["TREND_PRESSURE"] = (((out["EMA8"] - out["EMA21"]) / out["Close"].replace(0, np.nan)) * 100.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out["CARRY_PRESSURE"] = (((out["EMA21"] - out["EMA89"]) / out["Close"].replace(0, np.nan)) * 100.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out["ENERGY_SCORE"] = ((out["VOL_Z"].abs() * 0.30) + (out["MACD_HIST"].abs() * 8.0) + (out["RIBBON_SPREAD_PCT"] * 120.0)).fillna(0.0)
    return out.replace([np.inf, -np.inf], np.nan).ffill().bfill()


@dataclass
class TimeframePacket:
    minutes: int
    last_price: float
    trend: float
    momentum: float
    structure: float
    volatility: float
    volume_pressure: float
    entropy: float
    sweep_score: float
    ribbon_pressure: float


@dataclass
class SensorPacket:
    rgb: Tuple[int, int, int]
    quantum_vector: Tuple[float, float, float, float]
    quantum_alignment: float
    entropic_gain: float
    system_stress: float
    text: str


@dataclass
class MemoryHit:
    score: float
    signal: str
    regime: str
    action: str
    theory: str
    summary: str


@dataclass
class TheoryPacket:
    coherence_field: float
    liquidity_sweep: float
    compression_breakout: float
    reflexive_acceleration: float
    entropic_drift: float
    funding_pressure_tensor: float
    basis_dislocation_pulse: float
    liquidation_ladder_magnetism: float
    inventory_imbalance_resonance: float
    convexity_trap_gradient: float
    term_structure_shear: float
    maker_absorption_edge: float
    funding_reflex_inversion: float
    entropic_carry_harvest: float
    regime_handoff_arbitrage: float
    basis_pct: float
    basis_z: float
    risk_fragility: float
    total_edge: float
    dominant_theory: str
    quantum_alignment: float
    entropic_gain: float


@dataclass
class SurfaceState:
    price: float
    reference_price: float
    state_vector: Tuple[float, float, float]
    phase_position: int
    color_encoding: str
    confidence_level: float
    anomaly_status: str
    dominant_signal: str
    regime: str
    signal_score: float
    theory_packet: TheoryPacket
    timeframes: List[TimeframePacket]
    summary: str
    suggested_action: str
    suggested_notional_usd: float
    top_theories: List[Tuple[str, float]]
    sensor_packet: Optional[SensorPacket] = None
    memory_hits: List[MemoryHit] = field(default_factory=list)


class EntropicRAGMemory:
    def __init__(self, limit: int = 120):
        self.entries: deque = deque(maxlen=max(20, limit))

    @staticmethod
    def _vector(surface: SurfaceState) -> List[float]:
        tp = surface.theory_packet
        q = list(surface.sensor_packet.quantum_vector[:3]) if surface.sensor_packet else [0.0, 0.0, 0.0]
        return [
            surface.signal_score,
            surface.confidence_level,
            tp.coherence_field,
            tp.reflexive_acceleration,
            tp.entropic_drift,
            tp.funding_pressure_tensor,
            tp.basis_dislocation_pulse,
            tp.inventory_imbalance_resonance,
            tp.convexity_trap_gradient,
            tp.term_structure_shear,
            tp.maker_absorption_edge,
            tp.funding_reflex_inversion,
            tp.entropic_carry_harvest,
            tp.regime_handoff_arbitrage,
            tp.basis_pct * 100.0,
            tp.basis_z,
            tp.quantum_alignment,
            tp.entropic_gain,
            *q,
        ]

    def remember(self, surface: SurfaceState) -> None:
        self.entries.append(
            {
                "vector": self._vector(surface),
                "signal": surface.dominant_signal,
                "regime": surface.regime,
                "action": surface.suggested_action,
                "theory": surface.theory_packet.dominant_theory,
                "summary": surface.summary,
            }
        )

    def retrieve(self, surface: SurfaceState, limit: int = 5) -> List[MemoryHit]:
        query = np.array(self._vector(surface), dtype=float)
        hits: List[MemoryHit] = []
        for entry in list(self.entries):
            vec = np.array(entry["vector"], dtype=float)
            size = min(query.size, vec.size)
            if size == 0:
                continue
            a = query[:size]
            b = vec[:size]
            denom = float(np.linalg.norm(a) * np.linalg.norm(b))
            score = 0.0 if denom <= 1e-12 else float(np.dot(a, b) / denom)
            if score < 0.10:
                continue
            hits.append(
                MemoryHit(
                    score=score,
                    signal=entry["signal"],
                    regime=entry["regime"],
                    action=entry["action"],
                    theory=entry["theory"],
                    summary=entry["summary"],
                )
            )
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[: max(1, limit)]


class QuantumRGBSensor:
    def __init__(self):
        self.qnode = None
        if qml is not None:
            try:
                dev = qml.device("default.qubit", wires=4)

                @qml.qnode(dev)
                def circuit(features, weights):
                    qml.AngleEmbedding(features, wires=range(4), rotation="Y")
                    qml.StronglyEntanglingLayers(weights, wires=range(4))
                    return [qml.expval(qml.PauliZ(i)) for i in range(4)]

                self.qnode = circuit
            except Exception:
                self.qnode = None
        self.weights = np.zeros((2, 4, 3), dtype=float)
        for a in range(2):
            for b in range(4):
                for c in range(3):
                    self.weights[a, b, c] = math.sin((a + 1) * (b + 1) * (c + 1) * 0.37)

    def sample(self, primary_df: pd.DataFrame, basis_pct: float) -> SensorPacket:
        latest = primary_df.iloc[-1]
        cpu = float(psutil.cpu_percent(interval=None)) if psutil is not None else 20.0
        mem = float(psutil.virtual_memory().percent) if psutil is not None else 35.0
        disk = float(psutil.disk_usage("/").percent) if psutil is not None else 40.0
        load = float(os.getloadavg()[0] * 100.0 / max(1, os.cpu_count() or 1)) if hasattr(os, "getloadavg") else cpu
        f = np.array(
            [
                clamp(safe_float(latest.get("TREND_PRESSURE")) / 2.5, -1.0, 1.0),
                clamp((safe_float(latest.get("RSI14"), 50.0) - 50.0) / 50.0, -1.0, 1.0),
                clamp(safe_float(latest.get("ATR_PCT")) * 32.0, -1.0, 1.0),
                clamp((basis_pct * 120.0) + safe_float(latest.get("FLOW_Z")) * 0.15, -1.0, 1.0),
            ],
            dtype=float,
        )
        rgb = (
            max(0, min(255, int(round(255 * clamp(0.15 + 0.70 * abs(f[0]) + 0.15 * (cpu / 100.0), 0.0, 1.0))))),
            max(0, min(255, int(round(255 * clamp(0.20 + 0.55 * (1.0 - f[2] * 0.5) + 0.25 * (1.0 - mem / 100.0), 0.0, 1.0))))),
            max(0, min(255, int(round(255 * clamp(0.25 + 0.40 * abs(f[3]) + 0.35 * (1.0 - disk / 100.0), 0.0, 1.0))))),
        )
        if self.qnode is not None:
            try:
                raw = np.array(self.qnode(f, self.weights), dtype=float)
            except Exception:
                raw = np.array(
                    [
                        math.sin(f[0] * math.pi),
                        math.cos(f[1] * math.pi),
                        math.sin((f[2] - f[3]) * math.pi / 2.0),
                        math.cos((f[0] + f[2]) * math.pi / 2.0),
                    ],
                    dtype=float,
                )
        else:
            raw = np.array(
                [
                    math.sin(f[0] * math.pi),
                    math.cos(f[1] * math.pi),
                    math.sin((f[2] - f[3]) * math.pi / 2.0),
                    math.cos((f[0] + f[2]) * math.pi / 2.0),
                ],
                dtype=float,
            )
        qvec = tuple(float(np.clip(x, -1.0, 1.0)) for x in raw[:4])
        qalign = clamp(float(np.mean(raw[:3])), -1.0, 1.0)
        stress = clamp((cpu / 100.0) * 0.35 + (mem / 100.0) * 0.25 + (disk / 100.0) * 0.10 + (load / 100.0) * 0.30, 0.0, 1.0)
        gain = clamp((0.34 * (1.0 - abs(f[2]))) + (0.36 * max(0.0, qalign)) + (0.30 * (1.0 - stress)), 0.0, 1.0)
        text = f"RGB {rgb} · quantum {tuple(round(v, 3) for v in qvec)} · align {qalign:+.2f} · gain {gain:.2f} · stress {stress:.2f}"
        return SensorPacket(
            rgb=rgb,
            quantum_vector=qvec,
            quantum_alignment=qalign,
            entropic_gain=gain,
            system_stress=stress,
            text=text,
        )


class AdvancedAlphaEngine:
    @staticmethod
    def _norm(value: float, scale: float = 1.0) -> float:
        return clamp(math.tanh(safe_float(value) * scale), -1.0, 1.0)

    def _packet(self, minutes: int, df: pd.DataFrame) -> TimeframePacket:
        latest = df.iloc[-1]
        close = safe_float(latest["Close"])
        trend = self._norm((safe_float(latest.get("EMA8"), close) - safe_float(latest.get("EMA21"), close)) / max(close, 1e-9), 26)
        structure = self._norm((safe_float(latest.get("EMA21"), close) - safe_float(latest.get("EMA89"), close)) / max(close, 1e-9), 20)
        momentum = self._norm(
            safe_float(latest.get("MACD_HIST")) * 42 + ((safe_float(latest.get("RSI14"), 50.0) - 50.0) / 50.0) * 0.8
        )
        volatility = self._norm(safe_float(latest.get("ATR_PCT")), 24)
        volume_pressure = self._norm(
            safe_float(latest.get("VOL_Z")) * 0.62
            + safe_float(latest.get("FLOW_IMPULSE")) / max(1.0, safe_float(df["Volume"].tail(12).mean(), 1.0)) * 4.2
        )
        entropy = hist_entropy(df["RET_1"].dropna().tail(48).to_numpy(), bins=7)
        sweep_score = self._norm(
            (safe_float(latest.get("WICK_IMBALANCE")) * 1.8)
            + (safe_float(latest.get("CLV")) * 1.2)
            - (safe_float(latest.get("BODY_TO_RANGE"), 0.5) * 0.7)
        )
        ribbon_pressure = self._norm(safe_float(latest.get("RIBBON_SPREAD_PCT")) * 110.0)
        return TimeframePacket(
            minutes=minutes,
            last_price=close,
            trend=trend,
            momentum=momentum,
            structure=structure,
            volatility=volatility,
            volume_pressure=volume_pressure,
            entropy=entropy,
            sweep_score=sweep_score,
            ribbon_pressure=ribbon_pressure,
        )

    def evaluate(self, frames: Dict[int, pd.DataFrame], reference_frames: Dict[int, pd.DataFrame], sensor: Optional[SensorPacket]) -> SurfaceState:
        packets = [self._packet(m, df) for m, df in sorted(frames.items()) if not df.empty]
        if not packets:
            raise RuntimeError("No market frames.")
        sorted_packets = sorted(packets, key=lambda p: p.minutes)
        primary = sorted_packets[len(sorted_packets) // 2]
        lower = sorted_packets[0]
        higher = sorted_packets[-1]

        perp = frames[primary.minutes]["Close"]
        reference_primary = reference_frames.get(primary.minutes)
        if reference_primary is None or reference_primary.empty:
            reference_primary = frames[primary.minutes]
        spot = reference_primary["Close"]
        joined = pd.concat([perp.rename("perp"), spot.rename("spot")], axis=1, join="inner").dropna()
        basis_pct = safe_float(((joined.iloc[-1]["perp"] - joined.iloc[-1]["spot"]) / joined.iloc[-1]["spot"])) if not joined.empty else 0.0
        recent_basis = ((joined["perp"] - joined["spot"]) / joined["spot"]).dropna() if not joined.empty else pd.Series(dtype=float)
        basis_mean = safe_float(recent_basis.tail(72).mean()) if not recent_basis.empty else 0.0
        basis_std = safe_float(recent_basis.tail(72).std()) if not recent_basis.empty else 0.0
        basis_z = (basis_pct - basis_mean) / basis_std if abs(basis_std) > 1e-9 else 0.0

        b = self._norm(basis_pct, 50.0)
        bz = self._norm(basis_z, 0.8)
        q = sensor.quantum_alignment if sensor else 0.0
        g = sensor.entropic_gain if sensor else 0.0
        stress = sensor.system_stress if sensor else 0.0

        coherence_field = clamp(
            primary.trend * 0.22
            + primary.structure * 0.16
            + lower.momentum * 0.10
            + higher.structure * 0.16
            + ((lower.trend + primary.trend + higher.trend) / 3.0) * 0.10
            + b * 0.08
            + primary.volume_pressure * 0.08
            + q * 0.10,
            -1.0,
            1.0,
        )
        liquidity_sweep = clamp(primary.sweep_score * 0.50 + primary.volume_pressure * 0.18 - primary.volatility * 0.16 + b * -0.04, -1.0, 1.0)
        compression_breakout = clamp(
            (1.0 - float(frames[primary.minutes]["ATR_PCT"].tail(50).rank(pct=True).iloc[-1])) * 0.34
            + max(0.0, primary.volume_pressure) * 0.18
            + max(0.0, primary.momentum) * 0.16
            + max(0.0, primary.ribbon_pressure) * 0.12
            + g * 0.10,
            -1.0,
            1.0,
        )
        reflexive_acceleration = clamp(
            primary.trend * 0.22
            + primary.momentum * 0.22
            + higher.trend * 0.12
            + primary.volume_pressure * 0.10
            + b * 0.10
            + primary.ribbon_pressure * 0.12,
            -1.0,
            1.0,
        )
        entropic_drift = clamp((0.5 - primary.entropy) * 1.0 + higher.structure * 0.20 + b * 0.08 + g * 0.12, -1.0, 1.0)
        funding_pressure_tensor = clamp(b * 0.42 + primary.trend * 0.14 + higher.trend * 0.10 + primary.structure * 0.10 + primary.volume_pressure * 0.10, -1.0, 1.0)
        basis_dislocation_pulse = clamp(-b * 0.46 + -bz * 0.16 + primary.sweep_score * 0.14 + (0.5 - primary.entropy) * 0.10, -1.0, 1.0)
        liquidation_ladder_magnetism = clamp(
            primary.sweep_score * 0.28
            + primary.volatility * math.copysign(1.0, primary.momentum if abs(primary.momentum) > 1e-9 else 1.0) * 0.16
            + primary.volume_pressure * 0.16
            + (lower.momentum - primary.momentum) * 0.16
            + bz * 0.08,
            -1.0,
            1.0,
        )
        inventory_imbalance_resonance = clamp(
            primary.volume_pressure * 0.28
            + lower.volume_pressure * 0.18
            + primary.trend * 0.14
            + primary.momentum * 0.12
            + primary.structure * 0.12,
            -1.0,
            1.0,
        )
        convexity_trap_gradient = clamp(
            compression_breakout * 0.24
            + -b * 0.14
            + -bz * 0.10
            + primary.sweep_score * 0.12
            + (abs(primary.momentum) - abs(higher.trend)) * math.copysign(1.0, primary.momentum if abs(primary.momentum) > 1e-9 else 1.0) * 0.12
            + (0.5 - primary.entropy) * 0.10
            + q * 0.08,
            -1.0,
            1.0,
        )

        term_structure_shear = clamp(
            (lower.trend - higher.trend) * 0.30
            + (lower.momentum - primary.momentum) * 0.20
            + bz * 0.14
            + primary.ribbon_pressure * 0.10
            + -stress * 0.10,
            -1.0,
            1.0,
        )
        maker_absorption_edge = clamp(
            primary.sweep_score * 0.24
            + inventory_imbalance_resonance * 0.18
            + (0.5 - primary.volatility) * 0.14
            + (0.5 - primary.entropy) * 0.12
            + q * 0.10,
            -1.0,
            1.0,
        )
        funding_reflex_inversion = clamp(
            -funding_pressure_tensor * 0.34
            + -bz * 0.16
            + (lower.momentum - higher.trend) * 0.16
            + primary.sweep_score * 0.14
            + (0.5 - primary.entropy) * 0.10,
            -1.0,
            1.0,
        )
        entropic_carry_harvest = clamp(
            entropic_drift * 0.28
            + coherence_field * 0.16
            + (0.5 - primary.entropy) * 0.14
            + (1.0 - primary.volatility) * 0.10
            + g * 0.16,
            -1.0,
            1.0,
        )
        regime_handoff_arbitrage = clamp(
            (lower.momentum - primary.momentum) * 0.20
            + (primary.momentum - higher.trend) * 0.18
            + compression_breakout * 0.16
            + convexity_trap_gradient * 0.12
            + basis_dislocation_pulse * 0.12
            + term_structure_shear * 0.12
            + q * 0.10,
            -1.0,
            1.0,
        )

        crowding = max(0.0, abs(b) - 0.22)
        risk_fragility = clamp(
            primary.volatility * 0.28
            + max(0.0, primary.entropy - 0.55) * 0.24
            + crowding * 0.16
            + max(0.0, abs(bz) - 0.25) * 0.10
            + stress * 0.10,
            0.0,
            1.0,
        )

        theories = {
            "COHERENCE_FIELD": coherence_field,
            "LIQUIDITY_SWEEP": liquidity_sweep,
            "COMPRESSION_BREAKOUT": compression_breakout,
            "REFLEXIVE_ACCELERATION": reflexive_acceleration,
            "ENTROPIC_DRIFT": entropic_drift,
            "FUNDING_PRESSURE_TENSOR": funding_pressure_tensor,
            "BASIS_DISLOCATION_PULSE": basis_dislocation_pulse,
            "LIQUIDATION_LADDER_MAGNETISM": liquidation_ladder_magnetism,
            "INVENTORY_IMBALANCE_RESONANCE": inventory_imbalance_resonance,
            "CONVEXITY_TRAP_GRADIENT": convexity_trap_gradient,
            "TERM_STRUCTURE_SHEAR": term_structure_shear,
            "MAKER_ABSORPTION_EDGE": maker_absorption_edge,
            "FUNDING_REFLEX_INVERSION": funding_reflex_inversion,
            "ENTROPIC_CARRY_HARVEST": entropic_carry_harvest,
            "REGIME_HANDOFF_ARBITRAGE": regime_handoff_arbitrage,
        }

        top = sorted(theories.items(), key=lambda item: abs(item[1]), reverse=True)
        total_edge = clamp(
            sum(
                value * weight
                for value, weight in [
                    (coherence_field, 0.11),
                    (liquidity_sweep, 0.06),
                    (compression_breakout, 0.07),
                    (reflexive_acceleration, 0.10),
                    (entropic_drift, 0.07),
                    (funding_pressure_tensor, 0.08),
                    (basis_dislocation_pulse, 0.07),
                    (liquidation_ladder_magnetism, 0.07),
                    (inventory_imbalance_resonance, 0.08),
                    (convexity_trap_gradient, 0.07),
                    (term_structure_shear, 0.06),
                    (maker_absorption_edge, 0.05),
                    (funding_reflex_inversion, 0.05),
                    (entropic_carry_harvest, 0.08),
                    (regime_handoff_arbitrage, 0.06),
                    (q, 0.05),
                    ((g - 0.5), 0.16),
                ]
            )
            - risk_fragility * 0.30,
            -1.0,
            1.0,
        )

        signal = "LONG_BIAS" if total_edge > 0.24 else "SHORT_BIAS" if total_edge < -0.24 else "NEUTRAL"
        regime = (
            "BASIS_STRETCH"
            if abs(basis_pct) > 0.0025 and abs(basis_z) > 1.0
            else "TURBULENT_EXPANSION"
            if np.mean([p.volatility for p in packets]) > 0.58
            else "DIRECTIONAL_DISCOVERY"
            if np.mean([p.entropy for p in packets]) < 0.42 and abs(total_edge) > 0.28
            else "REGIME_HANDOFF"
            if abs(term_structure_shear) > 0.42
            else "MEAN_REVERSION_CHOP"
            if abs(total_edge) < 0.14
            else "TRANSITIONAL"
        )
        anomaly = (
            "BASIS_DISLOCATION"
            if abs(basis_z) > 1.5
            else "LIQUIDATION_SWEEP"
            if abs(liquidation_ladder_magnetism) > 0.58
            else "FRAGILE_REGIME"
            if risk_fragility > 0.62
            else "NONE"
        )
        confidence = clamp(
            abs(total_edge) * 0.34
            + abs(top[0][1]) * 0.10
            + abs(top[1][1]) * 0.09
            + (1.0 - risk_fragility) * 0.20
            + max(0.0, g - 0.4) * 0.18
            + max(0.0, q) * 0.10,
            0.08,
            0.98,
        )
        state_vector = (
            clamp((coherence_field + funding_pressure_tensor + reflexive_acceleration + q) / 4.0, -1.0, 1.0),
            clamp((compression_breakout + liquidation_ladder_magnetism + inventory_imbalance_resonance + term_structure_shear) / 4.0, -1.0, 1.0),
            clamp((entropic_drift + basis_dislocation_pulse - risk_fragility + (g - 0.5)) / 4.0, -1.0, 1.0),
        )
        suggested_action = "WAIT"
        if regime == "MEAN_REVERSION_CHOP" and abs(basis_dislocation_pulse) > 0.24:
            suggested_action = "BASIS_FADE_SCALP"
        elif signal == "LONG_BIAS" and confidence > 0.72 and entropic_carry_harvest > 0.12:
            suggested_action = "PAPER_LONG_PERP"
        elif signal == "SHORT_BIAS" and confidence > 0.72 and funding_reflex_inversion < -0.06:
            suggested_action = "PAPER_SHORT_PERP"
        elif maker_absorption_edge > 0.34 and confidence > 0.62:
            suggested_action = "SELECTIVE_MAKER_LADDER"
        elif entropic_carry_harvest > 0.32 and confidence > 0.66:
            suggested_action = "CARRY_HARVEST_PROBE"
        elif regime == "REGIME_HANDOFF" and abs(regime_handoff_arbitrage) > 0.22:
            suggested_action = "REGIME_HANDOFF_SCALP"
        elif risk_fragility > 0.60:
            suggested_action = "REDUCE_CROWDING_WAIT_FOR_RESET"

        size = round(
            max(0.0, min(1.0, confidence))
            * 40.0
            * max(0.16, 1.0 - risk_fragility)
            * max(0.35, 1.0 - crowding),
            2,
        )
        theory_packet = TheoryPacket(
            coherence_field=coherence_field,
            liquidity_sweep=liquidity_sweep,
            compression_breakout=compression_breakout,
            reflexive_acceleration=reflexive_acceleration,
            entropic_drift=entropic_drift,
            funding_pressure_tensor=funding_pressure_tensor,
            basis_dislocation_pulse=basis_dislocation_pulse,
            liquidation_ladder_magnetism=liquidation_ladder_magnetism,
            inventory_imbalance_resonance=inventory_imbalance_resonance,
            convexity_trap_gradient=convexity_trap_gradient,
            term_structure_shear=term_structure_shear,
            maker_absorption_edge=maker_absorption_edge,
            funding_reflex_inversion=funding_reflex_inversion,
            entropic_carry_harvest=entropic_carry_harvest,
            regime_handoff_arbitrage=regime_handoff_arbitrage,
            basis_pct=basis_pct,
            basis_z=basis_z,
            risk_fragility=risk_fragility,
            total_edge=total_edge,
            dominant_theory=top[0][0],
            quantum_alignment=q,
            entropic_gain=g,
        )
        summary = (
            f"{top[0][0].replace('_', ' ')} dominates. "
            f"Basis {basis_pct*100:+.3f}% (z {basis_z:+.2f}). "
            f"Primary {primary.minutes}m trend {primary.trend:+.2f}, momentum {primary.momentum:+.2f}, "
            f"entropy {primary.entropy:.2f}, vol {primary.volatility:+.2f}. "
            f"Edge {total_edge:+.2f}. Q {q:+.2f}; gain {g:.2f}."
        )
        rgb = sensor.rgb if sensor else (0, 0, 0)
        return SurfaceState(
            price=primary.last_price,
            reference_price=safe_float(joined.iloc[-1]["spot"]) if not joined.empty else primary.last_price,
            state_vector=state_vector,
            phase_position=int(((total_edge + 1.0) / 2.0) * 299) % 300,
            color_encoding=f"RGB-{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}" if sensor else "Q-000000",
            confidence_level=confidence,
            anomaly_status=anomaly,
            dominant_signal=signal,
            regime=regime,
            signal_score=total_edge,
            theory_packet=theory_packet,
            timeframes=packets,
            summary=summary,
            suggested_action=suggested_action,
            suggested_notional_usd=size,
            top_theories=top[:8],
            sensor_packet=sensor,
        )


def require_litert_lm() -> None:
    if litert_lm is None:
        raise RuntimeError(
            "LiteRT-LM is not installed. Install the LiteRT dependencies first so the local Gemma runtime is available."
        )


def _litert_backend_attr(*names: str) -> Any:
    require_litert_lm()
    for name in names:
        try:
            return getattr(litert_lm.Backend, name)
        except Exception:
            continue
    return None


def _litert_cpu_backend() -> Any:
    backend = _litert_backend_attr("CPU")
    if backend is None:
        raise RuntimeError("This LiteRT-LM build does not expose a CPU backend.")
    return backend


def load_litert_engine(model_path: Path, *, enable_vision: bool = False):
    require_litert_lm()
    try:
        litert_lm.set_min_log_severity(litert_lm.LogSeverity.ERROR)
    except Exception:
        pass

    engine_kwargs: Dict[str, Any] = {"cache_dir": str(LITERT_CACHE_DIR)}
    try:
        cpu_backend = _litert_cpu_backend()
        engine_kwargs["backend"] = cpu_backend
        if enable_vision:
            engine_kwargs["vision_backend"] = cpu_backend
    except Exception:
        pass

    try:
        return litert_lm.Engine(str(model_path), **engine_kwargs)
    except TypeError:
        fallback_kwargs = dict(engine_kwargs)
        fallback_kwargs.pop("vision_backend", None)
        return litert_lm.Engine(str(model_path), **fallback_kwargs)


def create_default_messages(system_text: Optional[str] = None) -> List[dict]:
    if not system_text:
        return []
    return [{"role": "system", "content": [{"type": "text", "text": sanitize_structured_text(system_text)}]}]


def response_to_text(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, bytes):
        try:
            return response.decode("utf-8", errors="ignore").strip()
        except Exception:
            return ""
    if isinstance(response, str):
        return response.strip()
    if isinstance(response, list):
        return "".join(response_to_text(item) for item in response).strip()
    if isinstance(response, dict):
        for key in ("text", "output_text", "message", "response", "generated_text"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        texts: List[str] = []
        content = response.get("content", [])
        if isinstance(content, str):
            return content.strip()
        for item in content if isinstance(content, list) else []:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    texts.append(str(item.get("text", "")))
                elif isinstance(item.get("text"), str):
                    texts.append(item.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)
        if texts:
            return "".join(texts).strip()
        # Some bindings return nested candidates/output structures.
        for key in ("candidates", "outputs", "choices"):
            nested = response.get(key)
            text = response_to_text(nested)
            if text:
                return text
        return ""
    for attr in ("text", "output_text", "content", "message", "response"):
        try:
            value = getattr(response, attr)
        except Exception:
            continue
        if callable(value):
            continue
        text = response_to_text(value)
        if text:
            return text
    rendered = str(response).strip()
    return "" if rendered in {"None", "{}", "[]"} else rendered


def create_user_message(user_text: str, image_path: Optional[Path] = None) -> Any:
    clean_text = sanitize_structured_text(user_text)
    if image_path is None:
        return clean_text
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": clean_text},
            {"type": "image", "path": str(image_path)},
        ],
    }


def approximate_litert_tokens(text: str) -> int:
    # Conservative approximation for English + JSON-style market packets.
    # It intentionally overestimates so we stay under LiteRT's hard context limit.
    clean = str(text or "")
    return max(1, int(math.ceil(len(clean) / 2.0)))


def trim_for_litert_context(text: Any, *, max_input_tokens: int = LITERT_SAFE_INPUT_TOKENS) -> str:
    clean = sanitize_structured_text(text, max_chars=50000)
    safe_tokens = max(512, min(int(max_input_tokens), LITERT_CONTEXT_TOKENS - 512))
    char_budget = min(LITERT_CHAR_BUDGET, max(1200, safe_tokens * 2))
    if len(clean) <= char_budget:
        return clean

    marker = (
        "\n\n[... compressed for LiteRT 4096-token context; "
        "older candles / verbose packet fields removed ...]\n\n"
    )
    # Preserve the system framing at the front and the actual request / output
    # instructions at the end. The bulky MARKET_PACKET lives in the middle.
    head_chars = min(1100, max(500, char_budget // 3))
    tail_chars = max(500, char_budget - head_chars - len(marker))
    return (clean[:head_chars].rstrip() + marker + clean[-tail_chars:].lstrip()).strip()


def clamp_litert_output_tokens(max_tokens: Any) -> int:
    try:
        requested = int(max_tokens)
    except Exception:
        requested = LITERT_SAFE_OUTPUT_TOKENS
    return max(64, min(requested, LITERT_SAFE_OUTPUT_TOKENS))


def _short_runtime_detail(text: Any, *, max_chars: int = 1400) -> str:
    clean = sanitize_structured_text(text, max_chars=max_chars * 2)
    return clean if len(clean) <= max_chars else clean[: max_chars - 24].rstrip() + " ... [truncated]"


def generate_text_litert_subprocess(
    *,
    model_path: str,
    prompt: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    timeout_seconds: int = LITERT_SUBPROCESS_TIMEOUT_SECONDS,
) -> str:
    """Run LiteRT text generation in a child Python process.

    Tkinter is single-threaded, and some LiteRT Python bindings keep the GIL
    while decoding. A normal threading.Thread can therefore still starve the
    Tk event loop. This subprocess boundary keeps the GUI responsive and lets
    us kill a stuck generation cleanly.
    """
    clean_prompt = trim_for_litert_context(prompt)
    output_tokens = clamp_litert_output_tokens(max_tokens)
    script_path = Path(__file__).resolve()
    if not script_path.is_file():
        runtime = Gemma4Runtime(model_path)
        return runtime.generate_text(
            clean_prompt,
            temperature=temperature,
            top_p=top_p,
            max_tokens=output_tokens,
        )

    payload = {
        "model_path": str(model_path),
        "prompt": clean_prompt,
        "temperature": float(temperature),
        "top_p": float(top_p),
        "max_tokens": int(output_tokens),
    }
    with tempfile.TemporaryDirectory(prefix="litert_prompt_") as tmp:
        tmp_path = Path(tmp)
        input_path = tmp_path / "request.json"
        output_path = tmp_path / "response.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")

        env = os.environ.copy()
        env["LITERT_CHILD_PROCESS"] = "1"
        # Cap native worker threads so LiteRT generation does not starve Tkinter.
        env.setdefault("OMP_NUM_THREADS", "2")
        env.setdefault("OPENBLAS_NUM_THREADS", "2")
        env.setdefault("MKL_NUM_THREADS", "2")
        env.setdefault("VECLIB_MAXIMUM_THREADS", "2")
        env.setdefault("NUMEXPR_NUM_THREADS", "2")
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [sys.executable, str(script_path), LITERT_WORKER_FLAG, str(input_path), str(output_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
        )
        try:
            stdout, stderr = proc.communicate(timeout=max(30, int(timeout_seconds)))
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            raise TimeoutError(
                f"LiteRT generation exceeded {timeout_seconds}s and was stopped so the GUI stays usable. "
                "Try a shorter prompt, lower Max Tokens, or use the local fallback."
            )

        result: Dict[str, Any] = {}
        if output_path.exists():
            try:
                loaded = json.loads(output_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    result = loaded
            except Exception as exc:
                result = {"ok": False, "error": f"Could not read worker response: {exc}"}

        if proc.returncode == 0 and result.get("ok"):
            text = sanitize_structured_text(result.get("text", ""), max_chars=50000)
            if text:
                return text
            raise RuntimeError("LiteRT worker returned an empty response.")

        detail_parts = []
        if result.get("error"):
            detail_parts.append(str(result.get("error")))
        if stderr:
            detail_parts.append("stderr: " + _short_runtime_detail(stderr))
        if stdout:
            detail_parts.append("stdout: " + _short_runtime_detail(stdout))
        detail = "; ".join(part for part in detail_parts if part) or f"worker exited with code {proc.returncode}"
        raise RuntimeError("LiteRT worker failed: " + detail)


def _run_litert_text_worker_cli(input_path: str, output_path: str) -> int:
    try:
        # Lower priority inside the child worker. This preserves UI responsiveness
        # on machines where local inference saturates CPU.
        try:
            if os.name == "nt" and psutil is not None:
                psutil.Process(os.getpid()).nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            elif hasattr(os, "nice"):
                os.nice(5)
        except Exception:
            pass
        payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
        runtime = Gemma4Runtime(str(payload.get("model_path") or DEFAULT_MODEL_PATH))
        base_prompt = str(payload.get("prompt") or "")
        output_tokens = clamp_litert_output_tokens(payload.get("max_tokens", LITERT_SAFE_OUTPUT_TOKENS))
        last_error = ""
        text = ""
        # Tokenizers for some LiteRT builds are much less compact than a char
        # estimate. Retry with smaller prompts instead of failing the GUI action.
        for input_budget in (1500, 1200, 900, 650):
            try:
                text = runtime.generate_text(
                    trim_for_litert_context(base_prompt, max_input_tokens=input_budget),
                    temperature=float(payload.get("temperature", 0.20)),
                    top_p=float(payload.get("top_p", 0.90)),
                    max_tokens=output_tokens,
                )
                break
            except Exception as retry_exc:
                last_error = str(retry_exc)
                if "Input token ids are too long" not in last_error and "maximum number of tokens" not in last_error:
                    raise
        if not text:
            raise RuntimeError(last_error or "LiteRT returned no text after prompt budget retries.")
        Path(output_path).write_text(json.dumps({"ok": True, "text": text}), encoding="utf-8")
        return 0
    except Exception as exc:
        try:
            Path(output_path).write_text(json.dumps({"ok": False, "error": str(exc)}), encoding="utf-8")
        except Exception:
            pass
        return 1


def _strip_continue_marker(text: str) -> Tuple[str, bool]:
    clean = sanitize_structured_text(text, max_chars=80000)
    upper = clean.upper()
    markers = (LITERT_CONTINUE_MARKER, "[CONTINUE]", "CONTINUE")
    requested = False
    for marker in markers:
        idx = upper.rfind(marker.upper())
        if idx >= 0 and len(clean) - idx <= 120:
            clean = clean[:idx].rstrip()
            requested = True
            break
    return clean.strip(), requested


def _looks_cut_off(text: str, *, output_tokens: int) -> bool:
    clean = sanitize_structured_text(text, max_chars=80000).rstrip()
    if not clean:
        return False
    if approximate_litert_tokens(clean) >= int(output_tokens) * 0.82:
        return True
    tail = clean[-220:].strip()
    if not tail:
        return False
    if tail.endswith(":") or tail.endswith(("-", "•", "*")):
        return True
    if tail[-1] not in ".!?)]}\"'`":
        return True
    if clean.count("```") % 2 == 1:
        return True
    return False


def _reply_completion_score(text: str) -> float:
    clean = sanitize_structured_text(text, max_chars=120000).lower()
    if not clean:
        return 0.0
    checkpoints = (
        "thesis", "long", "short", "flat", "wait", "income", "invalidation",
        "risk", "confidence", "change", "data quality", "regime",
    )
    hits = sum(1 for item in checkpoints if item in clean)
    return min(1.0, hits / 8.0)


def _tail_sentence_window(text: str, *, max_chars: int = LITERT_OVERLAP_CHARS) -> str:
    clean = sanitize_structured_text(text, max_chars=120000).strip()
    if len(clean) <= max_chars:
        return clean
    tail = clean[-max_chars:]
    boundary_candidates = [tail.find("\n\n"), tail.find("\n#"), tail.find(". "), tail.find("! "), tail.find("? ")]
    boundary_candidates = [idx for idx in boundary_candidates if idx > 80]
    if boundary_candidates:
        return tail[min(boundary_candidates):].lstrip()
    return tail.lstrip()


def _extract_reply_headings(text: str, *, limit: int = 10) -> List[str]:
    clean = sanitize_structured_text(text, max_chars=120000)
    headings: List[str] = []
    for line in clean.splitlines():
        stripped = line.strip().strip("#* _`:-")
        if not stripped or len(stripped) > 90:
            continue
        looks_like_heading = (
            line.lstrip().startswith("#")
            or (line.strip().startswith("**") and line.strip().endswith("**"))
            or bool(re.match(r"^(?:\d+\.|[-*])\s*(Thesis|Long|Short|Flat|Risk|Confidence|Invalidation|Data Quality|Regime|Income)", line.strip(), re.I))
        )
        if looks_like_heading and stripped not in headings:
            headings.append(stripped)
        if len(headings) >= limit:
            break
    return headings


def _prior_chunk_digest(text: str, *, max_chars: int = 900) -> str:
    """Small deterministic digest for continuation prompts; avoids asking the model to reread/rewrite everything."""
    clean = sanitize_structured_text(text, max_chars=120000).strip()
    if not clean:
        return "No prior chunk yet."
    headings = _extract_reply_headings(clean, limit=8)
    tail = _tail_sentence_window(clean, max_chars=520)
    words = re.findall(r"[A-Za-z0-9_.%-]+", clean.lower())
    unique_ratio = (len(set(words)) / max(1, len(words))) if words else 0.0
    digest = [f"Prior length: {len(clean)} chars; lexical entropy proxy: {unique_ratio:.2f}."]
    if headings:
        digest.append("Sections already started: " + "; ".join(headings))
    digest.append("Latest completed context, do not restate: " + tail)
    out = "\n".join(digest)
    return out[-max_chars:]


def _chunk_entropy_telemetry(existing: str, new_chunk: str = "", *, chunk_index: int = 0, last_elapsed: Optional[float] = None) -> str:
    """Bounded telemetry for continuation prompts using psutil plus a deterministic pseudo-quantum phase.

    This is not market evidence. It only helps the local model vary continuation chunks and avoid loops.
    """
    sample = sanitize_structured_text((existing[-2000:] + "\n" + new_chunk[-1000:]), max_chars=4000)
    words = re.findall(r"[A-Za-z0-9_.%-]+", sample.lower())
    unique_ratio = (len(set(words)) / max(1, len(words))) if words else 0.0
    bigrams = list(zip(words, words[1:])) if len(words) > 1 else []
    repeat_ratio = 1.0 - (len(set(bigrams)) / max(1, len(bigrams))) if bigrams else 0.0
    try:
        digest = hashlib.sha256(sample.encode("utf-8", "ignore")).digest()
        phase = int.from_bytes(digest[:2], "big") % 300
        qx = (digest[2] / 127.5) - 1.0
        qy = (digest[3] / 127.5) - 1.0
        qz = (digest[4] / 127.5) - 1.0
    except Exception:
        phase, qx, qy, qz = 0, 0.0, 0.0, 0.0
    if psutil is not None:
        try:
            cpu = float(psutil.cpu_percent(interval=None))
            mem = float(psutil.virtual_memory().percent)
        except Exception:
            cpu, mem = 0.0, 0.0
    else:
        cpu, mem = 0.0, 0.0
    timing = "n/a" if last_elapsed is None else f"{last_elapsed:.2f}s"
    return (
        f"chunk={chunk_index + 1}; last_chunk_elapsed={timing}; cpu={cpu:.1f}%; mem={mem:.1f}%; "
        f"entropy_unique={unique_ratio:.2f}; repeat_pressure={repeat_ratio:.2f}; "
        f"phase={phase}/300; q=({qx:+.2f},{qy:+.2f},{qz:+.2f})"
    )


def _build_chunked_initial_prompt(prompt: str) -> str:
    telemetry = _chunk_entropy_telemetry("", "", chunk_index=0, last_elapsed=None)
    controlled = (
        sanitize_structured_text(prompt, max_chars=22000)
        + "\n\nOUTPUT CONTROL:\n"
        + "Write one complete memo in compact sections. Prefer concise bullets over long prose. "
        + "Avoid reintroducing the same evidence in later sections; each section must add new decision value. "
        + "When near the limit, stop only at a clean sentence boundary and end with exactly "
        + f"{LITERT_CONTINUE_MARKER}. Do not use that marker if every requested section is complete.\n"
        + f"LOCAL GENERATION STATE (for pacing only, not evidence): {telemetry}"
    )
    return trim_for_litert_context(controlled, max_input_tokens=1350)


def _missing_reply_surface(combined_reply: str) -> str:
    clean = sanitize_structured_text(combined_reply, max_chars=120000).lower()
    required = [
        ("Thesis", ("thesis",)),
        ("Long case", ("long case", "long:")),
        ("Short case", ("short case", "short:")),
        ("Flat / wait case", ("flat", "wait case")),
        ("Income-generation research rail", ("income", "maker", "carry")),
        ("Invalidations", ("invalidation", "invalidations")),
        ("Risk box", ("risk box", "risk")),
        ("Confidence", ("confidence",)),
        ("What would change the view", ("change", "change your mind", "change the view")),
    ]
    missing = [label for label, needles in required if not any(needle in clean for needle in needles)]
    return ", ".join(missing) if missing else "None obvious; continue only unfinished thought and close cleanly."


def _build_continuation_prompt(
    original_prompt: str,
    combined_reply: str,
    chunk_index: int,
    *,
    last_elapsed: Optional[float] = None,
    repeat_pressure: float = 0.0,
) -> str:
    overlap = _tail_sentence_window(combined_reply, max_chars=max(420, int(LITERT_OVERLAP_CHARS * 0.68)))
    original_tail = sanitize_structured_text(original_prompt, max_chars=2400)[-1400:]
    missing = _missing_reply_surface(combined_reply)
    digest = _prior_chunk_digest(combined_reply, max_chars=850)
    telemetry = _chunk_entropy_telemetry(combined_reply, "", chunk_index=chunk_index, last_elapsed=last_elapsed)
    anti_loop = (
        "Repeat pressure is elevated. Move directly to the next missing section; do not restate evidence, thesis, or headings already listed in the digest. "
        if repeat_pressure >= 0.34 else
        "Continue with fresh incremental content only; avoid repeating prior wording. "
    )
    continuation = (
        "CONTINUATION TASK - STITCHED NON-REDUNDANT MODE:\n"
        "Continue the SAME answer. Do not restart the memo. Do not summarize the prior answer to the user. "
        "Use the digest only as memory, not as output text. "
        + anti_loop
        + "The OVERLAP below is already printed; resume after it and do not repeat it verbatim. "
        "If a section already exists, only add a genuinely new missing detail or move to the next section. "
        "Close cleanly once all missing surfaces are complete. "
        f"If more space is still needed, stop at a sentence boundary and end with exactly {LITERT_CONTINUE_MARKER}.\n\n"
        f"LOCAL GENERATION STATE (pacing only, not market evidence):\n{telemetry}\n\n"
        f"PRIOR CHUNK DIGEST - DO NOT OUTPUT THIS DIGEST:\n{digest}\n\n"
        f"MISSING / UNFINISHED SURFACES:\n{missing}\n\n"
        f"REQUEST / PACKET TAIL FOR CONTEXT:\n{original_tail}\n\n"
        f"OVERLAP - DO NOT REPEAT VERBATIM:\n{overlap}\n\n"
        f"CONTINUATION PART {chunk_index + 1} - START WITH THE NEXT NEW SENTENCE:"
    )
    return trim_for_litert_context(continuation, max_input_tokens=1120)


def _normalize_for_overlap(value: str) -> str:
    return re.sub(r"\s+", " ", sanitize_structured_text(value, max_chars=80000)).strip().lower()


def _sentence_fingerprint(sentence: str) -> str:
    normalized = _normalize_for_overlap(sentence)
    normalized = re.sub(r"[^a-z0-9.%$ -]", "", normalized)
    return normalized[:240]


def _dedupe_repeated_lines_and_sentences(existing: str, incoming: str) -> str:
    """Remove lines/sentences from a new chunk that already appeared recently in the combined answer."""
    left_norm = _normalize_for_overlap(existing[-6000:])
    kept_blocks: List[str] = []
    for block in re.split(r"(\n\s*\n)", incoming):
        if not block.strip() or re.match(r"\n\s*\n", block):
            kept_blocks.append(block)
            continue
        # Exact/near-exact paragraph duplicate.
        if len(block.strip()) > 80 and _normalize_for_overlap(block) in left_norm:
            continue
        sentences = re.split(r"(?<=[.!?])\s+", block)
        kept_sentences: List[str] = []
        for sent in sentences:
            fp = _sentence_fingerprint(sent)
            if len(fp) > 60 and fp in left_norm:
                continue
            kept_sentences.append(sent)
        rebuilt = " ".join(s.strip() for s in kept_sentences if s.strip()).strip()
        if rebuilt:
            kept_blocks.append(rebuilt)
    return "".join(kept_blocks).strip()


def _new_chunk_repeat_pressure(existing: str, new_chunk: str) -> float:
    left = _normalize_for_overlap(existing[-7000:])
    words = re.findall(r"[a-z0-9_.%-]+", _normalize_for_overlap(new_chunk))
    if len(words) < 12 or not left:
        return 0.0
    ngrams = [" ".join(words[i:i + 8]) for i in range(0, max(0, len(words) - 7), 4)]
    if not ngrams:
        return 0.0
    hits = sum(1 for gram in ngrams if gram and gram in left)
    return hits / max(1, len(ngrams))


def _merge_continuation_chunk(existing: str, new_chunk: str) -> str:
    """Merge continuation chunks while trimming repeated overlap and redundant restarts."""
    left = sanitize_structured_text(existing, max_chars=120000).rstrip()
    right = sanitize_structured_text(new_chunk, max_chars=80000).lstrip()
    if not left:
        return right.strip()
    if not right:
        return left.strip()
    max_check = min(1600, len(left), len(right))
    best = 0
    for size in range(max_check, 40, -1):
        if _normalize_for_overlap(left[-size:]) == _normalize_for_overlap(right[:size]):
            best = size
            break
    if best:
        right = right[best:].lstrip()
    # Remove common model restart preambles in continuation chunks.
    right = re.sub(r"^(?:Here(?:'s| is) (?:the )?(?:continued|continuation).*?:\s*)", "", right, flags=re.I | re.S)
    right = _dedupe_repeated_lines_and_sentences(left, right)
    if not right:
        return left.strip()
    return (left + "\n\n" + right).strip()


def generate_text_litert_chunked(
    *,
    model_path: str,
    prompt: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    max_chunks: int = LITERT_MAX_REPLY_CHUNKS,
    status_callback: Optional[Any] = None,
) -> str:
    """Generate long LiteRT answers as stitched continuation chunks with anti-repetition pacing."""
    output_tokens = max(320, min(clamp_litert_output_tokens(max_tokens), LITERT_REPLY_CHUNK_TOKENS))
    original_prompt = trim_for_litert_context(prompt, max_input_tokens=1350)
    current_prompt = _build_chunked_initial_prompt(original_prompt)
    total = max(1, int(max_chunks))
    combined = ""
    stopped_at_limit = False
    last_elapsed: Optional[float] = None
    repeat_pressure = 0.0
    empty_or_redundant_chunks = 0
    for chunk_index in range(total):
        if status_callback:
            try:
                status_callback(chunk_index + 1, total, "generating")
            except Exception:
                pass
        t0 = time.perf_counter()
        raw = generate_text_litert_subprocess(
            model_path=model_path,
            prompt=current_prompt,
            temperature=temperature,
            top_p=top_p,
            max_tokens=output_tokens,
        )
        last_elapsed = time.perf_counter() - t0
        cleaned, requested_continue = _strip_continue_marker(raw)
        repeat_pressure = _new_chunk_repeat_pressure(combined, cleaned)
        before_len = len(combined)
        if cleaned:
            combined = _merge_continuation_chunk(combined, cleaned)
        added_chars = len(combined) - before_len
        if added_chars < 90 and chunk_index > 0:
            empty_or_redundant_chunks += 1
        else:
            empty_or_redundant_chunks = 0
        needs_more = requested_continue or _looks_cut_off(cleaned or raw, output_tokens=output_tokens)
        if _reply_completion_score(combined) < 0.74 and chunk_index == 0:
            needs_more = True
        # If the model is looping, stop rather than printing another redundant chunk.
        if repeat_pressure >= 0.55 and added_chars < 220:
            needs_more = False
        if empty_or_redundant_chunks >= 2:
            needs_more = False
        if status_callback:
            try:
                status_callback(chunk_index + 1, total, f"merged · +{added_chars} chars · repeat {repeat_pressure:.2f}")
            except Exception:
                pass
        if not needs_more:
            break
        if chunk_index >= total - 1:
            stopped_at_limit = True
            break
        current_prompt = _build_continuation_prompt(
            original_prompt,
            combined,
            chunk_index + 1,
            last_elapsed=last_elapsed,
            repeat_pressure=repeat_pressure,
        )
    final = combined.strip()
    if stopped_at_limit and _looks_cut_off(final, output_tokens=output_tokens):
        final += "\n\n[Stopped after the configured continuation limit. Increase LITERT_MAX_REPLY_CHUNKS for longer answers.]"
    return final or "LiteRT returned an empty chunked response."


class Gemma4Runtime:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self._engine = None
        self._engine_supports_vision = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return Path(self.model_path).is_file() and litert_lm is not None

    def _ensure_engine(self, *, needs_vision: bool = False):
        if not self.available:
            raise RuntimeError("Gemma runtime unavailable. Download the model and ensure litert-lm is installed.")
        with self._lock:
            if self._engine is None or (needs_vision and not self._engine_supports_vision):
                self._engine = load_litert_engine(Path(self.model_path), enable_vision=needs_vision)
                self._engine_supports_vision = needs_vision

    def generate_text(self, prompt: str, *, temperature: float, top_p: float, max_tokens: int) -> str:
        self._ensure_engine()
        clean_prompt = trim_for_litert_context(prompt)
        output_tokens = clamp_litert_output_tokens(max_tokens)
        errors: List[str] = []

        # Prefer the conversation API when present; several LiteRT Python builds
        # expose create_session() but do not actually return text from raw session
        # prompt/generate/run calls.
        conversation_factory = getattr(self._engine, "create_conversation", None)
        if conversation_factory is not None:
            try:
                with conversation_factory(messages=[]) as conversation:
                    response = conversation.send_message(clean_prompt)
                    text = response_to_text(response)
                    if text:
                        return text
                    errors.append("conversation returned no text")
            except Exception as exc:
                errors.append(f"conversation error: {exc}")

        try:
            session = self._engine.create_session()
        except Exception as exc:
            session = None
            errors.append(f"create_session error: {exc}")

        if session is not None:
            for name in ("prompt", "generate", "run"):
                method = getattr(session, name, None)
                if method is None:
                    continue
                try:
                    response = method(
                        clean_prompt,
                        temperature=float(temperature),
                        top_p=float(top_p),
                        max_tokens=output_tokens,
                    )
                    text = response_to_text(response)
                    if text:
                        return text
                    errors.append(f"session.{name} returned no text")
                except TypeError as exc:
                    try:
                        response = method(clean_prompt)
                        text = response_to_text(response)
                        if text:
                            return text
                        errors.append(f"session.{name} returned no text without kwargs")
                    except Exception as inner_exc:
                        errors.append(f"session.{name} TypeError path failed: {inner_exc}")
                except Exception as exc:
                    errors.append(f"session.{name} error: {exc}")

        detail = "; ".join(errors[-4:]) or "no compatible text method was available"
        raise RuntimeError(
            "LiteRT text generation failed after compressing the prompt to "
            f"{len(clean_prompt):,} chars / ~{approximate_litert_tokens(clean_prompt):,} tokens. "
            f"Detail: {detail}"
        )

    def generate_multimodal(self, *, system_text: str, user_text: str, image_path: Optional[str], temperature: float, top_p: float, max_tokens: int) -> str:
        native_image_path = resolve_native_image_path(image_path)
        self._ensure_engine(needs_vision=bool(native_image_path))
        clean_system = trim_for_litert_context(system_text, max_input_tokens=700)
        clean_user = trim_for_litert_context(user_text, max_input_tokens=2200)
        output_tokens = clamp_litert_output_tokens(max_tokens)
        errors: List[str] = []

        conversation_factory = getattr(self._engine, "create_conversation", None)
        if conversation_factory is not None:
            try:
                with conversation_factory(messages=create_default_messages(clean_system)) as conversation:
                    response = conversation.send_message(create_user_message(clean_user, native_image_path))
                    text = response_to_text(response)
                    if text:
                        return text
                    errors.append("multimodal conversation returned no text")
            except Exception as exc:
                errors.append(f"multimodal conversation error: {exc}")

            # If this LiteRT wheel lacks true image support, still return a useful
            # packet-only read instead of failing the whole UI action.
            if native_image_path is not None:
                try:
                    with conversation_factory(messages=create_default_messages(clean_system)) as conversation:
                        response = conversation.send_message(clean_user)
                        text = response_to_text(response)
                        if text:
                            return text
                        errors.append("text-only vision fallback returned no text")
                except Exception as exc:
                    errors.append(f"text-only vision fallback error: {exc}")

        try:
            session = self._engine.create_session()
        except Exception as exc:
            session = None
            errors.append(f"create_session error: {exc}")

        if session is not None:
            messages: List[Any] = create_default_messages(clean_system)
            if native_image_path is None:
                messages.append({"role": "user", "content": clean_user})
            else:
                messages.append(create_user_message(clean_user, native_image_path))
            for name in ("prompt", "generate", "run"):
                method = getattr(session, name, None)
                if method is None:
                    continue
                try:
                    response = method(messages, temperature=float(temperature), top_p=float(top_p), max_tokens=output_tokens)
                    text = response_to_text(response)
                    if text:
                        return text
                    errors.append(f"session.{name} returned no multimodal text")
                except TypeError:
                    try:
                        response = method(messages)
                        text = response_to_text(response)
                        if text:
                            return text
                        errors.append(f"session.{name} returned no multimodal text without kwargs")
                    except Exception as inner_exc:
                        errors.append(f"session.{name} TypeError path failed: {inner_exc}")
                except Exception as exc:
                    errors.append(f"session.{name} multimodal error: {exc}")

        detail = "; ".join(errors[-5:]) or "no compatible multimodal method was available"
        raise RuntimeError(f"LiteRT multimodal prompting failed. Detail: {detail}")


class PromptArchitect:
    def __init__(self, prompt_pack: PromptPackManager):
        self.prompt_pack = prompt_pack

    def market_packet_text(self, surface: SurfaceState, frames: Dict[int, pd.DataFrame], reference_frames: Dict[int, pd.DataFrame]) -> str:
        packet: Dict[str, Any] = {
            "price": surface.price,
            "reference_price": surface.reference_price,
            "basis_pct": surface.theory_packet.basis_pct,
            "basis_z": surface.theory_packet.basis_z,
            "state_vector": surface.state_vector,
            "phase_position": surface.phase_position,
            "color_encoding": surface.color_encoding,
            "confidence": surface.confidence_level,
            "anomaly": surface.anomaly_status,
            "signal": surface.dominant_signal,
            "regime": surface.regime,
            "signal_score": surface.signal_score,
            "suggested_action": surface.suggested_action,
            "suggested_notional_usd": surface.suggested_notional_usd,
            "top_theories": surface.top_theories,
            "theories": asdict(surface.theory_packet),
            "timeframes": [asdict(tf) for tf in surface.timeframes],
            "memory_hits": [asdict(hit) for hit in surface.memory_hits],
            "sensor": asdict(surface.sensor_packet) if surface.sensor_packet else {},
            "last_candles": {},
        }
        for minutes, df in sorted(frames.items()):
            if not df.empty:
                packet["last_candles"][str(minutes)] = (
                    df.tail(3)[["Open", "High", "Low", "Close", "Volume"]].reset_index().to_dict(orient="records")
                )
        return compact_json(packet, 6400)

    def build_gemma4_prompt(self, surface: SurfaceState, frames: Dict[int, pd.DataFrame], reference_frames: Dict[int, pd.DataFrame], user_request: str) -> str:
        packet = self.market_packet_text(surface, frames, reference_frames)
        compact_directive = (
            "Return a compact derivatives memo with: thesis, long case, short case, "
            "flat/wait case, income research rail, invalidations, risk box, confidence, "
            "and what would change the view. Separate evidence from inference. "
            "Use analog memory only as analogy. Never promise profit."
        )
        prompt = (
            f"<|turn|>system\n{self.prompt_pack.data['system']}\n<|endturn|>\n"
            f"<|turn|>user\nMARKET_PACKET:\n{packet}\n\nREQUEST:\n{sanitize_structured_text(user_request, max_chars=1800)}\n\n"
            f"DIRECTIVE:\n{compact_directive}\n"
            f"<|endturn|>\n<|turn|>model\n"
        )
        return trim_for_litert_context(prompt)

    def build_vision_request(self, surface: SurfaceState, frames: Dict[int, pd.DataFrame], reference_frames: Dict[int, pd.DataFrame], user_request: str) -> Tuple[str, str]:
        packet = self.market_packet_text(surface, frames, reference_frames)
        user_text = (
            f"MARKET_PACKET:\n{packet}\n\n"
            f"TASK:\n{sanitize_structured_text(user_request, max_chars=1200)}\n\n"
            "Compare the chart image against the text packet. "
            "Call out confirmations, contradictions, sweeps, traps, ribbon shifts, and ladder magnets."
        )
        return trim_for_litert_context(self.prompt_pack.data["vision_system"], max_input_tokens=700), trim_for_litert_context(user_text, max_input_tokens=2200)


class DerivativesChartPanel(ctk.CTkFrame):
    def __init__(self, master, timeframe_callback: Optional[Any] = None, fullscreen_callback: Optional[Any] = None, product_callback: Optional[Any] = None, vault_callback: Optional[Any] = None, **kwargs):
        super().__init__(master, fg_color=THEME["panel"], corner_radius=18, **kwargs)
        self.timeframe_callback = timeframe_callback
        self.fullscreen_callback = fullscreen_callback
        self.product_callback = product_callback
        self.vault_callback = vault_callback
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.df = pd.DataFrame()
        self.reference_df = pd.DataFrame()
        self.surface: Optional[SurfaceState] = None
        self.settings: Dict[str, Any] = {}
        self.visible_bars = 140
        self.right_offset = 0
        self.hover_index: Optional[int] = None
        self.hover_x = 0.0
        self.hover_y = 0.0
        self.drag_anchor_x: Optional[float] = None
        self.drag_anchor_offset = 0
        self._render_state: Dict[str, Any] = {}

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        header.grid_columnconfigure(0, weight=1)

        self.header_title = ctk.CTkLabel(
            header,
            text="Market Native Surface",
            text_color=THEME["text"],
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        self.header_title.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="In-house canvas renderer · mouse wheel zoom · drag to pan · hover crosshair · TradingView-inspired layout",
            text_color=THEME["muted"],
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, sticky="w")

        controls = ctk.CTkFrame(header, fg_color="transparent")
        controls.grid(row=0, column=1, rowspan=2, sticky="e")
        self.viewport_label = ctk.CTkLabel(controls, text="140 bars", text_color=THEME["muted"], font=ctk.CTkFont(size=11))
        self.viewport_label.grid(row=0, column=0, columnspan=8, sticky="e", pady=(0, 4))
        ctk.CTkButton(controls, text="Zoom -", width=74, command=self.zoom_out, fg_color=THEME["card_soft"]).grid(row=1, column=0, padx=(0, 4))
        ctk.CTkButton(controls, text="Zoom +", width=74, command=self.zoom_in, fg_color=THEME["accent_2"], text_color="#08131f").grid(row=1, column=1, padx=4)
        ctk.CTkButton(controls, text="Reset", width=74, command=self.reset_view, fg_color=THEME["accent"], text_color="#121212").grid(row=1, column=2, padx=4)
        self.market_button = ctk.CTkButton(
            controls,
            text="Market",
            width=102,
            command=self._request_product_picker,
            fg_color=THEME["accent_3"],
            hover_color=THEME["line"],
            text_color="#08131f",
        )
        self.market_button.grid(row=1, column=3, padx=4)
        self.vault_button = ctk.CTkButton(
            controls,
            text="Unlock Vault",
            width=104,
            command=self._request_vault_unlock,
            fg_color=THEME["warning"],
            hover_color=THEME["line"],
            text_color="#121212",
        )
        self.vault_button.grid(row=1, column=4, padx=4)
        self.side_tray_button = ctk.CTkButton(
            controls,
            text="Hide Tray",
            width=88,
            command=self._request_side_tray_toggle,
            fg_color=THEME["card_soft"],
            hover_color=THEME["line"],
            text_color=THEME["text"],
        )
        self.side_tray_button.grid(row=1, column=5, padx=4)
        self.fullscreen_button = ctk.CTkButton(
            controls,
            text="Fullscreen",
            width=96,
            command=self._request_fullscreen_toggle,
            fg_color=THEME["card_soft"],
            hover_color=THEME["line"],
            text_color=THEME["text"],
        )
        self.fullscreen_button.grid(row=1, column=6, padx=(4, 0))
        timeframe_row = ctk.CTkFrame(controls, fg_color="transparent")
        timeframe_row.grid(row=2, column=0, columnspan=8, sticky="e", pady=(6, 0))
        self.timeframe_buttons: Dict[int, Any] = {}
        for idx, (label, minutes) in enumerate(TIMEFRAME_BUTTONS):
            btn = ctk.CTkButton(
                timeframe_row, text=label, width=42, height=26, fg_color=THEME["card_soft"],
                hover_color=THEME["line"], text_color=THEME["text"],
                command=lambda m=minutes: self.set_timeframe(m),
            )
            btn.grid(row=idx // 6, column=idx % 6, padx=2, pady=2)
            self.timeframe_buttons[minutes] = btn

        self.canvas = tk.Canvas(self, bg=THEME["panel"], highlightthickness=0, bd=0, relief="flat", cursor="crosshair")
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 12))
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Motion>", self._on_mouse_move)
        self.canvas.bind("<Leave>", self._on_mouse_leave)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda event: self._zoom_from_event(+1, event.x))
        self.canvas.bind("<Button-5>", lambda event: self._zoom_from_event(-1, event.x))
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_end)

    def _request_product_picker(self) -> None:
        if callable(self.product_callback):
            try:
                self.product_callback()
            except Exception:
                pass

    def _request_vault_unlock(self) -> None:
        if callable(getattr(self, "vault_callback", None)):
            try:
                self.vault_callback()
            except Exception:
                pass

    def set_vault_state(self, unlocked: bool, has_vault: bool = True) -> None:
        try:
            if unlocked:
                self.vault_button.configure(text="Vault Unlocked", fg_color=THEME["success"], text_color="#08131f")
            elif has_vault:
                self.vault_button.configure(text="Unlock Vault", fg_color=THEME["warning"], text_color="#121212")
            else:
                self.vault_button.configure(text="No Vault", fg_color=THEME["card_soft"], text_color=THEME["muted"])
        except Exception:
            pass

    def set_market_label(self, product_id: str, reference_id: str = "") -> None:
        try:
            product = sanitize_product_id(product_id, default="ETH-PERP")
            label = product if not reference_id else f"{product} · Ref {sanitize_product_id(reference_id, default=reference_id)}"
            self.market_button.configure(text=label[:26])
        except Exception:
            pass

    def _request_side_tray_toggle(self) -> None:
        root = self.winfo_toplevel()
        callback = getattr(root, "toggle_side_tray", None)
        if callable(callback):
            callback()

    def set_side_tray_state(self, hidden: bool) -> None:
        try:
            self.side_tray_button.configure(
                text="Show Tray" if hidden else "Hide Tray",
                fg_color=THEME["accent_3"] if hidden else THEME["card_soft"],
                text_color="#08131f" if hidden else THEME["text"],
            )
        except Exception:
            pass
        self._draw()

    def _request_fullscreen_toggle(self) -> None:
        if callable(self.fullscreen_callback):
            self.fullscreen_callback()

    def set_fullscreen_state(self, active: bool) -> None:
        try:
            self.fullscreen_button.configure(
                text="Exit Fullscreen" if active else "Fullscreen",
                fg_color=THEME["accent"] if active else THEME["card_soft"],
                text_color="#121212" if active else THEME["text"],
            )
        except Exception:
            pass
        self._draw()

    @staticmethod
    def _crisp(value: float) -> float:
        return round(float(value)) + 0.5

    @staticmethod
    def _finite_bounds(*series: Any) -> Tuple[float, float]:
        lows: List[float] = []
        highs: List[float] = []
        for values in series:
            arr = np.asarray(values, dtype=float).reshape(-1)
            arr = arr[np.isfinite(arr)]
            if arr.size:
                lows.append(float(arr.min()))
                highs.append(float(arr.max()))
        if not lows:
            return 0.0, 1.0
        return min(lows), max(highs)

    def set_timeframe(self, minutes: int) -> None:
        minutes = max(1, int(minutes))
        self.set_active_timeframe(minutes)
        if self.timeframe_callback is not None:
            try:
                self.timeframe_callback(minutes)
            except Exception:
                pass

    def set_active_timeframe(self, minutes: int) -> None:
        active = max(1, int(minutes))
        for tf_minutes, button in getattr(self, "timeframe_buttons", {}).items():
            try:
                if int(tf_minutes) == active:
                    button.configure(fg_color=THEME["accent"], text_color="#121212")
                else:
                    button.configure(fg_color=THEME["card_soft"], text_color=THEME["text"])
            except Exception:
                pass

    @staticmethod
    def _map_y(value: float, rect: Tuple[float, float, float, float], low: float, high: float) -> float:
        x0, y0, x1, y1 = rect
        if not np.isfinite(value):
            return (y0 + y1) / 2.0
        span = high - low
        if abs(span) < 1e-12:
            return (y0 + y1) / 2.0
        ratio = (float(value) - low) / span
        return y1 - ratio * (y1 - y0)

    @staticmethod
    def _unmap_y(y: float, rect: Tuple[float, float, float, float], low: float, high: float) -> float:
        _x0, y0, _x1, y1 = rect
        span = y1 - y0
        if abs(span) < 1e-12:
            return float((low + high) / 2.0)
        ratio = (y1 - float(y)) / span
        return float(low + ratio * (high - low))

    def _on_resize(self, _event=None) -> None:
        self._redraw()

    def _on_mousewheel(self, event) -> None:
        steps = 1 if event.delta > 0 else -1
        self._zoom_from_event(steps, event.x)

    def _on_mouse_move(self, event) -> None:
        self.hover_x = float(event.x)
        self.hover_y = float(event.y)
        state = self._render_state
        left = state.get("plot_left")
        right = state.get("plot_right")
        start = state.get("start")
        x_step = state.get("x_step")
        count = state.get("count")
        if left is None or right is None or start is None or x_step is None or count is None:
            return
        if not (left <= self.hover_x <= right):
            if self.hover_index is not None:
                self.hover_index = None
                self._redraw()
            return
        local = int((self.hover_x - left) / max(1.0, float(x_step)))
        local = max(0, min(int(count) - 1, local))
        idx = int(start) + local
        if idx != self.hover_index:
            self.hover_index = idx
            self._redraw()

    def _on_mouse_leave(self, _event=None) -> None:
        if self.hover_index is not None:
            self.hover_index = None
            self._redraw()

    def _on_drag_start(self, event) -> None:
        self.drag_anchor_x = float(event.x)
        self.drag_anchor_offset = int(self.right_offset)

    def _on_drag_move(self, event) -> None:
        if self.drag_anchor_x is None:
            return
        x_step = float(self._render_state.get("x_step", 0.0))
        if x_step <= 0.0 or self.df.empty:
            return
        delta_bars = int(round((self.drag_anchor_x - float(event.x)) / x_step))
        max_offset = max(0, len(self.df) - min(len(self.df), int(self.visible_bars)))
        self.right_offset = max(0, min(max_offset, self.drag_anchor_offset + delta_bars))
        self._redraw()

    def _on_drag_end(self, _event=None) -> None:
        self.drag_anchor_x = None

    def zoom_in(self) -> None:
        self._zoom(0.82)

    def zoom_out(self) -> None:
        self._zoom(1.18)

    def reset_view(self) -> None:
        if not self.df.empty:
            self.visible_bars = min(len(self.df), 140)
        else:
            self.visible_bars = 140
        self.right_offset = 0
        self._redraw()

    def _zoom_from_event(self, direction: int, focus_x: float) -> None:
        self._zoom(0.82 if direction > 0 else 1.18, focus_x=focus_x)

    def _zoom(self, factor: float, focus_x: Optional[float] = None) -> None:
        if self.df.empty:
            return
        total = len(self.df)
        current = min(total, max(24, int(self.visible_bars)))
        target = min(total, max(24, int(round(current * factor))))
        if target == current:
            return
        start, end = self._window_bounds(current)
        plot_left = float(self._render_state.get("plot_left", 0.0))
        plot_right = float(self._render_state.get("plot_right", 0.0))
        if focus_x is None or plot_right <= plot_left:
            focus_ratio = 1.0
        else:
            focus_ratio = clamp((float(focus_x) - plot_left) / max(1.0, plot_right - plot_left), 0.0, 1.0)
        anchor_index = start + int(round(max(0, end - start - 1) * focus_ratio))
        new_start = anchor_index - int(round(max(0, target - 1) * focus_ratio))
        new_start = max(0, min(max(0, total - target), new_start))
        self.visible_bars = target
        self.right_offset = max(0, total - (new_start + target))
        self._redraw()

    def _window_bounds(self, requested_bars: Optional[int] = None) -> Tuple[int, int]:
        if self.df.empty:
            return 0, 0
        total = len(self.df)
        bars = min(total, max(24, int(requested_bars if requested_bars is not None else self.visible_bars)))
        max_offset = max(0, total - bars)
        self.right_offset = max(0, min(max_offset, int(self.right_offset)))
        end = total - self.right_offset
        start = max(0, end - bars)
        self.visible_bars = bars
        return start, end

    def _panel_layout(self, width: float, height: float, show_quantum: bool) -> Dict[str, Tuple[float, float, float, float]]:
        left = 16.0
        right = max(left + 240.0, width - 84.0)
        top = 10.0
        bottom = max(top + 160.0, height - 26.0)
        gap = 10.0
        names = ["price", "basis", "flow"] + (["quant"] if show_quantum else [])
        ratios = [0.58, 0.14, 0.16] + ([0.12] if show_quantum else [])
        usable = max(120.0, bottom - top - gap * (len(names) - 1))
        total_ratio = sum(ratios)
        rects: Dict[str, Tuple[float, float, float, float]] = {}
        cursor = top
        for name, ratio in zip(names, ratios):
            panel_height = usable * (ratio / total_ratio)
            rects[name] = (left, cursor, right, cursor + panel_height)
            cursor += panel_height + gap
        return rects

    def _draw_panel_chrome(
        self,
        rect: Tuple[float, float, float, float],
        title: str,
        x_marks: List[Tuple[float, str]],
        low: float,
        high: float,
        formatter,
    ) -> None:
        x0, y0, x1, y1 = rect
        self.canvas.create_rectangle(x0, y0, x1, y1, fill=blend_hex(THEME["card"], THEME["panel"], 0.94), outline=THEME["line"], width=1)
        self.canvas.create_text(x0 + 10, y0 + 8, text=title, anchor="nw", fill=THEME["muted"], font=("TkDefaultFont", 9, "bold"))
        for x, _ in x_marks:
            crisp_x = self._crisp(x)
            self.canvas.create_line(crisp_x, y0, crisp_x, y1, fill=THEME["line_soft"], width=1, dash=(2, 5))
        ticks = np.linspace(low, high, 5)
        for value in ticks:
            y = self._map_y(float(value), rect, low, high)
            crisp_y = self._crisp(y)
            self.canvas.create_line(x0, crisp_y, x1, crisp_y, fill=THEME["line_soft"], width=1, dash=(2, 5))
            self.canvas.create_text(x1 - 8, y, text=formatter(float(value)), anchor="e", fill=THEME["muted"], font=("TkDefaultFont", 8))

    def _draw_series(
        self,
        x_values: List[float],
        values: Any,
        rect: Tuple[float, float, float, float],
        low: float,
        high: float,
        color: str,
        width: float = 1.0,
        dash: Optional[Tuple[int, ...]] = None,
    ) -> None:
        points: List[float] = []
        for x, value in zip(x_values, np.asarray(values, dtype=float)):
            if not np.isfinite(value):
                if len(points) >= 4:
                    self.canvas.create_line(*points, fill=color, width=width, dash=dash or (), capstyle=tk.ROUND, joinstyle=tk.ROUND)
                points = []
                continue
            points.extend((x, self._map_y(float(value), rect, low, high)))
        if len(points) >= 4:
            self.canvas.create_line(*points, fill=color, width=width, dash=dash or (), capstyle=tk.ROUND, joinstyle=tk.ROUND)

    def _draw_cloud(
        self,
        x_values: List[float],
        lower_values: Any,
        upper_values: Any,
        mask: Any,
        rect: Tuple[float, float, float, float],
        low: float,
        high: float,
        fill: str,
    ) -> None:
        uppers: List[Tuple[float, float]] = []
        lowers: List[Tuple[float, float]] = []

        def flush() -> None:
            if len(uppers) < 2:
                return
            coords: List[float] = []
            for x, value in uppers:
                coords.extend((x, self._map_y(value, rect, low, high)))
            for x, value in reversed(lowers):
                coords.extend((x, self._map_y(value, rect, low, high)))
            self.canvas.create_polygon(*coords, fill=fill, outline="", stipple="gray25")

        for x, lower_value, upper_value, enabled in zip(x_values, np.asarray(lower_values, dtype=float), np.asarray(upper_values, dtype=float), np.asarray(mask, dtype=bool)):
            valid = bool(enabled) and np.isfinite(lower_value) and np.isfinite(upper_value)
            if not valid:
                flush()
                uppers = []
                lowers = []
                continue
            uppers.append((x, float(upper_value)))
            lowers.append((x, float(lower_value)))
        flush()

    def _draw_area_fill(
        self,
        x_values: List[float],
        values: Any,
        rect: Tuple[float, float, float, float],
        low: float,
        high: float,
        baseline: float,
        positive_fill: str,
        negative_fill: str,
    ) -> None:
        segment: List[Tuple[float, float]] = []
        positive: Optional[bool] = None

        def flush() -> None:
            if len(segment) < 2 or positive is None:
                return
            coords: List[float] = []
            for x, value in segment:
                coords.extend((x, self._map_y(value, rect, low, high)))
            base_y = self._map_y(baseline, rect, low, high)
            for x, _value in reversed(segment):
                coords.extend((x, base_y))
            self.canvas.create_polygon(*coords, fill=positive_fill if positive else negative_fill, outline="", stipple="gray25")

        for x, value in zip(x_values, np.asarray(values, dtype=float)):
            if not np.isfinite(value):
                flush()
                segment = []
                positive = None
                continue
            is_positive = float(value) >= baseline
            if positive is None:
                positive = is_positive
            elif is_positive != positive:
                flush()
                segment = [(x, float(value))]
                positive = is_positive
                continue
            segment.append((x, float(value)))
        flush()

    def _draw_value_marker(
        self,
        rect: Tuple[float, float, float, float],
        value: float,
        low: float,
        high: float,
        text: str,
        fill: str,
        text_color: str,
    ) -> None:
        if not np.isfinite(value):
            return
        x0, y0, x1, y1 = rect
        y = self._map_y(value, rect, low, high)
        if not (y0 <= y <= y1):
            return
        self.canvas.create_line(x0, y, x1, y, fill=blend_hex(fill, THEME["card"], 0.42), width=1, dash=(5, 4))
        badge_left = x1 + 8
        badge_right = badge_left + 66
        self.canvas.create_rectangle(badge_left, y - 11, badge_right, y + 11, fill=fill, outline="")
        self.canvas.create_text((badge_left + badge_right) / 2.0, y, text=text, fill=text_color, font=("TkDefaultFont", 8, "bold"))

    def _draw_hover_overlay(
        self,
        plot_df: pd.DataFrame,
        joined: pd.DataFrame,
        x_values: List[float],
        rects: Dict[str, Tuple[float, float, float, float]],
        price_low: float,
        price_high: float,
    ) -> None:
        if self.hover_index is None:
            return
        state = self._render_state
        start = int(state.get("start", 0))
        end = int(state.get("end", 0))
        if not (start <= self.hover_index < end):
            return
        local = self.hover_index - start
        if local < 0 or local >= len(plot_df):
            return
        last_rect = rects["quant"] if "quant" in rects else rects["flow"]
        hover_x = x_values[local]
        self.canvas.create_line(hover_x, rects["price"][1], hover_x, last_rect[3], fill=THEME["line"], width=1, dash=(4, 4))

        active_rect = None
        for name in ("price", "basis", "flow", "quant"):
            rect = rects.get(name)
            if rect and rect[1] <= self.hover_y <= rect[3]:
                active_rect = rect
                break
        if active_rect is not None:
            self.canvas.create_line(active_rect[0], self.hover_y, active_rect[2], self.hover_y, fill=THEME["line"], width=1, dash=(4, 4))

        candle = plot_df.iloc[local]
        basis_value = safe_float(joined["basis_pct"].iloc[local], 0.0) if "basis_pct" in joined else 0.0
        change_pct = ((safe_float(candle["Close"]) / max(1e-9, safe_float(candle["Open"], 1.0))) - 1.0) * 100.0
        info = (
            f"{format_chart_time(plot_df.index[local])}\n"
            f"O {safe_float(candle['Open']):,.2f}   H {safe_float(candle['High']):,.2f}   "
            f"L {safe_float(candle['Low']):,.2f}   C {safe_float(candle['Close']):,.2f}\n"
            f"Delta {change_pct:+.2f}%   Vol {safe_float(candle['Volume']):,.0f}   Basis {basis_value:+.3f}%"
        )
        x0, y0, _x1, _y1 = rects["price"]
        self.canvas.create_rectangle(x0 + 10, y0 + 34, x0 + 355, y0 + 90, fill=THEME["card_soft"], outline=THEME["line"], width=1)
        self.canvas.create_text(x0 + 20, y0 + 44, text=info, anchor="nw", fill=THEME["text"], justify="left", font=("TkDefaultFont", 9))
        if active_rect == rects["price"]:
            hovered_price = self._unmap_y(self.hover_y, rects["price"], price_low, price_high)
            self._draw_value_marker(rects["price"], hovered_price, price_low, price_high, f"{hovered_price:,.2f}", THEME["accent_2"], "#08131f")

    def draw(self, df: pd.DataFrame, reference_df: pd.DataFrame, surface: Optional[SurfaceState], settings: Dict[str, Any]) -> None:
        self.df = df.copy()
        self.reference_df = reference_df.copy()
        self.surface = surface
        self.settings = dict(settings)
        try:
            product_id = sanitize_product_id(self.settings.get("product_id"), default="ETH-PERP")
            reference_id = sanitize_product_id(self.settings.get("reference_product_id"), default="ETH-USD")
            self.header_title.configure(text=f"{product_id} Native Surface · Ref {reference_id}")
            self.set_market_label(product_id, reference_id)
        except Exception:
            pass
        if self.df.empty:
            self.hover_index = None
            self.right_offset = 0
        else:
            self.visible_bars = min(len(self.df), max(24, int(self.visible_bars)))
            self.right_offset = max(0, min(int(self.right_offset), max(0, len(self.df) - self.visible_bars)))
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.delete("all")
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        if width < 200 or height < 160:
            return
        if self.df.empty:
            self.viewport_label.configure(text="0 bars")
            self.canvas.create_text(width / 2.0, height / 2.0, text="Awaiting market data...", fill=THEME["muted"], font=("TkDefaultFont", 14))
            self._render_state = {}
            return

        start, end = self._window_bounds()
        plot_df = self.df.iloc[start:end].copy()
        if plot_df.empty:
            return

        show_quantum = bool(self.settings.get("chart_show_quantum_panel", True))
        rects = self._panel_layout(width, height, show_quantum)
        plot_left = rects["price"][0]
        plot_right = rects["price"][2]
        x_step = (plot_right - plot_left) / max(1, len(plot_df))
        x_values = [plot_left + ((idx + 0.5) * x_step) for idx in range(len(plot_df))]
        x_marks_idx = sorted(set(int(round(v)) for v in np.linspace(0, len(plot_df) - 1, num=min(7, len(plot_df)))))
        x_marks = [(x_values[idx], format_chart_time(plot_df.index[idx])) for idx in x_marks_idx]

        joined = pd.DataFrame(index=plot_df.index)
        joined["perp"] = plot_df["Close"]
        joined["spot"] = self.reference_df["Close"].reindex(plot_df.index).ffill() if not self.reference_df.empty else np.nan
        joined["basis_pct"] = ((joined["perp"] - joined["spot"]) / joined["spot"].replace(0, np.nan) * 100.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)

        spans = EMA_SPANS if self.settings.get("chart_show_ema233", True) else PRIMARY_EMA_SPANS
        price_low, price_high = self._finite_bounds(
            plot_df["Low"],
            plot_df["High"],
            plot_df["RIBBON_LOW"],
            plot_df["RIBBON_HIGH"],
            plot_df["VWMA20"],
            *[plot_df[f"EMA{s}"] for s in spans if f"EMA{s}" in plot_df],
        )
        price_pad = max((price_high - price_low) * 0.08, max(abs(price_high), 1.0) * 0.0025)
        price_low -= price_pad
        price_high += price_pad

        basis_abs = max(abs(self._finite_bounds(joined["basis_pct"])[0]), abs(self._finite_bounds(joined["basis_pct"])[1]), 0.08)
        basis_low = -basis_abs * 1.08
        basis_high = basis_abs * 1.08

        flow_scale = max(
            1.0,
            abs(self._finite_bounds(plot_df["FLOW_Z"])[0]),
            abs(self._finite_bounds(plot_df["FLOW_Z"])[1]),
            abs(self._finite_bounds(plot_df["VOL_Z"])[0]),
            abs(self._finite_bounds(plot_df["VOL_Z"])[1]),
        ) * 1.18
        flow_low = -flow_scale
        flow_high = flow_scale

        denom = max(1.0, float(plot_df["ENERGY_SCORE"].abs().max()))
        quant_entropy = np.clip(np.asarray(plot_df["ROLLING_ENTROPY_24"], dtype=float), -1.0, 1.0)
        quant_ribbon = np.clip(np.asarray(plot_df["RIBBON_SPREAD_PCT"], dtype=float) * 12.0, -1.0, 1.0)
        quant_energy = np.clip((np.asarray(plot_df["ENERGY_SCORE"], dtype=float) / denom) * 0.9, -1.0, 1.0)

        self._draw_panel_chrome(rects["price"], "Price Surface", x_marks, price_low, price_high, lambda value: f"{value:,.0f}")
        self._draw_panel_chrome(rects["basis"], "Basis %", x_marks, basis_low, basis_high, lambda value: f"{value:+.2f}")
        self._draw_panel_chrome(rects["flow"], "Flow + Volume", x_marks, flow_low, flow_high, lambda value: f"{value:+.1f}")
        if show_quantum and "quant" in rects:
            self._draw_panel_chrome(rects["quant"], "Quantum Surface", x_marks, -1.05, 1.05, lambda value: f"{value:+.1f}")

        bull = (
            (plot_df["EMA8"] > plot_df["EMA13"])
            & (plot_df["EMA13"] > plot_df["EMA21"])
            & (plot_df["EMA21"] > plot_df["EMA34"])
            & (plot_df["EMA34"] > plot_df["EMA55"])
            & (plot_df["EMA55"] > plot_df["EMA89"])
        )
        bear = (
            (plot_df["EMA8"] < plot_df["EMA13"])
            & (plot_df["EMA13"] < plot_df["EMA21"])
            & (plot_df["EMA21"] < plot_df["EMA34"])
            & (plot_df["EMA34"] < plot_df["EMA55"])
            & (plot_df["EMA55"] < plot_df["EMA89"])
        )
        if self.settings.get("chart_show_ribbon_cloud", True):
            self._draw_cloud(
                x_values,
                plot_df["RIBBON_LOW"],
                plot_df["RIBBON_HIGH"],
                bull,
                rects["price"],
                price_low,
                price_high,
                blend_hex(THEME["candle_up"], THEME["card"], 0.20),
            )
            self._draw_cloud(
                x_values,
                plot_df["RIBBON_LOW"],
                plot_df["RIBBON_HIGH"],
                bear,
                rects["price"],
                price_low,
                price_high,
                blend_hex(THEME["candle_down"], THEME["card"], 0.12),
            )

        candle_width = max(3.0, min(16.0, x_step * 0.72))
        for x, (_idx, row) in zip(x_values, plot_df.iterrows()):
            open_price = safe_float(row["Open"])
            high_price = safe_float(row["High"])
            low_price = safe_float(row["Low"])
            close_price = safe_float(row["Close"])
            up = close_price >= open_price
            color = THEME["candle_up"] if up else THEME["candle_down"]
            wick_y0 = self._map_y(high_price, rects["price"], price_low, price_high)
            wick_y1 = self._map_y(low_price, rects["price"], price_low, price_high)
            self.canvas.create_line(self._crisp(x), wick_y0, self._crisp(x), wick_y1, fill=color, width=1)
            body_top = self._map_y(max(open_price, close_price), rects["price"], price_low, price_high)
            body_bottom = self._map_y(min(open_price, close_price), rects["price"], price_low, price_high)
            top = min(body_top, body_bottom)
            bottom = max(body_top, body_bottom)
            if bottom - top < 2:
                bottom = top + 2
            self.canvas.create_rectangle(x - (candle_width / 2.0), top, x + (candle_width / 2.0), bottom, fill=color, outline=color, width=1)

        ema_colors = {
            8: THEME["ema_fast"],
            13: THEME["ema_fast2"],
            21: THEME["ema_mid"],
            34: THEME["ema_mid"],
            55: THEME["ema_slow"],
            89: THEME["ema_slow2"],
            144: "#aebdff",
            233: "#7f92ff",
        }
        for span in spans:
            column = f"EMA{span}"
            if column in plot_df:
                self._draw_series(x_values, plot_df[column], rects["price"], price_low, price_high, ema_colors[span], width=1.2 if span >= 55 else 1.0)
        self._draw_series(x_values, plot_df["VWMA20"], rects["price"], price_low, price_high, "#9be8ff", width=1.0)

        self._draw_area_fill(
            x_values,
            joined["basis_pct"],
            rects["basis"],
            basis_low,
            basis_high,
            0.0,
            blend_hex(THEME["candle_up"], THEME["card"], 0.18),
            blend_hex(THEME["candle_down"], THEME["card"], 0.12),
        )
        self._draw_series(x_values, joined["basis_pct"], rects["basis"], basis_low, basis_high, THEME["basis"], width=1.4)
        self.canvas.create_line(rects["basis"][0], self._map_y(0.0, rects["basis"], basis_low, basis_high), rects["basis"][2], self._map_y(0.0, rects["basis"], basis_low, basis_high), fill=THEME["line"], width=1)

        volume_max = max(1.0, safe_float(plot_df["Volume"].max(), 1.0))
        flow_bottom = rects["flow"][3]
        flow_height = rects["flow"][3] - rects["flow"][1]
        volume_height = flow_height * 0.52
        for x, (_idx, row) in zip(x_values, plot_df.iterrows()):
            volume = safe_float(row["Volume"])
            close_price = safe_float(row["Close"])
            open_price = safe_float(row["Open"])
            up = close_price >= open_price
            bar_fill = blend_hex(THEME["candle_up"] if up else THEME["candle_down"], THEME["card"], 0.24)
            bar_top = flow_bottom - (volume / volume_max) * volume_height
            self.canvas.create_rectangle(x - (candle_width / 2.5), bar_top, x + (candle_width / 2.5), flow_bottom - 1, fill=bar_fill, outline="")
        self.canvas.create_line(rects["flow"][0], self._map_y(0.0, rects["flow"], flow_low, flow_high), rects["flow"][2], self._map_y(0.0, rects["flow"], flow_low, flow_high), fill=THEME["line"], width=1)
        self._draw_series(x_values, plot_df["FLOW_Z"], rects["flow"], flow_low, flow_high, THEME["flow"], width=1.4)
        self._draw_series(x_values, plot_df["VOL_Z"], rects["flow"], flow_low, flow_high, THEME["flow_2"], width=1.1)

        if show_quantum and "quant" in rects:
            quant_rect = rects["quant"]
            self.canvas.create_line(quant_rect[0], self._map_y(0.0, quant_rect, -1.05, 1.05), quant_rect[2], self._map_y(0.0, quant_rect, -1.05, 1.05), fill=THEME["line"], width=1)
            self._draw_series(x_values, quant_entropy, quant_rect, -1.05, 1.05, THEME["entropy"], width=1.2)
            self._draw_series(x_values, quant_ribbon, quant_rect, -1.05, 1.05, THEME["quantum"], width=1.0)
            self._draw_series(x_values, quant_energy, quant_rect, -1.05, 1.05, THEME["basis"], width=1.0)
            if self.surface and self.surface.sensor_packet:
                self.canvas.create_line(quant_rect[0], self._map_y(self.surface.sensor_packet.quantum_alignment, quant_rect, -1.05, 1.05), quant_rect[2], self._map_y(self.surface.sensor_packet.quantum_alignment, quant_rect, -1.05, 1.05), fill=THEME["candle_up"], width=1, dash=(4, 4))
                self.canvas.create_line(quant_rect[0], self._map_y(self.surface.sensor_packet.entropic_gain, quant_rect, -1.05, 1.05), quant_rect[2], self._map_y(self.surface.sensor_packet.entropic_gain, quant_rect, -1.05, 1.05), fill=THEME["candle_down"], width=1, dash=(2, 4))

        label_rect = rects["quant"] if "quant" in rects else rects["flow"]
        for x, label in x_marks:
            self.canvas.create_text(x, label_rect[3] + 8, text=label, anchor="n", fill=THEME["muted"], font=("TkDefaultFont", 8))

        last_close = safe_float(plot_df["Close"].iloc[-1])
        last_basis = safe_float(joined["basis_pct"].iloc[-1])
        last_flow = safe_float(plot_df["FLOW_Z"].iloc[-1])
        self._draw_value_marker(rects["price"], last_close, price_low, price_high, f"{last_close:,.0f}", THEME["accent"], "#121212")
        self._draw_value_marker(rects["basis"], last_basis, basis_low, basis_high, f"{last_basis:+.2f}", THEME["basis"], "#08131f")
        self._draw_value_marker(rects["flow"], last_flow, flow_low, flow_high, f"{last_flow:+.2f}", THEME["flow"], "#08131f")

        if self.surface:
            title = f"{self.surface.dominant_signal.replace('_', ' ')} | {self.surface.regime} | edge {self.surface.signal_score:+.2f} | confidence {self.surface.confidence_level*100:.1f}%"
            price_rect = rects["price"]
            self.canvas.create_rectangle(price_rect[0] + 10, price_rect[1] + 8, price_rect[0] + 520, price_rect[1] + 28, fill=THEME["card_soft"], outline=THEME["line"], width=1)
            self.canvas.create_text(price_rect[0] + 18, price_rect[1] + 18, text=title, anchor="w", fill=THEME["text"], font=("TkDefaultFont", 9, "bold"))

        self._render_state = {
            "plot_left": plot_left,
            "plot_right": plot_right,
            "x_step": x_step,
            "start": start,
            "end": end,
            "count": len(plot_df),
        }
        self.viewport_label.configure(text=f"{len(plot_df)} bars | zoom {(len(self.df) / max(1, len(plot_df))):.1f}x")
        self._draw_hover_overlay(plot_df, joined, x_values, rects, price_low, price_high)

    def export_snapshot(self) -> str:
        tmp_dir = Path(tempfile.gettempdir()) / "gemma4_quantum_trader"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        path = tmp_dir / f"chart_snapshot_{int(time.time())}.ps"
        self.canvas.update_idletasks()
        self.canvas.postscript(file=str(path), colormode="color")
        return str(path)


class StatusTile(ctk.CTkFrame):
    def __init__(self, master, title: str):
        super().__init__(master, fg_color=THEME["card"], corner_radius=16)
        self.grid_columnconfigure(0, weight=1)
        self.title_label = ctk.CTkLabel(self, text=title, text_color=THEME["muted"], font=ctk.CTkFont(size=11, weight="bold"))
        self.title_label.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        self.value = ctk.CTkLabel(self, text="—", text_color=THEME["text"], font=ctk.CTkFont(size=18, weight="bold"))
        self.value.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 10))


def center_toplevel(dialog: tk.Toplevel, parent: tk.Misc, width: int, height: int) -> None:
    try:
        parent.update_idletasks()
        root_x = parent.winfo_rootx()
        root_y = parent.winfo_rooty()
        root_w = max(width, parent.winfo_width())
        root_h = max(height, parent.winfo_height())
        x = root_x + max(20, (root_w - width) // 2)
        y = root_y + max(20, (root_h - height) // 2)
    except Exception:
        x = 140
        y = 100
    dialog.geometry(f"{width}x{height}+{x}+{y}")


class VaultPasswordDialog(ctk.CTkToplevel):
    def __init__(self, master, *, mode: str, title: str, message: str):
        super().__init__(master)
        self.master = master
        self.mode = mode
        self.result: Optional[Dict[str, str]] = None
        self.title(title)
        self.configure(fg_color=THEME["panel"])
        self.resizable(False, False)
        self.transient(master)
        center_toplevel(self, master, 560, 360 if mode == "unlock" else 430)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        card = ctk.CTkFrame(self, fg_color=THEME["card"], corner_radius=18)
        card.pack(fill="both", expand=True, padx=18, pady=18)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text=title, text_color=THEME["text"], font=ctk.CTkFont(size=24, weight="bold")).grid(row=0, column=0, sticky="w", padx=22, pady=(22, 8))
        ctk.CTkLabel(card, text=message, text_color=THEME["muted"], justify="left", wraplength=500).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 16))

        self.error_var = tk.StringVar(value="")
        self.password_var = tk.StringVar(value="")
        self.confirm_var = tk.StringVar(value="")
        self.current_var = tk.StringVar(value="")

        row = 2
        first_focus: Optional[ctk.CTkEntry] = None

        if mode == "rotate":
            ctk.CTkLabel(card, text="Current Vault Password", text_color=THEME["muted"]).grid(row=row, column=0, sticky="w", padx=22, pady=(0, 6))
            self.current_entry = ctk.CTkEntry(card, textvariable=self.current_var, show="*", placeholder_text="Current password", height=42)
            self.current_entry.grid(row=row + 1, column=0, sticky="ew", padx=22, pady=(0, 12))
            first_focus = self.current_entry
            row += 2
        else:
            self.current_entry = None

        password_label = "Create Vault Password" if mode == "create" else "Vault Password"
        ctk.CTkLabel(card, text=password_label, text_color=THEME["muted"]).grid(row=row, column=0, sticky="w", padx=22, pady=(0, 6))
        self.password_entry = ctk.CTkEntry(card, textvariable=self.password_var, show="*", placeholder_text="Password", height=42)
        self.password_entry.grid(row=row + 1, column=0, sticky="ew", padx=22, pady=(0, 12))
        if first_focus is None:
            first_focus = self.password_entry
        row += 2

        self.confirm_entry = None
        if mode in {"create", "rotate"}:
            ctk.CTkLabel(card, text="Confirm Password", text_color=THEME["muted"]).grid(row=row, column=0, sticky="w", padx=22, pady=(0, 6))
            self.confirm_entry = ctk.CTkEntry(card, textvariable=self.confirm_var, show="*", placeholder_text="Confirm password", height=42)
            self.confirm_entry.grid(row=row + 1, column=0, sticky="ew", padx=22, pady=(0, 12))
            row += 2

        ctk.CTkLabel(card, textvariable=self.error_var, text_color=THEME["danger"], justify="left", wraplength=500).grid(row=row, column=0, sticky="w", padx=22, pady=(0, 12))
        row += 1

        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.grid(row=row, column=0, sticky="ew", padx=22, pady=(6, 22))
        footer.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(footer, text="Cancel", command=self._cancel, fg_color=THEME["card_soft"]).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        primary_text = {"unlock": "Unlock Vault", "create": "Create Vault", "rotate": "Rotate Vault Key"}.get(mode, "Continue")
        ctk.CTkButton(footer, text=primary_text, command=self._submit, fg_color=THEME["accent"], text_color="#121212").grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self.bind("<Return>", lambda _event: self._submit())
        self.bind("<Escape>", lambda _event: self._cancel())
        self.after(80, lambda: first_focus.focus_force() if first_focus is not None else None)

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def _submit(self) -> None:
        self.error_var.set("")
        password = self.password_var.get().strip()
        confirm = self.confirm_var.get().strip()
        current = self.current_var.get().strip()

        if self.mode == "unlock":
            if not password:
                self.error_var.set("Enter the vault password to unlock the encrypted Coinbase bundle.")
                return
            self.result = {"password": password}
            self.destroy()
            return

        if self.mode == "create":
            if len(password) < MIN_VAULT_PASSWORD_LENGTH:
                self.error_var.set(f"Use at least {MIN_VAULT_PASSWORD_LENGTH} characters for the vault password.")
                return
            if password != confirm:
                self.error_var.set("The confirmation password does not match.")
                return
            self.result = {"password": password}
            self.destroy()
            return

        if not current:
            self.error_var.set("Enter the current vault password first.")
            return
        if len(password) < MIN_VAULT_PASSWORD_LENGTH:
            self.error_var.set(f"Use at least {MIN_VAULT_PASSWORD_LENGTH} characters for the new vault password.")
            return
        if password != confirm:
            self.error_var.set("The new password confirmation does not match.")
            return
        if current == password:
            self.error_var.set("Choose a different new vault password for rotation.")
            return
        self.result = {"current_password": current, "new_password": password}
        self.destroy()

    def show_modal(self) -> Optional[Dict[str, str]]:
        self.wait_visibility()
        self.grab_set()
        self.focus_force()
        self.wait_window()
        return self.result


class StartupSetupDialog(ctk.CTkToplevel):
    def __init__(self, master, *, checklist_text: str):
        super().__init__(master)
        self.result = "continue"
        self.title("Startup Guide")
        self.configure(fg_color=THEME["panel"])
        self.resizable(False, False)
        self.transient(master)
        center_toplevel(self, master, 620, 380)
        self.protocol("WM_DELETE_WINDOW", self._close)

        card = ctk.CTkFrame(self, fg_color=THEME["card"], corner_radius=18)
        card.pack(fill="both", expand=True, padx=18, pady=18)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="Setup / Vault Workflow", text_color=THEME["text"], font=ctk.CTkFont(size=24, weight="bold")).grid(row=0, column=0, sticky="w", padx=22, pady=(22, 8))
        ctk.CTkLabel(
            card,
            text="This setup guide only opens automatically for a brand-new profile. Use Setup to seal the vault, add Coinbase credentials, choose products, and download the Gemma runtime.",
            text_color=THEME["muted"],
            justify="left",
            wraplength=560,
        ).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 14))
        ctk.CTkLabel(card, text=checklist_text, text_color=THEME["text"], justify="left", wraplength=560).grid(row=2, column=0, sticky="w", padx=22, pady=(0, 16))

        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=22, pady=(4, 22))
        footer.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(footer, text="Continue", command=self._close, fg_color=THEME["card_soft"]).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(footer, text="Open Setup Tab", command=self._open_setup, fg_color=THEME["accent"], text_color="#121212").grid(row=0, column=1, sticky="ew", padx=(6, 0))

    def _close(self) -> None:
        self.result = "continue"
        self.destroy()

    def _open_setup(self) -> None:
        self.result = "setup"
        self.destroy()

    def show_modal(self) -> str:
        self.wait_visibility()
        self.grab_set()
        self.focus_force()
        self.wait_window()
        return self.result



class ProductPickerDialog(ctk.CTkToplevel):
    """Scrollable, searchable product selector for large Coinbase product lists.

    The picker keeps the search field stable while filtering. It does not pre-fill
    the search box with the current product, debounces filtering, preserves focus,
    and renders only a small page at a time so very large product lists remain
    responsive.
    """

    def __init__(self, master, *, title: str, labels: List[str], current: str = ""):
        super().__init__(master)
        self.result: Optional[str] = None
        self.labels = list(dict.fromkeys(str(label) for label in labels if str(label).strip()))
        self.filtered = self.labels[:]
        self.current = str(current or "").strip()
        self._filter_after_id: Optional[str] = None
        self._render_after_id: Optional[str] = None
        self._max_render = 140
        self.title(title)
        self.configure(fg_color=THEME["panel"])
        self.transient(master)
        center_toplevel(self, master, 860, 620)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(self, text=title, text_color=THEME["text"], font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 4))
        ctk.CTkLabel(
            self,
            text="Search is keyboard-first: type part of the product ID, asset, expiry, quote currency, or display name. Only a small page is rendered so huge Coinbase product lists stay responsive.",
            text_color=THEME["muted"],
            justify="left",
            wraplength=800,
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 6))

        self.current_label = ctk.CTkLabel(
            self,
            text=f"Current: {self.current or '—'}",
            text_color=THEME["accent_3"],
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.current_label.grid(row=2, column=0, sticky="w", padx=18, pady=(0, 8))

        search_row = ctk.CTkFrame(self, fg_color="transparent")
        search_row.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 8))
        search_row.grid_columnconfigure(0, weight=1)
        self.search_var = tk.StringVar(value="")
        self.search_entry = ctk.CTkEntry(search_row, textvariable=self.search_var, placeholder_text="Search markets, e.g. BTC, ETH-USD, NOL oil, equity, stock, index, ETF, 18MAY26, USDC")
        self.search_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(search_row, text="Clear", width=72, command=self._clear_search, fg_color=THEME["card_soft"]).grid(row=0, column=1, padx=(8, 0))
        if self.current:
            ctk.CTkButton(search_row, text="Use Current", width=104, command=lambda: self._select(self.current), fg_color=THEME["line"]).grid(row=0, column=2, padx=(8, 0))

        self.search_entry.bind("<KeyRelease>", self._on_search_keyrelease)
        self.search_entry.bind("<Return>", self._select_first)
        self.search_entry.bind("<Escape>", lambda _event: self._cancel())
        self.search_entry.bind("<Control-a>", self._select_search_text)
        self.search_entry.bind("<Control-A>", self._select_search_text)

        self.list_frame = ctk.CTkScrollableFrame(self, fg_color=THEME["card"], corner_radius=14, height=370)
        self.list_frame.grid(row=4, column=0, sticky="nsew", padx=18, pady=(0, 10))
        self.list_frame.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(self, text="", text_color=THEME["muted"], font=ctk.CTkFont(size=11))
        self.status_label.grid(row=5, column=0, sticky="w", padx=18, pady=(0, 8))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=6, column=0, sticky="ew", padx=18, pady=(0, 18))
        buttons.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(buttons, text="Select First Match", command=lambda: self._select_first(None), fg_color=THEME["accent"], text_color="#121212").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(buttons, text="Cancel", command=self._cancel, fg_color=THEME["card_soft"]).grid(row=0, column=1, sticky="e")

        self._filter(immediate=True)
        self.after(80, self._focus_search_entry)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _focus_search_entry(self) -> None:
        try:
            self.search_entry.focus_force()
            self.search_entry.icursor("end")
        except Exception:
            pass

    def _select_search_text(self, _event=None):
        try:
            self.search_entry.select_range(0, "end")
            self.search_entry.icursor("end")
        except Exception:
            pass
        return "break"

    def _clear_search(self) -> None:
        self.search_var.set("")
        self._filter(immediate=True)
        self._focus_search_entry()

    def _on_search_keyrelease(self, _event=None) -> None:
        # Debounce filtering so fast typing is not interrupted by widget rebuilds.
        if self._filter_after_id:
            try:
                self.after_cancel(self._filter_after_id)
            except Exception:
                pass
        self._filter_after_id = self.after(90, self._filter)

    @staticmethod
    def _score_label(label: str, terms: List[str]) -> Tuple[int, str]:
        upper = label.upper()
        product = upper.split(" — ", 1)[0]
        score = 0
        for term in terms:
            if product == term:
                score += 1000
            elif product.startswith(term):
                score += 500
            elif term in product:
                score += 250
            elif term in upper:
                score += 80
        return (-score, upper)

    def _filter(self, immediate: bool = False) -> None:
        self._filter_after_id = None
        query = sanitize_structured_text(self.search_var.get(), max_chars=120).upper()
        # Let users paste common separators; searching "ARB/USD" should find ARB-USD.
        query = query.replace("/", "-").replace("_", "-").replace(":", " ")
        terms = [term for term in re.split(r"\s+", query) if term]
        if terms:
            normalized_labels = [(label, label.upper().replace("/", "-").replace("_", "-")) for label in self.labels]
            matched = [label for label, normalized in normalized_labels if all(term in normalized for term in terms)]
            matched.sort(key=lambda label: self._score_label(label, terms))
            self.filtered = matched
        else:
            self.filtered = self.labels[:]
        if immediate:
            self._render()
            return
        if self._render_after_id:
            try:
                self.after_cancel(self._render_after_id)
            except Exception:
                pass
        self._render_after_id = self.after(10, self._render)

    def _render(self) -> None:
        self._render_after_id = None
        try:
            for child in self.list_frame.winfo_children():
                child.destroy()
        except Exception:
            return
        visible = self.filtered[: self._max_render]
        if not visible:
            ctk.CTkLabel(
                self.list_frame,
                text="No matching Coinbase products. Try a shorter search like BTC, ETH, USD, USDC, NOL, or the expiry month.",
                text_color=THEME["muted"],
                justify="left",
                wraplength=760,
            ).grid(row=0, column=0, sticky="ew", padx=10, pady=12)
        for row, label in enumerate(visible):
            is_current = self.current and label == self.current
            ctk.CTkButton(
                self.list_frame,
                text=("✓ " if is_current else "") + label,
                anchor="w",
                height=34,
                fg_color=THEME["line"] if is_current else THEME["card_soft"],
                hover_color=THEME["line"],
                text_color=THEME["text"],
                command=lambda value=label: self._select(value),
            ).grid(row=row, column=0, sticky="ew", padx=8, pady=3)
        extra = max(0, len(self.filtered) - len(visible))
        suffix = f" · showing {len(visible)} at a time; keep typing to narrow {extra} more" if extra else ""
        self.status_label.configure(text=f"{len(self.filtered)} matching products{suffix} · Enter selects first match · Esc cancels")
        self.after(1, self._focus_search_entry)

    def _select_first(self, _event=None):
        if self.filtered:
            self._select(self.filtered[0])
        return "break"

    def _select(self, value: str) -> None:
        self.result = value
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def show_modal(self) -> Optional[str]:
        # Tk can raise "grab failed: window not viewable" if grab_set happens
        # before the toplevel has actually been mapped. Wait for visibility and
        # then use a local grab; if the window manager still refuses the grab,
        # keep the picker usable without blocking the entire app.
        try:
            self.deiconify()
            self.lift()
            self.wait_visibility()
        except Exception:
            pass
        try:
            self.grab_set()
        except tk.TclError:
            try:
                self.after(120, self.grab_set)
            except Exception:
                pass
        self._focus_search_entry()
        self.wait_window()
        return self.result


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1840x1100")
        self.configure(fg_color=THEME["window"])

        self.settings = SettingsManager(SETTINGS_PATH, DEFAULT_SETTINGS)
        self.prompt_pack = PromptPackManager(self.settings)
        self.prompt_library_flat: Dict[str, str] = {}
        self.selected_prompt_name = ""
        self.coinbase = CoinbaseAdvancedClient(self.settings)
        self.product_store = EncryptedProductStore(PRODUCT_CACHE_DB_PATH)
        self.available_products: List[Dict[str, Any]] = []
        self.product_label_to_id: Dict[str, str] = {}
        self.alpha = AdvancedAlphaEngine()
        self.sensor = QuantumRGBSensor()
        self.memory = EntropicRAGMemory(limit=int(self.settings.data.get("rag_memory_limit", 120)))
        self.prompt_architect = PromptArchitect(self.prompt_pack)
        self.gemma = Gemma4Runtime(self.settings.data.get("model_path", DEFAULT_MODEL_PATH))

        self.frames: Dict[int, pd.DataFrame] = {}
        self.reference_frames: Dict[int, pd.DataFrame] = {}
        self.surface: Optional[SurfaceState] = None
        self.last_preview_id: Optional[str] = None
        self.vision_image_path: Optional[str] = None
        self.autonomy_cooldown = deque(maxlen=10)
        self._refreshing = False
        self._refresh_token = 0
        self._model_download_in_progress = False
        self.last_market_error = ""
        self.vault_master_key: Optional[bytes] = None
        self.vault_mode = self.settings.vault_mode()
        self.vault_unlocked = False
        self._startup_workflow_ran = False
        self._coinbase_private_key_plain = ""
        self.chart_fullscreen_active = False
        self.side_tray_hidden = bool(self.settings.data.get("side_tray_hidden", False))
        self._llm_running_jobs = 0
        self._llm_spinner_index = 0
        self._llm_started_at = 0.0
        self._llm_status_context = "LLM"
        self._llm_active_chunk = ""
        self._llm_spinner_after_id: Optional[str] = None

        self._build()
        self._load_settings_into_widgets()
        self._select_initial_tab()
        self.bind("<F11>", lambda event: self.toggle_chart_fullscreen())
        self.bind("<Escape>", self.exit_chart_fullscreen)
        self.bind("<F10>", lambda event: self.toggle_side_tray())
        self.refresh_market()
        self.after(max(5, int(self.settings.data.get("refresh_seconds", 30))) * 1000, self._schedule_refresh)
        self.after(180, self._run_startup_workflows)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color=THEME["canvas"], corner_radius=0)
        self.header_frame = header
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(header, text=APP_NAME, text_color=THEME["text"], font=ctk.CTkFont(size=28, weight="bold")).pack(anchor="w", padx=22, pady=(12, 0))
        ctk.CTkLabel(
            header,
            text="Gemma 4 multimodal cockpit · ETH-PERP derivatives theories · EMA ribbon surfacing · entropic RAG memory · Pennylane RGB sensor",
            text_color=THEME["muted"],
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=22, pady=(0, 12))

        body = ctk.CTkFrame(self, fg_color="transparent")
        self.body_frame = body
        body.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
        body.grid_columnconfigure(0, weight=2)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        self.chart = DerivativesChartPanel(body, timeframe_callback=self.set_primary_timeframe, fullscreen_callback=self.toggle_chart_fullscreen, product_callback=self.open_central_market_picker, vault_callback=self.unlock_secrets)
        self.chart.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 10))
        self.chart.set_vault_state(bool(self.vault_unlocked and self.coinbase.secrets), bool(self.settings.data.get("secrets")))
        self.chart.set_side_tray_state(bool(getattr(self, "side_tray_hidden", False)))

        right = ctk.CTkFrame(body, fg_color="transparent")
        self.right_panel = right
        right.grid(row=0, column=1, rowspan=2, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=3)
        right.grid_rowconfigure(1, weight=2)

        self.tabs = ctk.CTkTabview(
            right,
            fg_color=THEME["panel"],
            segmented_button_fg_color=THEME["card"],
            segmented_button_selected_color=THEME["tab_selected"],
            segmented_button_selected_hover_color=THEME["tab_selected_hover"],
            segmented_button_unselected_color=THEME["card_soft"],
            text_color=THEME["text"],
            corner_radius=18,
        )
        self.tabs.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        self.setup_tab = self.tabs.add("Setup")
        self.chat_tab = self.tabs.add("Chat")
        self.vision_tab = self.tabs.add("Vision")
        self.trade_tab = self.tabs.add("Trading")
        self.prompt_tab = self.tabs.add("Prompt Studio")
        self.settings_tab = self.tabs.add("Settings")

        self.surface_panel = ctk.CTkScrollableFrame(right, fg_color=THEME["panel"], corner_radius=18)
        self.surface_panel.grid(row=1, column=0, sticky="nsew")
        self.surface_panel.grid_columnconfigure((0, 1), weight=1)

        self.tiles = {}
        titles = [
            "ETH-PERP Price",
            "Dominant Signal",
            "Regime",
            "Confidence",
            "Phase",
            "Color Encoding",
            "Dominant Theory",
            "Suggested Action",
            "Anomaly",
            "Composite Edge",
            "Quantum Align",
            "Entropic Gain",
        ]
        for idx, title in enumerate(titles):
            tile = StatusTile(self.surface_panel, title)
            tile.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=10, pady=10)
            self.tiles[title] = tile

        self.summary_box = ctk.CTkTextbox(
            self.surface_panel,
            fg_color=THEME["card"],
            text_color=THEME["text"],
            border_width=1,
            border_color=THEME["line"],
            corner_radius=14,
            height=128,
        )
        self.summary_box.grid(row=6, column=0, columnspan=2, sticky="nsew", padx=10, pady=(0, 10))
        self.summary_box.insert("1.0", "Observe → infer → retrieve analogs → debate → risk-map → act or stay flat.")
        self.summary_box.configure(state="disabled")

        self.surface_tabs = ctk.CTkTabview(
            self.surface_panel,
            fg_color=THEME["card"],
            segmented_button_fg_color=THEME["card_soft"],
            segmented_button_selected_color=THEME["tab_selected"],
            segmented_button_selected_hover_color=THEME["tab_selected_hover"],
            segmented_button_unselected_color=THEME["card_soft"],
            text_color=THEME["text"],
            corner_radius=14,
        )
        self.surface_tabs.grid(row=7, column=0, columnspan=2, sticky="nsew", padx=10, pady=(0, 12))
        theory_tab = self.surface_tabs.add("Theory Matrix")
        sensor_tab = self.surface_tabs.add("Sensor")
        memory_tab = self.surface_tabs.add("Memory")
        self.theory_box = ctk.CTkTextbox(theory_tab, fg_color=THEME["card_soft"], text_color=THEME["text"], border_width=0, wrap="word")
        self.sensor_box = ctk.CTkTextbox(sensor_tab, fg_color=THEME["card_soft"], text_color=THEME["text"], border_width=0, wrap="word")
        self.memory_box = ctk.CTkTextbox(memory_tab, fg_color=THEME["card_soft"], text_color=THEME["text"], border_width=0, wrap="word")
        for box in (self.theory_box, self.sensor_box, self.memory_box):
            box.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_setup_tab()
        self._build_chat_tab()
        self._build_vision_tab()
        self._build_trade_tab()
        self._build_prompt_tab()
        self._build_settings_tab()
        self._apply_side_tray_state(persist=False)

    def toggle_side_tray(self) -> None:
        self.side_tray_hidden = not bool(getattr(self, "side_tray_hidden", False))
        self._apply_side_tray_state(persist=True)

    def _apply_side_tray_state(self, *, persist: bool = False) -> None:
        hidden = bool(getattr(self, "side_tray_hidden", False))
        try:
            if bool(getattr(self, "chart_fullscreen_active", False)):
                if hasattr(self, "chart"):
                    self.chart.set_side_tray_state(hidden)
                return
            if hidden:
                self.right_panel.grid_remove()
                self.body_frame.grid_columnconfigure(0, weight=1)
                self.body_frame.grid_columnconfigure(1, weight=0)
                self.chart.grid_configure(row=0, column=0, rowspan=2, sticky="nsew", padx=0, pady=0)
            else:
                self.body_frame.grid_columnconfigure(0, weight=2)
                self.body_frame.grid_columnconfigure(1, weight=1)
                self.chart.grid_configure(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 10), pady=0)
                self.right_panel.grid(row=0, column=1, rowspan=2, sticky="nsew")
            if hasattr(self, "chart"):
                self.chart.set_side_tray_state(hidden)
            if hasattr(self, "side_tray_switch"):
                if hidden:
                    self.side_tray_switch.select()
                else:
                    self.side_tray_switch.deselect()
            if persist:
                self.settings.data["side_tray_hidden"] = hidden
                self.settings.save()
        except Exception as exc:
            messagebox.showerror("Side tray", f"Could not toggle the side tray.\n\n{exc}")

    def toggle_chart_fullscreen(self) -> None:
        self.chart_fullscreen_active = not bool(getattr(self, "chart_fullscreen_active", False))
        self._apply_chart_fullscreen_state()

    def _apply_chart_fullscreen_state(self) -> None:
        active = bool(getattr(self, "chart_fullscreen_active", False))
        try:
            if active:
                self.right_panel.grid_remove()
                self.header_frame.grid_remove()
                self.body_frame.grid_configure(padx=0, pady=0)
                self.body_frame.grid_columnconfigure(0, weight=1)
                self.body_frame.grid_columnconfigure(1, weight=0)
                self.chart.grid_configure(row=0, column=0, rowspan=2, sticky="nsew", padx=0, pady=0)
                self.attributes("-fullscreen", True)
            else:
                self.attributes("-fullscreen", False)
                self.header_frame.grid()
                self.body_frame.grid_configure(padx=14, pady=14)
                self._apply_side_tray_state(persist=False)
            if hasattr(self, "chart"):
                self.chart.set_fullscreen_state(active)
                self.chart.set_side_tray_state(bool(getattr(self, "side_tray_hidden", False)))
        except Exception as exc:
            try:
                self.attributes("-fullscreen", False)
            except Exception:
                pass
            self.chart_fullscreen_active = False
            messagebox.showerror("Fullscreen", f"Could not toggle fullscreen chart mode: {exc}")

    def exit_chart_fullscreen(self, event: Optional[Any] = None) -> None:
        if bool(getattr(self, "chart_fullscreen_active", False)):
            self.chart_fullscreen_active = False
            self._apply_chart_fullscreen_state()

    def _build_setup_tab(self) -> None:
        host = self.setup_tab
        host.grid_columnconfigure(0, weight=1)
        host.grid_rowconfigure(0, weight=1)

        tab = ctk.CTkScrollableFrame(host, fg_color="transparent")
        tab.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(tab, text="First Start Workflow", text_color=THEME["text"], font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 4))
        ctk.CTkLabel(
            tab,
            text="Set your encrypted vault passphrase, add Coinbase credentials, choose market products, download the Gemma 4 model, then save and unlock everything from one place.",
            text_color=THEME["muted"],
            justify="left",
            wraplength=760,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 12))

        checklist = ctk.CTkFrame(tab, fg_color=THEME["card"], corner_radius=16)
        checklist.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))
        checklist.grid_columnconfigure(0, weight=1)
        self.setup_checklist_label = ctk.CTkLabel(checklist, text="Startup checklist loading...", text_color=THEME["text"], justify="left")
        self.setup_checklist_label.grid(row=0, column=0, sticky="w", padx=14, pady=14)

        ctk.CTkLabel(tab, text="Vault Passphrase", text_color=THEME["muted"]).grid(row=3, column=0, sticky="w", padx=12, pady=(8, 6))
        self.setup_passphrase_entry = ctk.CTkEntry(tab, show="*", placeholder_text="Optional: the secure vault popup can ask when needed")
        self.setup_passphrase_entry.grid(row=3, column=1, sticky="ew", padx=(6, 12), pady=(8, 6))

        ctk.CTkLabel(tab, text="Coinbase API Key", text_color=THEME["muted"]).grid(row=4, column=0, sticky="w", padx=12, pady=6)
        self.setup_coinbase_api_key_entry = ctk.CTkEntry(tab, placeholder_text="Paste Coinbase CDP API key name: organizations/{org}/apiKeys/{key}")
        self.setup_coinbase_api_key_entry.grid(row=4, column=1, sticky="ew", padx=(6, 12), pady=6)

        ctk.CTkLabel(tab, text="Coinbase Private Key / JSON", text_color=THEME["muted"]).grid(row=5, column=0, sticky="nw", padx=12, pady=6)
        self.setup_coinbase_private_key_box = ctk.CTkTextbox(tab, height=120, fg_color=THEME["card"], text_color=THEME["text"], border_color=THEME["line"], border_width=1, corner_radius=14)
        self.setup_coinbase_private_key_box.grid(row=5, column=1, sticky="ew", padx=(6, 12), pady=6)
        self.setup_coinbase_private_key_box.insert("1.0", "")
        self.setup_coinbase_private_key_box.configure(state="disabled")
        setup_key_actions = ctk.CTkFrame(tab, fg_color="transparent")
        setup_key_actions.grid(row=6, column=1, sticky="w", padx=(6, 12), pady=(0, 8))
        ctk.CTkButton(setup_key_actions, text="Paste PEM/JSON", command=self._paste_private_key_from_clipboard, width=128, height=30, fg_color=THEME["card_soft"]).pack(side="left", padx=(0, 6))
        ctk.CTkButton(setup_key_actions, text="Load Key File", command=self._load_private_key_file, width=118, height=30, fg_color=THEME["card_soft"]).pack(side="left", padx=6)
        ctk.CTkButton(setup_key_actions, text="Clear", command=self._clear_private_key_value, width=86, height=30, fg_color=THEME["card_soft"]).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(tab, text="Market selection", text_color=THEME["muted"]).grid(row=7, column=0, sticky="w", padx=12, pady=6)
        ctk.CTkButton(tab, text="Use Chart Market Button", command=self.open_central_market_picker, fg_color=THEME["accent_3"], text_color="#08131f").grid(row=7, column=1, sticky="ew", padx=(6, 12), pady=6)
        self.setup_product_id_entry = ctk.CTkComboBox(tab, values=[str(self.settings.data.get("product_id", "ETH-PERP"))], state="normal")
        self.setup_product_id_entry.set(str(self.settings.data.get("product_id", "ETH-PERP")))
        self.setup_reference_product_entry = ctk.CTkComboBox(tab, values=[str(self.settings.data.get("reference_product_id", "ETH-USD"))], state="normal")
        self.setup_reference_product_entry.set(str(self.settings.data.get("reference_product_id", "ETH-USD")))

        ctk.CTkLabel(tab, text="Gemma Model Path", text_color=THEME["muted"]).grid(row=9, column=0, sticky="w", padx=12, pady=6)
        self.setup_model_path_entry = ctk.CTkEntry(tab, placeholder_text=DEFAULT_MODEL_PATH)
        self.setup_model_path_entry.grid(row=9, column=1, sticky="ew", padx=(6, 12), pady=6)

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=10, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 10))
        actions.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)
        ctk.CTkButton(actions, text="Download Model", command=self.download_model_action, fg_color=THEME["accent_2"], text_color="#08131f").grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(actions, text="Save + Unlock", command=self.save_initial_setup_action, fg_color=THEME["accent"], text_color="#121212").grid(row=0, column=1, sticky="ew", padx=6)
        ctk.CTkButton(actions, text="Refresh Products", command=self.refresh_products_action, fg_color=THEME["card_soft"]).grid(row=0, column=2, sticky="ew", padx=6)
        ctk.CTkButton(actions, text="Refresh Market", command=self.refresh_market, fg_color=THEME["accent_3"], text_color="#08131f").grid(row=0, column=3, sticky="ew", padx=6)
        ctk.CTkButton(actions, text="Open Settings", command=lambda: self.tabs.set("Settings"), fg_color=THEME["card_soft"]).grid(row=0, column=4, sticky="ew", padx=(6, 0))

        self.setup_status_box = ctk.CTkTextbox(tab, height=240, fg_color=THEME["card"], text_color=THEME["text"], border_color=THEME["line"], border_width=1, corner_radius=14)
        self.setup_status_box.grid(row=11, column=0, columnspan=2, sticky="nsew", padx=12, pady=(4, 12))
        self.setup_status_box.insert(
            "1.0",
            "Setup log:\n"
            "- Save will prompt for a vault password if Coinbase credentials need a new encrypted seal.\n"
            "- Download the model or point to an existing `.litertlm` file.\n"
            "- Refresh Market will report candle fetch issues here when they happen.\n",
        )

    def _build_chat_tab(self) -> None:
        tab = self.chat_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        quick = ctk.CTkFrame(tab, fg_color="transparent")
        quick.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        for col in range(5):
            quick.grid_columnconfigure(col, weight=1)
        for idx, (label, prompt) in enumerate(self.prompt_pack.data["quick_prompts"].items()):
            ctk.CTkButton(
                quick,
                text=label,
                command=lambda p=prompt: self._seed_chat_prompt(p),
                fg_color=THEME["card_soft"],
                hover_color=THEME["line"],
                text_color=THEME["text"],
                corner_radius=12,
                height=34,
            ).grid(row=idx // 5, column=idx % 5, padx=4, pady=4, sticky="ew")

        self.chat_history = ctk.CTkTextbox(
            tab,
            fg_color=THEME["card"],
            text_color=THEME["text"],
            border_color=THEME["line"],
            border_width=1,
            corner_radius=14,
            wrap="word",
        )
        self.chat_history.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=12, pady=(0, 10))
        self.chat_history.insert(
            "1.0",
            "System:\n"
            "- Multi-timeframe ETH-PERP alpha engine runs 15 theory rails.\n"
            "- Visual mode audits the chart.\n"
            "- RAG memory surfaces similar prior in-session states.\n"
            "- RGB / Pennylane sensor is bounded auxiliary context, not magic.\n"
            "- This is research software, not guaranteed edge.\n\n",
        )
        self.chat_history.configure(state="disabled")

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        row.grid_columnconfigure(0, weight=1)

        self.chat_input = ctk.CTkTextbox(
            row,
            height=120,
            fg_color=THEME["card"],
            text_color=THEME["text"],
            border_color=THEME["line"],
            border_width=1,
            corner_radius=14,
        )
        self.chat_input.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.chat_input.bind("<Return>", self._handle_chat_return)
        self.chat_input.bind("<Shift-Return>", self._handle_chat_shift_return)

        side = ctk.CTkFrame(row, fg_color="transparent")
        side.grid(row=0, column=1, sticky="ns")
        ctk.CTkButton(side, text="Ask Gemma 4", command=self.ask_model, fg_color=THEME["accent"], text_color="#121212", corner_radius=14, width=136).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkButton(side, text="Debate Packet", command=lambda: self._seed_and_send(self.prompt_pack.data["debate_request"]), fg_color=THEME["accent_2"], text_color="#0c1720", corner_radius=14, width=136).grid(row=1, column=0, sticky="ew", pady=6)
        ctk.CTkButton(side, text="Execution Map", command=lambda: self._seed_and_send(self.prompt_pack.data["execution_request"]), fg_color=THEME["accent_3"], text_color="#0c1720", corner_radius=14, width=136).grid(row=2, column=0, sticky="ew", pady=6)
        ctk.CTkButton(side, text="Risk Officer", command=lambda: self._seed_and_send(self.prompt_pack.data["risk_officer_request"]), fg_color=THEME["card_soft"], text_color=THEME["text"], corner_radius=14, width=136).grid(row=3, column=0, sticky="ew", pady=6)

        status_row = ctk.CTkFrame(tab, fg_color="transparent")
        status_row.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        status_row.grid_columnconfigure(0, weight=1)
        self.llm_status_label = ctk.CTkLabel(
            status_row,
            text="LLM idle",
            text_color=THEME["muted"],
            anchor="w",
            font=ctk.CTkFont(size=12),
        )
        self.llm_status_label.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.llm_progress = ctk.CTkProgressBar(status_row, mode="indeterminate", height=8)
        self.llm_progress.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.llm_progress.set(0)

    def _build_vision_tab(self) -> None:
        tab = self.vision_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(tab, text="Visual LLM Surface", text_color=THEME["text"], font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))
        self.vision_prompt = ctk.CTkTextbox(tab, height=90, fg_color=THEME["card"], text_color=THEME["text"], border_color=THEME["line"], border_width=1, corner_radius=14)
        self.vision_prompt.grid(row=1, column=0, sticky="ew", padx=(12, 6), pady=(0, 10))
        self.vision_prompt.insert("1.0", "Read this ETH-PERP chart like a professional discretionary trader. Identify sweeps, failed breaks, volatility compression, ribbon shifts, basis displacement, passive absorption, and what the image says that the signal packet may be missing.")
        button_col = ctk.CTkFrame(tab, fg_color="transparent")
        button_col.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 10))
        ctk.CTkButton(button_col, text="Use Live Chart Snapshot", command=self.run_live_vision_analysis).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkButton(button_col, text="Choose Image", command=self._choose_vision_image).grid(row=1, column=0, sticky="ew", pady=6)
        ctk.CTkButton(button_col, text="Analyze Selected Image", command=self.run_selected_vision_analysis, fg_color=THEME["accent"], text_color="#121212").grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.vision_output = ctk.CTkTextbox(tab, fg_color=THEME["card"], text_color=THEME["text"], border_color=THEME["line"], border_width=1, corner_radius=14)
        self.vision_output.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=12, pady=(0, 12))
        self.vision_output.insert("1.0", "Visual mode notes:\n- If the binding supports multimodal input, Gemma 4 receives both text and image.\n- Native image input currently expects PNG, JPG, or WEBP files.\n- Live chart snapshots still export from the native canvas as vector PostScript, so that path may fall back to text-only packet analysis.\n- Otherwise the app falls back to a text-only description anchored to the market packet.\n")

    def _build_trade_tab(self) -> None:
        tab = self.trade_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(10, weight=1)
        ctk.CTkLabel(
            tab,
            text="Execution rail is optional. Paper mode is the default. Use the Market button beside chart zoom to change products globally.",
            text_color=THEME["muted"],
            wraplength=720,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 8))

        market_status = ctk.CTkFrame(tab, fg_color=THEME["card"], corner_radius=16)
        market_status.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        market_status.grid_columnconfigure(0, weight=1)
        self.trade_market_label = ctk.CTkLabel(
            market_status,
            text=f"Active market: {self.settings.data.get('product_id', 'ETH-PERP')} · Reference: {self.settings.data.get('reference_product_id', 'ETH-USD')}",
            text_color=THEME["text"],
            font=ctk.CTkFont(size=15, weight="bold"),
            anchor="w",
        )
        self.trade_market_label.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        ctk.CTkButton(market_status, text="Change Market Near Chart Zoom", command=self.open_central_market_picker, fg_color=THEME["accent_3"], text_color="#08131f").grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))

        # Hidden canonical widgets preserve compatibility with preview/place logic while removing duplicate market selectors.
        self.trade_product_combo = ctk.CTkComboBox(tab, values=[str(self.settings.data.get("product_id", "ETH-PERP"))], state="normal")
        self.trade_product_combo.set(str(self.settings.data.get("product_id", "ETH-PERP")))
        self.trade_reference_combo = ctk.CTkComboBox(tab, values=[str(self.settings.data.get("reference_product_id", "ETH-USD"))], state="normal")
        self.trade_reference_combo.set(str(self.settings.data.get("reference_product_id", "ETH-USD")))

        self.order_side = ctk.CTkComboBox(tab, values=["BUY", "SELL"])
        self.order_side.set("BUY")
        self.order_side.grid(row=2, column=0, sticky="ew", padx=(12, 6), pady=6)
        self.order_quote_size = ctk.CTkEntry(tab, placeholder_text="Quote size in USD")
        self.order_quote_size.insert(0, str(self.settings.data.get("default_order_quote_size", 25.0)))
        self.order_quote_size.grid(row=2, column=1, sticky="ew", padx=(6, 12), pady=6)
        self.order_leverage = ctk.CTkEntry(tab, placeholder_text="Leverage")
        self.order_leverage.insert(0, str(self.settings.data.get("default_order_leverage", 3.0)))
        self.order_leverage.grid(row=3, column=0, sticky="ew", padx=(12, 6), pady=6)
        self.margin_type_combo = ctk.CTkComboBox(tab, values=["CROSS", "ISOLATED", ""])
        self.margin_type_combo.set(str(self.settings.data.get("margin_type", "CROSS")))
        self.margin_type_combo.grid(row=3, column=1, sticky="ew", padx=(6, 12), pady=6)
        self.autonomy_size_label = ctk.CTkLabel(tab, text="—", text_color=THEME["text"])
        ctk.CTkLabel(tab, text="Autonomy Proposed Size", text_color=THEME["muted"]).grid(row=4, column=0, sticky="w", padx=12, pady=(2, 0))
        self.autonomy_size_label.grid(row=4, column=1, sticky="w", padx=12, pady=(2, 0))
        ctk.CTkButton(tab, text="Seed From Signal", command=self.seed_trade_from_surface, fg_color=THEME["card_soft"]).grid(row=5, column=0, sticky="ew", padx=(12, 6), pady=6)
        ctk.CTkButton(tab, text="Preview Order", command=self.preview_order, fg_color=THEME["accent_2"], text_color="#08131f").grid(row=5, column=1, sticky="ew", padx=(6, 12), pady=6)
        ctk.CTkButton(tab, text="Place Live Order", command=self.place_order, fg_color=THEME["danger"]).grid(row=6, column=0, columnspan=2, sticky="ew", padx=12, pady=6)

        ai_card = ctk.CTkFrame(tab, fg_color=THEME["card"], corner_radius=16)
        ai_card.grid(row=7, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 8))
        ai_card.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkLabel(ai_card, text="AI trading replies", text_color=THEME["text"], font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 4))
        ctk.CTkButton(ai_card, text="AI Market Read", command=lambda: self.ask_trade_ai("market_read"), fg_color=THEME["accent"], text_color="#121212").grid(row=1, column=0, sticky="ew", padx=(12, 4), pady=(2, 12))
        ctk.CTkButton(ai_card, text="AI Risk Audit", command=lambda: self.ask_trade_ai("risk_audit"), fg_color=THEME["card_soft"]).grid(row=1, column=1, sticky="ew", padx=4, pady=(2, 12))
        ctk.CTkButton(ai_card, text="AI Execution Plan", command=lambda: self.ask_trade_ai("execution_plan"), fg_color=THEME["accent_2"], text_color="#08131f").grid(row=1, column=2, sticky="ew", padx=(4, 12), pady=(2, 12))
        self.trade_ai_status_label = ctk.CTkLabel(ai_card, text="AI idle", text_color=THEME["muted"], anchor="w")
        self.trade_ai_status_label.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 10))

        self.trade_log = ctk.CTkTextbox(tab, fg_color=THEME["card"], text_color=THEME["text"], border_color=THEME["line"], border_width=1, corner_radius=14)
        self.trade_log.grid(row=10, column=0, columnspan=2, sticky="nsew", padx=12, pady=(6, 12))
        self.trade_log.insert("1.0", "Execution notes:\n- Market selection is centralized in one place: the Market button beside chart Zoom +/- controls.\n- Trading, chart, settings, preview, and live-order code all read the same saved product_id.\n- AI trading replies use the active market packet and fall back to local signal-lab commentary if Gemma is unavailable.\n- Live rail requires explicit enablement and explicit confirmation.\n- Income rails are research frameworks, not profit guarantees.\n")

    def _build_prompt_tab(self) -> None:
        tab = self.prompt_tab
        tab.grid_columnconfigure(0, weight=0)
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 8))
        ctk.CTkButton(top, text="Reload Prompt Pack", command=self._reload_prompt_pack).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkButton(top, text="Save Prompt Pack", command=self._save_prompt_pack).grid(row=0, column=1, padx=6)
        ctk.CTkButton(top, text="Build Current Prompt", command=self._show_built_prompt, fg_color=THEME["accent"], text_color="#121212").grid(row=0, column=2, padx=6)
        ctk.CTkButton(top, text="Use Selected In Chat", command=self._insert_selected_prompt_into_chat, fg_color=THEME["accent_2"], text_color="#0c1720").grid(row=0, column=3, padx=(6, 0))

        library_card = ctk.CTkFrame(tab, fg_color=THEME["card"], corner_radius=16, width=300)
        library_card.grid(row=1, column=0, sticky="ns", padx=(12, 8), pady=(0, 12))
        library_card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(library_card, text="Advanced LLM Prompt Library", text_color=THEME["text"], font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 2))
        ctk.CTkLabel(library_card, text="Core systems, research chains, income rails, execution prompts, and visual prompts all live here inside main.py.", text_color=THEME["muted"], font=ctk.CTkFont(size=11), justify="left", wraplength=260).grid(row=1, column=0, sticky="nw", padx=12, pady=(0, 4))
        self.prompt_library_scroll = ctk.CTkScrollableFrame(library_card, fg_color=THEME["card_soft"], corner_radius=12, width=280, height=760)
        self.prompt_library_scroll.grid(row=2, column=0, sticky="nsew", padx=12, pady=(8, 12))

        right = ctk.CTkFrame(tab, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew", padx=(0, 12), pady=(0, 12))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(right, text="Selected Prompt", text_color=THEME["text"], font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.prompt_library_name_label = ctk.CTkLabel(right, text="—", text_color=THEME["accent"], font=ctk.CTkFont(size=13, weight="bold"))
        self.prompt_library_name_label.grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.prompt_library_preview = ctk.CTkTextbox(right, height=220, fg_color=THEME["card"], text_color=THEME["text"], border_color=THEME["line"], border_width=1, corner_radius=14, wrap="word")
        self.prompt_library_preview.grid(row=2, column=0, sticky="ew", pady=(0, 12))

        ctk.CTkLabel(right, text="Full Embedded Prompt Pack JSON", text_color=THEME["text"], font=ctk.CTkFont(size=16, weight="bold")).grid(row=3, column=0, sticky="sw", pady=(0, 4))
        self.prompt_editor = ctk.CTkTextbox(right, fg_color=THEME["card"], text_color=THEME["text"], border_color=THEME["line"], border_width=1, corner_radius=14)
        self.prompt_editor.grid(row=4, column=0, sticky="nsew")
        self.prompt_editor.insert("1.0", json.dumps(self.prompt_pack.data, indent=2))

        self._rebuild_prompt_library_buttons()

    def _build_settings_tab(self) -> None:
        host = self.settings_tab
        host.grid_columnconfigure(0, weight=1)
        host.grid_rowconfigure(0, weight=1)
        tab = ctk.CTkScrollableFrame(host, fg_color="transparent")
        tab.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        tab.grid_columnconfigure(1, weight=1)
        labels = [
            ("Model Path", "model_path_entry"),
            ("Refresh Seconds", "refresh_entry"),
            ("Primary Timeframe Minutes", "tf_primary_entry"),
            ("Secondary Timeframe Minutes", "tf_secondary_entry"),
            ("Tertiary Timeframe Minutes", "tf_tertiary_entry"),
            ("Bars", "bars_entry"),
            ("Default Quote Size USD", "default_quote_size_entry"),
            ("Default Leverage", "default_leverage_entry"),
            ("Autonomy Min Confidence", "autonomy_conf_entry"),
            ("Autonomy Cooldown Seconds", "autonomy_cooldown_entry"),
        ]
        row = 0
        for label, name in labels:
            ctk.CTkLabel(tab, text=label, text_color=THEME["muted"]).grid(row=row, column=0, sticky="w", padx=12, pady=(12 if row == 0 else 6, 6))
            if name in {"product_id_entry", "reference_product_entry"}:
                entry = ctk.CTkComboBox(tab, values=["ETH-PERP", "ETH-USD", "NOL-18MAY26-CDE"], state="normal")
            else:
                entry = ctk.CTkEntry(tab)
            entry.grid(row=row, column=1, sticky="ew", padx=(6, 12), pady=(12 if row == 0 else 6, 6))
            setattr(self, name, entry)
            row += 1

        self.product_id_entry = ctk.CTkComboBox(tab, values=[str(self.settings.data.get("product_id", "ETH-PERP"))], state="normal")
        self.product_id_entry.set(str(self.settings.data.get("product_id", "ETH-PERP")))
        self.reference_product_entry = ctk.CTkComboBox(tab, values=[str(self.settings.data.get("reference_product_id", "ETH-USD"))], state="normal")
        self.reference_product_entry.set(str(self.settings.data.get("reference_product_id", "ETH-USD")))
        ctk.CTkLabel(tab, text="Market", text_color=THEME["muted"]).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        ctk.CTkButton(tab, text="Change Global Market Near Chart Zoom", command=self.open_central_market_picker, fg_color=THEME["accent_3"], text_color="#08131f").grid(row=row, column=1, sticky="ew", padx=(6, 12), pady=6)
        row += 1

        ctk.CTkLabel(tab, text="Vault Passphrase", text_color=THEME["muted"]).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        self.vault_passphrase_entry = ctk.CTkEntry(tab, show="*", placeholder_text="Optional: leave blank to use the vault popup")
        self.vault_passphrase_entry.grid(row=row, column=1, sticky="ew", padx=(6, 12), pady=6)
        row += 1

        ctk.CTkLabel(tab, text="Coinbase API Key", text_color=THEME["muted"]).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        self.coinbase_api_key_entry = ctk.CTkEntry(tab)
        self.coinbase_api_key_entry.grid(row=row, column=1, sticky="ew", padx=(6, 12), pady=6)
        row += 1

        ctk.CTkLabel(tab, text="Coinbase Private Key / JSON", text_color=THEME["muted"]).grid(row=row, column=0, sticky="nw", padx=12, pady=6)
        self.coinbase_private_key_box = ctk.CTkTextbox(tab, height=84)
        self.coinbase_private_key_box.grid(row=row, column=1, sticky="ew", padx=(6, 12), pady=6)
        self.coinbase_private_key_box.insert("1.0", "")
        self.coinbase_private_key_box.configure(state="disabled")
        row += 1

        key_actions = ctk.CTkFrame(tab, fg_color="transparent")
        key_actions.grid(row=row, column=1, sticky="w", padx=(6, 12), pady=(0, 8))
        ctk.CTkButton(key_actions, text="Paste PEM/JSON", command=self._paste_private_key_from_clipboard, width=128, height=30, fg_color=THEME["card_soft"]).pack(side="left", padx=(0, 6))
        ctk.CTkButton(key_actions, text="Load Key File", command=self._load_private_key_file, width=118, height=30, fg_color=THEME["card_soft"]).pack(side="left", padx=6)
        ctk.CTkButton(key_actions, text="Clear", command=self._clear_private_key_value, width=86, height=30, fg_color=THEME["card_soft"]).pack(side="left", padx=(6, 0))
        row += 1

        ctk.CTkLabel(tab, text="JWT Algorithm", text_color=THEME["muted"]).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        self.jwt_algo_combo = ctk.CTkComboBox(tab, values=["ES256", "EdDSA"])
        self.jwt_algo_combo.grid(row=row, column=1, sticky="ew", padx=(6, 12), pady=6)
        row += 1

        ctk.CTkButton(tab, text="Refresh Coinbase Product Dropdowns", command=self.refresh_products_action, fg_color=THEME["card_soft"]).grid(row=row, column=1, sticky="ew", padx=(6, 12), pady=6)
        row += 1

        self.paper_mode_switch = ctk.CTkSwitch(tab, text="Paper trading enabled")
        self.paper_mode_switch.grid(row=row, column=0, sticky="w", padx=12, pady=6)
        self.live_mode_switch = ctk.CTkSwitch(tab, text="Allow live trading")
        self.live_mode_switch.grid(row=row, column=1, sticky="w", padx=12, pady=6)
        row += 1
        self.visual_mode_switch = ctk.CTkSwitch(tab, text="Enable Visual LLM mode")
        self.visual_mode_switch.grid(row=row, column=0, sticky="w", padx=12, pady=6)
        self.autonomy_mode_switch = ctk.CTkSwitch(tab, text="Enable autonomy suggestions")
        self.autonomy_mode_switch.grid(row=row, column=1, sticky="w", padx=12, pady=6)
        row += 1
        self.sensor_mode_switch = ctk.CTkSwitch(tab, text="Enable Pennylane / RGB sensor")
        self.sensor_mode_switch.grid(row=row, column=0, sticky="w", padx=12, pady=6)
        self.rag_mode_switch = ctk.CTkSwitch(tab, text="Enable entropic RAG memory")
        self.rag_mode_switch.grid(row=row, column=1, sticky="w", padx=12, pady=6)
        row += 1
        self.cloud_mode_switch = ctk.CTkSwitch(tab, text="Show EMA ribbon cloud")
        self.cloud_mode_switch.grid(row=row, column=0, sticky="w", padx=12, pady=6)
        self.quant_panel_switch = ctk.CTkSwitch(tab, text="Show quantum panel on chart")
        self.quant_panel_switch.grid(row=row, column=1, sticky="w", padx=12, pady=6)
        row += 1
        self.ema233_switch = ctk.CTkSwitch(tab, text="Show EMA 233")
        self.ema233_switch.grid(row=row, column=0, sticky="w", padx=12, pady=6)
        self.side_tray_switch = ctk.CTkSwitch(tab, text="Hide side tray / enlarge chart")
        self.side_tray_switch.grid(row=row, column=1, sticky="w", padx=12, pady=6)
        row += 1

        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=row, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 10))
        btns.grid_columnconfigure((0, 1, 2, 3), weight=1)
        ctk.CTkButton(btns, text="Browse Model", command=self._browse_model).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(btns, text="Download Model", command=self.download_model_action, fg_color=THEME["accent_2"], text_color="#08131f").grid(row=0, column=1, sticky="ew", padx=6)
        ctk.CTkButton(btns, text="Unlock Secrets", command=self.unlock_secrets).grid(row=0, column=2, sticky="ew", padx=6)
        ctk.CTkButton(btns, text="Save Settings", command=self.save_settings_action, fg_color=THEME["accent"], text_color="#121212").grid(row=0, column=3, sticky="ew", padx=(6, 0))
        row += 1

        vault_tools = ctk.CTkFrame(tab, fg_color="transparent")
        vault_tools.grid(row=row, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        vault_tools.grid_columnconfigure((0, 1, 2, 3), weight=1)
        ctk.CTkButton(vault_tools, text="Rotate Vault Key", command=self.rotate_vault_password, fg_color=THEME["card_soft"]).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(vault_tools, text="Lock Vault", command=self.lock_vault_session, fg_color=THEME["card_soft"]).grid(row=0, column=1, sticky="ew", padx=6)
        ctk.CTkButton(vault_tools, text="Replay Startup Guide", command=lambda: self.open_startup_setup_dialog(force=True), fg_color=THEME["card_soft"]).grid(row=0, column=2, sticky="ew", padx=6)
        ctk.CTkButton(vault_tools, text="Refresh Market", command=self.refresh_market, fg_color=THEME["accent_3"], text_color="#08131f").grid(row=0, column=3, sticky="ew", padx=(6, 0))
        row += 1

        self.vault_status_label = ctk.CTkLabel(tab, text="Vault: locked", text_color=THEME["muted"], justify="left", wraplength=720)
        self.vault_status_label.grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))
        row += 1

        self.settings_status = ctk.CTkTextbox(tab, height=180, fg_color=THEME["card"], text_color=THEME["text"], border_color=THEME["line"], border_width=1, corner_radius=14)
        self.settings_status.grid(row=row, column=0, columnspan=2, sticky="nsew", padx=12, pady=(4, 12))
        self.settings_status.insert(
            "1.0",
            "Settings notes:\n"
            "- Secrets now use a wrapped-master AES-GCM vault, not direct passphrase encryption.\n"
            "- The vault password is never persisted in the JSON settings file.\n"
            "- Unlock uses an in-session popup and key rotation can reseal the bundle with a fresh master key.\n"
            "- Download Model fetches the default Gemma 4 LiteRT file and verifies its SHA256.\n"
            "- The RGB / Pennylane sensor is bounded auxiliary context.\n"
            "- Entropic RAG memory only uses in-session derived states.\n"
            "- Use the Hide Tray button beside chart zoom for a large chart without fullscreen.\n",
        )

    def _set_llm_chunk_status(self, chunk: str) -> None:
        self._llm_active_chunk = sanitize_ui_text(chunk, max_chars=80)

    def _start_llm_indicator(self, context: str = "LLM") -> None:
        self._llm_running_jobs += 1
        self._llm_status_context = sanitize_ui_text(context, max_chars=48) or "LLM"
        if self._llm_running_jobs == 1:
            self._llm_started_at = time.time()
            self._llm_spinner_index = 0
            self._llm_active_chunk = ""
            try:
                if hasattr(self, "llm_progress"):
                    self.llm_progress.start()
            except Exception:
                pass
            self._animate_llm_indicator()

    def _finish_llm_indicator(self, final_text: str = "LLM idle") -> None:
        self._llm_running_jobs = max(0, self._llm_running_jobs - 1)
        if self._llm_running_jobs > 0:
            return
        self._llm_active_chunk = ""
        try:
            if hasattr(self, "llm_progress"):
                self.llm_progress.stop()
                self.llm_progress.set(0)
        except Exception:
            pass
        clean = sanitize_ui_text(final_text, max_chars=96) or "LLM idle"
        for attr in ("llm_status_label", "trade_ai_status_label"):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget.configure(text=clean, text_color=THEME["muted"])
                except Exception:
                    pass

    def _animate_llm_indicator(self) -> None:
        if self._llm_running_jobs <= 0:
            return
        frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
        frame = frames[self._llm_spinner_index % len(frames)]
        self._llm_spinner_index += 1
        elapsed = int(max(0, time.time() - self._llm_started_at))
        chunk = f" · {self._llm_active_chunk}" if self._llm_active_chunk else ""
        text = f"{frame} {self._llm_status_context} running · {elapsed}s{chunk}"
        for attr in ("llm_status_label", "trade_ai_status_label"):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget.configure(text=text, text_color=THEME["accent"])
                except Exception:
                    pass
        self._llm_spinner_after_id = self.after(160, self._animate_llm_indicator)

    def _seed_chat_prompt(self, prompt: str) -> None:
        self.chat_input.delete("1.0", "end")
        self.chat_input.insert("1.0", prompt)

    def _handle_chat_shift_return(self, _event: Any) -> str:
        self.chat_input.insert("insert", "\n")
        return "break"

    def _handle_chat_return(self, event: Any) -> str:
        if getattr(event, "state", 0) & 0x0001:
            return self._handle_chat_shift_return(event)
        self.ask_model()
        return "break"

    def _seed_and_send(self, prompt: str) -> None:
        self._seed_chat_prompt(prompt)
        self.ask_model()

    def _append_chat(self, speaker: str, text: str) -> None:
        self.chat_history.configure(state="normal")
        self.chat_history.insert("end", f"{speaker}:\n{text}\n\n")
        self.chat_history.see("end")
        self.chat_history.configure(state="disabled")

    def _set_summary(self, text: str) -> None:
        self.summary_box.configure(state="normal")
        self.summary_box.delete("1.0", "end")
        self.summary_box.insert("1.0", text)
        self.summary_box.configure(state="disabled")

    def _flatten_prompt_library(self) -> Dict[str, str]:
        library = self.prompt_pack.data.get("prompt_library", {})
        flat: Dict[str, str] = {}

        def walk(prefix: str, node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    new_prefix = f"{prefix}{key}"
                    if isinstance(value, dict):
                        walk(new_prefix + " / ", value)
                    elif isinstance(value, str):
                        flat[new_prefix] = value

        walk("", library)
        return flat

    def _select_prompt_library_item(self, name: str, prompt: str) -> None:
        self.selected_prompt_name = name
        if hasattr(self, "prompt_library_name_label"):
            self.prompt_library_name_label.configure(text=name)
        if hasattr(self, "prompt_library_preview"):
            self.prompt_library_preview.delete("1.0", "end")
            self.prompt_library_preview.insert("1.0", prompt)

    def _insert_selected_prompt_into_chat(self) -> None:
        if not self.selected_prompt_name:
            return
        prompt = self.prompt_library_preview.get("1.0", "end").strip()
        if not prompt:
            return
        self.chat_input.delete("1.0", "end")
        self.chat_input.insert("1.0", prompt)
        self.tabs.set("Chat")

    def _rebuild_prompt_library_buttons(self) -> None:
        if not hasattr(self, "prompt_library_scroll"):
            return
        for child in self.prompt_library_scroll.winfo_children():
            child.destroy()
        self.prompt_library_flat = self._flatten_prompt_library()
        ordered = list(self.prompt_library_flat.items())
        for row, (name, prompt) in enumerate(ordered):
            btn = ctk.CTkButton(
                self.prompt_library_scroll,
                text=name,
                anchor="w",
                height=34,
                fg_color=THEME["card"],
                hover_color=THEME["line"],
                text_color=THEME["text"],
                corner_radius=10,
                command=lambda n=name, p=prompt: self._select_prompt_library_item(n, p),
            )
            btn.grid(row=row, column=0, sticky="ew", padx=4, pady=4)
        if ordered:
            self._select_prompt_library_item(*ordered[0])

    def _reload_prompt_pack(self) -> None:
        self.prompt_pack.data = merge_prompt_pack(self.settings.data.get("prompt_pack"))
        self.prompt_editor.delete("1.0", "end")
        self.prompt_editor.insert("1.0", json.dumps(self.prompt_pack.data, indent=2))
        self._rebuild_prompt_library_buttons()

    def _save_prompt_pack(self) -> None:
        data = json.loads(self.prompt_editor.get("1.0", "end"))
        self.prompt_pack.data = merge_prompt_pack(data)
        self.prompt_pack.save()
        self._rebuild_prompt_library_buttons()

    def _show_built_prompt(self) -> None:
        if self.surface is None:
            messagebox.showwarning("No packet", "Refresh market data first.")
            return
        prompt = self.prompt_architect.build_gemma4_prompt(
            self.surface,
            self.frames,
            self.reference_frames,
            self.chat_input.get("1.0", "end").strip() or "Produce the best current ETH-PERP research memo.",
        )
        self.prompt_editor.delete("1.0", "end")
        self.prompt_editor.insert("1.0", prompt)

    def _append_settings_status(self, text: str) -> None:
        if not hasattr(self, "settings_status") and not hasattr(self, "setup_status_box"):
            return
        clean = str(text).strip()
        if not clean:
            return
        if hasattr(self, "settings_status"):
            self.settings_status.insert("end", f"{clean}\n")
            self.settings_status.see("end")
        if hasattr(self, "setup_status_box"):
            self.setup_status_box.insert("end", f"{clean}\n")
            self.setup_status_box.see("end")

    def _read_inline_vault_password(self) -> str:
        for name in ("setup_passphrase_entry", "vault_passphrase_entry"):
            widget = getattr(self, name, None)
            if widget is None:
                continue
            text = widget.get().strip()
            if text:
                return text
        return ""

    def _clear_vault_password_inputs(self) -> None:
        for name in ("setup_passphrase_entry", "vault_passphrase_entry"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.delete(0, "end")

    def _clear_decrypted_secret_inputs(self) -> None:
        for name in ("coinbase_api_key_entry", "setup_coinbase_api_key_entry"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.delete(0, "end")
        self._coinbase_private_key_plain = ""
        self._render_private_key_preview()

    def _render_private_key_preview(self) -> None:
        masked = mask_secret_multiline(self._coinbase_private_key_plain)
        preview = masked or ""
        for name in ("coinbase_private_key_box", "setup_coinbase_private_key_box"):
            widget = getattr(self, name, None)
            if widget is None:
                continue
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            if preview:
                widget.insert("1.0", preview)
            widget.configure(state="disabled")

    def _set_private_key_value(self, value: str) -> None:
        normalized = CoinbaseAdvancedClient._normalize_private_key_input(value or "")
        self._coinbase_private_key_plain = sanitize_structured_text(normalized, max_chars=50000)
        self._render_private_key_preview()

    def _get_private_key_value(self) -> str:
        return self._coinbase_private_key_plain.strip()

    def _paste_private_key_from_clipboard(self) -> None:
        try:
            clipboard_text = self.clipboard_get()
        except Exception as exc:
            messagebox.showerror("Clipboard unavailable", f"Could not read the clipboard.\n\n{exc}")
            return
        clean = sanitize_structured_text(clipboard_text, max_chars=50000).strip()
        if not clean:
            messagebox.showwarning("Clipboard empty", "Copy the Coinbase private key first, then try Paste Key again.")
            return
        normalized = CoinbaseAdvancedClient._normalize_private_key_input(clean)
        ok, detail = CoinbaseAdvancedClient._validate_private_key_pem(normalized)
        self._set_private_key_value(normalized)
        self._append_settings_status("Coinbase private key loaded from clipboard, newline-repaired, and masked in the UI.")
        self._append_settings_status(detail if ok else f"Key warning: {detail}")

    def _load_private_key_file(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Choose Coinbase private key file",
            filetypes=[("Key files", "*.pem *.txt *.key *.json"), ("All files", "*.*")],
        )
        if not chosen:
            return
        try:
            clean = Path(chosen).read_text(encoding="utf-8").strip()
        except Exception as exc:
            messagebox.showerror("Read failed", f"Could not read the selected key file.\n\n{exc}")
            return
        if not clean:
            messagebox.showwarning("Empty file", "That private-key file was empty.")
            return
        normalized = CoinbaseAdvancedClient._normalize_private_key_input(clean)
        ok, detail = CoinbaseAdvancedClient._validate_private_key_pem(normalized)
        self._set_private_key_value(normalized)
        self._append_settings_status(f"Coinbase private key loaded from file, newline-repaired, and masked in the UI: {chosen}")
        self._append_settings_status(detail if ok else f"Key warning: {detail}")

    def _clear_private_key_value(self) -> None:
        self._coinbase_private_key_plain = ""
        self._render_private_key_preview()
        self._append_settings_status("Coinbase private key cleared from the current session fields.")

    def _prompt_vault_password(self, mode: str, title: str, message: str) -> Optional[Dict[str, str]]:
        dialog = VaultPasswordDialog(self, mode=mode, title=title, message=message)
        return dialog.show_modal()

    def _startup_checklist_text(self) -> str:
        model_ready = self._current_model_path().is_file()
        secrets_ready = bool(self.settings.data.get("secrets"))
        vault_ready = bool(self.vault_unlocked and self.vault_master_key)
        market_ready = any(not df.empty for df in self.frames.values()) if self.frames else False
        lines = [
            f"{'OK' if model_ready else 'TODO'} Model runtime {'ready' if model_ready else 'missing'}",
            f"{'OK' if secrets_ready else 'TODO'} Encrypted Coinbase bundle {'saved' if secrets_ready else 'not saved yet'}",
            f"{'OK' if vault_ready else 'TODO'} Vault session {'unlocked in memory' if vault_ready else 'still sealed in this session'}",
            f"{'OK' if market_ready else 'TODO'} Market data {'flowing' if market_ready else 'not loaded yet'}",
        ]
        if self.last_market_error:
            lines.append(f"Last market issue: {self.last_market_error}")
        return "\n".join(lines)

    def _vault_status_text(self) -> str:
        state = self.settings.vault_security()
        mode = state.get("mode") or self.settings.vault_mode()
        if not state.get("enabled"):
            label = "not configured"
        else:
            label = "unlocked in session" if self.vault_unlocked and self.vault_master_key else "sealed"
        parts = [f"Vault: {label}", f"mode {str(mode).replace('_', ' ')}"]
        rotation_count = max(0, int(safe_float(state.get("rotation_count", 0))))
        if rotation_count:
            parts.append(f"rotations {rotation_count}")
        if state.get("last_rotated_at"):
            parts.append(f"last rotate {state['last_rotated_at']}")
        if state.get("last_unlocked_at"):
            parts.append(f"last unlock {state['last_unlocked_at']}")
        return " · ".join(parts)

    def _update_vault_security_ui(self) -> None:
        self.vault_mode = self.settings.vault_mode()
        if hasattr(self, "vault_status_label"):
            self.vault_status_label.configure(text=self._vault_status_text())
        if hasattr(self, "chart"):
            self.chart.set_vault_state(bool(self.vault_unlocked and self.coinbase.secrets), bool(self.settings.data.get("secrets")))
        self._refresh_startup_checklist()

    def _unlock_vault_with_password(self, password: str, *, quiet: bool = False) -> bool:
        if not self.settings.data.get("secrets"):
            if not quiet:
                messagebox.showinfo("Vault empty", "Save Coinbase credentials first, then create a vault password.")
            return False
        try:
            secrets, master_key, mode = self.settings.unlock_secret_bundle(password)
            if mode == "legacy_direct_v1":
                secrets, master_key, mode = self.settings.migrate_legacy_secrets(password)
                self._append_settings_status("Legacy Coinbase vault upgraded to wrapped-master security.")
            self.settings.note_vault_unlock(mode)
            self.coinbase.set_secrets(secrets)
            self.vault_master_key = master_key
            self.vault_mode = mode
            self.vault_unlocked = True
            self.coinbase_api_key_entry.delete(0, "end")
            self.coinbase_api_key_entry.insert(0, secrets.get("coinbase_api_key", ""))
            self._set_private_key_value(secrets.get("coinbase_private_key", ""))
            self._clear_vault_password_inputs()
            self._sync_setup_widgets_from_settings_widgets()
            self.trade_log.insert("end", "Vault unlocked. Coinbase preview rail is ready.\n")
            self._append_settings_status("Vault unlocked. Coinbase secrets are loaded only for this session.")
            self._update_vault_security_ui()
            return True
        except Exception as exc:
            self.coinbase.set_secrets({})
            self.vault_master_key = None
            self.vault_unlocked = False
            self._append_settings_status(f"Vault unlock failed: {exc}")
            self._update_vault_security_ui()
            if not quiet:
                messagebox.showerror("Unlock failed", f"Could not unlock the encrypted Coinbase vault.\n\n{exc}")
            return False

    def ensure_secrets_unlocked(self, *, startup_prompt: bool = False) -> bool:
        if self.coinbase.secrets:
            return True
        if not self.settings.data.get("secrets"):
            if not startup_prompt:
                messagebox.showwarning("No saved vault", "Save encrypted Coinbase credentials first.")
            return False
        password = self._read_inline_vault_password()
        self._clear_vault_password_inputs()
        if not password:
            prompt = self._prompt_vault_password(
                "unlock",
                "Unlock Coinbase Vault",
                "Enter the vault password to unlock the encrypted Coinbase bundle for this session."
                if not startup_prompt
                else "Startup detected a sealed Coinbase vault. Enter the password to unlock it now, or cancel and continue in read-only mode.",
            )
            if not prompt:
                return False
            password = prompt["password"]
        return self._unlock_vault_with_password(password, quiet=False)

    def lock_vault_session(self) -> None:
        self.coinbase.set_secrets({})
        self.vault_master_key = None
        self.vault_unlocked = False
        self._clear_vault_password_inputs()
        self._clear_decrypted_secret_inputs()
        self._append_settings_status("Vault locked. Decrypted Coinbase credentials were cleared from the UI.")
        self._update_vault_security_ui()

    def rotate_vault_password(self) -> None:
        if not self.settings.data.get("secrets"):
            messagebox.showwarning("No vault", "There is no encrypted Coinbase bundle to rotate yet.")
            return
        prompt = self._prompt_vault_password(
            "rotate",
            "Rotate Vault Password",
            "Enter the current vault password and choose a new one. Rotation reseals the Coinbase bundle with a fresh wrapped master key.",
        )
        if not prompt:
            return
        try:
            secrets, master_key, mode = self.settings.rotate_secret_passphrase(prompt["current_password"], prompt["new_password"])
            self.coinbase.set_secrets(secrets)
            self.vault_master_key = master_key
            self.vault_mode = mode
            self.vault_unlocked = True
            self.coinbase_api_key_entry.delete(0, "end")
            self.coinbase_api_key_entry.insert(0, secrets.get("coinbase_api_key", ""))
            self._set_private_key_value(secrets.get("coinbase_private_key", ""))
            self._clear_vault_password_inputs()
            self._sync_setup_widgets_from_settings_widgets()
            self._append_settings_status("Vault password rotated. Coinbase secrets were resealed with a fresh wrapped master key.")
            self._update_vault_security_ui()
            messagebox.showinfo("Vault rotated", "The Coinbase vault password was rotated successfully.")
        except Exception as exc:
            self._append_settings_status(f"Vault rotation failed: {exc}")
            messagebox.showerror("Rotation failed", f"Could not rotate the vault password.\n\n{exc}")

    def open_startup_setup_dialog(self, *, force: bool = False) -> str:
        if not force and not self._initial_setup_needed():
            return "continue"
        dialog = StartupSetupDialog(self, checklist_text=self._startup_checklist_text())
        result = dialog.show_modal()
        if result == "setup":
            self.tabs.set("Setup")
        return result

    def _run_startup_workflows(self) -> None:
        if self._startup_workflow_ran:
            return
        self._startup_workflow_ran = True
        self._mark_startup_flow_complete_if_ready()
        self._select_initial_tab()
        # Do not auto-open the first-start workflow anymore. It is available only
        # through Settings -> Replay Startup Guide. A saved vault still prompts
        # to unlock when that startup setting is enabled.
        state = self.settings.vault_security()
        if bool(self.settings.data.get("secrets")) and bool(state.get("prompt_on_startup", True)) and not self.coinbase.secrets:
            self.ensure_secrets_unlocked(startup_prompt=True)
        self._select_initial_tab()

    def _refresh_startup_checklist(self) -> None:
        if not hasattr(self, "setup_checklist_label"):
            return
        self.setup_checklist_label.configure(text=self._startup_checklist_text())

    @staticmethod
    def _get_widget_text(widget: Any) -> str:
        try:
            return str(widget.get()).strip()
        except Exception:
            return ""

    @staticmethod
    def _set_widget_text(widget: Any, value: Any) -> None:
        text = str(value or "")
        if hasattr(widget, "set"):
            try:
                widget.set(text)
                return
            except Exception:
                pass
        try:
            widget.delete(0, "end")
            widget.insert(0, text)
        except Exception:
            pass

    def _product_dropdown_labels(self) -> List[str]:
        labels = list(getattr(self, "product_label_to_id", {}).keys())
        if not labels:
            labels = ["ETH-PERP", "ETH-USD", "BTC-USD", "NOL-18MAY26-CDE"]
        for fallback in ("ETH-PERP", "ETH-USD", "BTC-USD", "NOL-18MAY26-CDE"):
            if fallback not in labels:
                labels.append(fallback)
        return labels

    def _label_for_product_id(self, product_id: str) -> str:
        clean = sanitize_product_id(product_id, default=str(product_id or ""))
        for label, pid in getattr(self, "product_label_to_id", {}).items():
            if pid == clean:
                return label
        return clean

    def open_central_market_picker(self) -> None:
        """Single global market selector used by chart, trading, settings, and order routing."""
        current = self._label_for_product_id(str(self.settings.data.get("product_id", "ETH-PERP")))
        dialog = ProductPickerDialog(self, title="Select Global Coinbase Market", labels=self._product_dropdown_labels(), current=current)
        selected = dialog.show_modal()
        if not selected:
            return
        product_id = getattr(self, "product_label_to_id", {}).get(selected, sanitize_product_id(str(selected).split(" — ", 1)[0], default="ETH-PERP"))
        previous_product = sanitize_product_id(self.settings.data.get("product_id"), default="ETH-PERP")
        previous_reference = sanitize_product_id(self.settings.data.get("reference_product_id"), default="ETH-USD")
        reference_id = self._default_reference_for_product(product_id, previous_reference if product_id == previous_product else "")
        self._apply_global_market(product_id, reference_id, refresh=True)

    def _apply_global_market(self, product_id: str, reference_id: str = "", *, refresh: bool = True) -> None:
        product_id = sanitize_product_id(product_id, default="ETH-PERP")
        reference_id = sanitize_product_id(reference_id, default=self._default_reference_for_product(product_id, ""))
        self.settings.data["product_id"] = product_id
        self.settings.data["reference_product_id"] = reference_id
        self.settings.save()
        self._sync_market_dropdown_values()
        if hasattr(self, "chart"):
            self.chart.set_market_label(product_id, reference_id)
        if hasattr(self, "trade_log"):
            self.trade_log.insert("end", f"Global market set to {product_id} with reference {reference_id}.\n")
            self.trade_log.see("end")
        self._append_settings_status(f"Global market set to {product_id}; reference set to {reference_id}.")
        if refresh:
            self.refresh_market(force=True)

    def _open_product_picker(self, widget_name: str, *, apply_after: bool = False) -> None:
        widget = getattr(self, widget_name, None)
        current = self._get_widget_text(widget) if widget is not None else ""
        dialog = ProductPickerDialog(self, title="Select Coinbase Product", labels=self._product_dropdown_labels(), current=current)
        selected = dialog.show_modal()
        if not selected:
            return
        if widget is not None:
            self._set_widget_text(widget, selected)
        if apply_after:
            self.apply_trade_market_selection()

    def _sync_market_dropdown_values(self) -> None:
        product_id = self.settings.data.get("product_id", "ETH-PERP")
        reference_id = self.settings.data.get("reference_product_id", "ETH-USD")
        for name, value in (("product_id_entry", product_id), ("setup_product_id_entry", product_id), ("trade_product_combo", product_id)):
            widget = getattr(self, name, None)
            if widget is not None:
                self._set_widget_text(widget, value)
        for name, value in (("reference_product_entry", reference_id), ("setup_reference_product_entry", reference_id), ("trade_reference_combo", reference_id)):
            widget = getattr(self, name, None)
            if widget is not None:
                self._set_widget_text(widget, value)
        if hasattr(self, "trade_market_label"):
            try:
                self.trade_market_label.configure(text=f"Active market: {product_id} · Reference: {reference_id}")
            except Exception:
                pass
        if hasattr(self, "chart"):
            self.chart.set_market_label(product_id, reference_id)

    def _default_reference_for_product(self, product_id: str, current_reference: str = "") -> str:
        product_id = sanitize_product_id(product_id, default="ETH-PERP")
        current_reference = sanitize_product_id(current_reference, default="")
        if product_id.endswith("-PERP"):
            return product_id.removesuffix("-PERP") + "-USD"
        if product_id.endswith("-PERP-INTX"):
            return product_id.removesuffix("-PERP-INTX") + "-USD"
        if product_id.endswith("-USDC"):
            return product_id.removesuffix("-USDC") + "-USD"
        if re.match(r"^[A-Z]{2,4}-\d{1,2}[A-Z]{3}\d{2}-CDE$", product_id):
            return product_id
        if product_id.endswith("-USD"):
            return product_id
        return current_reference or product_id

    def apply_trade_market_selection(self) -> None:
        product_id = self._extract_product_id_from_widget(getattr(self, "trade_product_combo", None), self.settings.data.get("product_id", "ETH-PERP"))
        reference_id = self._extract_product_id_from_widget(getattr(self, "trade_reference_combo", None), self._default_reference_for_product(product_id, ""))
        self._apply_global_market(product_id, reference_id, refresh=True)

    def _configure_product_dropdowns(self, labels: List[str]) -> None:
        values = labels or ["ETH-PERP", "ETH-USD", "BTC-USD", "NOL-18MAY26-CDE"]
        for name in ("product_id_entry", "setup_product_id_entry", "reference_product_entry", "setup_reference_product_entry", "trade_product_combo", "trade_reference_combo"):
            widget = getattr(self, name, None)
            if widget is not None and hasattr(widget, "configure"):
                try:
                    widget.configure(values=values)
                except Exception:
                    pass

    def _extract_product_id_from_widget(self, widget: Any, default: str) -> str:
        raw = self._get_widget_text(widget)
        if raw in self.product_label_to_id:
            return self.product_label_to_id[raw]
        return sanitize_product_id(raw.split(" — ", 1)[0], default=default)

    def _refresh_product_dropdowns_from_products(self, products: List[Dict[str, Any]]) -> None:
        labels: List[str] = []
        mapping: Dict[str, str] = {}
        for product in products:
            pid = sanitize_product_id(product.get("product_id"), default="")
            if not pid:
                continue
            label = safe_product_label(product)
            labels.append(label)
            mapping[label] = pid
        for fallback in ("ETH-PERP", "ETH-USD", "BTC-USD", "NOL-18MAY26-CDE"):
            if fallback not in mapping.values():
                labels.append(fallback)
                mapping[fallback] = fallback
        self.product_label_to_id = mapping
        self._configure_product_dropdowns(labels)
        self._sync_market_dropdown_values()

    def refresh_products_action(self) -> None:
        def worker() -> None:
            try:
                products = self.coinbase.list_products(master_key=self.vault_master_key, cache=self.product_store)
            except Exception as exc:
                self.after(0, lambda e=str(exc): self._append_settings_status(f"Product refresh failed: {e}"))
                return

            def done() -> None:
                self.available_products = products
                self._refresh_product_dropdowns_from_products(products)
                self._append_settings_status(f"Loaded {len(products)} Coinbase products into dropdowns, including available CFM futures for oil, equity/stock, ETF, and index underlyings when Coinbase returns them.")
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def _sync_setup_widgets_from_settings_widgets(self) -> None:
        if not hasattr(self, "setup_model_path_entry"):
            return
        pairs = [
            (self.model_path_entry, self.setup_model_path_entry),
            (self.product_id_entry, self.setup_product_id_entry),
            (self.reference_product_entry, self.setup_reference_product_entry),
            (self.coinbase_api_key_entry, self.setup_coinbase_api_key_entry),
        ]
        for source, target in pairs:
            self._set_widget_text(target, self._get_widget_text(source))
        self._render_private_key_preview()
        self._refresh_startup_checklist()

    def _sync_settings_widgets_from_setup_widgets(self) -> None:
        if not hasattr(self, "setup_model_path_entry"):
            return
        pairs = [
            (self.setup_model_path_entry, self.model_path_entry),
            (self.setup_product_id_entry, self.product_id_entry),
            (self.setup_reference_product_entry, self.reference_product_entry),
            (self.setup_coinbase_api_key_entry, self.coinbase_api_key_entry),
        ]
        for source, target in pairs:
            self._set_widget_text(target, self._get_widget_text(source))
        self._render_private_key_preview()

    def _initial_setup_needed(self) -> bool:
        """Only show first-start automatically for a truly new profile.

        Missing model files or sealed credentials should be reported in status panels,
        but they should not force returning users back into the Setup tab forever.
        """
        try:
            state = self.settings.vault_security()
            if bool(state.get("startup_flow_complete")) or bool(state.get("startup_setup_popup_seen")):
                return False
            if SETTINGS_PATH.exists():
                return False
        except Exception:
            return False
        return True

    def _select_initial_tab(self) -> None:
        """Launch into Trading. Setup is opened only when the user explicitly chooses it."""
        try:
            self.tabs.set("Trading")
        except Exception:
            pass

    def _mark_startup_flow_complete_if_ready(self) -> None:
        """Persist that first-run guidance is complete when the user saves or dismisses setup."""
        try:
            state = self.settings.vault_security()
            state["startup_flow_complete"] = True
            state["startup_setup_popup_seen"] = True
            self.settings.data["vault_security"] = state
            self.settings.save()
        except Exception:
            pass

    def save_initial_setup_action(self) -> None:
        self._sync_settings_widgets_from_setup_widgets()
        self.save_settings_action()
        self._sync_setup_widgets_from_settings_widgets()
        self._mark_startup_flow_complete_if_ready()
        self._select_initial_tab()
        self.refresh_market()

    def _current_model_path(self) -> Path:
        raw = self.model_path_entry.get().strip() if hasattr(self, "model_path_entry") else ""
        return Path(raw or DEFAULT_MODEL_PATH).expanduser()

    def _describe_model_state(self) -> str:
        model_path = self._current_model_path()
        if model_path.is_file():
            size = human_size(model_path.stat().st_size)
            return f"Model ready: {model_path} ({size})"
        return f"Model missing: {model_path} . Use Download Model to fetch the default Gemma 4 runtime."

    def _browse_model(self) -> None:
        chosen = filedialog.askopenfilename(title="Choose Gemma model", filetypes=[("LiteRT-LM Models", "*.litertlm"), ("All files", "*.*")])
        if chosen:
            self.model_path_entry.delete(0, "end")
            self.model_path_entry.insert(0, chosen)
            self.gemma = Gemma4Runtime(chosen)
            self._append_settings_status(f"Model path updated: {chosen}")
            self._sync_setup_widgets_from_settings_widgets()

    def download_model_action(self) -> None:
        if self._model_download_in_progress:
            self._append_settings_status("Model download already in progress.")
            return
        if hasattr(self, "setup_model_path_entry"):
            setup_path = self.setup_model_path_entry.get().strip()
            if setup_path:
                self.model_path_entry.delete(0, "end")
                self.model_path_entry.insert(0, setup_path)
        target = self._current_model_path()
        if target.suffix.lower() != ".litertlm":
            target = target.with_suffix(".litertlm")
        if not messagebox.askyesno(
            "Download Gemma 4 Model",
            f"Download the default Gemma 4 LiteRT model to:\n\n{target}\n\nThis will verify the expected SHA256 before enabling the runtime.",
        ):
            return

        self._model_download_in_progress = True
        self._append_settings_status(f"Downloading model from {MODEL_REPO + MODEL_FILE}")

        def worker() -> None:
            progress_state = {"last_done": -1}

            def progress(done: int, total: int) -> None:
                if total <= 0:
                    return
                if done - progress_state["last_done"] < max(total // 12, 8 * 1024 * 1024):
                    return
                progress_state["last_done"] = done
                percent = (done / total) * 100.0
                self.after(
                    0,
                    lambda d=done, t=total, p=percent: self._append_settings_status(
                        f"Download progress: {p:.1f}% ({human_size(d)} / {human_size(t)})"
                    ),
                )

            try:
                sha = download_model_httpx(
                    MODEL_REPO + MODEL_FILE,
                    target,
                    expected_sha=EXPECTED_MODEL_SHA256,
                    progress_callback=progress,
                )
            except Exception as exc:
                self.after(
                    0,
                    lambda e=str(exc): (
                        self._append_settings_status(f"Model download failed: {e}"),
                        messagebox.showerror("Download failed", f"Could not download the Gemma 4 model.\n\n{e}"),
                    ),
                )
                self.after(0, lambda: setattr(self, "_model_download_in_progress", False))
                return

            def on_success() -> None:
                self._model_download_in_progress = False
                self.model_path_entry.delete(0, "end")
                self.model_path_entry.insert(0, str(target))
                self.settings.data["model_path"] = str(target)
                self.settings.save()
                self.gemma = Gemma4Runtime(str(target))
                self._append_settings_status(
                    f"Model download complete: {target} ({human_size(target.stat().st_size)}) SHA256 {sha[:12]}..."
                )
                self._sync_setup_widgets_from_settings_widgets()
                messagebox.showinfo("Model ready", f"Gemma 4 downloaded and verified.\n\nPath: {target}\nSHA256: {sha}")

            self.after(0, on_success)

        threading.Thread(target=worker, daemon=True).start()

    def _choose_vision_image(self) -> None:
        chosen = filedialog.askopenfilename(title="Choose chart image", filetypes=[("Images", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")])
        if chosen:
            self.vision_image_path = chosen
            self.vision_output.insert("end", f"\nSelected image: {chosen}\n")

    def _load_settings_into_widgets(self) -> None:
        self.model_path_entry.insert(0, self.settings.data.get("model_path", DEFAULT_MODEL_PATH))
        self._set_widget_text(self.product_id_entry, self.settings.data.get("product_id", "ETH-PERP"))
        self._set_widget_text(self.reference_product_entry, self.settings.data.get("reference_product_id", "ETH-USD"))
        self.refresh_entry.insert(0, str(self.settings.data.get("refresh_seconds", 25)))
        self.tf_primary_entry.insert(0, str(self.settings.data.get("timeframe_minutes", 15)))
        self.tf_secondary_entry.insert(0, str(self.settings.data.get("secondary_minutes", 5)))
        self.tf_tertiary_entry.insert(0, str(self.settings.data.get("tertiary_minutes", 60)))
        self.bars_entry.insert(0, str(self.settings.data.get("bars", 320)))
        self.default_quote_size_entry.insert(0, str(self.settings.data.get("default_order_quote_size", 25.0)))
        self.default_leverage_entry.insert(0, str(self.settings.data.get("default_order_leverage", 3.0)))
        self.autonomy_conf_entry.insert(0, str(self.settings.data.get("autonomy_min_confidence", 0.70)))
        self.autonomy_cooldown_entry.insert(0, str(self.settings.data.get("autonomy_cooldown_seconds", 420)))
        self.jwt_algo_combo.set(self.settings.data.get("jwt_algorithm", "ES256"))
        for switch, key in [
            (self.paper_mode_switch, "paper_trading_enabled"),
            (self.live_mode_switch, "live_trading_enabled"),
            (self.visual_mode_switch, "use_visual_llm"),
            (self.autonomy_mode_switch, "autonomy_enabled"),
            (self.sensor_mode_switch, "pennylane_sensor_enabled"),
            (self.rag_mode_switch, "rag_memory_enabled"),
            (self.cloud_mode_switch, "chart_show_ribbon_cloud"),
            (self.quant_panel_switch, "chart_show_quantum_panel"),
            (self.ema233_switch, "chart_show_ema233"),
            (self.side_tray_switch, "side_tray_hidden"),
        ]:
            if self.settings.data.get(key, False):
                switch.select()
        cached = self.product_store.load_index()
        if cached:
            self._refresh_product_dropdowns_from_products(cached)
        else:
            self._refresh_product_dropdowns_from_products([])
        self._append_settings_status(self._describe_model_state())
        self._sync_setup_widgets_from_settings_widgets()
        self._sync_market_dropdown_values()
        self._update_vault_security_ui()
        if hasattr(self, "chart"):
            self.chart.set_active_timeframe(int(self.settings.data.get("timeframe_minutes", 15)))
        self._select_initial_tab()

    def unlock_secrets(self) -> None:
        self.ensure_secrets_unlocked()

    def save_settings_action(self) -> None:
        self.settings.data["product_id"] = self._extract_product_id_from_widget(self.product_id_entry, "ETH-PERP")
        self.settings.data["reference_product_id"] = self._extract_product_id_from_widget(self.reference_product_entry, "ETH-USD")
        self.settings.data["refresh_seconds"] = max(5, int(safe_float(self.refresh_entry.get(), 25)))
        self.settings.data["timeframe_minutes"] = max(1, int(safe_float(self.tf_primary_entry.get(), 15)))
        self.settings.data["secondary_minutes"] = max(1, int(safe_float(self.tf_secondary_entry.get(), 5)))
        self.settings.data["tertiary_minutes"] = max(1, int(safe_float(self.tf_tertiary_entry.get(), 60)))
        if hasattr(self, "chart"):
            self.chart.set_active_timeframe(int(self.settings.data["timeframe_minutes"]))
        self.settings.data["bars"] = max(120, int(safe_float(self.bars_entry.get(), 320)))
        self.settings.data["default_order_quote_size"] = max(1.0, safe_float(self.default_quote_size_entry.get(), 25.0))
        self.settings.data["default_order_leverage"] = max(1.0, safe_float(self.default_leverage_entry.get(), 3.0))
        self.settings.data["autonomy_min_confidence"] = clamp(safe_float(self.autonomy_conf_entry.get(), 0.70), 0.05, 0.98)
        self.settings.data["autonomy_cooldown_seconds"] = max(20, int(safe_float(self.autonomy_cooldown_entry.get(), 420)))
        self.settings.data["margin_type"] = self.margin_type_combo.get().strip() or "CROSS"
        self.settings.data["model_path"] = self.model_path_entry.get().strip() or DEFAULT_MODEL_PATH
        self.settings.data["jwt_algorithm"] = self.jwt_algo_combo.get().strip() or "ES256"

        for switch, key in [
            (self.paper_mode_switch, "paper_trading_enabled"),
            (self.live_mode_switch, "live_trading_enabled"),
            (self.visual_mode_switch, "use_visual_llm"),
            (self.autonomy_mode_switch, "autonomy_enabled"),
            (self.sensor_mode_switch, "pennylane_sensor_enabled"),
            (self.rag_mode_switch, "rag_memory_enabled"),
            (self.cloud_mode_switch, "chart_show_ribbon_cloud"),
            (self.quant_panel_switch, "chart_show_quantum_panel"),
            (self.ema233_switch, "chart_show_ema233"),
            (self.side_tray_switch, "side_tray_hidden"),
        ]:
            self.settings.data[key] = bool(switch.get())

        self.side_tray_hidden = bool(self.settings.data.get("side_tray_hidden", False))
        self._apply_side_tray_state(persist=False)

        raw_api_key = self.coinbase_api_key_entry.get().strip()
        raw_private_key = self._get_private_key_value()
        api_key, private_key, credential_notes = CoinbaseAdvancedClient.extract_coinbase_credentials(raw_api_key, raw_private_key)
        for note in credential_notes:
            self._append_settings_status(note)
        if private_key:
            key_ok, key_detail = CoinbaseAdvancedClient._validate_private_key_pem(private_key)
            self._append_settings_status(key_detail if key_ok else f"Key warning: {key_detail}")
        existing_vault = bool(self.settings.data.get("secrets"))
        has_secret_input = bool(api_key or private_key)
        if has_secret_input:
            if existing_vault and not self.vault_master_key:
                pending_api_key = api_key
                pending_private_key = private_key
                if not self.ensure_secrets_unlocked():
                    return
                api_key = pending_api_key
                private_key = pending_private_key
                self.coinbase_api_key_entry.delete(0, "end")
                self.coinbase_api_key_entry.insert(0, api_key)
                self._set_private_key_value(private_key)
            if existing_vault and self.vault_master_key:
                _, master_key, mode = self.settings.save_secrets(
                    {"coinbase_api_key": api_key, "coinbase_private_key": private_key},
                    master_key=self.vault_master_key,
                    reason="refresh_seal",
                )
            else:
                passphrase = self._read_inline_vault_password()
                self._clear_vault_password_inputs()
                if not passphrase:
                    prompt = self._prompt_vault_password(
                        "create",
                        "Create Coinbase Vault",
                        "Choose a vault password to encrypt Coinbase credentials with a wrapped master-key vault.",
                    )
                    if not prompt:
                        return
                    passphrase = prompt["password"]
                _, master_key, mode = self.settings.save_secrets(
                    {"coinbase_api_key": api_key, "coinbase_private_key": private_key},
                    passphrase,
                    reason="initial_seal",
                )
            self.coinbase.set_secrets({"coinbase_api_key": api_key, "coinbase_private_key": private_key})
            self.vault_master_key = master_key
            self.vault_mode = mode
            self.vault_unlocked = True
            self._append_settings_status("Coinbase credentials sealed into the wrapped-master vault.")
            self.refresh_products_action()

        self.settings.data["prompt_pack"] = merge_prompt_pack(self.prompt_pack.data)
        self.settings.save()
        self.gemma = Gemma4Runtime(self.settings.data.get("model_path", DEFAULT_MODEL_PATH))
        self._clear_vault_password_inputs()
        self._append_settings_status(f"Settings saved. {self._describe_model_state()}")
        self._sync_setup_widgets_from_settings_widgets()
        self._sync_market_dropdown_values()
        self._update_vault_security_ui()
        messagebox.showinfo("Saved", "Settings updated.")

    @staticmethod
    def _neighbor_timeframes(primary_minutes: int) -> Tuple[int, int]:
        primary = max(1, int(primary_minutes))
        choices = sorted(set(SUPPORTED_TIMEFRAME_MINUTES + tuple(ADVANCED_GRANULARITY_MAP.keys())))
        lower = max([m for m in choices if m < primary], default=primary)
        higher = min([m for m in choices if m > primary], default=primary)
        return lower, higher

    def set_primary_timeframe(self, minutes: int) -> None:
        primary = max(1, int(minutes))
        lower, higher = self._neighbor_timeframes(primary)
        self.settings.data["timeframe_minutes"] = primary
        self.settings.data["secondary_minutes"] = lower
        self.settings.data["tertiary_minutes"] = higher
        self.settings.save()
        for name, value in (("tf_primary_entry", primary), ("tf_secondary_entry", lower), ("tf_tertiary_entry", higher)):
            widget = getattr(self, name, None)
            if widget is not None:
                self._set_widget_text(widget, value)
        if hasattr(self, "chart"):
            self.chart.set_active_timeframe(primary)
        label = TIMEFRAME_LABEL_BY_MINUTE.get(primary, f"{primary}m")
        self._append_settings_status(f"Candle timeframe set to {label}. Refreshing chart...")
        self.refresh_market(force=True)

    def _load_market_frames(self, product_id: str, timeframes: List[int], bars: int) -> Tuple[Dict[int, pd.DataFrame], Dict[int, str]]:
        frames: Dict[int, pd.DataFrame] = {}
        errors: Dict[int, str] = {}
        for minutes in timeframes:
            df = enrich_indicators(self.coinbase.public_candles(product_id, minutes, bars))
            frames[minutes] = df
            if df.empty:
                errors[minutes] = self.coinbase.last_public_error or f"No candle data returned for {product_id} @ {minutes}m"
        return frames, errors

    @staticmethod
    def _summarize_market_frames(product_id: str, frames: Dict[int, pd.DataFrame], errors: Dict[int, str]) -> str:
        loaded = [f"{minutes}m" for minutes, df in sorted(frames.items()) if not df.empty]
        parts: List[str] = []
        if loaded:
            parts.append(f"loaded {', '.join(loaded)}")
        seen_errors: List[str] = []
        for minutes in sorted(errors):
            detail = f"{minutes}m failed: {errors[minutes]}"
            if detail not in seen_errors:
                seen_errors.append(detail)
        if seen_errors:
            parts.extend(seen_errors[:3])
        if not parts:
            parts.append("no candle data returned")
        return f"{product_id}: " + " | ".join(parts)

    def _schedule_refresh(self) -> None:
        self.refresh_market()
        self.after(max(5, int(self.settings.data.get("refresh_seconds", 30))) * 1000, self._schedule_refresh)

    def refresh_market(self, *, force: bool = False) -> None:
        if self._refreshing and not force:
            return
        self._refresh_token += 1
        refresh_token = self._refresh_token
        bars = int(self.settings.data.get("bars", 320))
        timeframes = sorted(
            {
                int(self.settings.data.get("secondary_minutes", 5)),
                int(self.settings.data.get("timeframe_minutes", 15)),
                int(self.settings.data.get("tertiary_minutes", 60)),
            }
        )
        product_id = sanitize_product_id(self.settings.data.get("product_id"), default="ETH-PERP")
        reference_product_id = sanitize_product_id(self.settings.data.get("reference_product_id"), default="ETH-USD")
        self._refreshing = True
        self._append_settings_status(f"Refreshing Coinbase market data for {product_id} / ref {reference_product_id}...")
        threading.Thread(target=self._refresh_worker, args=(refresh_token, product_id, reference_product_id, timeframes, bars), daemon=True).start()

    def _refresh_worker(self, refresh_token: int, product_id: str, reference_product_id: str, timeframes: List[int], bars: int) -> None:
        try:
            frames, frame_errors = self._load_market_frames(product_id, timeframes, bars)
            refs, ref_errors = self._load_market_frames(reference_product_id, timeframes, bars)
            if all(df.empty for df in refs.values()) and any(not df.empty for df in frames.values()):
                refs = {minutes: df.copy() for minutes, df in frames.items()}
                ref_errors = {}
            surface = None
            if any(not df.empty for df in frames.values()):
                self.last_market_error = ""
                configured_primary = int(self.settings.data.get("timeframe_minutes", 15))
                if configured_primary in frames and not frames[configured_primary].empty:
                    primary_minutes = configured_primary
                else:
                    primary_minutes = min((m for m in timeframes if not frames.get(m, pd.DataFrame()).empty), key=lambda x: abs(x - configured_primary))
                basis_pct = 0.0
                joined = pd.concat([frames[primary_minutes]["Close"].rename("perp"), refs.get(primary_minutes, frames[primary_minutes])["Close"].rename("spot")], axis=1, join="inner").dropna()
                if not joined.empty:
                    basis_pct = safe_float(((joined.iloc[-1]["perp"] - joined.iloc[-1]["spot"]) / joined.iloc[-1]["spot"]))
                sensor = self.sensor.sample(frames[primary_minutes], basis_pct) if self.settings.data.get("pennylane_sensor_enabled", True) else None
                surface = self.alpha.evaluate(frames, refs, sensor)
                if self.settings.data.get("rag_memory_enabled", True):
                    surface.memory_hits = self.memory.retrieve(surface, limit=5)
                    self.memory.remember(surface)
            else:
                details = [self._summarize_market_frames(product_id, frames, frame_errors)]
                if reference_product_id != product_id:
                    details.append(self._summarize_market_frames(reference_product_id, refs, ref_errors))
                self.last_market_error = " || ".join(detail for detail in details if detail)
            def apply_if_current() -> None:
                if refresh_token != self._refresh_token:
                    return
                if sanitize_product_id(self.settings.data.get("product_id"), default="") != product_id:
                    return
                self._apply_market(frames, refs, surface)
            self.after(0, apply_if_current)
        except Exception as exc:
            self.last_market_error = str(exc)
            self.after(0, lambda e=str(exc): self._append_settings_status(f"Market refresh failed: {e}"))
        finally:
            if refresh_token == self._refresh_token:
                self._refreshing = False

    def _apply_market(self, frames: Dict[int, pd.DataFrame], refs: Dict[int, pd.DataFrame], surface: Optional[SurfaceState]) -> None:
        self.frames = frames
        self.reference_frames = refs
        self.surface = surface
        primary = int(self.settings.data.get("timeframe_minutes", 15))
        display_primary = primary
        if frames.get(display_primary, pd.DataFrame()).empty:
            loaded = [m for m, df in frames.items() if not df.empty]
            if loaded:
                display_primary = min(loaded, key=lambda m: abs(m - primary))
                self._append_settings_status(
                    f"Selected {TIMEFRAME_LABEL_BY_MINUTE.get(primary, str(primary) + 'm')} candles were unavailable; "
                    f"displaying nearest loaded {TIMEFRAME_LABEL_BY_MINUTE.get(display_primary, str(display_primary) + 'm')} candles."
                )
        self.chart.set_active_timeframe(display_primary)
        self.chart.draw(frames.get(display_primary, pd.DataFrame()), refs.get(display_primary, pd.DataFrame()), surface, self.settings.data)
        if surface is None:
            self._set_summary(
                "Market data is not loaded yet.\n"
                f"- Product: {self.settings.data.get('product_id', 'ETH-PERP')}\n"
                f"- Reference: {self.settings.data.get('reference_product_id', 'ETH-USD')}\n"
                f"- Detail: {self.last_market_error or 'No candle response yet.'}\n"
                "- Open Setup or Settings to adjust products, save credentials, or refresh again."
            )
            self._append_settings_status(f"Market refresh returned no usable candles. {self.last_market_error or ''}".strip())
            self._refresh_startup_checklist()
            return

        product_id = sanitize_product_id(self.settings.data.get("product_id"), default="ETH-PERP")
        price_tile = self.tiles.get("ETH-PERP Price")
        if price_tile is not None:
            try:
                price_tile.title_label.configure(text=f"{product_id} Price")
                price_tile.value.configure(text=human_money(surface.price))
            except Exception:
                pass
        tile_values = {
            "Dominant Signal": surface.dominant_signal.replace("_", " "),
            "Regime": surface.regime,
            "Confidence": human_pct(surface.confidence_level),
            "Phase": f"Θ_{surface.phase_position:03d}",
            "Color Encoding": surface.color_encoding,
            "Dominant Theory": surface.theory_packet.dominant_theory.replace("_", " "),
            "Suggested Action": f"{surface.suggested_action} · {human_money(surface.suggested_notional_usd)}",
            "Anomaly": surface.anomaly_status,
            "Composite Edge": f"{surface.signal_score:+.2f}",
            "Quantum Align": f"{surface.theory_packet.quantum_alignment:+.2f}",
            "Entropic Gain": human_pct(surface.theory_packet.entropic_gain),
        }
        for key, value in tile_values.items():
            self.tiles[key].value.configure(text=value)

        tf_lines = " | ".join(
            f"{tf.minutes}m t={tf.trend:+.2f} m={tf.momentum:+.2f} s={tf.structure:+.2f} v={tf.volatility:+.2f} e={tf.entropy:.2f}"
            for tf in surface.timeframes
        )
        memory_line = "none" if not surface.memory_hits else " | ".join(f"{m.signal}/{m.regime}/{m.theory}({m.score:+.2f})" for m in surface.memory_hits[:3])
        self._set_summary(
            f"Advanced {product_id} intelligence surface\n"
            f"- {surface.summary}\n"
            f"- Timeframe lattice: {tf_lines}\n"
            f"- Memory analogs: {memory_line}\n"
            f"- Action rail: {surface.suggested_action} up to {human_money(surface.suggested_notional_usd)} in paper mode."
        )
        self.autonomy_size_label.configure(text=human_money(surface.suggested_notional_usd))

        theory_lines = [f"{name}: {value:+.4f}" for name, value in surface.top_theories]
        theory_lines += [
            "",
            "Five newly added concept rails:",
            "- TERM_STRUCTURE_SHEAR",
            "- MAKER_ABSORPTION_EDGE",
            "- FUNDING_REFLEX_INVERSION",
            "- ENTROPIC_CARRY_HARVEST",
            "- REGIME_HANDOFF_ARBITRAGE",
        ]
        self.theory_box.delete("1.0", "end")
        self.theory_box.insert("1.0", "\n".join(theory_lines))

        self.sensor_box.delete("1.0", "end")
        self.sensor_box.insert("1.0", surface.sensor_packet.text if surface.sensor_packet else "Sensor unavailable.")

        self.memory_box.delete("1.0", "end")
        self.memory_box.insert("1.0", "\n".join(f"- {m.score:+.2f} · {m.signal} · {m.regime} · {m.theory}\n  {m.summary}" for m in surface.memory_hits) or "No similar in-session states surfaced yet.")

        self._append_settings_status(
            f"Market refresh ok. {product_id} price {surface.price:,.2f} | signal {surface.dominant_signal} | regime {surface.regime}"
        )
        self._refresh_startup_checklist()
        self._run_autonomy(surface)

    def _run_autonomy(self, surface: SurfaceState) -> None:
        if not self.settings.data.get("autonomy_enabled", True) or not self.settings.data.get("paper_trading_enabled", True):
            return
        now = time.time()
        cooldown = int(self.settings.data.get("autonomy_cooldown_seconds", 420))
        if self.autonomy_cooldown and now - self.autonomy_cooldown[-1] < cooldown:
            return
        if surface.confidence_level < safe_float(self.settings.data.get("autonomy_min_confidence", 0.70), 0.70):
            return
        if surface.suggested_action == "WAIT":
            return
        self.autonomy_cooldown.append(now)
        self.trade_log.insert("end", f"[AUTO PAPER] {surface.suggested_action} · size {human_money(surface.suggested_notional_usd)} · edge {surface.signal_score:+.2f} · theory {surface.theory_packet.dominant_theory}\n")
        self.trade_log.see("end")

    def ask_trade_ai(self, mode: str) -> None:
        prompts = {
            "market_read": "Produce a concise AI market read for the active product. Separate evidence, inference, contradiction, and no-trade reasons.",
            "risk_audit": "Audit the active market packet like a risk governor. Emphasize what can fail, candle/data weakness, liquidity, leverage risk, and reasons to stay flat.",
            "execution_plan": "Create a conditional execution plan for the active product: trigger, invalidation, scale logic, cancel conditions, and a no-trade checklist. Do not promise profit.",
        }
        user_text = prompts.get(mode, prompts["market_read"])
        product_id = sanitize_product_id(self.settings.data.get("product_id"), default="ETH-PERP")
        if hasattr(self, "trade_log"):
            self.trade_log.insert("end", f"\n[AI REQUEST] {product_id} · {mode.replace('_', ' ').title()}\n")
            self.trade_log.insert("end", "[AI STATUS] LiteRT generation started. The spinner below will move while chunks are being generated.\n")
            self.trade_log.see("end")
        self._start_llm_indicator(f"Trade AI · {mode.replace('_', ' ').title()}")
        threading.Thread(target=self._trade_ai_worker, args=(user_text,), daemon=True).start()

    def _trade_ai_worker(self, user_text: str) -> None:
        try:
            if self.surface is None:
                reply = "Market packet unavailable. Refresh market data first."
            elif self.gemma.available:
                prompt = self.prompt_architect.build_gemma4_prompt(self.surface, self.frames, self.reference_frames, user_text)
                reply = generate_text_litert_chunked(
                    model_path=self.gemma.model_path,
                    prompt=prompt,
                    temperature=float(self.settings.data.get("model_temperature", 0.20)),
                    top_p=float(self.settings.data.get("model_top_p", 0.90)),
                    max_tokens=clamp_litert_output_tokens(self.settings.data.get("model_max_tokens", 1024)),
                    status_callback=lambda idx, total, phase: self.after(0, lambda i=idx, t=total, p=phase: self._set_llm_chunk_status(f"chunk {i}/{t} {p}")),
                )
            else:
                reply = (
                    f"Gemma runtime unavailable at {self.gemma.model_path}. "
                    "Using local signal-lab fallback.\n\n"
                    f"{self._fallback_commentary(user_text)}"
                )
        except Exception as exc:
            reply = f"AI trading reply failed; using local signal-lab fallback.\n\n{self._fallback_commentary(user_text)}\n\nRuntime detail: {exc}"
        def append() -> None:
            if hasattr(self, "trade_log"):
                self.trade_log.insert("end", f"[AI REPLY]\n{reply}\n")
                self.trade_log.see("end")
            self._finish_llm_indicator("Trade AI complete")
        self.after(0, append)

    def ask_model(self) -> None:
        user_text = self.chat_input.get("1.0", "end").strip()
        if not user_text:
            return
        self.chat_input.delete("1.0", "end")
        self._append_chat("User", user_text)
        self._append_chat("System", "LiteRT generation started. Watch the animated status bar below; long replies are extended with continuation chunks.")
        self._start_llm_indicator("Oracle")
        threading.Thread(target=self._ask_model_worker, args=(user_text,), daemon=True).start()

    def _ask_model_worker(self, user_text: str) -> None:
        try:
            if self.surface is None:
                reply = "Market packet unavailable. Refresh market data first."
            elif self.gemma.available:
                prompt = self.prompt_architect.build_gemma4_prompt(self.surface, self.frames, self.reference_frames, user_text)
                reply = generate_text_litert_chunked(
                    model_path=self.gemma.model_path,
                    prompt=prompt,
                    temperature=float(self.settings.data.get("model_temperature", 0.20)),
                    top_p=float(self.settings.data.get("model_top_p", 0.90)),
                    max_tokens=clamp_litert_output_tokens(self.settings.data.get("model_max_tokens", 1024)),
                    status_callback=lambda idx, total, phase: self.after(0, lambda i=idx, t=total, p=phase: self._set_llm_chunk_status(f"chunk {i}/{t} {p}")),
                )
            else:
                reply = (
                    f"Gemma runtime unavailable at {self.gemma.model_path}. "
                    "Open Settings and use Download Model or point Model Path at an existing `.litertlm` file.\n\n"
                    f"{self._fallback_commentary(user_text)}"
                )
        except Exception as exc:
            reply = f"Gemma runtime unavailable; using local signal lab.\n\n{self._fallback_commentary(user_text)}\n\nRuntime detail: {exc}"
        self.after(0, lambda: (self._append_chat("Oracle", reply), self._finish_llm_indicator("Oracle complete")))

    def _fallback_commentary(self, user_text: str) -> str:
        if self.surface is None:
            return "I do not have enough live ETH-PERP data yet to answer well."
        tf_map = "\n".join(
            f"- {tf.minutes}m: trend {tf.trend:+.2f}, momentum {tf.momentum:+.2f}, structure {tf.structure:+.2f}, vol {tf.volatility:+.2f}, entropy {tf.entropy:.2f}"
            for tf in self.surface.timeframes
        )
        return (
            f"Derivatives signal-lab response for: {user_text}\n"
            f"- Bias: {self.surface.dominant_signal}\n"
            f"- Regime: {self.surface.regime}\n"
            f"- Composite edge: {self.surface.signal_score:+.2f}\n"
            f"- Dominant theory: {self.surface.theory_packet.dominant_theory}\n"
            f"- Confidence: {self.surface.confidence_level*100:.1f}%\n"
            f"- Suggested action: {self.surface.suggested_action} at about {human_money(self.surface.suggested_notional_usd)}\n"
            f"- Sensor: {self.surface.sensor_packet.text if self.surface.sensor_packet else 'unavailable'}\n"
            f"Timeframe lattice:\n{tf_map}\n"
            "- This is a research summary, not financial advice."
        )

    def run_live_vision_analysis(self) -> None:
        if self.surface is None:
            messagebox.showwarning("No packet", "Refresh market data first.")
            return
        prompt = self.vision_prompt.get("1.0", "end").strip() or "Analyze the chart image."
        snapshot = self.chart.export_snapshot()
        self._start_llm_indicator("Vision LLM")
        threading.Thread(target=self._vision_worker, args=(snapshot, prompt), daemon=True).start()

    def run_selected_vision_analysis(self) -> None:
        if not self.vision_image_path:
            messagebox.showwarning("No image", "Choose an image first.")
            return
        prompt = self.vision_prompt.get("1.0", "end").strip() or "Analyze the chart image."
        self._start_llm_indicator("Vision LLM")
        threading.Thread(target=self._vision_worker, args=(self.vision_image_path, prompt), daemon=True).start()

    def _vision_worker(self, image_path: str, prompt: str) -> None:
        if self.surface is None:
            self.after(0, lambda: (self.vision_output.insert("end", "\nNo market packet available.\n"), self._finish_llm_indicator("Vision stopped")))
            return
        try:
            if self.gemma.available and self.settings.data.get("use_visual_llm", True):
                system_text, user_text = self.prompt_architect.build_vision_request(self.surface, self.frames, self.reference_frames, prompt)
                reply = self.gemma.generate_multimodal(
                    system_text=system_text,
                    user_text=user_text,
                    image_path=image_path,
                    temperature=float(self.settings.data.get("model_temperature", 0.20)),
                    top_p=float(self.settings.data.get("model_top_p", 0.90)),
                    max_tokens=min(700, clamp_litert_output_tokens(self.settings.data.get("model_max_tokens", 768))),
                )
            else:
                reply = (
                    f"Gemma runtime unavailable at {self.gemma.model_path}. "
                    "Vision fell back to packet-only analysis.\n\n"
                    f"{self._fallback_commentary(prompt)}"
                )
        except Exception as exc:
            reply = f"Visual LLM path failed.\nImage: {image_path}\nDetail: {exc}\n\nFallback packet analysis:\n{self._fallback_commentary(prompt)}"
        self.after(0, lambda: (self.vision_output.insert("end", f"\n{reply}\n"), self._finish_llm_indicator("Vision complete")))

    def seed_trade_from_surface(self) -> None:
        if self.surface is None:
            self.trade_log.insert("end", "No live surface available to seed a trade.\n")
            return
        self.order_side.set("BUY" if self.surface.dominant_signal == "LONG_BIAS" else "SELL" if self.surface.dominant_signal == "SHORT_BIAS" else "BUY")
        self.order_quote_size.delete(0, "end")
        self.order_quote_size.insert(0, f"{max(1.0, self.surface.suggested_notional_usd):.2f}")
        self.trade_log.insert("end", f"Seeded controls from signal: {self.surface.suggested_action}\n")

    def preview_order(self) -> None:
        if not self.ensure_secrets_unlocked():
            self.trade_log.insert("end", "Preview skipped: the Coinbase vault is still sealed.\n")
            return
        quote_size = self.order_quote_size.get().strip() or "25.00"
        side = self.order_side.get().strip().upper()
        leverage = self.order_leverage.get().strip()
        margin_type = self.margin_type_combo.get().strip()
        product_id = self._extract_product_id_from_widget(getattr(self, "trade_product_combo", None), self.settings.data.get("product_id", "ETH-PERP"))
        self.settings.data["product_id"] = product_id
        self.settings.save()
        self._sync_market_dropdown_values()

        def worker():
            try:
                preview = self.coinbase.preview_market_order(side, quote_size, product_id, leverage or None, margin_type or None)
                self.last_preview_id = preview.get("preview_id") or preview.get("order_preview_id")
                self.after(0, lambda: self.trade_log.insert("end", f"[PREVIEW {side} ${quote_size}]\n{compact_json(preview, 4200)}\n"))
            except Exception as exc:
                self.after(0, lambda: self.trade_log.insert("end", f"[PREVIEW ERROR] {exc}\n"))

        threading.Thread(target=worker, daemon=True).start()

    def place_order(self) -> None:
        if not self.settings.data.get("live_trading_enabled", False):
            self.trade_log.insert("end", "Live order blocked: enable live trading in Settings first.\n")
            return
        if not self.ensure_secrets_unlocked():
            self.trade_log.insert("end", "Live order blocked: the Coinbase vault is still sealed.\n")
            return
        if not messagebox.askyesno("Confirm live order", "Submit a live market order through Coinbase Advanced Trade?"):
            return

        quote_size = self.order_quote_size.get().strip() or "25.00"
        side = self.order_side.get().strip().upper()
        leverage = self.order_leverage.get().strip()
        margin_type = self.margin_type_combo.get().strip()
        product_id = self._extract_product_id_from_widget(getattr(self, "trade_product_combo", None), self.settings.data.get("product_id", "ETH-PERP"))
        self.settings.data["product_id"] = product_id
        self.settings.save()
        self._sync_market_dropdown_values()
        preview_id = self.last_preview_id

        def worker():
            try:
                response = self.coinbase.place_market_order(side, quote_size, product_id, leverage or None, margin_type or None, preview_id)
                self.after(0, lambda: self.trade_log.insert("end", f"[LIVE ORDER {side} ${quote_size}]\n{compact_json(response, 4200)}\n"))
            except Exception as exc:
                self.after(0, lambda: self.trade_log.insert("end", f"[LIVE ORDER ERROR] {exc}\n"))

        threading.Thread(target=worker, daemon=True).start()



def main() -> None:
    app = App()
    app.mainloop()


def _maybe_run_litert_worker_from_argv() -> bool:
    """Run the hidden LiteRT child worker instead of launching the Tk app.

    The GUI calls this same Python file in a subprocess for local model
    generation. Without this argv gate, the child process executes main() and
    opens a second copy of the full app every time a prompt is sent, which
    looks like the app rebooting.
    """
    try:
        if len(sys.argv) >= 4 and sys.argv[1] == LITERT_WORKER_FLAG:
            raise SystemExit(_run_litert_text_worker_cli(sys.argv[2], sys.argv[3]))
    except SystemExit:
        raise
    except Exception as exc:
        try:
            if len(sys.argv) >= 4:
                Path(sys.argv[3]).write_text(json.dumps({"ok": False, "error": str(exc)}), encoding="utf-8")
        except Exception:
            pass
        raise SystemExit(1)
    return False


if __name__ == "__main__":
    _maybe_run_litert_worker_from_argv()
    main()
