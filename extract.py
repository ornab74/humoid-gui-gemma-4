#!/usr/bin/env python3
from pathlib import Path
import argparse, getpass, hmac, os, struct
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

STREAM_MAGIC = b"HGGM2"
KEY_FILE_MAGIC = b"HMK2"
KEY_FILE_VERSION = 2
KEY_FILE_SALT_BYTES = 16
KEY_FILE_NONCE_BYTES = 12
KEY_FILE_MASTER_BYTES = 32
LEGACY_PBKDF2_ITERATIONS = 200_000

def derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=max(100_000, int(iterations)),
    )
    return kdf.derive(password.encode("utf-8"))

def unlock_key_file(key_path: Path, password: str) -> bytes:
    data = key_path.read_bytes()

    # New wrapped master-key format: HMK2 + version + iterations + salt + nonce + ciphertext
    if data.startswith(KEY_FILE_MAGIC):
        minimum = len(KEY_FILE_MAGIC) + 1 + 4 + KEY_FILE_SALT_BYTES + KEY_FILE_NONCE_BYTES + 16
        if len(data) < minimum:
            raise ValueError("Invalid .enc_key file")

        version = data[len(KEY_FILE_MAGIC)]
        if version != KEY_FILE_VERSION:
            raise ValueError(f"Unsupported key file version: {version}")

        cursor = len(KEY_FILE_MAGIC) + 1
        iterations = struct.unpack(">I", data[cursor:cursor + 4])[0]
        cursor += 4

        salt = data[cursor:cursor + KEY_FILE_SALT_BYTES]
        cursor += KEY_FILE_SALT_BYTES

        nonce = data[cursor:cursor + KEY_FILE_NONCE_BYTES]
        cursor += KEY_FILE_NONCE_BYTES

        ciphertext = data[cursor:]
        wrapping_key = derive_key(password, salt, iterations)

        try:
            master_key = AESGCM(wrapping_key).decrypt(nonce, ciphertext, KEY_FILE_MAGIC)
        except Exception as exc:
            raise ValueError("Incorrect password, or .enc_key does not match this .aes file") from exc

        if len(master_key) != KEY_FILE_MASTER_BYTES:
            raise ValueError("Unwrapped master key has invalid length")
        return master_key

    # Older passphrase-derived key file: salt + derived key
    if len(data) >= 48:
        salt = data[:16]
        stored_key = data[16:48]
        derived_key = derive_key(password, salt, LEGACY_PBKDF2_ITERATIONS)
        if not hmac.compare_digest(stored_key, derived_key):
            raise ValueError("Incorrect password")
        return derived_key

    # Old raw key file, no password wrapping
    if len(data) >= 32:
        print("Using legacy raw key file; password was not needed for .enc_key")
        return data[:32]

    raise ValueError("Invalid .enc_key file")

def decrypt_model(src: Path, dest: Path, key: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)

    with src.open("rb") as f:
        header = f.read(len(STREAM_MAGIC))

    # Stream format from this app: HGGM2 + 12-byte nonce + ciphertext + 16-byte GCM tag
    if header == STREAM_MAGIC:
        tag_size = 16
        header_size = len(STREAM_MAGIC) + 12
        total_size = src.stat().st_size
        if total_size < header_size + tag_size:
            raise ValueError("Encrypted model file is too small")

        with src.open("rb") as in_f:
            magic = in_f.read(len(STREAM_MAGIC))
            if magic != STREAM_MAGIC:
                raise ValueError("Unknown encrypted model format")

            nonce = in_f.read(12)

            in_f.seek(total_size - tag_size)
            tag = in_f.read(tag_size)

            in_f.seek(header_size)
            remaining = total_size - header_size - tag_size

            decryptor = Cipher(
                algorithms.AES(key),
                modes.GCM(nonce, tag),
            ).decryptor()

            with dest.open("wb") as out_f:
                while remaining > 0:
                    chunk = in_f.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    out_f.write(decryptor.update(chunk))
                out_f.write(decryptor.finalize())

    else:
        # Older small-file format: 12-byte nonce + AESGCM ciphertext
        data = src.read_bytes()
        nonce, ciphertext = data[:12], data[12:]
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        dest.write_bytes(plaintext)

    os.chmod(dest, 0o600)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="infile", default="models/gemma-4-E2B-it.litertlm.aes")
    parser.add_argument("--out", dest="outfile", default="models/gemma-4-E2B-it.litertlm")
    parser.add_argument("--key", default=".enc_key")
    args = parser.parse_args()

    src = Path(args.infile)
    dest = Path(args.outfile)
    key_path = Path(args.key)

    if not src.exists():
        raise SystemExit(f"Missing encrypted model: {src}")
    if not key_path.exists():
        raise SystemExit(f"Missing key file: {key_path}")

    password = getpass.getpass("Password: ")
    key = unlock_key_file(key_path, password)
    decrypt_model(src, dest, key)

    print(f"Decrypted model written to: {dest}")
    print(f"Size: {dest.stat().st_size} bytes")

if __name__ == "__main__":
    main()
