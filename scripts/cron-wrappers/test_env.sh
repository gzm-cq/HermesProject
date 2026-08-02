#!/bin/bash
# Test env var passing to _py

_py() {
    python3 <<< "$1"
}

# Test: inline env var + command substitution
CV=10 ST=2 PM=20 at_upper=$(_py 'import os; print(repr(os.environ.get("CV")))' 2>/dev/null || echo "FAIL")
echo "CV via inline: $at_upper"

# Test: export first
export CV=10 ST=2 PM=20
at_lower=$(_py 'import os; print(repr(os.environ.get("CV")))' 2>/dev/null || echo "FAIL")
echo "CV via export: $at_lower"

# Test: separate lines, no export
CV=10 ST=2 PM=20
at_separate=$(_py 'import os; print(repr(os.environ.get("CV")))' 2>/dev/null || echo "FAIL")
echo "CV via separate: $at_separate"