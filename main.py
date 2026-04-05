from __future__ import annotations

import hashlib
import hmac
import json
import math
import multiprocessing as mp
import os
import queue
import random
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
    TK_IMPORT_ERROR = None
except Exception as exc:
    tk = None
    filedialog = None
    messagebox = None
    TK_IMPORT_ERROR = exc

try:
    import customtkinter as ctk
    CTK_IMPORT_ERROR = None
except Exception as exc:
    ctk = None
    CTK_IMPORT_ERROR = exc

try:
    import litert_lm
except Exception:
    litert_lm = None

try:
    import psutil
except Exception:
    psutil = None

try:
    import pennylane as qml
    from pennylane import numpy as pnp
except Exception:
    qml = None
    pnp = None


MODEL_REPO = "https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm/resolve/main/"
MODEL_FILE = "gemma-4-E2B-it.litertlm"
EXPECTED_HASH = "ab7838cdfc8f77e54d8ca45eadceb20452d9f01e4bfade03e5dce27911b27e42"

MODELS_DIR = Path("models")
MODEL_PATH = MODELS_DIR / MODEL_FILE
ENCRYPTED_MODEL = MODEL_PATH.with_suffix(MODEL_PATH.suffix + ".aes")
LEGACY_RUNTIME_MODEL_PATH = MODELS_DIR / (MODEL_FILE + ".runtime")
RUNTIME_MODEL_PATH = MODELS_DIR / f"runtime-{MODEL_FILE}"
DB_PATH = Path("chat_history.db.aes")
KEY_PATH = Path(".enc_key")
SETTINGS_PATH = Path("gui_settings.json")
CACHE_DIR = Path(".litert_lm_cache")
STREAM_MAGIC = b"HGGM2"

MODELS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

PALETTE = {
    "window": "#030604",
    "canvas": "#07110a",
    "panel": "#0b120d",
    "panel_alt": "#111a14",
    "card": "#0f1711",
    "card_soft": "#162119",
    "text": "#d6ffe1",
    "muted": "#86a892",
    "line": "#254130",
    "accent_orange": "#39ff88",
    "accent_gold": "#94ffb8",
    "accent_teal": "#00d46a",
    "accent_blue": "#1ecf7a",
    "accent_pink": "#68ff9a",
    "accent_indigo": "#4f6b57",
    "danger": "#ff5470",
    "ok": "#5cff9d",
}

TIE_DYE = [
    ("#39ff88", "#2de06f"),
    ("#00d46a", "#00b85c"),
    ("#1ecf7a", "#14b066"),
    ("#68ff9a", "#4be783"),
    ("#2b3830", "#1e2a22"),
    ("#94ffb8", "#75e69a"),
]

DEFAULT_SETTINGS = {
    "include_system_entropy": True,
    "delete_plaintext_after_encrypt": True,
    "chat_memory_turns": 6,
}

GUI_READY = tk is not None and ctk is not None
DialogBase = ctk.CTkToplevel if GUI_READY else object
AppBase = ctk.CTk if GUI_READY else object


def human_size(num_bytes: int) -> str:
    size = float(max(0, num_bytes))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024.0
    return f"{int(num_bytes)}B"


def safe_cleanup(paths: List[Path]) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass


def _temp_path(directory: Path, suffix: str, prefix: str = "humoid_") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=prefix, suffix=suffix, dir=directory, delete=False)
    handle.close()
    return Path(handle.name)


def _temp_db_path() -> Path:
    return _temp_path(DB_PATH.parent, ".db", prefix="history_")


def _temp_model_path() -> Path:
    return _temp_path(MODELS_DIR, ".litertlm", prefix="model_")


def _temp_encrypted_model_path() -> Path:
    return _temp_path(MODELS_DIR, ".litertlm.aes", prefix="vault_")


def _temp_encrypted_db_path() -> Path:
    return _temp_path(DB_PATH.parent, ".db.aes", prefix="vault_history_")


def _write_key_file(key_bytes: bytes) -> None:
    KEY_PATH.write_bytes(key_bytes)
    try:
        os.chmod(KEY_PATH, 0o600)
    except Exception:
        pass


def derive_key_from_passphrase(password: str, salt: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    if salt is None:
        salt = os.urandom(16)
    kdf_der = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200_000,
    )
    return salt, kdf_der.derive(password.encode("utf-8"))


def detect_key_mode() -> str:
    if not KEY_PATH.exists():
        return "missing"
    data = KEY_PATH.read_bytes()
    if len(data) >= 48:
        return "passphrase"
    if len(data) >= 32:
        return "legacy_raw"
    return "invalid"


def read_legacy_key() -> bytes:
    data = KEY_PATH.read_bytes()
    if len(data) < 32:
        raise ValueError("The existing key file is invalid.")
    return data[:32]


def unlock_key_with_passphrase(password: str) -> bytes:
    data = KEY_PATH.read_bytes()
    if len(data) < 48:
        raise ValueError("No passphrase-derived key is stored yet.")
    salt = data[:16]
    stored_key = data[16:48]
    _, derived_key = derive_key_from_passphrase(password, salt)
    if not hmac.compare_digest(stored_key, derived_key):
        raise ValueError("Incorrect password.")
    return derived_key


def create_passphrase_key(password: str) -> bytes:
    salt, key = derive_key_from_passphrase(password)
    _write_key_file(salt + key)
    return key


def aes_encrypt_bytes(data: bytes, key: bytes) -> bytes:
    aes = AESGCM(key)
    nonce = os.urandom(12)
    return nonce + aes.encrypt(nonce, data, None)


def aes_decrypt_bytes(data: bytes, key: bytes) -> bytes:
    aes = AESGCM(key)
    nonce, ciphertext = data[:12], data[12:]
    return aes.decrypt(nonce, ciphertext, None)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encrypt_file(src: Path, dest: Path, key: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()

    with src.open("rb") as in_handle, dest.open("wb") as out_handle:
        out_handle.write(STREAM_MAGIC)
        out_handle.write(nonce)
        for chunk in iter(lambda: in_handle.read(1024 * 1024), b""):
            out_handle.write(encryptor.update(chunk))
        out_handle.write(encryptor.finalize())
        out_handle.write(encryptor.tag)


def _decrypt_stream_file(src: Path, dest: Path, key: bytes) -> None:
    header_size = len(STREAM_MAGIC) + 12
    tag_size = 16
    total_size = src.stat().st_size
    if total_size < header_size + tag_size:
        raise ValueError(f"{src} is not a valid encrypted model file.")

    with src.open("rb") as in_handle:
        magic = in_handle.read(len(STREAM_MAGIC))
        if magic != STREAM_MAGIC:
            raise ValueError(f"{src} has an unknown encrypted file format.")
        nonce = in_handle.read(12)
        in_handle.seek(total_size - tag_size)
        tag = in_handle.read(tag_size)
        in_handle.seek(header_size)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        remaining = total_size - header_size - tag_size

        with dest.open("wb") as out_handle:
            while remaining > 0:
                chunk = in_handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                out_handle.write(decryptor.update(chunk))
            out_handle.write(decryptor.finalize())


def decrypt_file(src: Path, dest: Path, key: bytes) -> None:
    with src.open("rb") as handle:
        header = handle.read(len(STREAM_MAGIC))
    if header == STREAM_MAGIC:
        _decrypt_stream_file(src, dest, key)
        return

    plaintext = aes_decrypt_bytes(src.read_bytes(), key)
    dest.write_bytes(plaintext)


def download_model_httpx(
    url: str,
    dest: Path,
    *,
    expected_sha: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()

    with httpx.stream("GET", url, follow_redirects=True, timeout=None) as response:
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
        safe_cleanup([dest])
        raise ValueError(f"SHA256 mismatch. Expected {expected_sha}, got {sha}.")
    return sha


def _initialize_plaintext_db(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, prompt TEXT, response TEXT)"
        )
        db.commit()


@contextmanager
def unlocked_db_path(key: bytes):
    temp_db = _temp_db_path()
    try:
        if DB_PATH.exists():
            decrypt_file(DB_PATH, temp_db, key)
        else:
            _initialize_plaintext_db(temp_db)
        yield temp_db
        encrypt_file(temp_db, DB_PATH, key)
    finally:
        safe_cleanup([temp_db])


def init_db(key: bytes) -> None:
    if DB_PATH.exists():
        return
    with unlocked_db_path(key):
        return


def log_interaction(prompt: str, response: str, key: bytes) -> None:
    with unlocked_db_path(key) as temp_db:
        with sqlite3.connect(temp_db) as db:
            db.execute(
                "INSERT INTO history (timestamp, prompt, response) VALUES (?, ?, ?)",
                (time.strftime("%Y-%m-%d %H:%M:%S"), prompt, response),
            )
            db.commit()


def fetch_history(key: bytes, limit: int = 12, offset: int = 0, search: Optional[str] = None) -> List[Tuple[int, str, str, str]]:
    rows: List[Tuple[int, str, str, str]] = []
    with unlocked_db_path(key) as temp_db:
        with sqlite3.connect(temp_db) as db:
            if search:
                query = f"%{search}%"
                cursor = db.execute(
                    "SELECT id, timestamp, prompt, response FROM history "
                    "WHERE prompt LIKE ? OR response LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (query, query, limit, offset),
                )
            else:
                cursor = db.execute(
                    "SELECT id, timestamp, prompt, response FROM history ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            rows.extend(cursor.fetchall())
    return rows


def count_history_rows(key: bytes) -> int:
    with unlocked_db_path(key) as temp_db:
        with sqlite3.connect(temp_db) as db:
            row = db.execute("SELECT COUNT(*) FROM history").fetchone()
            return int(row[0] if row else 0)


def require_litert_lm() -> None:
    if litert_lm is None:
        raise RuntimeError(
            "LiteRT-LM is not installed. Install the project dependencies first so the local model runtime is available."
        )


def load_litert_engine(model_path: Path):
    require_litert_lm()
    try:
        litert_lm.set_min_log_severity(litert_lm.LogSeverity.ERROR)
    except Exception:
        pass
    return litert_lm.Engine(
        str(model_path),
        backend=litert_lm.Backend.CPU,
        cache_dir=str(CACHE_DIR),
    )


def create_default_messages(system_text: Optional[str] = None) -> List[dict]:
    if not system_text:
        return []
    return [
        {
            "role": "system",
            "content": [{"type": "text", "text": system_text}],
        }
    ]


def response_to_text(response: dict) -> str:
    if not isinstance(response, dict):
        return str(response).strip()
    parts = response.get("content", [])
    texts: List[str] = []
    for item in parts:
        if isinstance(item, dict) and item.get("type") == "text":
            texts.append(item.get("text", ""))
    return "".join(texts).strip()


def litert_chat_blocking(model_path: Path, user_text: str, *, system_text: Optional[str] = None) -> str:
    engine = load_litert_engine(model_path)
    with engine:
        messages = create_default_messages(system_text)
        with engine.create_conversation(messages=messages) as conversation:
            return response_to_text(conversation.send_message(user_text))


def collect_system_metrics() -> Dict[str, float]:
    if psutil is None:
        raise RuntimeError("psutil is required for system metrics.")

    cpu = psutil.cpu_percent(interval=0.1) / 100.0
    mem = psutil.virtual_memory().percent / 100.0
    try:
        load_raw = os.getloadavg()[0]
        cpu_count = psutil.cpu_count(logical=True) or 1
        load1 = max(0.0, min(1.0, load_raw / max(1.0, float(cpu_count))))
    except Exception:
        load1 = cpu
    try:
        temp_groups = psutil.sensors_temperatures()
        if temp_groups:
            first_group = next(iter(temp_groups.values()))
            first_value = first_group[0].current
            temp = max(0.0, min(1.0, (first_value - 20.0) / 70.0))
        else:
            temp = 0.0
    except Exception:
        temp = 0.0
    return {"cpu": cpu, "mem": mem, "load1": load1, "temp": temp}


def metrics_to_rgb(metrics: dict) -> Tuple[float, float, float]:
    cpu = metrics.get("cpu", 0.1)
    mem = metrics.get("mem", 0.1)
    temp = metrics.get("temp", 0.1)
    load1 = metrics.get("load1", 0.0)
    r = cpu * (1.0 + load1)
    g = mem * (1.0 + load1 * 0.5)
    b = temp * (0.5 + cpu * 0.5)
    top = max(r, g, b, 1.0)
    return (
        float(max(0.0, min(1.0, r / top))),
        float(max(0.0, min(1.0, g / top))),
        float(max(0.0, min(1.0, b / top))),
    )


def pennylane_entropic_score(rgb: Tuple[float, float, float], shots: int = 256) -> float:
    if qml is None or pnp is None:
        r, g, b = rgb
        ri = max(0, min(255, int(r * 255)))
        gi = max(0, min(255, int(g * 255)))
        bi = max(0, min(255, int(b * 255)))
        seed = (ri << 16) | (gi << 8) | bi
        random.seed(seed)
        base = 0.3 * r + 0.4 * g + 0.3 * b
        noise = (random.random() - 0.5) * 0.08
        return max(0.0, min(1.0, base + noise))

    device = qml.device("default.qubit", wires=2, shots=shots)

    @qml.qnode(device)
    def circuit(a: float, b: float, c: float):
        qml.RX(a * math.pi, wires=0)
        qml.RY(b * math.pi, wires=1)
        qml.CNOT(wires=[0, 1])
        qml.RZ(c * math.pi, wires=1)
        qml.RX((a + b) * math.pi / 2, wires=0)
        qml.RY((b + c) * math.pi / 2, wires=1)
        return qml.expval(qml.PauliZ(0)), qml.expval(qml.PauliZ(1))

    a, b, c = map(float, rgb)
    try:
        ev0, ev1 = circuit(a, b, c)
        combined = ((ev0 + 1.0) / 2.0 * 0.6) + ((ev1 + 1.0) / 2.0 * 0.4)
        score = 1.0 / (1.0 + math.exp(-6.0 * (combined - 0.5)))
        return float(max(0.0, min(1.0, score)))
    except Exception:
        return float((a + b + c) / 6.0)


def entropic_summary_text(score: float) -> str:
    if score >= 0.75:
        level = "high"
    elif score >= 0.45:
        level = "medium"
    else:
        level = "low"
    return f"entropic_score={score:.3f} (level={level})"


def build_road_scanner_prompt(data: dict, include_system_entropy: bool = True) -> Tuple[str, str]:
    entropy_text = "entropic_score=unknown"
    if include_system_entropy:
        try:
            metrics = collect_system_metrics()
            rgb = metrics_to_rgb(metrics)
            score = pennylane_entropic_score(rgb)
            entropy_text = entropic_summary_text(score)
            metrics_line = "sys_metrics: cpu={cpu:.2f},mem={mem:.2f},load={load1:.2f},temp={temp:.2f}".format(
                cpu=metrics.get("cpu", 0.0),
                mem=metrics.get("mem", 0.0),
                load1=metrics.get("load1", 0.0),
                temp=metrics.get("temp", 0.0),
            )
        except Exception:
            metrics_line = "sys_metrics: unavailable"
    else:
        metrics_line = "sys_metrics: disabled"

    system_text = "You are a precise road risk classifier. Return only one word: Low, Medium, or High."
    user_text = (
        "Analyze the driving scene and return exactly one word: Low, Medium, or High.\n\n"
        f"Location: {data.get('location', '')}\n"
        f"Road type: {data.get('road_type', '')}\n"
        f"Weather: {data.get('weather', '')}\n"
        f"Traffic: {data.get('traffic', '')}\n"
        f"Obstacles: {data.get('obstacles', '')}\n"
        f"Sensor notes: {data.get('sensor_notes', '')}\n"
        f"{metrics_line}\n"
        f"Quantum State: {entropy_text}\n\n"
        "Rules:\n"
        "- Evaluate visibility, traction, traffic, and obstacles.\n"
        "- If sensor integrity seems unreliable, lean conservative.\n"
        "- Return exactly one word.\n"
        "- Valid outputs: Low, Medium, High.\n"
    )
    return system_text, user_text


def normalize_risk_label(text: str) -> str:
    pieces = (text or "").strip().split()
    label = pieces[0].capitalize() if pieces else ""
    if label in ("Low", "Medium", "High"):
        return label
    lowered = (text or "").lower()
    if "low" in lowered:
        return "Low"
    if "medium" in lowered:
        return "Medium"
    if "high" in lowered:
        return "High"
    return "Medium"


def load_settings() -> Dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_SETTINGS)
    settings = dict(DEFAULT_SETTINGS)
    settings.update({k: raw.get(k, v) for k, v in DEFAULT_SETTINGS.items()})
    return settings


def save_settings(settings: Dict[str, Any]) -> None:
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _status_report(reporter: Optional[Callable[[str, Any], None]], kind: str, payload: Any) -> None:
    if reporter:
        reporter(kind, payload)


@contextmanager
def unlocked_model_path(key: bytes):
    safe_cleanup([LEGACY_RUNTIME_MODEL_PATH])
    if ENCRYPTED_MODEL.exists():
        decrypt_file(ENCRYPTED_MODEL, RUNTIME_MODEL_PATH, key)
        try:
            yield RUNTIME_MODEL_PATH
        finally:
            if RUNTIME_MODEL_PATH.exists():
                encrypt_file(RUNTIME_MODEL_PATH, ENCRYPTED_MODEL, key)
                safe_cleanup([RUNTIME_MODEL_PATH])
        return

    if MODEL_PATH.exists():
        yield MODEL_PATH
        return

    raise FileNotFoundError("No model is available yet. Download and encrypt it from the Model Lab tab first.")


def build_chat_prompt(user_text: str, memory: List[Tuple[str, str]], turns: int = 6) -> str:
    recent = memory[-max(0, turns * 2) :]
    if not recent:
        return user_text

    lines = ["Conversation so far:"]
    for role, message in recent:
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {message}")
    lines.append("")
    lines.append(f"User: {user_text}")
    lines.append("Assistant:")
    return "\n".join(lines)


def run_chat_request(key: bytes, prompt: str, memory: List[Tuple[str, str]], memory_turns: int) -> str:
    init_db(key)
    compiled_prompt = build_chat_prompt(prompt, memory, turns=memory_turns)
    with unlocked_model_path(key) as model_path:
        reply = litert_chat_blocking(
            model_path,
            compiled_prompt,
            system_text="You are a warm, concise, helpful assistant running fully on the local machine.",
        )
    log_interaction(prompt, reply, key)
    return reply


def run_road_scan(key: bytes, data: Dict[str, str], include_system_entropy: bool) -> Dict[str, str]:
    init_db(key)
    system_text, prompt = build_road_scanner_prompt(data, include_system_entropy=include_system_entropy)
    with unlocked_model_path(key) as model_path:
        raw = litert_chat_blocking(model_path, prompt, system_text=system_text)
    label = normalize_risk_label(raw)
    log_interaction("ROAD_SCANNER_PROMPT:\n" + prompt, "ROAD_SCANNER_RESULT:\n" + label, key)
    return {
        "label": label,
        "raw": raw,
        "prompt": prompt,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def download_and_encrypt_model(key: bytes, reporter: Optional[Callable[[str, Any], None]] = None) -> str:
    plain_temp = _temp_model_path()
    encrypted_temp = _temp_encrypted_model_path()
    try:
        _status_report(reporter, "status", "Downloading the Gemma model into a temporary vault...")

        def progress(done: int, total: int) -> None:
            if total:
                _status_report(reporter, "progress", done / total)
            _status_report(
                reporter,
                "status",
                f"Downloading model... {human_size(done)} of {human_size(total) if total else 'unknown'}",
            )

        sha = download_model_httpx(MODEL_REPO + MODEL_FILE, plain_temp, expected_sha=EXPECTED_HASH, progress_callback=progress)
        _status_report(reporter, "status", "Encrypting the verified model for safe local storage...")
        encrypt_file(plain_temp, encrypted_temp, key)
        encrypted_temp.replace(ENCRYPTED_MODEL)
        safe_cleanup([MODEL_PATH])
        _status_report(reporter, "status", "Model ready. The encrypted vault is sealed again.")
        return sha
    finally:
        safe_cleanup([plain_temp, encrypted_temp])


def encrypt_existing_plaintext_model(key: bytes, delete_plaintext: bool = True) -> None:
    if not MODEL_PATH.exists():
        raise FileNotFoundError("There is no plaintext model copy to encrypt.")
    temp_encrypted = _temp_encrypted_model_path()
    try:
        encrypt_file(MODEL_PATH, temp_encrypted, key)
        temp_encrypted.replace(ENCRYPTED_MODEL)
        if delete_plaintext:
            safe_cleanup([MODEL_PATH])
    finally:
        safe_cleanup([temp_encrypted])


def verify_model_hash(key: bytes) -> Tuple[str, bool]:
    with unlocked_model_path(key) as model_path:
        sha = sha256_file(model_path)
    return sha, sha.lower() == EXPECTED_HASH.lower()


def reencrypt_assets(old_key: bytes, new_key: bytes, reporter: Optional[Callable[[str, Any], None]] = None) -> None:
    temp_plain_model = _temp_model_path()
    temp_plain_db = _temp_db_path()
    temp_encrypted_model = _temp_encrypted_model_path()
    temp_encrypted_db = _temp_encrypted_db_path()

    try:
        if ENCRYPTED_MODEL.exists():
            _status_report(reporter, "status", "Unlocking the current model vault...")
            decrypt_file(ENCRYPTED_MODEL, temp_plain_model, old_key)
            _status_report(reporter, "status", "Sealing a fresh model vault with the new password...")
            encrypt_file(temp_plain_model, temp_encrypted_model, new_key)
        if DB_PATH.exists():
            _status_report(reporter, "status", "Unlocking encrypted history...")
            decrypt_file(DB_PATH, temp_plain_db, old_key)
            _status_report(reporter, "status", "Re-encrypting chat history with the new password...")
            encrypt_file(temp_plain_db, temp_encrypted_db, new_key)

        if temp_encrypted_model.exists():
            temp_encrypted_model.replace(ENCRYPTED_MODEL)
        if temp_encrypted_db.exists():
            temp_encrypted_db.replace(DB_PATH)
    finally:
        safe_cleanup([temp_plain_model, temp_plain_db, temp_encrypted_model, temp_encrypted_db])


def migrate_legacy_key_to_passphrase(password: str, reporter: Optional[Callable[[str, Any], None]] = None) -> bytes:
    old_key = read_legacy_key()
    salt, new_key = derive_key_from_passphrase(password)
    reencrypt_assets(old_key, new_key, reporter=reporter)
    _write_key_file(salt + new_key)
    return new_key


def rotate_to_new_passphrase(current_key: bytes, password: str, reporter: Optional[Callable[[str, Any], None]] = None) -> bytes:
    salt, new_key = derive_key_from_passphrase(password)
    reencrypt_assets(current_key, new_key, reporter=reporter)
    _write_key_file(salt + new_key)
    return new_key


def storage_summary(key: Optional[bytes]) -> Dict[str, str]:
    if ENCRYPTED_MODEL.exists() and MODEL_PATH.exists():
        model_state = "Encrypted vault plus plaintext copy"
    elif ENCRYPTED_MODEL.exists():
        model_state = "Encrypted vault ready"
    elif MODEL_PATH.exists():
        model_state = "Plaintext model present"
    else:
        model_state = "No model downloaded yet"

    encrypted_size = human_size(ENCRYPTED_MODEL.stat().st_size) if ENCRYPTED_MODEL.exists() else "0B"
    plaintext_size = human_size(MODEL_PATH.stat().st_size) if MODEL_PATH.exists() else "0B"

    if key is None:
        history_count = "Locked"
    else:
        try:
            history_count = str(count_history_rows(key))
        except Exception:
            history_count = "Unavailable"

    return {
        "model_state": model_state,
        "encrypted_size": encrypted_size,
        "plaintext_size": plaintext_size,
        "history_count": history_count,
        "key_mode": detect_key_mode(),
    }


PROCESS_TASKS = {
    "chat_request": run_chat_request,
    "road_scan": run_road_scan,
}


def process_task_runner(result_queue: Any, task_name: str, task_args: Tuple[Any, ...]) -> None:
    try:
        task_fn = PROCESS_TASKS[task_name]
        result = task_fn(*task_args)
    except Exception as exc:
        result_queue.put(("error", f"{exc}\n\n{traceback.format_exc()}"))
    else:
        result_queue.put(("success", result))


class StartupPasswordDialog(DialogBase):
    def __init__(self, app: "HumoidStudioApp"):
        if not GUI_READY:
            raise RuntimeError("The GUI dependencies are not available.")
        super().__init__(app)
        self.app = app
        self.mode = detect_key_mode()
        self.password_var = tk.StringVar()
        self.confirm_var = tk.StringVar()
        self.error_var = tk.StringVar(value="")

        self.title("Unlock Humoid Studio")
        self.geometry("560x520")
        self.minsize(560, 520)
        self.resizable(False, False)
        self.configure(fg_color=PALETTE["panel"])
        self.transient(app)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close_app)

        frame = ctk.CTkFrame(
            self,
            fg_color=PALETTE["card"],
            corner_radius=26,
            border_width=1,
            border_color=PALETTE["line"],
        )
        frame.pack(fill="both", expand=True, padx=24, pady=24)

        ctk.CTkLabel(
            frame,
            text="Humoid Gemma Studio",
            font=app.title_font,
            text_color=PALETTE["text"],
        ).pack(anchor="w", padx=28, pady=(24, 6))

        subtitle = self._subtitle_text()
        ctk.CTkLabel(
            frame,
            text=subtitle,
            font=app.body_font,
            text_color=PALETTE["muted"],
            justify="left",
            wraplength=430,
        ).pack(anchor="w", padx=28, pady=(0, 18))

        self.banner = ctk.CTkLabel(
            frame,
            text=self._banner_text(),
            font=app.small_font,
            text_color=PALETTE["text"],
            fg_color=PALETTE["card_soft"],
            corner_radius=14,
            padx=12,
            pady=10,
            justify="left",
            wraplength=430,
        )
        self.banner.pack(fill="x", padx=28, pady=(0, 18))

        password_label_text = "Create vault password" if self.mode in ("missing", "legacy_raw") else "Vault password"
        ctk.CTkLabel(
            frame,
            text=password_label_text,
            font=app.small_font,
            text_color=PALETTE["muted"],
        ).pack(anchor="w", padx=28, pady=(0, 6))

        self.password_entry = ctk.CTkEntry(
            frame,
            textvariable=self.password_var,
            show="*",
            placeholder_text="Password",
            height=46,
            corner_radius=16,
            border_color=PALETTE["line"],
            fg_color=PALETTE["panel_alt"],
            text_color=PALETTE["text"],
            font=app.body_font,
        )
        self.password_entry.pack(fill="x", padx=28)

        if self.mode in ("missing", "legacy_raw"):
            ctk.CTkLabel(
                frame,
                text="Confirm password",
                font=app.small_font,
                text_color=PALETTE["muted"],
            ).pack(anchor="w", padx=28, pady=(14, 6))

        self.confirm_entry = ctk.CTkEntry(
            frame,
            textvariable=self.confirm_var,
            show="*",
            placeholder_text="Confirm password",
            height=46,
            corner_radius=16,
            border_color=PALETTE["line"],
            fg_color=PALETTE["panel_alt"],
            text_color=PALETTE["text"],
            font=app.body_font,
        )
        if self.mode in ("missing", "legacy_raw"):
            self.confirm_entry.pack(fill="x", padx=28)

        ctk.CTkLabel(
            frame,
            textvariable=self.error_var,
            font=app.small_font,
            text_color=PALETTE["danger"],
            justify="left",
            wraplength=430,
        ).pack(anchor="w", padx=28, pady=(14, 0))

        footer = ctk.CTkFrame(frame, fg_color="transparent")
        footer.pack(fill="x", padx=28, pady=(24, 28))

        if self.mode == "legacy_raw":
            legacy_button = app.make_button(
                footer,
                "Use Legacy Key",
                self._use_legacy_key,
                5,
                width=150,
                height=44,
            )
            legacy_button.pack(side="left")

        primary_text = {
            "missing": "Create Vault Password",
            "passphrase": "Unlock Studio",
            "legacy_raw": "Migrate and Unlock",
            "invalid": "Inspect Key File",
        }.get(self.mode, "Unlock Studio")

        self.primary_button = app.make_button(
            footer,
            primary_text,
            self._submit,
            0,
            width=200,
            height=46,
        )
        self.primary_button.pack(side="right")

        self.password_entry.focus_set()

    def _subtitle_text(self) -> str:
        if self.mode == "missing":
            return "Set a password for the encrypted model vault before the app starts."
        if self.mode == "legacy_raw":
            return "A legacy raw key was found. Set a password now to migrate safely to a passphrase-first vault."
        if self.mode == "invalid":
            return "The key file does not look valid. You can fix or remove it, then launch again."
        return "Enter the vault password to unlock the encrypted model, history, and settings."

    def _banner_text(self) -> str:
        if self.mode == "missing":
            return "New setup: the GUI now opens with a password gate before any model or history data is touched."
        if self.mode == "legacy_raw":
            return "Recommended: migrate to a password-protected vault now. Existing encrypted data will be re-wrapped safely."
        if self.mode == "invalid":
            return "The current key file is too short to decrypt anything. Remove it only if you know the vault data is disposable."
        return "Passphrase mode detected. The window unlocks only after the stored key is derived from your password."

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.password_entry.configure(state=state)
        if self.mode in ("missing", "legacy_raw"):
            self.confirm_entry.configure(state=state)
        self.primary_button.configure(state=state)

    def _close_app(self) -> None:
        self.app.destroy()

    def _use_legacy_key(self) -> None:
        try:
            key = read_legacy_key()
        except Exception as exc:
            self.error_var.set(str(exc))
            return
        self.app.complete_unlock(key, "legacy_raw")
        self.destroy()

    def _submit(self) -> None:
        password = self.password_var.get().strip()
        confirm = self.confirm_var.get().strip()
        self.error_var.set("")

        if self.mode == "invalid":
            self.error_var.set("Remove or replace the invalid key file before launching the studio.")
            return
        if self.mode in ("missing", "legacy_raw") and len(password) < 8:
            self.error_var.set("Use at least 8 characters so the vault password has some strength.")
            return
        if self.mode in ("missing", "legacy_raw") and password != confirm:
            self.error_var.set("The confirmation password does not match.")
            return

        if self.mode == "passphrase":
            try:
                key = unlock_key_with_passphrase(password)
            except Exception as exc:
                self.error_var.set(str(exc))
                return
            self.app.complete_unlock(key, "passphrase")
            self.destroy()
            return

        if self.mode == "missing":
            key = create_passphrase_key(password)
            self.app.complete_unlock(key, "passphrase")
            self.destroy()
            return

        self._set_busy(True)

        def on_success(key: bytes) -> None:
            self.app.complete_unlock(key, "passphrase")
            self.destroy()

        def on_error(exc: Exception) -> None:
            self._set_busy(False)
            self.error_var.set(str(exc))

        self.app.run_task(
            "Migrating the legacy key to a password-protected vault...",
            lambda reporter: migrate_legacy_key_to_passphrase(password, reporter=reporter),
            on_success=on_success,
            on_error=on_error,
        )


class HumoidStudioApp(AppBase):
    def __init__(self):
        if not GUI_READY:
            raise RuntimeError("The GUI dependencies are not available.")
        super().__init__()
        self.settings_data = load_settings()
        self.key: Optional[bytes] = None
        self.key_mode = "locked"
        self.busy = False
        self.task_queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self.chat_memory: List[Tuple[str, str]] = []
        self.last_scan_result: Optional[Dict[str, str]] = None
        self.history_offset = 0
        self.active_process: Optional[mp.Process] = None

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        self.title_font = ctk.CTkFont(family="DejaVu Sans Mono", size=32, weight="bold")
        self.section_font = ctk.CTkFont(family="DejaVu Sans Mono", size=21, weight="bold")
        self.body_font = ctk.CTkFont(family="DejaVu Sans", size=14)
        self.small_font = ctk.CTkFont(family="DejaVu Sans Mono", size=12)
        self.metric_font = ctk.CTkFont(family="DejaVu Sans", size=28, weight="bold")

        self.status_var = tk.StringVar(value="Locked. Enter the vault password to begin.")
        self.key_status_var = tk.StringVar(value="Key: Locked")
        self.model_status_var = tk.StringVar(value="Model: Checking local vault...")
        self.hash_status_var = tk.StringVar(value="Hash: Not checked")
        self.dashboard_vault_var = tk.StringVar(value="Locked")
        self.dashboard_history_var = tk.StringVar(value="Locked")
        self.dashboard_plaintext_var = tk.StringVar(value="0B")
        self.history_search_var = tk.StringVar()
        self.settings_entropy_var = tk.BooleanVar(value=bool(self.settings_data.get("include_system_entropy", True)))
        self.settings_delete_plaintext_var = tk.BooleanVar(
            value=bool(self.settings_data.get("delete_plaintext_after_encrypt", True))
        )
        self.settings_memory_turns_var = tk.IntVar(value=int(self.settings_data.get("chat_memory_turns", 6)))
        self.change_current_password_var = tk.StringVar()
        self.change_new_password_var = tk.StringVar()
        self.change_confirm_password_var = tk.StringVar()

        self.title("Humoid Gemma Studio")
        self.geometry("1420x940")
        self.minsize(1260, 860)
        self.configure(fg_color=PALETTE["window"])
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.background = tk.Canvas(self, bg=PALETTE["canvas"], highlightthickness=0, bd=0)
        self.background.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.bind("<Configure>", self.draw_background)

        self.shell = ctk.CTkFrame(self, fg_color="transparent")
        self.shell.pack(fill="both", expand=True, padx=24, pady=24)

        self.action_widgets: List[Any] = []
        self.progress_mode = "indeterminate"

        self.build_layout()
        self.after(120, self.draw_background)
        self.after(150, self.open_startup_dialog)
        self.after(120, self.process_task_queue)

    def build_layout(self) -> None:
        self.shell.grid_columnconfigure(0, weight=1)
        self.shell.grid_rowconfigure(1, weight=1)

        self.hero = ctk.CTkFrame(
            self.shell,
            fg_color=PALETTE["panel"],
            corner_radius=28,
            border_width=1,
            border_color=PALETTE["line"],
        )
        self.hero.grid(row=0, column=0, sticky="nsew", pady=(0, 18))
        self.hero.grid_columnconfigure(0, weight=1)
        self.hero.grid_columnconfigure(1, weight=0)

        left = ctk.CTkFrame(self.hero, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=24, pady=20)

        ctk.CTkLabel(
            left,
            text="Humoid Gemma Studio",
            font=self.title_font,
            text_color=PALETTE["text"],
        ).pack(anchor="w")

        ctk.CTkLabel(
            left,
            text="A colorful local control room for your encrypted Gemma model, chat vault, and road scanner workflows.",
            font=self.body_font,
            text_color=PALETTE["muted"],
            wraplength=720,
            justify="left",
        ).pack(anchor="w", pady=(8, 18))

        chips = ctk.CTkFrame(left, fg_color="transparent")
        chips.pack(anchor="w")
        self.make_chip(chips, self.key_status_var, PALETTE["accent_orange"]).pack(side="left", padx=(0, 10))
        self.make_chip(chips, self.model_status_var, PALETTE["accent_teal"]).pack(side="left", padx=(0, 10))
        self.make_chip(chips, self.hash_status_var, PALETTE["accent_blue"]).pack(side="left")

        right = ctk.CTkFrame(self.hero, fg_color="transparent")
        right.grid(row=0, column=1, sticky="ne", padx=24, pady=20)

        self.progress_bar = ctk.CTkProgressBar(
            right,
            width=280,
            height=16,
            mode="indeterminate",
            fg_color="#17261c",
            progress_color=PALETTE["accent_orange"],
        )
        self.progress_bar.pack(anchor="e", pady=(6, 12))
        self.progress_bar.stop()
        self.progress_bar.set(0)

        ctk.CTkLabel(
            right,
            textvariable=self.status_var,
            font=self.small_font,
            text_color=PALETTE["muted"],
            justify="right",
            wraplength=280,
        ).pack(anchor="e")

        self.tabview = ctk.CTkTabview(
            self.shell,
            fg_color=PALETTE["panel"],
            corner_radius=26,
            border_width=1,
            border_color=PALETTE["line"],
            segmented_button_fg_color=PALETTE["panel_alt"],
            segmented_button_selected_color=PALETTE["accent_orange"],
            segmented_button_selected_hover_color="#22d868",
            segmented_button_unselected_color="#18231b",
            segmented_button_unselected_hover_color="#223128",
            text_color=PALETTE["text"],
            anchor="n",
        )
        self.tabview.grid(row=1, column=0, sticky="nsew")

        self.dashboard_tab = self.tabview.add("Dashboard")
        self.chat_tab = self.tabview.add("Chat")
        self.model_tab = self.tabview.add("Model Lab")
        self.road_tab = self.tabview.add("Road Scanner")
        self.history_tab = self.tabview.add("History")
        self.settings_tab = self.tabview.add("Settings")

        for tab in (
            self.dashboard_tab,
            self.chat_tab,
            self.model_tab,
            self.road_tab,
            self.history_tab,
            self.settings_tab,
        ):
            tab.grid_columnconfigure(0, weight=1)

        self.build_dashboard_tab()
        self.build_chat_tab()
        self.build_model_tab()
        self.build_road_tab()
        self.build_history_tab()
        self.build_settings_tab()
        self.set_action_state(False)

    def draw_background(self, _event: Optional[Any] = None) -> None:
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        self.background.delete("all")
        self.background.create_rectangle(0, 0, width, height, fill=PALETTE["canvas"], outline="")
        self.background.create_oval(-140, -120, width * 0.38, height * 0.42, fill="#0d2514", outline="")
        self.background.create_oval(width * 0.50, -110, width + 140, height * 0.36, fill="#12301c", outline="")
        self.background.create_oval(width * 0.16, height * 0.42, width * 0.88, height + 160, fill="#0a1a10", outline="")
        self.background.create_oval(width * 0.66, height * 0.30, width + 120, height + 90, fill="#143825", outline="")
        self.background.create_oval(-90, height * 0.58, width * 0.34, height + 120, fill="#0f2216", outline="")
        for y in range(0, height, 34):
            self.background.create_line(0, y, width, y, fill="#0d1d13")

    def make_chip(self, parent: Any, variable: tk.StringVar, color: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent,
            textvariable=variable,
            font=self.small_font,
            text_color=PALETTE["text"],
            fg_color=color,
            corner_radius=16,
            padx=12,
            pady=6,
        )

    def make_button(
        self,
        parent: Any,
        text: str,
        command: Callable[[], None],
        palette_index: int,
        *,
        width: int = 170,
        height: int = 44,
    ) -> ctk.CTkButton:
        fg_color, hover_color = TIE_DYE[palette_index % len(TIE_DYE)]
        button = ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=height,
            corner_radius=18,
            fg_color=fg_color,
            hover_color=hover_color,
            text_color="#041308",
            border_width=1,
            border_color="#7cf7aa",
            font=ctk.CTkFont(family="DejaVu Sans", size=13, weight="bold"),
        )
        return button

    def register_action(self, widget: Any) -> Any:
        self.action_widgets.append(widget)
        return widget

    def set_action_state(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in self.action_widgets:
            try:
                widget.configure(state=state)
            except Exception:
                pass

    def set_busy(self, busy: bool, label: Optional[str] = None) -> None:
        self.busy = busy
        self.set_action_state(not busy and self.key is not None)
        if label:
            self.status_var.set(label)
        if busy:
            self.progress_mode = "indeterminate"
            self.progress_bar.configure(mode="indeterminate")
            self.progress_bar.start()
        else:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
            self.progress_bar.set(0)

    def open_startup_dialog(self) -> None:
        dialog = StartupPasswordDialog(self)
        dialog.wait_visibility()
        dialog.focus_force()

    def complete_unlock(self, key: bytes, key_mode: str) -> None:
        self.key = key
        self.key_mode = key_mode
        self.key_status_var.set(f"Key: {'Passphrase' if key_mode == 'passphrase' else 'Legacy Raw'}")
        self.status_var.set("Studio unlocked. The vault is ready.")
        self.set_action_state(True)
        try:
            init_db(key)
        except Exception as exc:
            self.status_var.set(f"Unlocked, but the history vault could not be initialized: {exc}")
        self.refresh_dashboard()

    def ensure_unlocked(self) -> bool:
        if self.key is not None:
            return True
        self.status_var.set("The studio is locked. Unlock it first.")
        if messagebox:
            messagebox.showinfo("Vault Locked", "Unlock the studio before running model or history actions.")
        return False

    def run_task(
        self,
        label: str,
        task: Callable[[Callable[[str, Any], None]], Any],
        *,
        on_success: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        if self.busy:
            if messagebox:
                messagebox.showinfo("One task at a time", "A background job is already running.")
            return

        self.set_busy(True, label)

        def reporter(kind: str, payload: Any) -> None:
            self.task_queue.put(("report", kind, payload))

        def worker() -> None:
            try:
                result = task(reporter)
            except Exception as exc:
                self.task_queue.put(("error", exc, on_error))
                return
            self.task_queue.put(("success", result, on_success))

        threading.Thread(target=worker, daemon=True).start()

    def run_process_task(
        self,
        label: str,
        task_name: str,
        task_args: Tuple[Any, ...],
        *,
        on_success: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        if self.busy:
            if messagebox:
                messagebox.showinfo("One task at a time", "A background job is already running.")
            return

        self.set_busy(True, label)
        ctx = mp.get_context("spawn")
        result_queue = ctx.Queue()
        process = ctx.Process(target=process_task_runner, args=(result_queue, task_name, task_args), daemon=True)
        process.start()
        self.active_process = process

        def watcher() -> None:
            try:
                while True:
                    try:
                        kind, payload = result_queue.get(timeout=0.2)
                        break
                    except queue.Empty:
                        if not process.is_alive():
                            exit_code = process.exitcode
                            self.task_queue.put(
                                (
                                    "error",
                                    RuntimeError(f"Background process exited unexpectedly with code {exit_code}."),
                                    on_error,
                                )
                            )
                            return
            finally:
                process.join(timeout=0.5)
                result_queue.close()
                self.active_process = None

            if kind == "success":
                self.task_queue.put(("success", payload, on_success))
            else:
                self.task_queue.put(("error", RuntimeError(str(payload)), on_error))

        threading.Thread(target=watcher, daemon=True).start()

    def process_task_queue(self) -> None:
        try:
            while True:
                event = self.task_queue.get_nowait()
                kind = event[0]

                if kind == "report":
                    _, report_kind, payload = event
                    if report_kind == "status":
                        self.status_var.set(str(payload))
                    elif report_kind == "progress":
                        if self.progress_mode != "determinate":
                            self.progress_mode = "determinate"
                            self.progress_bar.stop()
                            self.progress_bar.configure(mode="determinate")
                        self.progress_bar.set(float(payload))

                elif kind == "success":
                    _, result, callback = event
                    self.set_busy(False)
                    self.refresh_dashboard()
                    if callback:
                        callback(result)
                    elif self.status_var.get() == "":
                        self.status_var.set("Task completed.")

                elif kind == "error":
                    _, exc, callback = event
                    self.set_busy(False)
                    self.refresh_dashboard()
                    if callback:
                        callback(exc)
                    else:
                        self.status_var.set(str(exc))
                        if messagebox:
                            messagebox.showerror("Task failed", str(exc))
        except Exception:
            pass

        self.after(120, self.process_task_queue)

    def build_dashboard_tab(self) -> None:
        tab = self.dashboard_tab
        tab.grid_columnconfigure((0, 1, 2), weight=1)

        top_card = ctk.CTkFrame(
            tab,
            fg_color=PALETTE["card"],
            corner_radius=24,
            border_width=1,
            border_color=PALETTE["line"],
        )
        top_card.grid(row=0, column=0, columnspan=3, sticky="ew", padx=20, pady=(20, 16))
        top_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            top_card,
            text="Control Deck",
            font=self.section_font,
            text_color=PALETTE["text"],
        ).grid(row=0, column=0, sticky="w", padx=22, pady=(18, 6))

        ctk.CTkLabel(
            top_card,
            text="Fast launch actions for the secure parts of the app.",
            font=self.body_font,
            text_color=PALETTE["muted"],
        ).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 18))

        buttons = ctk.CTkFrame(top_card, fg_color="transparent")
        buttons.grid(row=0, column=1, rowspan=2, sticky="e", padx=22, pady=18)

        self.register_action(self.make_button(buttons, "Model Lab", lambda: self.tabview.set("Model Lab"), 0)).pack(
            side="left", padx=(0, 10)
        )
        self.register_action(self.make_button(buttons, "Chat", lambda: self.tabview.set("Chat"), 2)).pack(
            side="left", padx=(0, 10)
        )
        self.register_action(self.make_button(buttons, "Road Scanner", lambda: self.tabview.set("Road Scanner"), 4)).pack(
            side="left"
        )

        self.metric_cards: Dict[str, tk.StringVar] = {}
        cards = [
            ("Vault State", self.dashboard_vault_var, PALETTE["accent_orange"]),
            ("History Entries", self.dashboard_history_var, PALETTE["accent_teal"]),
            ("Plaintext Copy", self.dashboard_plaintext_var, PALETTE["accent_blue"]),
        ]
        for idx, (title, variable, accent) in enumerate(cards):
            card = ctk.CTkFrame(
                tab,
                fg_color=PALETTE["card"],
                corner_radius=22,
                border_width=1,
                border_color=accent,
            )
            card.grid(row=1, column=idx, sticky="nsew", padx=(20 if idx == 0 else 10, 20 if idx == 2 else 10), pady=(0, 16))
            ctk.CTkLabel(card, text=title, font=self.small_font, text_color=PALETTE["muted"]).pack(anchor="w", padx=20, pady=(16, 10))
            ctk.CTkLabel(card, textvariable=variable, font=self.metric_font, text_color=PALETTE["text"]).pack(
                anchor="w", padx=20, pady=(0, 18)
            )

        mood = ctk.CTkFrame(
            tab,
            fg_color=PALETTE["card_soft"],
            corner_radius=22,
            border_width=1,
            border_color=PALETTE["line"],
        )
        mood.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=20, pady=(0, 20))

        ctk.CTkLabel(mood, text="Studio Mood", font=self.section_font, text_color=PALETTE["text"]).pack(
            anchor="w", padx=22, pady=(18, 8)
        )
        ctk.CTkLabel(
            mood,
            text=(
                "Tie-dye bright buttons, a warm glassy background, and a passphrase-first startup flow. "
                "Heavy work stays off the UI thread so the interface keeps breathing while downloads, "
                "hash checks, and inference jobs run."
            ),
            font=self.body_font,
            text_color=PALETTE["muted"],
            justify="left",
            wraplength=1180,
        ).pack(anchor="w", padx=22, pady=(0, 20))

    def build_chat_tab(self) -> None:
        tab = self.chat_tab
        tab.grid_columnconfigure(0, weight=4)
        tab.grid_columnconfigure(1, weight=2)
        tab.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(tab, fg_color=PALETTE["card"], corner_radius=24, border_width=1, border_color=PALETTE["line"])
        left.grid(row=0, column=0, sticky="nsew", padx=(20, 10), pady=20)
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="Chat", font=self.section_font, text_color=PALETTE["text"]).grid(
            row=0, column=0, sticky="w", padx=20, pady=(18, 6)
        )
        ctk.CTkLabel(
            left,
            text="Each prompt runs locally and re-seals the model vault when the response finishes.",
            font=self.body_font,
            text_color=PALETTE["muted"],
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 12))

        self.chat_output = ctk.CTkTextbox(
            left,
            fg_color=PALETTE["panel_alt"],
            text_color=PALETTE["text"],
            corner_radius=20,
            border_width=1,
            border_color=PALETTE["line"],
            font=self.body_font,
            wrap="word",
        )
        self.chat_output.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 16))
        self.chat_output.insert(
            "1.0",
            "Humoid Gemma Studio is ready for local prompts once the vault is unlocked.\n\n",
        )
        self.chat_output.configure(state="disabled")
        self.configure_textbox_tags(self.chat_output)

        compose = ctk.CTkFrame(left, fg_color="transparent")
        compose.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 20))
        compose.grid_columnconfigure(0, weight=1)

        self.chat_input = ctk.CTkTextbox(
            compose,
            height=110,
            fg_color=PALETTE["panel_alt"],
            text_color=PALETTE["text"],
            corner_radius=18,
            border_width=1,
            border_color=PALETTE["line"],
            font=self.body_font,
            wrap="word",
        )
        self.chat_input.grid(row=0, column=0, sticky="ew")
        self.register_action(self.chat_input)
        self.chat_input.bind("<Return>", self.handle_chat_return)
        self.chat_input.bind("<Shift-Return>", self.handle_chat_shift_return)

        actions = ctk.CTkFrame(compose, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="ns", padx=(14, 0))

        send_button = self.make_button(actions, "Send Prompt", self.submit_chat, 1, width=150, height=46)
        send_button.pack(pady=(0, 10))
        self.register_action(send_button)

        clear_button = self.make_button(actions, "Clear Chat", self.clear_chat, 3, width=150, height=42)
        clear_button.pack()
        self.register_action(clear_button)

        right = ctk.CTkFrame(tab, fg_color=PALETTE["card"], corner_radius=24, border_width=1, border_color=PALETTE["line"])
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 20), pady=20)
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right, text="Session Memory", font=self.section_font, text_color=PALETTE["text"]).grid(
            row=0, column=0, sticky="w", padx=20, pady=(18, 6)
        )
        ctk.CTkLabel(
            right,
            text="Recent turns are tucked into each prompt so the chat feels conversational even though the model is reopened per request.",
            font=self.body_font,
            text_color=PALETTE["muted"],
            wraplength=320,
            justify="left",
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 14))

        self.memory_preview = ctk.CTkTextbox(
            right,
            fg_color=PALETTE["panel_alt"],
            text_color=PALETTE["text"],
            corner_radius=18,
            border_width=1,
            border_color=PALETTE["line"],
            font=self.small_font,
            wrap="word",
            height=320,
        )
        self.memory_preview.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 16))
        self.memory_preview.insert("1.0", "No turns yet.\n")
        self.memory_preview.configure(state="disabled")

        hint = ctk.CTkLabel(
            right,
            text="Tip: keep prompts short and concrete for faster local responses.",
            font=self.small_font,
            text_color=PALETTE["muted"],
            justify="left",
            wraplength=320,
        )
        hint.grid(row=3, column=0, sticky="w", padx=20, pady=(0, 20))

    def build_model_tab(self) -> None:
        tab = self.model_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(tab, fg_color=PALETTE["card"], corner_radius=24, border_width=1, border_color=PALETTE["line"])
        left.grid(row=0, column=0, sticky="nsew", padx=(20, 10), pady=20)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="Model Vault", font=self.section_font, text_color=PALETTE["text"]).grid(
            row=0, column=0, sticky="w", padx=20, pady=(18, 6)
        )
        ctk.CTkLabel(
            left,
            text="Download, verify, and seal the LiteRT-LM model without keeping loose plaintext around.",
            font=self.body_font,
            text_color=PALETTE["muted"],
            wraplength=520,
            justify="left",
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 18))

        button_row = ctk.CTkFrame(left, fg_color="transparent")
        button_row.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 14))

        download_button = self.make_button(button_row, "Download + Encrypt", self.download_model_action, 0, width=180)
        download_button.pack(side="left", padx=(0, 10))
        self.register_action(download_button)

        verify_button = self.make_button(button_row, "Verify Hash", self.verify_model_action, 2, width=150)
        verify_button.pack(side="left", padx=(0, 10))
        self.register_action(verify_button)

        encrypt_button = self.make_button(button_row, "Encrypt Plaintext", self.encrypt_plaintext_action, 4, width=170)
        encrypt_button.pack(side="left")
        self.register_action(encrypt_button)

        button_row_2 = ctk.CTkFrame(left, fg_color="transparent")
        button_row_2.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 16))

        delete_plain_button = self.make_button(button_row_2, "Delete Plaintext", self.delete_plaintext_action, 5, width=170)
        delete_plain_button.pack(side="left")
        self.register_action(delete_plain_button)

        self.model_notes = ctk.CTkTextbox(
            left,
            fg_color=PALETTE["panel_alt"],
            text_color=PALETTE["text"],
            corner_radius=18,
            border_width=1,
            border_color=PALETTE["line"],
            font=self.body_font,
            wrap="word",
        )
        self.model_notes.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.model_notes.insert(
            "1.0",
            "Safe defaults:\n"
            "- Downloads land in a temporary file first.\n"
            "- Hash mismatches are rejected automatically.\n"
            "- Encryption uses a streamed format so large model files do not have to live fully in memory.\n",
        )
        self.model_notes.configure(state="disabled")

        right = ctk.CTkFrame(tab, fg_color=PALETTE["card"], corner_radius=24, border_width=1, border_color=PALETTE["line"])
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 20), pady=20)

        ctk.CTkLabel(right, text="Local Storage", font=self.section_font, text_color=PALETTE["text"]).pack(
            anchor="w", padx=20, pady=(18, 6)
        )
        ctk.CTkLabel(
            right,
            text="A quick read on what is stored locally right now.",
            font=self.body_font,
            text_color=PALETTE["muted"],
        ).pack(anchor="w", padx=20, pady=(0, 18))

        self.model_status_box = ctk.CTkTextbox(
            right,
            height=260,
            fg_color=PALETTE["panel_alt"],
            text_color=PALETTE["text"],
            corner_radius=18,
            border_width=1,
            border_color=PALETTE["line"],
            font=self.body_font,
            wrap="word",
        )
        self.model_status_box.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.model_status_box.configure(state="disabled")

    def build_road_tab(self) -> None:
        tab = self.road_tab
        tab.grid_columnconfigure(0, weight=3)
        tab.grid_columnconfigure(1, weight=2)

        form = ctk.CTkFrame(tab, fg_color=PALETTE["card"], corner_radius=24, border_width=1, border_color=PALETTE["line"])
        form.grid(row=0, column=0, sticky="nsew", padx=(20, 10), pady=20)
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="Road Scanner", font=self.section_font, text_color=PALETTE["text"]).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 6)
        )
        ctk.CTkLabel(
            form,
            text="Fill in the scene details and let the local model classify the driving risk.",
            font=self.body_font,
            text_color=PALETTE["muted"],
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 18))

        fields = [
            ("location", "Location", "I-95 NB mile 12"),
            ("road_type", "Road Type", "highway"),
            ("weather", "Weather", "clear"),
            ("traffic", "Traffic", "medium"),
            ("obstacles", "Obstacles", "none"),
            ("sensor_notes", "Sensor Notes", "camera and lidar stable"),
        ]
        self.road_inputs: Dict[str, Any] = {}
        for row_index, (name, label, placeholder) in enumerate(fields, start=2):
            ctk.CTkLabel(form, text=label, font=self.small_font, text_color=PALETTE["muted"]).grid(
                row=row_index, column=0, sticky="w", padx=20, pady=(0, 10)
            )
            entry = ctk.CTkEntry(
                form,
                placeholder_text=placeholder,
                height=42,
                corner_radius=16,
                border_color=PALETTE["line"],
                fg_color=PALETTE["panel_alt"],
                text_color=PALETTE["text"],
                font=self.body_font,
            )
            entry.grid(row=row_index, column=1, sticky="ew", padx=20, pady=(0, 10))
            self.register_action(entry)
            self.road_inputs[name] = entry

        road_buttons = ctk.CTkFrame(form, fg_color="transparent")
        road_buttons.grid(row=8, column=0, columnspan=2, sticky="w", padx=20, pady=(6, 20))

        run_button = self.make_button(road_buttons, "Run Scan", self.run_road_scan_action, 1, width=150)
        run_button.pack(side="left", padx=(0, 10))
        self.register_action(run_button)

        export_button = self.make_button(road_buttons, "Export JSON", self.export_last_scan, 3, width=150)
        export_button.pack(side="left")
        self.register_action(export_button)
        export_button.configure(state="disabled")
        self.road_export_button = export_button

        result = ctk.CTkFrame(tab, fg_color=PALETTE["card"], corner_radius=24, border_width=1, border_color=PALETTE["line"])
        result.grid(row=0, column=1, sticky="nsew", padx=(10, 20), pady=20)
        result.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(result, text="Scan Result", font=self.section_font, text_color=PALETTE["text"]).grid(
            row=0, column=0, sticky="w", padx=20, pady=(18, 10)
        )

        self.road_result_label = ctk.CTkLabel(
            result,
            text="Waiting for a scan",
            font=ctk.CTkFont(family="DejaVu Sans", size=34, weight="bold"),
            text_color=PALETTE["accent_indigo"],
            fg_color=PALETTE["panel_alt"],
            corner_radius=18,
            padx=18,
            pady=18,
        )
        self.road_result_label.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 16))

        self.road_detail_box = ctk.CTkTextbox(
            result,
            fg_color=PALETTE["panel_alt"],
            text_color=PALETTE["text"],
            corner_radius=18,
            border_width=1,
            border_color=PALETTE["line"],
            font=self.small_font,
            wrap="word",
        )
        self.road_detail_box.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.road_detail_box.insert("1.0", "Prompt and raw model output will appear here.\n")
        self.road_detail_box.configure(state="disabled")

    def build_history_tab(self) -> None:
        tab = self.history_tab
        controls = ctk.CTkFrame(tab, fg_color="transparent")
        controls.pack(fill="x", padx=20, pady=(20, 14))

        self.history_search_entry = ctk.CTkEntry(
            controls,
            textvariable=self.history_search_var,
            placeholder_text="Search prompts or responses",
            height=44,
            corner_radius=16,
            border_color=PALETTE["line"],
            fg_color=PALETTE["panel_alt"],
            text_color=PALETTE["text"],
            font=self.body_font,
            width=360,
        )
        self.history_search_entry.pack(side="left", padx=(0, 10))
        self.register_action(self.history_search_entry)

        refresh_history = self.make_button(controls, "Refresh", self.refresh_history_action, 2, width=130, height=42)
        refresh_history.pack(side="left", padx=(0, 10))
        self.register_action(refresh_history)

        prev_history = self.make_button(controls, "Previous", self.history_prev_page, 4, width=130, height=42)
        prev_history.pack(side="left", padx=(0, 10))
        self.register_action(prev_history)

        next_history = self.make_button(controls, "Next", self.history_next_page, 5, width=110, height=42)
        next_history.pack(side="left")
        self.register_action(next_history)

        self.history_box = ctk.CTkTextbox(
            tab,
            fg_color=PALETTE["card"],
            text_color=PALETTE["text"],
            corner_radius=24,
            border_width=1,
            border_color=PALETTE["line"],
            font=self.body_font,
            wrap="word",
        )
        self.history_box.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.history_box.insert("1.0", "Unlock the vault, then refresh to browse encrypted history.\n")
        self.history_box.configure(state="disabled")

    def build_settings_tab(self) -> None:
        tab = self.settings_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)

        look = ctk.CTkFrame(tab, fg_color=PALETTE["card"], corner_radius=24, border_width=1, border_color=PALETTE["line"])
        look.grid(row=0, column=0, sticky="nsew", padx=(20, 10), pady=20)

        ctk.CTkLabel(look, text="Behavior", font=self.section_font, text_color=PALETTE["text"]).pack(
            anchor="w", padx=20, pady=(18, 8)
        )

        entropy_switch = ctk.CTkSwitch(
            look,
            text="Include live system entropy in road scanner prompts",
            variable=self.settings_entropy_var,
            progress_color=PALETTE["accent_teal"],
            button_color=PALETTE["accent_teal"],
            button_hover_color="#00b85c",
            text_color=PALETTE["text"],
            font=self.body_font,
        )
        entropy_switch.pack(anchor="w", padx=20, pady=(0, 12))
        self.register_action(entropy_switch)

        delete_switch = ctk.CTkSwitch(
            look,
            text="Delete plaintext model copy after manual encryption",
            variable=self.settings_delete_plaintext_var,
            progress_color=PALETTE["accent_blue"],
            button_color=PALETTE["accent_blue"],
            button_hover_color="#12af63",
            text_color=PALETTE["text"],
            font=self.body_font,
        )
        delete_switch.pack(anchor="w", padx=20, pady=(0, 12))
        self.register_action(delete_switch)

        memory_label = ctk.CTkLabel(
            look,
            text="Chat memory turns",
            font=self.small_font,
            text_color=PALETTE["muted"],
        )
        memory_label.pack(anchor="w", padx=20, pady=(4, 4))

        memory_slider = ctk.CTkSlider(
            look,
            from_=2,
            to=10,
            number_of_steps=8,
            variable=self.settings_memory_turns_var,
            progress_color=PALETTE["accent_pink"],
            button_color=PALETTE["accent_pink"],
            button_hover_color="#48d87d",
        )
        memory_slider.pack(fill="x", padx=20, pady=(0, 12))
        self.register_action(memory_slider)

        save_button = self.make_button(look, "Save Settings", self.save_settings_action, 0, width=160)
        save_button.pack(anchor="w", padx=20, pady=(0, 20))
        self.register_action(save_button)

        security = ctk.CTkFrame(tab, fg_color=PALETTE["card"], corner_radius=24, border_width=1, border_color=PALETTE["line"])
        security.grid(row=0, column=1, sticky="nsew", padx=(10, 20), pady=20)

        ctk.CTkLabel(security, text="Vault Password", font=self.section_font, text_color=PALETTE["text"]).pack(
            anchor="w", padx=20, pady=(18, 8)
        )
        ctk.CTkLabel(
            security,
            text="Change the password protecting the encrypted model and chat history.",
            font=self.body_font,
            text_color=PALETTE["muted"],
            wraplength=500,
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 16))

        self.current_password_entry = ctk.CTkEntry(
            security,
            textvariable=self.change_current_password_var,
            show="*",
            placeholder_text="Current password (leave blank for legacy raw mode)",
            height=42,
            corner_radius=16,
            border_color=PALETTE["line"],
            fg_color=PALETTE["panel_alt"],
            text_color=PALETTE["text"],
            font=self.body_font,
        )
        self.current_password_entry.pack(fill="x", padx=20, pady=(0, 10))
        self.register_action(self.current_password_entry)

        self.new_password_entry = ctk.CTkEntry(
            security,
            textvariable=self.change_new_password_var,
            show="*",
            placeholder_text="New password",
            height=42,
            corner_radius=16,
            border_color=PALETTE["line"],
            fg_color=PALETTE["panel_alt"],
            text_color=PALETTE["text"],
            font=self.body_font,
        )
        self.new_password_entry.pack(fill="x", padx=20, pady=(0, 10))
        self.register_action(self.new_password_entry)

        self.confirm_password_entry = ctk.CTkEntry(
            security,
            textvariable=self.change_confirm_password_var,
            show="*",
            placeholder_text="Confirm new password",
            height=42,
            corner_radius=16,
            border_color=PALETTE["line"],
            fg_color=PALETTE["panel_alt"],
            text_color=PALETTE["text"],
            font=self.body_font,
        )
        self.confirm_password_entry.pack(fill="x", padx=20, pady=(0, 14))
        self.register_action(self.confirm_password_entry)

        password_button = self.make_button(security, "Update Password", self.change_password_action, 1, width=180)
        password_button.pack(anchor="w", padx=20, pady=(0, 10))
        self.register_action(password_button)

        lock_button = self.make_button(security, "Lock Studio", self.lock_studio, 5, width=140)
        lock_button.pack(anchor="w", padx=20, pady=(0, 20))
        self.register_action(lock_button)

    def configure_textbox_tags(self, textbox: ctk.CTkTextbox) -> None:
        try:
            text_widget = textbox._textbox
            text_widget.tag_config("user_header", foreground=PALETTE["accent_orange"], font=("DejaVu Sans", 11, "bold"))
            text_widget.tag_config("assistant_header", foreground=PALETTE["accent_teal"], font=("DejaVu Sans", 11, "bold"))
            text_widget.tag_config("meta", foreground=PALETTE["muted"], font=("DejaVu Sans", 10))
        except Exception:
            return

    def append_chat_message(self, role: str, message: str) -> None:
        self.chat_output.configure(state="normal")
        tag = "user_header" if role == "You" else "assistant_header"
        timestamp = time.strftime("%H:%M:%S")
        try:
            text_widget = self.chat_output._textbox
            text_widget.insert("end", f"{role}  {timestamp}\n", (tag,))
            text_widget.insert("end", message.strip() + "\n\n")
            text_widget.see("end")
        except Exception:
            self.chat_output.insert("end", f"{role}  {timestamp}\n{message.strip()}\n\n")
        self.chat_output.configure(state="disabled")

    def refresh_memory_preview(self) -> None:
        self.memory_preview.configure(state="normal")
        self.memory_preview.delete("1.0", "end")
        if not self.chat_memory:
            self.memory_preview.insert("1.0", "No turns yet.\n")
        else:
            for role, message in self.chat_memory[-12:]:
                label = "You" if role == "user" else "Gemma"
                self.memory_preview.insert("end", f"{label}: {message}\n\n")
        self.memory_preview.configure(state="disabled")

    def refresh_dashboard(self) -> None:
        summary = storage_summary(self.key)
        self.model_status_var.set(f"Model: {summary['model_state']}")
        self.dashboard_vault_var.set(summary["encrypted_size"])
        self.dashboard_history_var.set(summary["history_count"])
        self.dashboard_plaintext_var.set(summary["plaintext_size"])
        self.update_model_status_box(summary)

    def update_model_status_box(self, summary: Dict[str, str]) -> None:
        lines = [
            f"Model state: {summary['model_state']}",
            f"Encrypted size: {summary['encrypted_size']}",
            f"Plaintext size: {summary['plaintext_size']}",
            f"Key mode: {summary['key_mode']}",
            f"Expected SHA256: {EXPECTED_HASH}",
            "",
            "Safety notes:",
            "- Runtime model access uses a temporary unlocked file when the encrypted vault exists.",
            "- Chat history lives in an encrypted SQLite file and is re-sealed after each read or write.",
            "- Password changes re-encrypt the model vault and history vault before the key file is replaced.",
        ]
        self.model_status_box.configure(state="normal")
        self.model_status_box.delete("1.0", "end")
        self.model_status_box.insert("1.0", "\n".join(lines))
        self.model_status_box.configure(state="disabled")

    def clear_chat(self) -> None:
        self.chat_memory.clear()
        self.chat_output.configure(state="normal")
        self.chat_output.delete("1.0", "end")
        self.chat_output.insert("1.0", "Chat cleared. The local vault is still ready.\n\n")
        self.chat_output.configure(state="disabled")
        self.refresh_memory_preview()

    def handle_chat_return(self, _event: Any) -> str:
        self.submit_chat()
        return "break"

    def handle_chat_shift_return(self, _event: Any) -> Optional[str]:
        return None

    def submit_chat(self) -> None:
        if not self.ensure_unlocked():
            return
        prompt = self.chat_input.get("1.0", "end").strip()
        if not prompt:
            return

        memory_snapshot = list(self.chat_memory)
        memory_turns = int(self.settings_data.get("chat_memory_turns", 6))
        self.append_chat_message("You", prompt)
        self.chat_input.delete("1.0", "end")
        self.status_var.set("Sending prompt to the local model...")

        def on_success(reply: str) -> None:
            self.chat_memory.extend([("user", prompt), ("assistant", reply)])
            self.append_chat_message("Gemma", reply)
            self.refresh_memory_preview()
            self.status_var.set("Reply ready. The model vault has been sealed again.")

        self.run_process_task(
            "Generating a local reply...",
            "chat_request",
            (self.key, prompt, memory_snapshot, memory_turns),
            on_success=on_success,
        )

    def download_model_action(self) -> None:
        if not self.ensure_unlocked():
            return
        if messagebox and not messagebox.askyesno(
            "Download model",
            "Download the Gemma LiteRT-LM model, verify its hash, and seal it into the encrypted vault?",
        ):
            return

        def on_success(sha: str) -> None:
            self.hash_status_var.set(f"Hash: Verified {sha[:12]}...")
            self.status_var.set("Model download finished and the encrypted vault is ready.")

        self.run_task(
            "Downloading and sealing the model vault...",
            lambda reporter: download_and_encrypt_model(self.key, reporter=reporter),
            on_success=on_success,
        )

    def verify_model_action(self) -> None:
        if not self.ensure_unlocked():
            return

        def on_success(result: Tuple[str, bool]) -> None:
            sha, matches = result
            status = "Verified" if matches else "Mismatch"
            self.hash_status_var.set(f"Hash: {status} {sha[:12]}...")
            if messagebox:
                if matches:
                    messagebox.showinfo("Hash verified", f"SHA256 matches the expected model hash.\n\n{sha}")
                else:
                    messagebox.showwarning("Hash mismatch", f"The model hash does not match the expected value.\n\n{sha}")

        self.run_task(
            "Verifying the local model hash...",
            lambda reporter: verify_model_hash(self.key),
            on_success=on_success,
        )

    def encrypt_plaintext_action(self) -> None:
        if not self.ensure_unlocked():
            return
        delete_plaintext = bool(self.settings_delete_plaintext_var.get())

        def on_success(_: Any) -> None:
            self.status_var.set("Plaintext model encrypted into the vault.")

        self.run_task(
            "Encrypting the plaintext model copy...",
            lambda reporter: encrypt_existing_plaintext_model(self.key, delete_plaintext=delete_plaintext),
            on_success=on_success,
        )

    def delete_plaintext_action(self) -> None:
        if not MODEL_PATH.exists():
            if messagebox:
                messagebox.showinfo("No plaintext copy", "There is no plaintext model file to remove.")
            return
        if messagebox and not messagebox.askyesno(
            "Delete plaintext model",
            "Delete the plaintext model copy from disk? The encrypted vault will remain untouched.",
        ):
            return
        safe_cleanup([MODEL_PATH])
        self.status_var.set("Plaintext model copy removed.")
        self.refresh_dashboard()

    def collect_road_form(self) -> Dict[str, str]:
        data: Dict[str, str] = {}
        for key, widget in self.road_inputs.items():
            data[key] = widget.get().strip()
        return data

    def run_road_scan_action(self) -> None:
        if not self.ensure_unlocked():
            return
        data = self.collect_road_form()
        include_entropy = bool(self.settings_data.get("include_system_entropy", True))

        def on_success(result: Dict[str, str]) -> None:
            self.last_scan_result = dict(result)
            label = result["label"]
            color = PALETTE["ok"] if label == "Low" else PALETTE["accent_gold"] if label == "Medium" else PALETTE["danger"]
            self.road_result_label.configure(text=label, text_color=color)
            self.road_detail_box.configure(state="normal")
            self.road_detail_box.delete("1.0", "end")
            self.road_detail_box.insert(
                "1.0",
                f"Timestamp: {result['timestamp']}\n\nPrompt:\n{result['prompt']}\n\nRaw model output:\n{result['raw']}\n",
            )
            self.road_detail_box.configure(state="disabled")
            self.road_export_button.configure(state="normal")
            self.status_var.set("Road scan complete. The result has been logged into the encrypted history vault.")

        self.run_process_task(
            "Running the road scanner locally...",
            "road_scan",
            (self.key, data, include_entropy),
            on_success=on_success,
        )

    def export_last_scan(self) -> None:
        if not self.last_scan_result:
            return
        if filedialog is None:
            if messagebox:
                messagebox.showwarning("Export unavailable", "File dialog support is not available in this environment.")
            return
        suggested = f"road_scan_{time.strftime('%Y%m%d_%H%M%S')}.json"
        target = filedialog.asksaveasfilename(
            title="Export road scan result",
            initialfile=suggested,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not target:
            return
        Path(target).write_text(json.dumps(self.last_scan_result, indent=2), encoding="utf-8")
        self.status_var.set(f"Road scan exported to {target}.")

    def refresh_history_action(self) -> None:
        self.history_offset = 0
        self.load_history_page()

    def history_next_page(self) -> None:
        self.history_offset += 12
        self.load_history_page()

    def history_prev_page(self) -> None:
        self.history_offset = max(0, self.history_offset - 12)
        self.load_history_page()

    def load_history_page(self) -> None:
        if not self.ensure_unlocked():
            return
        search = self.history_search_var.get().strip() or None
        offset_snapshot = self.history_offset

        def on_success(rows: List[Tuple[int, str, str, str]]) -> None:
            if not rows and offset_snapshot > 0:
                self.history_offset = max(0, offset_snapshot - 12)
            self.render_history(rows, search)

        self.run_task(
            "Loading encrypted history...",
            lambda reporter: fetch_history(self.key, limit=12, offset=offset_snapshot, search=search),
            on_success=on_success,
        )

    def render_history(self, rows: List[Tuple[int, str, str, str]], search: Optional[str]) -> None:
        self.history_box.configure(state="normal")
        self.history_box.delete("1.0", "end")
        header = f"Search: {search or 'None'}\nOffset: {self.history_offset}\n\n"
        self.history_box.insert("1.0", header)
        if not rows:
            self.history_box.insert("end", "No history rows for this page.\n")
        else:
            for row_id, stamp, prompt, response in rows:
                self.history_box.insert(
                    "end",
                    f"[{row_id}] {stamp}\nPrompt:\n{prompt}\n\nResponse:\n{response}\n\n{'-' * 72}\n\n",
                )
        self.history_box.configure(state="disabled")
        self.status_var.set("Encrypted history loaded.")

    def save_settings_action(self) -> None:
        self.settings_data["include_system_entropy"] = bool(self.settings_entropy_var.get())
        self.settings_data["delete_plaintext_after_encrypt"] = bool(self.settings_delete_plaintext_var.get())
        self.settings_data["chat_memory_turns"] = int(self.settings_memory_turns_var.get())
        save_settings(self.settings_data)
        self.status_var.set("Settings saved.")

    def change_password_action(self) -> None:
        if not self.ensure_unlocked():
            return

        current_password = self.change_current_password_var.get().strip()
        new_password = self.change_new_password_var.get().strip()
        confirm_password = self.change_confirm_password_var.get().strip()

        if len(new_password) < 8:
            if messagebox:
                messagebox.showwarning("Password too short", "Use at least 8 characters for the new vault password.")
            return
        if new_password != confirm_password:
            if messagebox:
                messagebox.showwarning("Mismatch", "The new password and confirmation do not match.")
            return

        if detect_key_mode() == "passphrase":
            try:
                unlock_key_with_passphrase(current_password)
            except Exception as exc:
                if messagebox:
                    messagebox.showwarning("Current password incorrect", str(exc))
                return

        def on_success(new_key: bytes) -> None:
            self.key = new_key
            self.key_mode = "passphrase"
            self.key_status_var.set("Key: Passphrase")
            self.change_current_password_var.set("")
            self.change_new_password_var.set("")
            self.change_confirm_password_var.set("")
            self.status_var.set("Vault password updated and encrypted assets were rewrapped safely.")

        self.run_task(
            "Re-encrypting the vault with the new password...",
            lambda reporter: rotate_to_new_passphrase(self.key, new_password, reporter=reporter),
            on_success=on_success,
        )

    def lock_studio(self) -> None:
        self.key = None
        self.key_mode = "locked"
        self.key_status_var.set("Key: Locked")
        self.model_status_var.set("Model: Locked")
        self.status_var.set("Studio locked.")
        self.hash_status_var.set("Hash: Not checked")
        self.chat_memory.clear()
        self.refresh_memory_preview()
        self.set_action_state(False)
        self.open_startup_dialog()

    def on_close(self) -> None:
        if self.busy:
            if messagebox:
                messagebox.showinfo("Task running", "Please wait for the current task to finish before closing the studio.")
            return
        if RUNTIME_MODEL_PATH.exists() and self.key is not None:
            try:
                encrypt_file(RUNTIME_MODEL_PATH, ENCRYPTED_MODEL, self.key)
                safe_cleanup([RUNTIME_MODEL_PATH])
            except Exception:
                pass
        self.destroy()


def main() -> None:
    if not GUI_READY:
        missing = []
        if tk is None:
            missing.append("tkinter")
        if ctk is None:
            missing.append("customtkinter")
        details = []
        if TK_IMPORT_ERROR is not None:
            details.append(f"tkinter import error: {TK_IMPORT_ERROR}")
        if CTK_IMPORT_ERROR is not None:
            details.append(f"customtkinter import error: {CTK_IMPORT_ERROR}")
        raise SystemExit(
            "This app now launches as a GUI and requires "
            + ", ".join(missing)
            + ". Install the project dependencies and make sure Python Tk support is available."
            + (f"\n\nDetails:\n- " + "\n- ".join(details) if details else "")
        )

    app = HumoidStudioApp()
    app.mainloop()


if __name__ == "__main__":
    main()
