FROM python:3.12-slim

# Ownership proof for the official MCP registry; must equal "name" in server.json.
LABEL io.modelcontextprotocol.server.name="io.github.JlMoreno00/sabor"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sabor_mcp.py .
COPY sabor ./sabor
COPY catalog ./catalog
COPY nutrition ./nutrition
COPY scripts ./scripts

RUN mkdir -p /data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -sf -X POST -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d "{\"jsonrpc\":\"2.0\",\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-03-26\",\"capabilities\":{},\"clientInfo\":{\"name\":\"healthcheck\",\"version\":\"1.0.0\"}},\"id\":1}" http://localhost:8080/mcp || exit 1

CMD ["python", "sabor_mcp.py"]
