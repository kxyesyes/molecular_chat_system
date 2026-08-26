#!/bin/sh
set +x
set -eu
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

PORT_RANGE=40000:60000
WAIT_SECONDS=5

fail() {
    printf '%s\n' \
        'opensandbox_firewall=failed code=firewall_configuration_failed' >&2
    exit 1
}

configure_family() {
    tool=$1
    loopback_source=$2

    command -v "$tool" >/dev/null 2>&1 || fail
    "$tool" -w "$WAIT_SECONDS" -S DOCKER-USER >/dev/null 2>&1 || fail

    allow_rule="-A DOCKER-USER -s $loopback_source -p tcp -m conntrack --ctdir ORIGINAL --ctorigdstport $PORT_RANGE -j RETURN"
    drop_rule="-A DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdstport $PORT_RANGE -j DROP"

    second_rule=$(
        "$tool" -w "$WAIT_SECONDS" -S DOCKER-USER 2 2>/dev/null
    ) || second_rule=
    if [ "$second_rule" != "$drop_rule" ]
    then
        "$tool" -w "$WAIT_SECONDS" -I DOCKER-USER 1 \
            -p tcp -m conntrack --ctdir ORIGINAL \
            --ctorigdstport "$PORT_RANGE" -j DROP >/dev/null 2>&1 || fail
    fi

    first_rule=$(
        "$tool" -w "$WAIT_SECONDS" -S DOCKER-USER 1 2>/dev/null
    ) || first_rule=
    if [ "$first_rule" != "$allow_rule" ]; then
        "$tool" -w "$WAIT_SECONDS" -I DOCKER-USER 1 \
            -s "$loopback_source" -p tcp -m conntrack --ctdir ORIGINAL \
            --ctorigdstport "$PORT_RANGE" -j RETURN >/dev/null 2>&1 || fail
    fi

    "$tool" -w "$WAIT_SECONDS" -C DOCKER-USER \
        -s "$loopback_source" -p tcp -m conntrack --ctdir ORIGINAL \
        --ctorigdstport "$PORT_RANGE" -j RETURN >/dev/null 2>&1 || fail
    "$tool" -w "$WAIT_SECONDS" -C DOCKER-USER \
        -p tcp -m conntrack --ctdir ORIGINAL \
        --ctorigdstport "$PORT_RANGE" -j DROP >/dev/null 2>&1 || fail
    first_rule=$(
        "$tool" -w "$WAIT_SECONDS" -S DOCKER-USER 1 2>/dev/null
    ) || fail
    second_rule=$(
        "$tool" -w "$WAIT_SECONDS" -S DOCKER-USER 2 2>/dev/null
    ) || fail
    [ "$first_rule" = "$allow_rule" ] || fail
    [ "$second_rule" = "$drop_rule" ] || fail
}

[ "$#" -eq 0 ] || fail
configure_family iptables 127.0.0.0/8
configure_family ip6tables ::1/128
printf '%s\n' 'opensandbox_firewall=passed'
