#!/bin/sh
#
# Format, lint, type-check and test emergenv.
#
#   ./check.sh            # dev mode: auto-fix formatting, then lint/type/test
#   CI=true ./check.sh    # CI mode: check formatting only (no modifications)
#   ./check.sh src/foo.py # operate on the given paths instead of the default
#
# Install the tooling with:  pip install -r requirements-dev.txt

# Exit on any error only in CI mode (dev mode keeps going so you see everything).
if [ "$CI" = "true" ]; then
  set -e
fi

if [ "$1" != "" ]; then
  FILES="${@}"
else
  FILES=$(git ls-files | grep '\.py$' | xargs echo)
fi

if [ "$CI" = "true" ]; then
  echo ----- ISORT CHECK -----
  isort --check-only --diff $FILES
  echo

  echo ----- BLACK CHECK -----
  black --check --diff $FILES
  echo
else
  echo ----- ISORT -----
  isort $FILES
  echo

  echo ----- BLACK -----
  black $FILES
  echo
fi

echo ----- FLAKE8 -----
pflake8 $FILES
echo

echo ----- PYRIGHT -----
pyright $FILES
echo

echo ----- MYPY -----
mypy $FILES
echo

# Only run the test suite for a whole-tree check; skip it when specific paths
# were passed (you're iterating on a file, not validating everything).
if [ "$1" = "" ]; then
  echo ----- PYTEST -----
  pytest
  echo

  # In CI, additionally run the full interpreter matrix (py39 - py314) via tox.
  if [ "$CI" = "true" ]; then
    echo ----- TOX -----
    tox
    echo
  fi
fi

if [ "$CI" = "true" ]; then
  echo "✅ All CI checks passed!"
fi
