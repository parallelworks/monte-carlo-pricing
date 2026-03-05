#!/bin/bash
# start_dashboard.sh — Launch the live dashboard server on compute node
#
# Creates coordination files:
#   - HOSTNAME     - Where dashboard runs
#   - SESSION_PORT - Dashboard port
#   - job.started  - Signals job has started

set -e

echo "=========================================="
echo "Dashboard Service Starting: $(date)"
echo "=========================================="
echo "Hostname: $(hostname)"
echo "Job dir:  ${PW_PARENT_JOB_DIR}"

JOB_DIR="${PW_PARENT_JOB_DIR%/}"
cd "${JOB_DIR}"

# Script directory — checkout places scripts under $JOB_DIR/scripts/
SCRIPT_DIR="${JOB_DIR}/scripts"
echo "Script dir: ${SCRIPT_DIR}"

# Verify scripts were checked out
if [ ! -f "${SCRIPT_DIR}/dashboard.py" ]; then
    echo "[ERROR] dashboard.py not found at ${SCRIPT_DIR}/dashboard.py"
    echo "[DEBUG] Contents of JOB_DIR:"
    ls -la "${JOB_DIR}" 2>&1
    echo "[DEBUG] Looking for scripts..."
    find "${JOB_DIR}" -name "dashboard.py" 2>/dev/null || echo "Not found anywhere"
    exit 1
fi

# =============================================================================
# Install dependencies
# =============================================================================
PYTHON_CMD=""
for cmd in python3 python; do
    command -v $cmd &>/dev/null && { PYTHON_CMD=$cmd; break; }
done

if [ -z "${PYTHON_CMD}" ]; then
    echo "[ERROR] Python not found"
    exit 1
fi
echo "Python: ${PYTHON_CMD}"

# Install pip deps if needed
${PYTHON_CMD} -c "import fastapi" 2>/dev/null || {
    echo "Installing fastapi, uvicorn, websockets..."
    ${PYTHON_CMD} -m pip install --user --quiet fastapi uvicorn websockets 2>&1 || {
        echo "pip --user failed (PEP 668?), trying virtual environment..."
        VENV_DIR="${JOB_DIR}/.venv"
        ${PYTHON_CMD} -m venv "${VENV_DIR}"
        PYTHON_CMD="${VENV_DIR}/bin/python"
        ${PYTHON_CMD} -m pip install --quiet fastapi uvicorn websockets 2>&1 || {
            echo "[ERROR] Failed to install dependencies"
            exit 1
        }
    }
}

# =============================================================================
# Port allocation
# =============================================================================
if [ -z "${service_port}" ] || [ "${service_port}" == "undefined" ]; then
    echo "Allocating port via pw agent..."
    # pw should be in PATH (injected by job_runner), fall back to ~/pw/pw
    # Use 2>/dev/null to suppress upgrade notices that corrupt the port value
    if command -v pw &>/dev/null; then
        service_port=$(pw agent open-port 2>/dev/null)
    elif [ -x "${HOME}/pw/pw" ]; then
        service_port=$(~/pw/pw agent open-port 2>/dev/null)
    else
        echo "[ERROR] pw CLI not found in PATH or ~/pw/pw"
        exit 1
    fi
    if [ -z "${service_port}" ]; then
        echo "[ERROR] Failed to allocate port"
        exit 1
    fi
fi

if ! [[ "${service_port}" =~ ^[0-9]+$ ]]; then
    echo "[ERROR] Invalid port: '${service_port}'"
    exit 1
fi
echo "Dashboard port: ${service_port}"

# =============================================================================
# Write coordination files
# =============================================================================
hostname > HOSTNAME
echo "${service_port}" > SESSION_PORT
touch job.started

echo "Coordination files written:"
echo "  HOSTNAME=$(cat HOSTNAME)"
echo "  SESSION_PORT=$(cat SESSION_PORT)"

# =============================================================================
# Start dashboard
# =============================================================================
mkdir -p logs

export DASHBOARD_PORT="${service_port}"

nohup ${PYTHON_CMD} -m uvicorn dashboard:app \
    --host 0.0.0.0 \
    --port "${service_port}" \
    --app-dir "${SCRIPT_DIR}" \
    > logs/dashboard.log 2>&1 &
disown
SERVER_PID=$!

echo "Dashboard PID: ${SERVER_PID}"
echo "${SERVER_PID}" > dashboard.pid

sleep 3

if kill -0 ${SERVER_PID} 2>/dev/null; then
    echo "=========================================="
    echo "Dashboard is RUNNING on port ${service_port}"
    echo "=========================================="

    # Keep this script (and the SSH session / compute job) alive
    # so the dashboard process persists and the session proxy stays valid.
    # Cancel the workflow to shut down.
    while kill -0 ${SERVER_PID} 2>/dev/null; do
        sleep 5
    done
    echo "Dashboard process exited"
else
    echo "[ERROR] Dashboard failed to start"
    cat logs/dashboard.log >&2
    exit 1
fi
