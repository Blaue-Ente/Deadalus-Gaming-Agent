FROM python:3.12-alpine

RUN apk add --no-cache nodejs npm \
    && addgroup -g 10000 sandbox \
    && adduser -D -u 10000 -G sandbox -h /work -s /sbin/nologin sandbox \
    && mkdir -p /work \
    && chown sandbox:sandbox /work

USER sandbox
WORKDIR /work
