FROM registry.access.redhat.com/ubi9/python-312

# Install uv for dependency management
RUN pip install --no-cache-dir uv

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml pyproject.toml
COPY README.md README.md
COPY src/ src/

# Install dependencies using pip directly
RUN pip install --no-cache-dir fastmcp sentence-transformers torch

# Add src to PYTHONPATH so okp_mcp module is available
ENV PYTHONPATH=/app/src:$PYTHONPATH

# Pre-download embedding model
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('ibm-granite/granite-embedding-30m-english')"



# Set environment variables for HTTP transport
ENV SOLR_HOST=okp:8080
ENV MCP_TRANSPORT=http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8001

# Expose port
EXPOSE 8001

# Default command - run the module directly
CMD ["python", "-m", "okp_mcp"]
