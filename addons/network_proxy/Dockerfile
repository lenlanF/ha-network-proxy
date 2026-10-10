FROM docker.io/metacubex/mihomo:v1.19.12 AS core
FROM python:3.13-alpine
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}"
RUN apk add --no-cache ca-certificates iproute2 iptables nftables && pip install --no-cache-dir 'PyYAML>=6,<7'
COPY --from=core /mihomo /usr/local/bin/mihomo
COPY LICENSE.mihomo THIRD_PARTY.md /usr/share/licenses/mihomo/
COPY generate.py /generate.py
COPY manager.py /manager.py
COPY service.py /service.py
COPY discovery.py /discovery.py
COPY subscription.py profiles.py traffic.py /
COPY web /web
COPY run.sh /run.sh
RUN chmod +x /run.sh
CMD ["/bin/sh", "/run.sh"]
