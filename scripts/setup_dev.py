"""Create local secrets without overwriting an existing environment or signing key."""

import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

root = Path(__file__).resolve().parent.parent
os.umask(0o077)
data = root / ".data"
data.mkdir(mode=0o700, exist_ok=True)
(data / "emails").mkdir(mode=0o700, exist_ok=True)
env = root / ".env"
if not env.exists():
    env.write_text(
        (root / ".env.example")
        .read_text()
        .replace("DJANGO_SECRET_KEY=\n", "DJANGO_SECRET_KEY=" + secrets.token_urlsafe(64) + "\n")
    )
key = data / "oidc-private.pem"
if not key.exists():
    key.write_bytes(
        rsa.generate_private_key(public_exponent=65537, key_size=3072).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
print("Local .env, private signing key, and email directory are ready. Existing files preserved.")
