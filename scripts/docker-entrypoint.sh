#!/bin/sh
set -e

echo "=== Highlight Bot container start ==="
echo "Date: $(date -u)"
echo "PORT=${PORT:-8080}"
echo "HOST=${HOST:-0.0.0.0}"
echo "PWD=$(pwd)"
echo "Python: $(python --version 2>&1)"
echo "Files in /app:"
ls -la /app | head -20
echo "======================================"

exec "$@"
