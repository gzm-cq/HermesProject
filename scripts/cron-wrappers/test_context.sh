#!/bin/bash
# Test the exact _py calls in context with if/elif

_py() {
    python3 <<< "$1"
}

current_val=3
step=1
param_max=10
param_min=0
direction="up"

local at_upper at_lower
CV="$current_val" ST="$step" PM="$param_max"
at_upper=$(_py 'import os; print("1" if float(os.environ["CV"]) + float(os.environ["ST"]) > float(os.environ["PM"]) else "0")' 2>/dev/null || echo "0")
CV="$current_val" ST="$step" PM="$param_min"
at_lower=$(_py 'import os; print("1" if float(os.environ["CV"]) - float(os.environ["ST"]) < float(os.environ["PM"]) else "0")' 2>/dev/null || echo "0")
if [[ "$direction" == "up" && "$at_upper" == "1" ]]; then
    direction="down"
    reason="Upper limit (${param_max}), go down"
elif [[ "$direction" == "down" && "$at_lower" == "1" ]]; then
    direction="up"
    reason="Lower limit (${param_min}), go up"
fi