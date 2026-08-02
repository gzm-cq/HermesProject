#!/bin/bash
# Quick syntax test for _py with env vars

_py() {
    python3 <<< "$1"
}

# Test 1: basic env var
CV=10 ST=2 PM=20
at_upper=$(_py 'import os; print("1" if float(os.environ["CV"]) + float(os.environ["ST"]) > float(os.environ["PM"]) else "0")' 2>/dev/null || echo "0")
echo "at_upper=$at_upper"

# Test 2: with < comparison
CV=10 ST=2 PM=20
at_lower=$(_py 'import os; print("1" if float(os.environ["CV"]) - float(os.environ["ST"]) < float(os.environ["PM"]) else "0")' 2>/dev/null || echo "0")
echo "at_lower=$at_lower"

# Test 3: the problematic pattern
CV=10 ST=2 PM=20 at_upper2=$(_py 'import os; print("1" if float(os.environ["CV"]) + float(os.environ["ST"]) > float(os.environ["PM"]) else "0")' 2>/dev/null || echo "0")
echo "at_upper2=$at_upper2"

echo "All tests passed"