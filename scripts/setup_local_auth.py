"""Generate local-only auth keys once, without printing or replacing existing keys."""
import base64
import os
import secrets
from pathlib import Path

path = Path(__file__).resolve().parents[1] / ".env"
existing = path.read_text() if path.exists() else ""
names = {line.split("=", 1)[0].strip() for line in existing.splitlines() if "=" in line and not line.startswith("#")}
with path.open("a") as stream:
    os.chmod(path, 0o600)
    for name in ("AUTH_SECRET_KEY", "AUTH_AUDIT_HASH_SECRET", "AUTH_TOKEN_ENCRYPTION_KEY"):
        if name not in names:
            value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
            stream.write(f"\n{name}={value}\n")
print("Local auth keys are configured in the ignored .env.")
