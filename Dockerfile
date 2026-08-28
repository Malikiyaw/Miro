FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create data directory
RUN mkdir -p data logs

# Make the Render/Discord entrypoint executable
RUN chmod +x start.sh

# IMPORTANT: use the async Render entrypoint. Do not start bot.py directly;
# bot.py has its own legacy retry loop and can create repeated Discord login
# attempts during a 429 block.
CMD ["python", "render_entrypoint.py"]
