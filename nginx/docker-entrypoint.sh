#!/bin/sh
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# Writes certificate/key/truststore material (injected as environment
# variables from Secrets Manager via the ECS task definition's `secrets`
# property) to files nginx can load, then execs the given command (nginx).
set -eu

CERT_DIR=/etc/nginx/certs
mkdir -p "$CERT_DIR"

: "${SERVER_CERT_PEM:?SERVER_CERT_PEM env var is required}"
: "${SERVER_KEY_PEM:?SERVER_KEY_PEM env var is required}"
: "${CA_BUNDLE_PEM:?CA_BUNDLE_PEM env var is required}"

printf '%s' "$SERVER_CERT_PEM" > "$CERT_DIR/server.crt"
printf '%s' "$SERVER_KEY_PEM" > "$CERT_DIR/server.key"
printf '%s' "$CA_BUNDLE_PEM" > "$CERT_DIR/ca_bundle.pem"

# 600 = owner read/write only. The TLS private key must not be readable by
# any other user/process in the container; nginx's master process (which
# reads this file before dropping privileges) runs as the file's owner.
chmod 600 "$CERT_DIR/server.key"

exec "$@"
