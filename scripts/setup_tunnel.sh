#!/bin/bash
# setup_tunnel.sh — Create a reverse SSH tunnel from on-prem to cloud
#
# Environment variables:
#   CLOUD_RESOURCE_NAME - Name of the cloud resource (e.g., googlerockyv3)
#   CLOUD_RESOURCE_IP   - IP address of the cloud resource (fallback for lookup)
#   DASHBOARD_PORT      - Port of the dashboard on this machine
#   PW_USER             - ACTIVATE username for SSH

set -e

echo "=========================================="
echo "Setting up reverse tunnel: $(date)"
echo "=========================================="
echo "Cloud resource name: ${CLOUD_RESOURCE_NAME}"
echo "Cloud resource IP:   ${CLOUD_RESOURCE_IP}"
echo "Dashboard port:      ${DASHBOARD_PORT}"
echo "PW user:             ${PW_USER}"

JOB_DIR="${PW_PARENT_JOB_DIR%/}"

# Find pw CLI
PW_CMD=""
for cmd in pw ~/pw/pw; do
    command -v $cmd &>/dev/null && { PW_CMD=$cmd; break; }
    [ -x "$cmd" ] && { PW_CMD=$cmd; break; }
done
echo "PW CLI: ${PW_CMD:-not found}"

# Resolve SSH target — try name first, then discover from pw cluster list
SSH_TARGET="${CLOUD_RESOURCE_NAME}"

if [ -z "${SSH_TARGET}" ] && [ -n "${PW_CMD}" ]; then
    echo "Resource name not provided, discovering from pw cluster list..."
    # pw cluster list outputs: pw://user/name   status   type
    # Find active cloud clusters (non-"existing" type) owned by this user
    while IFS= read -r uri; do
        name="${uri##*/}"
        SSH_TARGET="${name}"
        echo "  Discovered cloud resource: ${name}"
        break
    done < <(${PW_CMD} cluster list 2>/dev/null | awk '/^pw:\/\/'"${PW_USER}"'/ && /active/ && !/existing/ {print $1}')
fi

if [ -z "${SSH_TARGET}" ]; then
    echo "[ERROR] Could not determine cloud resource name"
    echo "  CLOUD_RESOURCE_NAME was empty"
    echo "  pw cluster list discovery found no active cloud clusters"
    echo "  Available clusters:"
    ${PW_CMD} cluster list 2>/dev/null || echo "  (pw CLI not available)"
    exit 1
fi

echo "SSH target: ${SSH_TARGET}"

# Helper: run command on cloud via pw ssh proxy
run_on_cloud() {
    ssh -i ~/.ssh/pwcli \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ProxyCommand="pw ssh --proxy-command %h" \
        "${PW_USER}@${SSH_TARGET}" "$@"
}

# Allocate a port on the cloud side for the tunnel
echo "Allocating port on cloud..."
TUNNEL_PORT=$(run_on_cloud 'python3 -c "import socket; s=socket.socket(); s.bind((\"\",0)); print(s.getsockname()[1]); s.close()"' 2>/dev/null)

if [ -z "${TUNNEL_PORT}" ] || ! [[ "${TUNNEL_PORT}" =~ ^[0-9]+$ ]]; then
    echo "[ERROR] Failed to allocate port on cloud (got: '${TUNNEL_PORT}')"
    exit 1
fi

echo "Tunnel port on cloud: ${TUNNEL_PORT}"
echo "${TUNNEL_PORT}" > "${JOB_DIR}/TUNNEL_PORT"

# Start reverse SSH tunnel: cloud:TUNNEL_PORT -> onprem:DASHBOARD_PORT
echo "Establishing reverse SSH tunnel..."
ssh -i ~/.ssh/pwcli \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 \
    -o ProxyCommand="pw ssh --proxy-command %h" \
    -R "${TUNNEL_PORT}:localhost:${DASHBOARD_PORT}" \
    -N "${PW_USER}@${SSH_TARGET}" &
TUNNEL_PID=$!
sleep 3

if kill -0 ${TUNNEL_PID} 2>/dev/null; then
    echo "=========================================="
    echo "Reverse tunnel ESTABLISHED (PID ${TUNNEL_PID})"
    echo "  Cloud localhost:${TUNNEL_PORT} -> On-prem localhost:${DASHBOARD_PORT}"
    echo "=========================================="
    echo "TUNNEL_PORT=${TUNNEL_PORT}" >> "${OUTPUTS}"

    # Keep tunnel alive until workflow completes
    while kill -0 ${TUNNEL_PID} 2>/dev/null; do
        sleep 5
    done
    echo "Tunnel process exited"
else
    echo "[ERROR] Failed to establish tunnel"
    exit 1
fi
