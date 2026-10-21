FROM docker.io/library/alpine:3
RUN apk add --no-cache tor \
 && addgroup -g 10001 -S onion \
 && adduser -S -D -H -u 10001 -G onion onion \
 && mkdir -p /var/lib/tor \
 && chown 10001:10001 /var/lib/tor \
 && chmod 700 /var/lib/tor
USER 10001:10001
ENTRYPOINT ["tor", "-f", "/etc/tor/torrc"]
