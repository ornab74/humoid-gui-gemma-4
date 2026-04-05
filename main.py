import os
import sys
import time
import json
import hashlib
import asyncio
import getpass
import math
import random
import re
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Tuple, Dict

import httpx
import aiosqlite
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

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
DB_PATH = Path("chat_history.db.aes")
KEY_PATH = Path(".enc_key")
CACHE_DIR = Path(".litert_lm_cache")
MODELS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CSI = "\x1b["


def clear_screen() -> None:
    sys.stdout.write(CSI + "2J" + CSI + "H")
    sys.stdout.flush()


def show_cursor() -> None:
    sys.stdout.write(CSI + "?25h")
    sys.stdout.flush()


def color(text: str, fg: Optional[int] = None, bold: bool = False) -> str:
    codes = []
    if fg is not None:
        codes.append(str(fg))
    if bold:
        codes.append("1")
    if not codes:
        return text
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


def boxed(title: str, lines: List[str], width: int = 72) -> str:
    top = "┌" + "─" * (width - 2) + "┐"
    bot = "└" + "─" * (width - 2) + "┘"
    title_line = f"│ {color(title, fg=36, bold=True):{width - 4}} │"
    body: List[str] = []
    for line in lines:
        chunks = [line[i : i + width - 4] for i in range(0, len(line), width - 4)] or [""]
        for chunk in chunks:
            body.append(f"│ {chunk:{width - 4}} │")
    return "\n".join([top, title_line] + body + [bot])


def getch() -> bytes:
    try:
        import tty
        import termios

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return os.read(fd, 3)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        s = input()
        return s[:1].encode() if s else b""


def read_menu_choice(num_items: int, prompt: str = "Use ↑↓ arrows or number, Enter to select: ") -> int:
    print(prompt)
    try:
        idx = 0
        while True:
            ch = getch()
            if not ch:
                continue
            if ch in (b"\x1b[A", b"\x1b\x00A"):
                idx = (idx - 1) % num_items
            elif ch in (b"\x1b[B", b"\x1b\x00B"):
                idx = (idx + 1) % num_items
            elif ch in (b"\r", b"\n", b"\x0d"):
                return idx
            else:
                try:
                    s = ch.decode(errors="ignore").strip()
                    if s.isdigit():
                        n = int(s)
                        if 1 <= n <= num_items:
                            return n - 1
                except Exception:
                    pass
            sys.stdout.write(f"\rSelected: {idx + 1}/{num_items} ")
            sys.stdout.flush()
    except Exception:
        while True:
            s = input("Enter number: ").strip()
            if s.isdigit():
                n = int(s)
                if 1 <= n <= num_items:
                    return n - 1



def aes_encrypt(data: bytes, key: bytes) -> bytes:
    aes = AESGCM(key)
    nonce = os.urandom(12)
    return nonce + aes.encrypt(nonce, data, None)


def aes_decrypt(data: bytes, key: bytes) -> bytes:
    aes = AESGCM(key)
    nonce, ct = data[:12], data[12:]
    return aes.decrypt(nonce, ct, None)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_key_file(key_bytes: bytes) -> None:
    KEY_PATH.write_bytes(key_bytes)
    try:
        os.chmod(KEY_PATH, 0o600)
    except Exception:
        pass


def derive_key_from_passphrase(pw: str, salt: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    if salt is None:
        salt = os.urandom(16)
    kdf_der = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200_000,
    )
    return salt, kdf_der.derive(pw.encode("utf-8"))


def get_or_create_key() -> bytes:
    if KEY_PATH.exists():
        d = KEY_PATH.read_bytes()
        if len(d) >= 48:
            return d[16:48]
        return d[:32]
    key = AESGCM.generate_key(256)
    _write_key_file(key)
    print(f"🔑 New random key generated and saved to {KEY_PATH}")
    return key


def ensure_key_interactive() -> bytes:
    if KEY_PATH.exists():
        data = KEY_PATH.read_bytes()
        if len(data) >= 48:
            return data[16:48]
        if len(data) >= 32:
            return data[:32]

    print("Key not found. Create new key:")
    print("  1) Generate random key (saved raw)")
    print("  2) Derive from passphrase (salt+derived saved)")
    opt = input("Choose (1/2): ").strip()

    if opt == "2":
        pw = getpass.getpass("Enter passphrase: ")
        pw2 = getpass.getpass("Confirm: ")
        if pw != pw2:
            print("Passphrases mismatch. Aborting.")
            sys.exit(1)
        salt, key = derive_key_from_passphrase(pw)
        _write_key_file(salt + key)
        print(f"Saved salt+derived key to {KEY_PATH}")
        return key

    key = AESGCM.generate_key(256)
    _write_key_file(key)
    print(f"Saved random key to {KEY_PATH}")
    return key


def download_model_httpx(
    url: str,
    dest: Path,
    show_progress: bool = True,
    timeout: Optional[float] = None,
    expected_sha: Optional[str] = None,
) -> str:
    print(f"⬇️  Downloading model from {url}\nTo: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)

    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        h = hashlib.sha256()

        with dest.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=8192):
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total and show_progress:
                    pct = done / total * 100
                    bar = int(pct // 2)
                    sys.stdout.write(
                        f"\r[{('#' * bar).ljust(50)}] {pct:5.1f}% ({done // 1024}KB/{total // 1024}KB)"
                    )
                    sys.stdout.flush()

    if show_progress:
        print("\n✅ Download complete.")

    sha = h.hexdigest()
    print(f"SHA256: {sha}")

    if expected_sha:
        if sha.lower() == expected_sha.lower():
            print(color("SHA256 matches expected.", fg=32, bold=True))
        else:
            print(color(f"SHA256 MISMATCH! expected {expected_sha} got {sha}", fg=31, bold=True))
            keep_file = input("Hash mismatch. Keep this download anyway? (y/N): ").strip().lower() == "y"
            if not keep_file:
                try:
                    dest.unlink()
                except Exception:
                    pass
                raise ValueError("Download aborted because SHA256 verification failed.")
    return sha


def encrypt_file(src: Path, dest: Path, key: bytes) -> None:
    print(f"🔐 Encrypting {src} -> {dest}")
    data = src.read_bytes()
    start = time.time()
    enc = aes_encrypt(data, key)
    dest.write_bytes(enc)
    print(f"✅ Encrypted ({len(enc)} bytes) in {time.time() - start:.2f}s")


def decrypt_file(src: Path, dest: Path, key: bytes) -> None:
    print(f"🔓 Decrypting {src} -> {dest}")
    enc = src.read_bytes()
    dest.write_bytes(aes_decrypt(enc, key))
    print(f"✅ Decrypted ({dest.stat().st_size} bytes)")


def _temp_db_path() -> Path:
    tmp = tempfile.NamedTemporaryFile(prefix="litert_", suffix=".db", delete=False)
    tmp.close()
    return Path(tmp.name)


def safe_cleanup(paths: List[Path]) -> None:
    for p in paths:
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass


async def init_db(key: bytes) -> None:
    if DB_PATH.exists():
        return
    tmp_db = _temp_db_path()
    try:
        async with aiosqlite.connect(tmp_db) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, prompt TEXT, response TEXT)"
            )
            await db.commit()
        DB_PATH.write_bytes(aes_encrypt(tmp_db.read_bytes(), key))
    finally:
        safe_cleanup([tmp_db])


async def log_interaction(prompt: str, response: str, key: bytes) -> None:
    dec = _temp_db_path()
    try:
        decrypt_file(DB_PATH, dec, key)
        async with aiosqlite.connect(dec) as db:
            await db.execute(
                "INSERT INTO history (timestamp, prompt, response) VALUES (?, ?, ?)",
                (time.strftime("%Y-%m-%d %H:%M:%S"), prompt, response),
            )
            await db.commit()
        DB_PATH.write_bytes(aes_encrypt(dec.read_bytes(), key))
    finally:
        safe_cleanup([dec])


async def fetch_history(key: bytes, limit: int = 20, offset: int = 0, search: Optional[str] = None):
    rows = []
    dec = _temp_db_path()
    try:
        decrypt_file(DB_PATH, dec, key)
        async with aiosqlite.connect(dec) as db:
            if search:
                q = f"%{search}%"
                async with db.execute(
                    "SELECT id,timestamp,prompt,response FROM history WHERE prompt LIKE ? OR response LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (q, q, limit, offset),
                ) as cur:
                    async for r in cur:
                        rows.append(r)
            else:
                async with db.execute(
                    "SELECT id,timestamp,prompt,response FROM history ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ) as cur:
                    async for r in cur:
                        rows.append(r)
        DB_PATH.write_bytes(aes_encrypt(dec.read_bytes(), key))
    finally:
        safe_cleanup([dec])
    return rows


def require_litert_lm() -> None:
    if litert_lm is None:
        raise RuntimeError(
            "LiteRT-LM Python package is not installed. Install it with `pip install litert-lm-api-nightly`."
        )


def load_litert_engine_blocking(model_path: Path):
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
    messages: List[dict] = []
    if system_text:
        messages.append(
            {
                "role": "system",
                "content": [{"type": "text", "text": system_text}],
            }
        )
    return messages


def response_to_text(response: dict) -> str:
    if not isinstance(response, dict):
        return str(response).strip()
    parts = response.get("content", [])
    texts = []
    for item in parts:
        if isinstance(item, dict) and item.get("type") == "text":
            texts.append(item.get("text", ""))
    return "".join(texts).strip()


def litert_chat_blocking(
    model_path: Path,
    user_text: str,
    *,
    system_text: Optional[str] = None,
    stream: bool = False,
) -> str:
    engine = load_litert_engine_blocking(model_path)
    with engine:
        messages = create_default_messages(system_text)
        with engine.create_conversation(messages=messages) as conversation:
            if stream:
                chunks: List[str] = []
                for chunk in conversation.send_message_async(user_text):
                    text = response_to_text(chunk)
                    if text:
                        print(text, end="", flush=True)
                        chunks.append(text)
                print()
                return "".join(chunks).strip()
            return response_to_text(conversation.send_message(user_text))


def litert_classify_blocking(
    model_path: Path,
    user_text: str,
    *,
    system_text: Optional[str] = None,
) -> str:
    return litert_chat_blocking(model_path, user_text, system_text=system_text, stream=False)



def collect_system_metrics() -> Dict[str, float]:
    if psutil is None:
        raise RuntimeError("psutil is required for system metrics")

    cpu = psutil.cpu_percent(interval=0.1) / 100.0
    mem = psutil.virtual_memory().percent / 100.0
    try:
        load_raw = os.getloadavg()[0]
        cpu_cnt = psutil.cpu_count(logical=True) or 1
        load1 = max(0.0, min(1.0, load_raw / max(1.0, float(cpu_cnt))))
    except Exception:
        load1 = cpu
    try:
        temps_map = psutil.sensors_temperatures()
        if temps_map:
            first = next(iter(temps_map.values()))[0].current
            temp = max(0.0, min(1.0, (first - 20.0) / 70.0))
        else:
            temp = 0.0
    except Exception:
        temp = 0.0
    return {
        "cpu": float(max(0.0, min(1.0, cpu))),
        "mem": float(max(0.0, min(1.0, mem))),
        "load1": float(max(0.0, min(1.0, load1))),
        "temp": float(max(0.0, min(1.0, temp))),
    }


def metrics_to_rgb(metrics: dict) -> Tuple[float, float, float]:
    cpu = metrics.get("cpu", 0.1)
    mem = metrics.get("mem", 0.1)
    temp = metrics.get("temp", 0.1)
    load1 = metrics.get("load1", 0.0)
    r = cpu * (1.0 + load1)
    g = mem * (1.0 + load1 * 0.5)
    b = temp * (0.5 + cpu * 0.5)
    maxi = max(r, g, b, 1.0)
    return (
        float(max(0.0, min(1.0, r / maxi))),
        float(max(0.0, min(1.0, g / maxi))),
        float(max(0.0, min(1.0, b / maxi))),
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

    dev = qml.device("default.qubit", wires=2, shots=shots)

    @qml.qnode(dev)
    def circuit(a, b, c):
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
        f"Location: {data.get('location', 'unspecified location')}\n"
        f"Road type: {data.get('road_type', 'unknown')}\n"
        f"Weather: {data.get('weather', 'unknown')}\n"
        f"Traffic: {data.get('traffic', 'unknown')}\n"
        f"Obstacles: {data.get('obstacles', 'none')}\n"
        f"Sensor notes: {data.get('sensor_notes', 'none')}\n"
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
    candidate = (text or "").strip().split()
    label = candidate[0].capitalize() if candidate else ""
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



def header(status: dict) -> None:
    s = f" Secure LiteRT-LM CLI — Model: {'loaded' if status.get('model_loaded') else 'none'} | Key: {'present' if status.get('key') else 'missing'} "
    print(color(s.center(80, '─'), fg=35, bold=True))


def model_manager(state: dict) -> None:
    while True:
        clear_screen()
        header(state)
        lines = [
            "1) Download model from Hugging Face",
            "2) Verify plaintext model hash (compute SHA256)",
            "3) Encrypt plaintext model -> .aes",
            "4) Decrypt .aes -> plaintext (temporary)",
            "5) Delete plaintext model",
            "6) Back",
        ]
        print(boxed("Model Manager", lines))
        choice = input("Choose (1-6): ").strip()

        if choice == "1":
            if MODEL_PATH.exists() and input("Plaintext model exists; overwrite? (y/N): ").strip().lower() != "y":
                continue
            try:
                url = MODEL_REPO + MODEL_FILE
                sha = download_model_httpx(url, MODEL_PATH, show_progress=True, timeout=None, expected_sha=EXPECTED_HASH)
                print(f"Downloaded to {MODEL_PATH}")
                print(f"Computed SHA256: {sha}")
                if input("Encrypt downloaded model with current key now? (Y/n): ").strip().lower() != "n":
                    encrypt_file(MODEL_PATH, ENCRYPTED_MODEL, state["key"])
                    print(f"Encrypted -> {ENCRYPTED_MODEL}")
                    if input("Remove plaintext model? (Y/n): ").strip().lower() != "n":
                        MODEL_PATH.unlink()
                        print("Plaintext removed.")
            except Exception as e:
                print(f"Download failed: {e}")
            input("Enter to continue...")

        elif choice == "2":
            if not MODEL_PATH.exists():
                print("No plaintext model found.")
            else:
                print(f"SHA256: {sha256_file(MODEL_PATH)}")
                if EXPECTED_HASH:
                    print(f"Expected: {EXPECTED_HASH}")
            input("Enter to continue...")

        elif choice == "3":
            if not MODEL_PATH.exists():
                print("No plaintext model to encrypt.")
            else:
                encrypt_file(MODEL_PATH, ENCRYPTED_MODEL, state["key"])
                if input("Remove plaintext? (Y/n): ").strip().lower() != "n":
                    MODEL_PATH.unlink()
                    print("Removed plaintext.")
            input("Enter...")

        elif choice == "4":
            if not ENCRYPTED_MODEL.exists():
                print("No .aes model present.")
            else:
                decrypt_file(ENCRYPTED_MODEL, MODEL_PATH, state["key"])
            input("Enter...")

        elif choice == "5":
            if MODEL_PATH.exists() and input(f"Delete {MODEL_PATH}? (y/N): ").strip().lower() == "y":
                MODEL_PATH.unlink()
                print("Deleted.")
            elif not MODEL_PATH.exists():
                print("No plaintext model.")
            input("Enter...")

        elif choice == "6":
            return

        else:
            print("Invalid.")
            input("Enter...")


async def chat_session(state: dict) -> None:
    if not ENCRYPTED_MODEL.exists():
        print("No encrypted model found. Please download & encrypt first.")
        input("Enter...")
        return

    decrypt_file(ENCRYPTED_MODEL, MODEL_PATH, state["key"])
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as ex:
        try:
            state["model_loaded"] = True
            await init_db(state["key"])
            print("Type /exit to return, /history to show last 10 messages.")
            while True:
                prompt = input("\nYou> ").strip()
                if not prompt:
                    continue
                if prompt in ("/exit", "exit", "quit"):
                    break
                if prompt == "/history":
                    rows = await fetch_history(state["key"], limit=10)
                    for r in rows:
                        print(f"[{r[0]}] {r[1]}\nQ: {r[2]}\nA: {r[3]}\n{'-' * 30}")
                    continue

                print("🤖 Thinking...")
                try:
                    result = await loop.run_in_executor(
                        ex,
                        lambda: litert_chat_blocking(
                            MODEL_PATH,
                            prompt,
                            system_text="You are a helpful assistant.",
                            stream=True,
                        ),
                    )
                    if not result:
                        result = ""
                    print()
                    await log_interaction(prompt, result, state["key"])
                except Exception as e:
                    print(f"Generation failed: {e}")
        finally:
            print("Re-encrypting model and removing plaintext...")
            try:
                encrypt_file(MODEL_PATH, ENCRYPTED_MODEL, state["key"])
                MODEL_PATH.unlink()
            except Exception as e:
                print(f"Cleanup failed: {e}")
            state["model_loaded"] = False
            input("Enter...")


async def road_scanner_flow(state: dict) -> None:
    if not ENCRYPTED_MODEL.exists():
        print("No encrypted model found.")
        input("Enter...")
        return

    data: Dict[str, str] = {}
    clear_screen()
    header(state)
    print(boxed("Road Scanner - Step 1/6", ["Leave blank for defaults"]))
    data["location"] = input("Location (e.g., 'I-95 NB mile 12'): ").strip() or "unspecified location"
    data["road_type"] = input("Road type (highway/urban/residential): ").strip() or "highway"
    data["weather"] = input("Weather/visibility: ").strip() or "clear"
    data["traffic"] = input("Traffic density (low/med/high): ").strip() or "low"
    data["obstacles"] = input("Reported obstacles: ").strip() or "none"
    data["sensor_notes"] = input("Sensor notes: ").strip() or "none"

    decrypt_file(ENCRYPTED_MODEL, MODEL_PATH, state["key"])
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as ex:
        try:
            system_text, prompt = build_road_scanner_prompt(data, include_system_entropy=True)
            print("Scanning...")
            raw = await loop.run_in_executor(
                ex,
                lambda: litert_classify_blocking(MODEL_PATH, prompt, system_text=system_text),
            )
            label = normalize_risk_label(raw)
            print("\n--- Road Scanner Result ---\n")
            print(color(label, fg=32 if label == "Low" else 33 if label == "Medium" else 31, bold=True))
            print("\nOptions: 1) Re-run with edits  2) Export to JSON  3) Save & return  4) Cancel")
            ch = input("Choose (1-4): ").strip()

            if ch == "1":
                print("Re-run: editing fields. Press Enter to keep current value.")
                for k in list(data.keys()):
                    v = input(f"{k} [{data[k]}]: ").strip()
                    if v:
                        data[k] = v
                system_text, prompt = build_road_scanner_prompt(data, include_system_entropy=True)
                raw = await loop.run_in_executor(
                    ex,
                    lambda: litert_classify_blocking(MODEL_PATH, prompt, system_text=system_text),
                )
                label = normalize_risk_label(raw)
                print(f"\n{label}")

            if ch in ("2", "3"):
                try:
                    await init_db(state["key"])
                    await log_interaction("ROAD_SCANNER_PROMPT:\n" + prompt, "ROAD_SCANNER_RESULT:\n" + label, state["key"])
                except Exception as e:
                    print(f"Failed to log: {e}")

            if ch == "2":
                outp = {
                    "input": data,
                    "prompt": prompt,
                    "result": label,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                fn = input("Filename to save JSON (default road_scan.json): ").strip() or "road_scan.json"
                Path(fn).write_text(json.dumps(outp, indent=2))
                print(f"Saved {fn}")
        finally:
            print("Re-encrypting model and removing plaintext...")
            try:
                encrypt_file(MODEL_PATH, ENCRYPTED_MODEL, state["key"])
                MODEL_PATH.unlink()
            except Exception as e:
                print(f"Cleanup error: {e}")
            input("Enter to return...")


async def db_viewer_flow(state: dict) -> None:
    if not DB_PATH.exists():
        print("No DB found.")
        input("Enter...")
        return

    page = 0
    per_page = 10
    search = None
    while True:
        rows = await fetch_history(state["key"], limit=per_page, offset=page * per_page, search=search)
        clear_screen()
        header(state)
        print(boxed(f"History (page {page + 1})", [f"Search: {search or '(none)'}", "Commands: n=next p=prev s=search q=quit"]))
        if not rows:
            print("No rows on this page.")
        else:
            for r in rows:
                print(f"[{r[0]}] {r[1]}\nQ: {r[2]}\nA: {r[3]}\n" + "-" * 60)
        cmd = input("cmd (n/p/s/q): ").strip().lower()
        if cmd == "n":
            page += 1
        elif cmd == "p" and page > 0:
            page -= 1
        elif cmd == "s":
            search = input("Enter search keyword (empty to clear): ").strip() or None
            page = 0
        else:
            break


def rekey_flow(state: dict) -> None:
    print("Rekey / Rotate Key")
    print(f"Current key file: {KEY_PATH}" if KEY_PATH.exists() else "No existing key file (creating new).")
    choice = input("1) New random key  2) Passphrase-derived  3) Cancel\nChoose: ").strip()
    if choice not in ("1", "2"):
        print("Canceled.")
        input("Enter...")
        return

    old_key = state["key"]
    tmp_model = MODELS_DIR / (MODEL_FILE + ".tmp")
    tmp_db = _temp_db_path()
    try:
        if ENCRYPTED_MODEL.exists():
            decrypt_file(ENCRYPTED_MODEL, tmp_model, old_key)
        if DB_PATH.exists():
            decrypt_file(DB_PATH, tmp_db, old_key)
    except Exception as e:
        print(f"Failed to decrypt existing data with current key: {e}")
        safe_cleanup([tmp_model, tmp_db])
        input("Enter...")
        return

    if choice == "1":
        new_key = AESGCM.generate_key(256)
        _write_key_file(new_key)
        print("New random key generated and saved.")
    else:
        pw = getpass.getpass("Enter new passphrase: ")
        pw2 = getpass.getpass("Confirm: ")
        if pw != pw2:
            print("Mismatch.")
            safe_cleanup([tmp_model, tmp_db])
            input("Enter...")
            return
        salt, derived = derive_key_from_passphrase(pw)
        _write_key_file(salt + derived)
        new_key = derived
        print("New passphrase-derived key saved (salt+derived).")

    try:
        if tmp_model.exists():
            encrypt_file(tmp_model, ENCRYPTED_MODEL, new_key)
        if tmp_db.exists():
            DB_PATH.write_bytes(aes_encrypt(tmp_db.read_bytes(), new_key))
    except Exception as e:
        print(f"Error during re-encryption: {e}")
    finally:
        safe_cleanup([tmp_model, tmp_db])
        raw = KEY_PATH.read_bytes()
        state["key"] = raw[16:48] if len(raw) >= 48 else raw[:32]
        print("Rekey attempt finished. Verify files manually.")
        input("Enter...")


def main_menu_loop(state: dict) -> None:
    options = [
        "Model Manager",
        "Chat with model",
        "Road Scanner",
        "View chat history",
        "Rekey / Rotate key",
        "Exit",
    ]
    while True:
        clear_screen()
        header(state)
        print()
        print(boxed("Main Menu", [f"{i + 1}) {opt}" for i, opt in enumerate(options)]))
        choice = options[read_menu_choice(len(options))]
        if choice == "Model Manager":
            model_manager(state)
        elif choice == "Chat with model":
            asyncio.run(chat_session(state))
        elif choice == "Road Scanner":
            asyncio.run(road_scanner_flow(state))
        elif choice == "View chat history":
            asyncio.run(db_viewer_flow(state))
        elif choice == "Rekey / Rotate key":
            rekey_flow(state)
        elif choice == "Exit":
            print("Goodbye.")
            return


def main() -> None:
    try:
        key = ensure_key_interactive()
    except Exception:
        key = get_or_create_key()
    state = {"key": key, "model_loaded": False}
    try:
        asyncio.run(init_db(state["key"]))
    except Exception:
        pass
    try:
        main_menu_loop(state)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        show_cursor()


if __name__ == "__main__":
    main()