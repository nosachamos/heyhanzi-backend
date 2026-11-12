import argparse
import os
from pathlib import Path

import uvicorn

from app.config_loader import env
from app.environment import is_development


def run():
    os.chdir(Path(__file__).resolve().parent)
    parser = argparse.ArgumentParser()
    parser.add_argument("--https", action="store_true")
    args = parser.parse_args()
    options = {}
    if args.https or os.getenv("LOCAL_HTTPS", "").lower() == "true":
        if not all(Path(f".cert/{name}.pem").is_file() for name in ("cert", "key")):
            parser.error("Local HTTPS certificates missing. Run make certificates.")
        options.update(ssl_keyfile=".cert/key.pem", ssl_certfile=".cert/cert.pem")
    development = is_development(env.config)
    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")),
        lifespan="on", reload=development, reload_dirs=["app"] if development else None,
        workers=1, proxy_headers=False, access_log=False, timeout_graceful_shutdown=20,
        **options,
    )


if __name__ == "__main__":
    run()
