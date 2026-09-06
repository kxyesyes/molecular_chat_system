#!/bin/sh
set +x
set -eu
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

PORT_RANGE=40000:60000
WAIT_SECONDS=5
MAX_LEGACY_RULES=64
NETWORK_NAME=medchat-opensandbox
BRIDGE_INTERFACE=br-medchat-sbox
NETWORK_LABEL=com.medchat.opensandbox.network=v1
SANDBOX_LABEL=opensandbox.io/id
LEGACY_CHAIN=MEDCHAT-OPENSANDBOX
MIN_DOCKER_MAJOR=25
MIN_DOCKER_MINOR=0
MIN_DOCKER_PATCH=5

fail() {
    printf '%s\n' \
        'opensandbox_firewall=failed code=firewall_configuration_failed' >&2
    exit 1
}

legacy_sandboxes_fail() {
    printf '%s\n' \
        'opensandbox_firewall=failed code=legacy_sandboxes_present' >&2
    exit 1
}

migration_fail() {
    printf '%s\n' \
        'opensandbox_firewall=failed code=firewall_migration_failed' >&2
    exit 1
}

docker_value() {
    format=$1
    shift
    docker "$@" --format "$format" "$NETWORK_NAME" 2>/dev/null
}

verify_network() {
    [ "$(docker_value '{{.Name}}' network inspect)" = "$NETWORK_NAME" ] || fail
    [ "$(docker_value '{{.Driver}}' network inspect)" = bridge ] || fail
    [ "$(docker_value '{{.Internal}}' network inspect)" = true ] || fail
    [ "$(docker_value '{{.EnableIPv6}}' network inspect)" = true ] || fail
    [ "$(docker_value '{{index .Options "com.docker.network.bridge.name"}}' network inspect)" = "$BRIDGE_INTERFACE" ] || fail
    [ "$(docker_value '{{index .Options "com.docker.network.bridge.enable_icc"}}' network inspect)" = false ] || fail
    [ "$(docker_value '{{json .Labels}}' network inspect)" = \
        '{"com.medchat.opensandbox.network":"v1"}' ] || fail
}

ensure_network() {
    command -v docker >/dev/null 2>&1 || fail
    verify_docker_version
    if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
        docker network create --driver bridge --internal --ipv6 \
            --label "$NETWORK_LABEL" \
            --opt "com.docker.network.bridge.name=$BRIDGE_INTERFACE" \
            --opt "com.docker.network.bridge.enable_icc=false" \
            "$NETWORK_NAME" >/dev/null 2>&1 || :
    fi
    verify_network
}

valid_version_component() {
    value=$1
    case "$value" in
        ''|*[!0-9]*|0[0-9]*) return 1 ;;
    esac
    [ "${#value}" -le 9 ]
}

verify_docker_version() {
    version=$(docker version --format '{{.Server.Version}}' 2>/dev/null) || fail
    [ "${#version}" -le 32 ] || fail
    case "$version" in
        .*|*.|*..*) fail ;;
    esac
    saved_ifs=$IFS
    IFS=.
    set -- $version
    IFS=$saved_ifs
    [ "$#" -eq 3 ] || fail
    valid_version_component "$1" || fail
    valid_version_component "$2" || fail
    valid_version_component "$3" || fail
    if [ "$1" -gt "$MIN_DOCKER_MAJOR" ]; then
        return
    fi
    [ "$1" -eq "$MIN_DOCKER_MAJOR" ] || fail
    if [ "$2" -gt "$MIN_DOCKER_MINOR" ]; then
        return
    fi
    [ "$2" -eq "$MIN_DOCKER_MINOR" ] || fail
    [ "$3" -ge "$MIN_DOCKER_PATCH" ] || fail
}

valid_container_id() {
    value=$1
    case "$value" in
        *[!0-9a-f]*|'') return 1 ;;
    esac
    [ "${#value}" -ge 12 ] && [ "${#value}" -le 64 ]
}

valid_sandbox_id() {
    value=$1
    case "$value" in
        *[!A-Za-z0-9._:-]*|'') return 1 ;;
    esac
    [ "${#value}" -le 128 ]
}

container_network_is_exact() {
    container=$1
    networks=$(docker container inspect --format \
        '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' \
        "$container" 2>/dev/null) || fail
    [ "$networks" = "$NETWORK_NAME" ]
}

verify_container_ownership() {
    attached=$(docker network inspect --format \
        '{{range $id, $_ := .Containers}}{{$id}}{{"\n"}}{{end}}' \
        "$NETWORK_NAME" 2>/dev/null) || fail
    for container in $attached; do
        valid_container_id "$container" || fail
        sandbox_id=$(docker container inspect --format \
            '{{index .Config.Labels "opensandbox.io/id"}}' \
            "$container" 2>/dev/null) || fail
        valid_sandbox_id "$sandbox_id" || fail
    done

    labeled=$(docker ps -aq --no-trunc --filter "label=$SANDBOX_LABEL" \
        2>/dev/null) || fail
    for container in $labeled; do
        valid_container_id "$container" || fail
        container_network_is_exact "$container" || legacy_sandboxes_fail
    done
}

rule_present() {
    tool=$1
    shift
    if "$tool" -w "$WAIT_SECONDS" -C "$@" >/dev/null 2>&1; then
        return 0
    else
        status=$?
        [ "$status" -eq 1 ] && return 1
        fail
    fi
}

ensure_rule_at() {
    tool=$1
    chain=$2
    position=$3
    shift 3
    deleted=0
    while rule_present "$tool" "$chain" "$@"; do
        deleted=$((deleted + 1))
        [ "$deleted" -le "$MAX_LEGACY_RULES" ] || fail
        "$tool" -w "$WAIT_SECONDS" -D "$chain" "$@" \
            >/dev/null 2>&1 || fail
    done
    "$tool" -w "$WAIT_SECONDS" -I "$chain" "$position" "$@" \
        >/dev/null 2>&1 || fail
    rule_present "$tool" "$chain" "$@" || fail
}

verify_unique_rule() {
    tool=$1
    chain=$2
    shift 2
    if rule_present "$tool" "$chain" "$@"; then
        return
    fi
    fail
}

configure_family() {
    tool=$1
    loopback_source=$2
    command -v "$tool" >/dev/null 2>&1 || fail
    "$tool" -w "$WAIT_SECONDS" -S DOCKER-USER >/dev/null 2>&1 || fail
    "$tool" -w "$WAIT_SECONDS" -S INPUT >/dev/null 2>&1 || fail

    ensure_rule_at "$tool" DOCKER-USER 1 \
        -i "$BRIDGE_INTERFACE" ! -o "$BRIDGE_INTERFACE" \
        -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
    ensure_rule_at "$tool" DOCKER-USER 2 \
        -o "$BRIDGE_INTERFACE" ! -s "$loopback_source" \
        -p tcp -m conntrack --ctdir ORIGINAL \
        --ctorigdstport "$PORT_RANGE" -j DROP
    ensure_rule_at "$tool" DOCKER-USER 3 \
        -i "$BRIDGE_INTERFACE" ! -o "$BRIDGE_INTERFACE" \
        -m conntrack --ctstate NEW -j DROP
    ensure_rule_at "$tool" INPUT 1 \
        -i "$BRIDGE_INTERFACE" -m conntrack --ctstate NEW -j DROP

    verify_unique_rule "$tool" DOCKER-USER \
        -i "$BRIDGE_INTERFACE" ! -o "$BRIDGE_INTERFACE" \
        -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
    verify_unique_rule "$tool" DOCKER-USER \
        -o "$BRIDGE_INTERFACE" ! -s "$loopback_source" \
        -p tcp -m conntrack --ctdir ORIGINAL \
        --ctorigdstport "$PORT_RANGE" -j DROP
    verify_unique_rule "$tool" DOCKER-USER \
        -i "$BRIDGE_INTERFACE" ! -o "$BRIDGE_INTERFACE" \
        -m conntrack --ctstate NEW -j DROP
    verify_unique_rule "$tool" INPUT \
        -i "$BRIDGE_INTERFACE" -m conntrack --ctstate NEW -j DROP
}

delete_all_legacy_rules() {
    tool=$1
    table=$2
    chain=$3
    shift 3
    deleted=0
    while :; do
        if "$tool" -w "$WAIT_SECONDS" -t "$table" -C "$chain" "$@" \
            >/dev/null 2>&1
        then
            deleted=$((deleted + 1))
            [ "$deleted" -le "$MAX_LEGACY_RULES" ] || migration_fail
            "$tool" -w "$WAIT_SECONDS" -t "$table" -D "$chain" "$@" \
                >/dev/null 2>&1 || migration_fail
            continue
        else
            status=$?
            [ "$status" -eq 1 ] || migration_fail
            break
        fi
    done
}

migrate_legacy_rules() {
    tool=$1
    loopback_source=$2
    delete_all_legacy_rules "$tool" filter DOCKER-USER \
        -s "$loopback_source" -p tcp -m conntrack --ctdir ORIGINAL \
        --ctorigdstport "$PORT_RANGE" -j RETURN
    delete_all_legacy_rules "$tool" filter DOCKER-USER \
        -p tcp -m conntrack --ctdir ORIGINAL \
        --ctorigdstport "$PORT_RANGE" -j DROP

    if legacy_state=$("$tool" -w "$WAIT_SECONDS" -t raw \
        -S "$LEGACY_CHAIN" 2>/dev/null)
    then
        expected="-N $LEGACY_CHAIN
-A $LEGACY_CHAIN -j DROP"
        [ "$legacy_state" = "$expected" ] || migration_fail
        delete_all_legacy_rules "$tool" raw PREROUTING \
            ! -i lo -m addrtype --dst-type LOCAL -p tcp -m tcp \
            --dport "$PORT_RANGE" -j "$LEGACY_CHAIN"
        "$tool" -w "$WAIT_SECONDS" -t raw -D "$LEGACY_CHAIN" -j DROP \
            >/dev/null 2>&1 || migration_fail
        "$tool" -w "$WAIT_SECONDS" -t raw -X "$LEGACY_CHAIN" \
            >/dev/null 2>&1 || migration_fail
    else
        status=$?
        [ "$status" -eq 1 ] || migration_fail
    fi
}

verify_legacy_absent() {
    tool=$1
    loopback_source=$2
    rule_present "$tool" DOCKER-USER \
        -s "$loopback_source" -p tcp -m conntrack --ctdir ORIGINAL \
        --ctorigdstport "$PORT_RANGE" -j RETURN && migration_fail || :
    rule_present "$tool" DOCKER-USER \
        -p tcp -m conntrack --ctdir ORIGINAL \
        --ctorigdstport "$PORT_RANGE" -j DROP && migration_fail || :
    if "$tool" -w "$WAIT_SECONDS" -t raw -S "$LEGACY_CHAIN" \
        >/dev/null 2>&1
    then
        migration_fail
    else
        status=$?
        [ "$status" -eq 1 ] || migration_fail
    fi
}

[ "$#" -eq 0 ] || fail
ensure_network
verify_container_ownership
configure_family iptables 127.0.0.0/8
configure_family ip6tables ::1/128
migrate_legacy_rules iptables 127.0.0.0/8
migrate_legacy_rules ip6tables ::1/128
verify_legacy_absent iptables 127.0.0.0/8
verify_legacy_absent ip6tables ::1/128
printf '%s\n' 'opensandbox_firewall=passed'
