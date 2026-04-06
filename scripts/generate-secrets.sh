#!/usr/bin/env bash
# Emit JWT_SECRET and WEBHOOK_SECRET_FERNET_KEY for .env (Docker / local).
# Typical: ./scripts/generate-secrets.sh >> .env
set -euo pipefail

printf 'JWT_SECRET=%s\n' "$(openssl rand -base64 48)"
printf 'WEBHOOK_SECRET_FERNET_KEY=%s\n' "$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
