#!/usr/bin/env python3
"""Run a persistent, localhost-only pilot. Never reads a production .env file."""
import argparse
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / ".pilot"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--no-seed", action="store_true", help="Open an existing pilot without adding example records")
    args = parser.parse_args()
    PILOT.mkdir(mode=0o700, exist_ok=True)
    secret_file = PILOT / "session-secret"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_urlsafe(48))
        secret_file.chmod(0o600)
    # Deliberately override inherited settings; this launcher is never a deploy tool.
    os.environ["DATABASE_URL"] = "sqlite:///" + str(PILOT / "workspace.db")
    os.environ["SESSION_SECRET"] = secret_file.read_text().strip()
    os.environ["HTTPS_ONLY"] = "0"
    os.environ["WORKSPACE_ENVIRONMENT"] = "local-pilot"
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("INTAKE_TOKEN", None)
    # A local demo must never inherit real mail or phone credentials.
    for key in list(os.environ):
        if key.startswith(("WEBSITE_", "GOOGLE_", "TWILIO_", "RESEND_", "COMMUNICATIONS_", "CAMPAIGN_")):
            os.environ.pop(key, None)
    sys.path.insert(0, str(ROOT / "backend"))
    from app import migrate
    from app.main import app
    from pilot_seed import seed

    migrate.upgrade()
    if args.no_seed:
        if not (PILOT / "seeded-v1").exists() and not (PILOT / "seeded-v2").exists():
            raise SystemExit("No existing pilot seed found; run without --no-seed first.")
    else:
        seed(app, PILOT)
    if args.seed_only:
        return
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        raise SystemExit("Build the frontend first: cd frontend && npm ci && npm run build")
    print(f"Local pilot: http://127.0.0.1:{args.port} — fictional data only", flush=True)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
