#!/bin/bash
# setup.sh — Controller-side setup (runs before simulation jobs)
# Verifies dependencies and installs numpy.

set -e

echo "=========================================="
echo "Monte Carlo Setup: $(date)"
echo "=========================================="
echo "Hostname: $(hostname)"
echo "Job dir:  ${PW_PARENT_JOB_DIR:-$(pwd)}"

JOB_DIR="${PW_PARENT_JOB_DIR%/}"

# Verify Python
PYTHON_CMD=""
for cmd in python3 python; do
    command -v $cmd &>/dev/null && { PYTHON_CMD=$cmd; break; }
done

if [ -z "${PYTHON_CMD}" ]; then
    echo "[ERROR] Python not found"
    exit 1
fi
echo "Python: ${PYTHON_CMD} ($(${PYTHON_CMD} --version 2>&1))"

# Install numpy if not available
${PYTHON_CMD} -c "import numpy" 2>/dev/null || {
    echo "Installing numpy..."
    ${PYTHON_CMD} -m pip install --user --quiet numpy 2>&1 || {
        echo "pip --user failed, trying venv..."
        VENV_DIR="${JOB_DIR}/.venv"
        if [ ! -d "${VENV_DIR}" ]; then
            ${PYTHON_CMD} -m venv "${VENV_DIR}"
        fi
        "${VENV_DIR}/bin/python" -m pip install --quiet numpy 2>&1 || {
            echo "[WARN] numpy install failed; simulator will use pure Python fallback"
        }
    }
}

# Mark setup complete
touch "${JOB_DIR}/SETUP_COMPLETE"
echo "Setup complete!"
