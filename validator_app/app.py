# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Validator app for the API Gateway outbound mTLS demo (NLB + ECS Fargate
backend variant).

Runs as the second container in the Fargate task, behind the nginx
sidecar. nginx terminates the inbound mTLS handshake and enforces
chain-of-trust validation against the truststore (ssl_verify_client on);
by the time a request reaches this app, TLS/CA validation has already
succeeded. This app receives the verified client certificate's details as
headers (forwarded by nginx) and performs additional application-level
checks:

  - Confirms a client certificate was actually presented/forwarded.
  - Re-parses the PEM certificate (nginx forwards it URL-escaped via
    $ssl_client_escaped_cert) with the `cryptography` library and
    re-checks the validity window.
  - Returns a structured 200/401 JSON response.

Implemented with only the Python standard library (http.server) plus
`cryptography` for certificate parsing -- no web framework needed for a
single validation endpoint. Runs on Python 3.14.
"""

import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.x509.oid import NameOID

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("validator")

LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))


def _get_common_name(cert: x509.Certificate) -> str | None:
    attributes = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return attributes[0].value if attributes else None


class ValidatorHandler(BaseHTTPRequestHandler):
    server_version = "MtlsValidator/1.0"

    def _respond_json(self, status_code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _handle(self) -> None:
        # nginx only proxies a request here once ssl_verify_client has
        # already succeeded, but we still defensively check the headers
        # are present (e.g. in case of direct access misconfiguration).
        verify_status = self.headers.get("X-Client-Cert-Verify")
        escaped_cert = self.headers.get("X-Client-Cert-Escaped")

        if not escaped_cert or verify_status != "SUCCESS":
            logger.warning("No verified client certificate forwarded by nginx.")
            self._respond_json(
                401,
                {
                    "message": "mTLS client certificate not found or not verified. "
                    "This endpoint requires mutual TLS.",
                },
            )
            return

        pem = urllib.parse.unquote(escaped_cert)

        try:
            certificate = x509.load_pem_x509_certificate(pem.encode("utf-8"), default_backend())
        except ValueError:
            # Full exception (may include internal cryptography library
            # details) is logged server-side only; the client only sees a
            # generic message.
            logger.exception("Failed to parse forwarded client certificate PEM.")
            self._respond_json(400, {"message": "Unable to parse client certificate."})
            return

        now = datetime.now(timezone.utc)
        not_before = certificate.not_valid_before_utc
        not_after = certificate.not_valid_after_utc

        if now < not_before or now > not_after:
            logger.warning(
                "Client certificate outside validity window: notBefore=%s notAfter=%s now=%s",
                not_before,
                not_after,
                now,
            )
            self._respond_json(
                401,
                {
                    "message": "Client certificate is expired or not yet valid.",
                    "notBefore": not_before.isoformat(),
                    "notAfter": not_after.isoformat(),
                },
            )
            return

        common_name = _get_common_name(certificate)

        logger.info("Client certificate validated successfully for CN '%s'.", common_name)
        self._respond_json(
            200,
            {
                "message": "mTLS client certificate validated successfully.",
                "commonName": common_name,
                "issuerDN": self.headers.get("X-Client-Cert-Issuer-DN"),
                "serialNumber": self.headers.get("X-Client-Cert-Serial"),
                "notBefore": not_before.isoformat(),
                "notAfter": not_after.isoformat(),
            },
        )

    def do_GET(self) -> None:  # noqa: N802 - required BaseHTTPRequestHandler name
        if self.path == "/healthz":
            self._respond_json(200, {"status": "ok"})
            return
        self._handle()

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        logger.info("%s - %s", self.address_string(), format % args)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), ValidatorHandler)
    logger.info("Validator app listening on port %s", LISTEN_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
