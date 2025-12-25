#!/usr/bin/env bash
set -euo pipefail

IP="${1:-}"
if [[ -z "$IP" ]]; then
  echo "Usage: $0 <PUBLIC_IP>"
  exit 1
fi

OUT_DIR="$(dirname "$0")/certs"
mkdir -p "$OUT_DIR"

OPENSSL_CONF="$OUT_DIR/openssl.cnf"
cat > "$OPENSSL_CONF" <<EOF
[ req ]
default_bits       = 2048
default_md         = sha256
prompt             = no
distinguished_name = dn
req_extensions     = v3_req

[ dn ]
CN = $IP

[ v3_req ]
subjectAltName = @alt_names

[ alt_names ]
IP.1 = $IP
EOF

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout "$OUT_DIR/selfsigned.key" \
  -out "$OUT_DIR/selfsigned.crt" \
  -config "$OPENSSL_CONF"

echo "Self-signed certificate generated in $OUT_DIR:"
ls -l "$OUT_DIR"/selfsigned.*
echo "Set WEBHOOK_BASE_URL=https://$IP and WEBHOOK_SELF_SIGNED_CERT_PATH=/app/certs/selfsigned.crt in .env"


