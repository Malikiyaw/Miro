#!/bin/bash

# Miro Bot Startup Script

echo "Starting Miro Discord Bot..."

# Check if .env exists
if [ ! -f .env ]; then
    echo "Error: .env file not found. Please copy .env.example to .env and configure your settings."
    exit 1
fi

# Create necessary directories
mkdir -p data logs

# Start the bot
python bot.py