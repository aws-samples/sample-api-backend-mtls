# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# nginx sidecar: terminates the inbound mTLS handshake for the Fargate
# task, validates the client certificate against the truststore (the CA
# chain), and proxies the request to the validator app container over
# plain HTTP on localhost, forwarding the verified client certificate (and
# parsed subject/issuer/serial) as headers.
FROM public.ecr.aws/docker/library/nginx:1.27-alpine

COPY nginx.conf /etc/nginx/nginx.conf

# Certificate material is written to /etc/nginx/certs at container start by
# the entrypoint below, sourced from environment variables populated from
# Secrets Manager via the ECS task definition's `secrets` property.
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh \
    # Run nginx as the non-root "nginx" user already created by the base
    # image (its worker processes already run as this user by default).
    # nginx still needs to bind to the privileged port 443, so grant the
    # binary CAP_NET_BIND_SERVICE instead of running the master process as
    # root -- this avoids changing the listen port, which would also
    # require updating the ECS task definition, target group, and
    # security group that all currently assume port 443.
    && apk add --no-cache libcap \
    && setcap 'cap_net_bind_service=+ep' /usr/sbin/nginx \
    && mkdir -p /etc/nginx/certs \
    && chown -R nginx:nginx /etc/nginx/certs \
    # nginx's default cache/temp directories are owned by root in the
    # base image; the worker processes create/write to these at startup,
    # so the non-root user needs ownership. /run and /var/run are
    # remounted as fresh, root-owned tmpfs at container start regardless
    # of image-build-time chown, which is why nginx.conf points `pid` at
    # /tmp instead (already world-writable, part of the image filesystem).
    && chown -R nginx:nginx /var/cache/nginx /var/log/nginx

USER nginx

EXPOSE 443

# mTLS is enforced for the entire server block (ssl_verify_client on), so
# a real HTTP health check would require a client certificate. Just
# confirm the TLS port is accepting TCP connections instead.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD nc -z 127.0.0.1 443 || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
