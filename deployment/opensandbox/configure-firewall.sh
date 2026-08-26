#!/bin/sh
set +x
set -eu
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

PORT_RANGE=40000:60000
WAIT_SECONDS=5
CHAIN=MEDCHAT-OPENSANDBOX
MAX_LEGACY_RULES=64

fail() {
    printf '%s\n' \
        'opensandbox_firewall=failed code=firewall_configuration_failed' >&2
    exit 1
}

migration_fail() {
    printf '%s\n' \
        'opensandbox_firewall=failed code=firewall_migration_failed' >&2
    exit 1
}

delete_all_legacy_rules() {
    tool=$1
    shift
    deleted=0
    while "$tool" -w "$WAIT_SECONDS" -C DOCKER-USER "$@" \
        >/dev/null 2>&1
    do
        deleted=$((deleted + 1))
        [ "$deleted" -le "$MAX_LEGACY_RULES" ] || migration_fail
        "$tool" -w "$WAIT_SECONDS" -D DOCKER-USER "$@" \
            >/dev/null 2>&1 || migration_fail
    done
}

migrate_legacy_rules() {
    tool=$1
    loopback_source=$2

    "$tool" -w "$WAIT_SECONDS" -S DOCKER-USER >/dev/null 2>&1 || fail
    delete_all_legacy_rules "$tool" \
        -s "$loopback_source" -p tcp -m conntrack --ctdir ORIGINAL \
        --ctorigdstport "$PORT_RANGE" -j RETURN
    delete_all_legacy_rules "$tool" \
        -p tcp -m conntrack --ctdir ORIGINAL \
        --ctorigdstport "$PORT_RANGE" -j DROP
    delete_all_legacy_rules "$tool" \
        -o br-medchat-sbox ! -s "$loopback_source" \
        -p tcp -m conntrack --ctstatus DNAT --ctdir ORIGINAL \
        --ctorigdstport "$PORT_RANGE" -j DROP
}

configure_ingress_gate() {
    tool=$1
    command -v "$tool" >/dev/null 2>&1 || fail
    command -v grep >/dev/null 2>&1 || fail
    migrate_legacy_rules "$tool" "$2"
    "$tool" -w "$WAIT_SECONDS" -t raw -S PREROUTING \
        >/dev/null 2>&1 || fail

    chain_state=$(
        "$tool" -w "$WAIT_SECONDS" -t raw -S "$CHAIN" 2>/dev/null
    ) || chain_state=
    empty_chain="-N $CHAIN"
    expected_chain="-N $CHAIN
-A $CHAIN -j DROP"
    if [ -z "$chain_state" ]; then
        "$tool" -w "$WAIT_SECONDS" -t raw -N "$CHAIN" \
            >/dev/null 2>&1 || fail
        chain_state=$empty_chain
    fi
    if [ "$chain_state" = "$empty_chain" ]; then
        "$tool" -w "$WAIT_SECONDS" -t raw -A "$CHAIN" -j DROP \
            >/dev/null 2>&1 || fail
    elif [ "$chain_state" != "$expected_chain" ]; then
        fail
    fi

    jump_rule="-A PREROUTING ! -i lo -m addrtype --dst-type LOCAL -p tcp -m tcp --dport $PORT_RANGE -j $CHAIN"
    raw_state=$(
        "$tool" -w "$WAIT_SECONDS" -t raw -S 2>/dev/null
    ) || fail
    exact_jumps=$(
        printf '%s\n' "$raw_state" | grep -Fxc -- "$jump_rule" || :
    )
    managed_jumps=$(
        printf '%s\n' "$raw_state" | grep -Ec -- "-[jg] $CHAIN($| )" || :
    )
    [ "$exact_jumps" -eq "$managed_jumps" ] || fail
    [ "$exact_jumps" -le 1 ] || fail
    if [ "$exact_jumps" -eq 0 ]; then
        "$tool" -w "$WAIT_SECONDS" -t raw -I PREROUTING 1 \
            ! -i lo -m addrtype --dst-type LOCAL \
            -p tcp -m tcp --dport "$PORT_RANGE" -j "$CHAIN" \
            >/dev/null 2>&1 || fail
    fi

    "$tool" -w "$WAIT_SECONDS" -t raw -C PREROUTING \
        ! -i lo -m addrtype --dst-type LOCAL \
        -p tcp -m tcp --dport "$PORT_RANGE" -j "$CHAIN" \
        >/dev/null 2>&1 || fail
    first_rule=$(
        "$tool" -w "$WAIT_SECONDS" -t raw -S PREROUTING 1 2>/dev/null
    ) || fail
    final_chain=$(
        "$tool" -w "$WAIT_SECONDS" -t raw -S "$CHAIN" 2>/dev/null
    ) || fail
    [ "$first_rule" = "$jump_rule" ] || fail
    [ "$final_chain" = "$expected_chain" ] || fail
}

[ "$#" -eq 0 ] || fail
configure_ingress_gate iptables 127.0.0.0/8
configure_ingress_gate ip6tables ::1/128
printf '%s\n' 'opensandbox_firewall=passed'
