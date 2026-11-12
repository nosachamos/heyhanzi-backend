"""Container-local readiness check with optional local-CA TLS verification."""
import argparse
import ssl
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("--https", action="store_true")
args = parser.parse_args()
context = ssl.create_default_context(cafile=".cert/rootCA.pem") if args.https else None
scheme = "https" if args.https else "http"
with urllib.request.urlopen(f"{scheme}://localhost:8000/ready", context=context, timeout=4) as response:
    if response.status != 200:
        raise SystemExit(1)
