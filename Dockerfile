# Use an official slim Python runtime as a parent image
FROM python:3.11-slim

# Set system variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV WORKDIR=/app

# Set work directory
WORKDIR ${WORKDIR}

# Install system-level dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements.txt first to leverage Docker build cache
COPY requirements.txt .

# Install python dependencies targeting PyPI
RUN pip install --no-cache-dir -r requirements.txt --index-url https://pypi.org/simple/

# Copy the rest of the application code
COPY . .

# Expose port for HTTP/SSE transport
EXPOSE 8000

# Default command to start MCP server
CMD ["python", "mcp_server.py"]
