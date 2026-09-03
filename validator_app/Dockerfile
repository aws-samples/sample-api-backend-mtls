# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
FROM public.ecr.aws/docker/library/python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Run as a non-root user rather than the image default (root).
RUN useradd --system --no-create-home --shell /usr/sbin/nologin validator \
    && chown -R validator:validator /app
USER validator

ENV LISTEN_PORT=8080
EXPOSE 8080

# The app exposes GET /healthz (see app.py) specifically for this check --
# it doesn't require a client certificate, unlike the main endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys,os; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('LISTEN_PORT','8080')+'/healthz', timeout=3).status==200 else 1)"

CMD ["python", "app.py"]
