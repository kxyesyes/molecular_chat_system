#!/bin/sh
set +x
set -eu
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

PORT_RANGE=40000:60000
WAIT_SECONDS=5
NETWORK_NAME=medchat-opensandbox
BRIDGE_INTERFACE=br-medchat-sbox

fail() {
    printf '%s\n' \
        'opensandbox_firewall=failed code=firewall_configuration_failed' >&2
    exit 1
}

ensure_network() {
    command -v docker >/dev/null 2>&1 || fail
    if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
        docker network create --driver bridge --ipv6 \
            --opt "com.docker.network.bridge.name=$BRIDGE_INTERFACE" \
            "$NETWORK_NAME" >/dev/null 2>&1 || fail
    fi

    network_driver=$(
        docker network inspect --format '{{.Driver}}' "$NETWORK_NAME" 2>/dev/null
    ) || fail
    network_options=$(
        docker network inspect --format '{{json .Options}}' \
            "$NETWORK_NAME" 2>/dev/null
    ) || fail
    network_ipv6=$(
        docker network inspect --format '{{.EnableIPv6}}' \
            "$NETWORK_NAME" 2>/dev/null
    ) || fail
    [ "$network_driver" = bridge ] || fail
    [ "$network_options" = \
        '{"com.docker.network.bridge.name":"br-medchat-sbox"}' ] || fail
    [ "$network_ipv6" = true ] || fail
    [ -d "/sys/class/net/$BRIDGE_INTERFACE" ] || fail
}

configure_family() {
    tool=$1
    loopback_source=$2

    command -v "$tool" >/dev/null 2>&1 || fail
    "$tool" -w "$WAIT_SECONDS" -S DOCKER-USER >/dev/null 2>&1 || fail

    drop_rule="-A DOCKER-USER -o $BRIDGE_INTERFACE ! -s $loopback_source -p tcp -m conntrack --ctstatus DNAT --ctdir ORIGINAL --ctorigdstport $PORT_RANGE -j DROP"

    first_rule=$(
        "$tool" -w "$WAIT_SECONDS" -S DOCKER-USER 1 2>/dev/null
    ) || first_rule=
    if [ "$first_rule" != "$drop_rule" ]; then
        "$tool" -w "$WAIT_SECONDS" -I DOCKER-USER 1 \
            -o "$BRIDGE_INTERFACE" ! -s "$loopback_source" \
            -p tcp -m conntrack --ctstatus DNAT --ctdir ORIGINAL \
            --ctorigdstport "$PORT_RANGE" -j DROP >/dev/null 2>&1 || fail
    fi

    "$tool" -w "$WAIT_SECONDS" -C DOCKER-USER \
        -o "$BRIDGE_INTERFACE" ! -s "$loopback_source" \
        -p tcp -m conntrack --ctstatus DNAT --ctdir ORIGINAL \
        --ctorigdstport "$PORT_RANGE" -j DROP >/dev/null 2>&1 || fail
    first_rule=$(
        "$tool" -w "$WAIT_SECONDS" -S DOCKER-USER 1 2>/dev/null
    ) || fail
    [ "$first_rule" = "$drop_rule" ] || fail
}

[ "$#" -eq 0 ] || fail
ensure_network
configure_family iptables 127.0.0.0/8
configure_family ip6tables ::1/128
printf '%s\n' 'opensandbox_firewall=passed'
