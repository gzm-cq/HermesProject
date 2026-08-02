#!/bin/bash
# Minimal test of the problematic section

_py() {
    python3 <<< "$1"
}

_py_timeout() {
    local secs="$1"
    local code="$2"
    timeout "$secs" python3 <<< "$code"
}

# Simulate the exact pattern from auto-tuner.sh
current_val=3
step=1
param_max=10
param_min=0
param_name="sag_max_inject"
direction="up"
dist_to_max=7
dist_to_min=3
new_val=4
state='{}'
today="2026-07-31"

# Test 1: separate env var lines
local at_upper at_lower
CV="$current_val" ST="$step" PM="$param_max"
at_upper=$(_py 'import os; print("1" if float(os.environ["CV"]) + float(os.environ["ST"]) > float(os.environ["PM"]) else "0")' 2>/dev/null || echo "0")
CV="$current_val" ST="$step" PM="$param_min"
at_lower=$(_py 'import os; print("1" if float(os.environ["CV"]) - float(os.environ["ST"]) < float(os.environ["PM"]) else "0")' 2>/dev/null || echo "0")
echo "at_upper=$at_upper at_lower=$at_lower"

if [[ "$direction" == "up" && "$at_upper" == "1" ]]; then
    direction="down"
    reason="Upper limit (${param_max}), go down"
elif [[ "$direction" == "down" && "$at_lower" == "1" ]]; then
    direction="up"
    reason="Lower limit (${param_min}), go up"
fi
echo "direction=$direction reason=$reason"

# Test 2: determine_direction pattern
DM="$dist_to_max" DN="$dist_to_min"
if [[ "$(_py 'import os; print("1" if float(os.environ["DM"]) < float(os.environ["DN"]) else "0")' 2>/dev/null || echo "0") == "1" ]]; then
    direction="down"
fi
echo "direction=$direction"

# Test 3: equality check
NV="$new_val" CV="$current_val"
if [[ "$(_py 'import os; print("1" if float(os.environ["NV"]) == float(os.environ["CV"]) else "0")' 2>/dev/null || echo "0") == "1" ]]; then
    echo "equal"
else
    echo "not equal"
fi

echo "All tests OK"