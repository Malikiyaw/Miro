#!/bin/bash

# Miro Bot Startup Script
# Always use the resilient Render entrypoint. Starting bot.py directly uses
# the legacy synchronous retry loop and can create repeated Discord login
# attempts while Render's outbound IP is rate-limited.

set -e

echo "Starting Miro Discord Bot with resilient Render entrypoint..."
mkdir -p data logs
exec python render_entrypoint.py
