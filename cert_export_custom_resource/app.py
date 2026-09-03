# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
CloudFormation custom resource that exports the Fargate server certificate
(issued by the private CA) from ACM and stores its certificate, decrypted
private key, and the CA bundle (truststore) in Secrets Manager, so the
ECS task definition can inject them into the nginx sidecar container via
the `secrets` property.

ACM only allows exporting the private key for certificates issued by
AWS Private CA (never for public ACM certificates) -- this is exactly why
the Fargate server certificate must be a private-CA-issued certificate
rather than a public one; see README for details.

On Create/Update: calls acm:ExportCertificate with a randomly generated
passphrase, decrypts the PKCS#8 private key locally with `cryptography`,
and writes three secrets:
  - <prefix>/server-cert   (PEM certificate)
  - <prefix>/server-key    (decrypted PEM private key)
  - <prefix>/ca-bundle     (PEM CA chain, supplied by the template)

On Delete: no-op (the secrets are separate CloudFormation resources and
are deleted independently).

Written for the Lambda python3.14 managed runtime. Uses boto3 (already
available in the Lambda runtime) plus the `cryptography` package to
decrypt the exported private key.
"""

import json
import logging
import secrets
import string
import urllib.request

import boto3
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger()
logger.setLevel("INFO")

acm = boto3.client("acm")
secretsmanager = boto3.client("secretsmanager")

# Length of the random passphrase used to encrypt the private key that ACM
# returns from export_certificate. 32 characters from a 62-character
# alphabet (upper+lower+digits) gives ~190 bits of entropy, well above the
# minimum needed to make brute-forcing the passphrase infeasible for the
# few seconds the encrypted key exists in memory before being decrypted
# and re-encrypted (PKCS8, no passphrase) for storage in Secrets Manager.
PASSPHRASE_LENGTH = 32


def _send_response(event, context, status, reason=None, data=None, physical_resource_id=None):
    response_body = {
        "Status": status,
        "Reason": reason or f"See CloudWatch log stream {context.log_stream_name}",
        "PhysicalResourceId": physical_resource_id or event.get("LogicalResourceId"),
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": data or {},
    }
    encoded_body = json.dumps(response_body).encode("utf-8")

    request = urllib.request.Request(
        url=event["ResponseURL"],
        data=encoded_body,
        method="PUT",
        headers={"Content-Type": "", "Content-Length": str(len(encoded_body))},
    )
    with urllib.request.urlopen(request) as response:
        logger.info("CloudFormation response status: %s", response.status)


def _generate_passphrase() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(PASSPHRASE_LENGTH))


def _put_secret(secret_id: str, secret_string: str) -> None:
    secretsmanager.put_secret_value(SecretId=secret_id, SecretString=secret_string)


def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps({k: v for k, v in event.items() if k != "ResourceProperties"}))

    request_type = event["RequestType"]
    props = event["ResourceProperties"]

    certificate_arn = props["CertificateArn"]
    server_cert_secret_id = props["ServerCertSecretId"]
    server_key_secret_id = props["ServerKeySecretId"]
    ca_bundle_secret_id = props["CaBundleSecretId"]
    ca_bundle_pem = props["CaBundlePem"]

    try:
        if request_type in ("Create", "Update"):
            passphrase = _generate_passphrase()
            export = acm.export_certificate(
                CertificateArn=certificate_arn,
                Passphrase=passphrase.encode("utf-8"),
            )

            encrypted_key_pem = export["PrivateKey"].encode("utf-8")
            private_key = serialization.load_pem_private_key(
                encrypted_key_pem,
                password=passphrase.encode("utf-8"),
            )
            decrypted_key_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode("utf-8")

            # nginx's `ssl_certificate` directive needs the full chain
            # (leaf + intermediate/root) in one file to present it during
            # the TLS handshake -- ACM's `Certificate` field is leaf-only.
            # Without this, downstream TLS clients that check for a valid
            # chain to a root CA (e.g. API Gateway, even with
            # insecureSkipVerification=true) fail with something like
            # "unable to find valid certification path to requested target".
            full_chain_pem = export["Certificate"] + "\n" + ca_bundle_pem

            _put_secret(server_cert_secret_id, full_chain_pem)
            _put_secret(server_key_secret_id, decrypted_key_pem)
            _put_secret(ca_bundle_secret_id, ca_bundle_pem)

            _send_response(
                event,
                context,
                "SUCCESS",
                physical_resource_id=f"cert-export-{certificate_arn.split('/')[-1]}",
            )
        elif request_type == "Delete":
            # Secrets are separate CloudFormation resources; nothing to
            # clean up here.
            _send_response(event, context, "SUCCESS", physical_resource_id=event.get("PhysicalResourceId"))
        else:
            _send_response(event, context, "FAILED", reason=f"Unknown RequestType: {request_type}")
    except Exception:  # noqa: BLE001 - must always signal CloudFormation
        # Full exception (which may include ARNs, file paths, or AWS
        # service error messages) is logged server-side only. The
        # CloudFormation response `reason` field is visible in the stack
        # events/console, so it gets a generic message plus a pointer to
        # the log stream for anyone who needs the details.
        logger.exception("Custom resource failed.")
        try:
            _send_response(
                event,
                context,
                "FAILED",
                reason=f"Certificate export failed. See CloudWatch log stream {context.log_stream_name} for details.",
            )
        except Exception:  # noqa: BLE001 - never let callback delivery mask the original failure
            # _send_response's urlopen PUT to the CloudFormation presigned
            # callback URL can itself fail (network error, timeout). If
            # that exception were allowed to propagate here, CloudFormation
            # would never receive a FAILED signal at all and the stack
            # operation would hang until the custom resource times out,
            # rather than failing fast with the original error already
            # captured above by logger.exception.
            logger.exception("Failed to deliver the CloudFormation FAILED response.")
