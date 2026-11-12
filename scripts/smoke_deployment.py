"""Check public liveness, DB readiness, release identity and frontend CORS."""
import json
import os
import ssl
import urllib.request


def main():
    base = os.getenv("HEYHANZI_DEPLOY_BASE_URL", "https://heyhanzi-backend.fly.dev").rstrip("/")
    origin = os.getenv("HEYHANZI_DEPLOY_FRONTEND_ORIGIN", "https://heyhanzi.com")
    context = ssl.create_default_context(cafile=os.getenv("SSL_CERT_FILE"))
    for path, status in (("/health", "ok"), ("/ready", "ready")):
        request = urllib.request.Request(base + path, headers={"Origin": origin})
        with urllib.request.urlopen(request, context=context, timeout=30) as response:
            body = json.load(response)
            assert response.status == 200 and body["status"] == status
            assert response.headers["Access-Control-Allow-Origin"] == origin
            assert response.headers["X-Request-ID"]
            if path == "/health" and os.getenv("RELEASE_SHA"):
                assert body["releaseSha"] == os.environ["RELEASE_SHA"]
    print("Liveness, readiness, CORS and release identity checks passed.")


if __name__ == "__main__":
    main()
