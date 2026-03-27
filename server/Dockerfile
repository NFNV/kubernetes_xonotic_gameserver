# Intended v1 deployment target: linux/amd64.
# On Apple Silicon, build and run local smoke tests with --platform linux/amd64.
FROM debian:bookworm-slim AS downloader

ARG XONOTIC_VERSION=0.8.6

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/xonotic.zip "https://dl.xonotic.org/xonotic-${XONOTIC_VERSION}.zip" \
    && curl -fsSL -o /tmp/xonotic.sha512 "https://dl.xonotic.org/xonotic-${XONOTIC_VERSION}.sha512" \
    && grep " xonotic-${XONOTIC_VERSION}.zip\$" /tmp/xonotic.sha512 > /tmp/xonotic.sha512.filtered \
    && cd /tmp \
    && sha512sum -c /tmp/xonotic.sha512.filtered \
    && mkdir -p /opt/xonotic-src \
    && unzip -q /tmp/xonotic.zip -d /opt/xonotic-src \
    && install_root="$(find /opt/xonotic-src -mindepth 1 -maxdepth 1 -type d | head -n 1)" \
    && mv "${install_root}" /opt/xonotic

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libcurl4 \
        libjpeg62-turbo \
        libpng16-16 \
        libsdl2-2.0-0 \
        libvorbisfile3 \
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --home-dir /home/xonotic --shell /usr/sbin/nologin xonotic

COPY --from=downloader /opt/xonotic /opt/xonotic
COPY config/server.cfg /opt/xonotic-config/server.cfg
COPY entrypoint.sh /entrypoint.sh

RUN test -x /opt/xonotic/xonotic-linux64-dedicated \
    && chmod 0755 /entrypoint.sh \
    && chown -R xonotic:xonotic /opt/xonotic /opt/xonotic-config /home/xonotic

USER xonotic
WORKDIR /opt/xonotic

ENV HOME=/home/xonotic
ENV XONOTIC_HOME=/opt/xonotic
ENV XONOTIC_USERDIR=/home/xonotic/.xonotic
ENV XONOTIC_PORT=26000
ENV XONOTIC_HOSTNAME="Xonotic Dedicated Server"
ENV XONOTIC_MAXPLAYERS=12
ENV XONOTIC_PUBLIC=0
ENV XONOTIC_MOTD="Welcome to the Xonotic dedicated server"
ENV XONOTIC_LOG_FILE=server.log

VOLUME ["/home/xonotic/.xonotic"]

EXPOSE 26000/udp

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
