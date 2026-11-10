#!/usr/bin/env bash
# Health check for this machine's part of fleet-monitoring. Prints one line per check:
#   PASS <name>: ...   FAIL <name>: ...   SKIP <name>: <why it does not apply here>
# and exits 1 if anything FAILs. Run it from this folder after `docker compose up -d`:
#   ./check.sh
# If you start the stack with extra compose options (an override file, another project
# name), give the same options to check.sh, and point it at the .env you use:
#   CHECK_ENV_FILE=/path/to/.env ./check.sh -f /path/to/site.compose.yml
# Passwords are read from the site's secrets/ files and handed to curl in a private
# temporary file, never on the command line.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 2
env_file=${CHECK_ENV_FILE:-./.env}
[ -f "$env_file" ] || { echo "check: no settings file at $env_file (cp .env.example .env)" >&2; exit 2; }

# KEY=VALUE lines only; values are not run as shell code.
setting() { # $1 = key, $2 = default
    local v
    v=$(grep -E "^$1=" "$env_file" | tail -n 1 | cut -d= -f2-)
    printf '%s' "${v:-$2}"
}
profiles=",$(setting COMPOSE_PROFILES ''),"
has() { case "$profiles" in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
bind=$(setting BIND_ADDRESS 127.0.0.1); [ "$bind" = 0.0.0.0 ] && bind=127.0.0.1
site=$(setting SITE_DIR ./site)
prom="http://$bind:$(setting PROMETHEUS_PORT 9090)"
am="http://$bind:$(setting ALERTMANAGER_PORT 9093)"
graf="http://$bind:$(setting GRAFANA_PORT 3000)"
loki="http://$bind:$(setting LOKI_PORT 3100)"
compose=(docker compose --env-file "$env_file" "$@")

failed=0
pass() { echo "PASS $1: $2"; }
fail() { echo "FAIL $1: $2"; failed=1; }
skip() { echo "SKIP $1: $2"; }
# verdict NAME PASS-MSG FAIL-MSG: PASS if the command just before it succeeded.
verdict() { local rc=$?; if [ "$rc" = 0 ]; then pass "$1" "$2"; else fail "$1" "$3"; fi; }

tmp=$(mktemp -d); chmod 700 "$tmp"; trap 'rm -rf "$tmp"' EXIT
# curl config with a user:password read from a file (never argv).
auth_cfg() { # $1 = user, $2 = password file, $3 = output config
    [ -r "$2" ] || return 1
    printf 'user = "%s:%s"\n' "$1" "$(cat "$2")" > "$3"
}
prom_curl=(curl -fsS --max-time 10)
if [ -n "$(setting PROMETHEUS_WEB_CONFIG '')" ]; then
    if auth_cfg "$(setting PROMETHEUS_CHECK_USER monitor)" "$site/secrets/prometheus_password" "$tmp/prom.cfg"; then
        prom_curl+=(-K "$tmp/prom.cfg")
    else
        fail prometheus-login "PROMETHEUS_WEB_CONFIG is set but $site/secrets/prometheus_password is not readable"
    fi
fi
json() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }

# Containers: every one running (not restarting) and labelled.
ps_out=$("${compose[@]}" ps --format '{{.Service}} {{.State}} {{.Labels}}' 2>&1) || true
if [ -z "$ps_out" ]; then
    fail containers "none running for this project (docker compose up -d)"
else
    bad=$(printf '%s\n' "$ps_out" | awk '$2 != "running" {print $1" is "$2}')
    unlabelled=$(printf '%s\n' "$ps_out" | grep -v 'lifecycle=' | awk '{print $1}')
    [ -z "$bad" ]; verdict containers "$(printf '%s\n' "$ps_out" | wc -l) running" "$(printf '%s' "$bad" | tr '\n' ' ')"
    [ -z "$unlabelled" ]; verdict labels "every container has a lifecycle label" "no lifecycle label: $(printf '%s' "$unlabelled" | tr '\n' ' ')"
fi

if has hub; then
    if "${prom_curl[@]}" "$prom/-/ready" >/dev/null 2>&1; then
        pass prometheus "ready at $prom"
        targets=$("${prom_curl[@]}" "$prom/api/v1/targets?state=active")
        down=$(printf '%s' "$targets" | json 'print(" ".join(sorted(t["labels"].get("job","?")+"/"+t["labels"].get("instance","?") for t in d["data"]["activeTargets"] if t["health"]!="up")))')
        total=$(printf '%s' "$targets" | json 'print(len(d["data"]["activeTargets"]))')
        [ -z "$down" ]; verdict targets "$total of $total up" "not up: $down"
        rules=$("${prom_curl[@]}" "$prom/api/v1/rules")
        rule_errs=$(printf '%s' "$rules" | json 'print(" ".join(r["name"] for g in d["data"]["groups"] for r in g["rules"] if r.get("health")=="err"))')
        nrules=$(printf '%s' "$rules" | json 'print(sum(len(g["rules"]) for g in d["data"]["groups"]))')
        [ -z "$rule_errs" ]; verdict rules "$nrules loaded, none failing to evaluate" "evaluation errors: $rule_errs"
    else
        fail prometheus "not answering at $prom"
    fi
    if ls "$site"/rules/tests/*.yml >/dev/null 2>&1; then
        if out=$("${compose[@]}" run --rm --no-deps -T -w /etc/prometheus/site/rules/tests --entrypoint sh prometheus -c 'promtool test rules *.yml' 2>&1); then
            pass rule-tests "$(printf '%s\n' "$out" | grep -c SUCCESS) test files pass"
        else
            fail rule-tests "$(printf '%s\n' "$out" | grep -E 'FAILED|error' | head -3 | tr '\n' ' ')"
        fi
    else
        skip rule-tests "no $site/rules/tests/*.yml"
    fi
else
    skip prometheus "hub role not on this machine"
fi

if has alerting; then
    if curl -fsS --max-time 10 "$am/-/ready" >/dev/null 2>&1; then
        pass alertmanager "ready at $am"
        if has hub; then
            n=$("${prom_curl[@]}" "$prom/api/v1/alertmanagers" | json 'print(len(d["data"]["activeAlertmanagers"]))')
            [ "${n:-0}" -ge 1 ]; verdict alert-path "Prometheus sends to $n Alertmanager(s)" "Prometheus knows no Alertmanager (site/alertmanagers/*.yml)"
        fi
    else
        fail alertmanager "not answering at $am"
    fi
else
    skip alertmanager "alerting role not on this machine"
fi

if has viewer; then
    if curl -fsS --max-time 10 "$graf/api/health" 2>/dev/null | grep -q '"ok"'; then
        pass grafana "healthy at $graf"
        if auth_cfg admin "$site/secrets/grafana_admin_password" "$tmp/graf.cfg"; then
            uids=$(curl -fsS --max-time 10 -K "$tmp/graf.cfg" "$graf/api/datasources" | json 'print(" ".join(x["uid"] for x in d))')
            for u in $uids; do
                if curl -fsS --max-time 20 -K "$tmp/graf.cfg" "$graf/api/datasources/uid/$u/health" 2>/dev/null | grep -q '"status":"OK"'; then
                    pass "datasource:$u" "reachable"
                else
                    fail "datasource:$u" "Grafana cannot query it"
                fi
            done
        else
            fail grafana-login "$site/secrets/grafana_admin_password is not readable"
        fi
    else
        fail grafana "not healthy at $graf"
    fi
else
    skip grafana "viewer role not on this machine"
fi

if has logs; then
    curl -fsS --max-time 10 "$loki/ready" 2>/dev/null | grep -qx ready; verdict loki "ready at $loki" "not ready at $loki"
else
    skip loki "logs role not on this machine"
fi

exit "$failed"
