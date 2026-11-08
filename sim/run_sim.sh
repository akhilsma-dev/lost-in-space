
#!/usr/bin/env bash

set -e

# move to the project root
cd "$(dirname "$0")"

# # check virtual environment
# if [ ! -f ".venv/bin/python" ]; then
#     echo "Virtual environment not found. Create it first:"
#     echo "python -m venv .venv"
#     exit 1
# fi

# run simulator
echo "Starting Lost in Space simulator..."

exec lsp_venv/bin/python -m sim.live "$@"
