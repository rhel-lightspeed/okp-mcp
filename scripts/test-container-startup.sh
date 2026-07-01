#!/bin/bash
docker run --rm -d -p 8000:8000 --name okp-mcp-test "$1"

for _attempt in {1..30}; do
STATUS=$(docker inspect -f '{{.State.Health.Status}}' okp-mcp-test 2>/dev/null || echo "missing")
if [[ "$STATUS" = "healthy" ]]; then
  echo "Container is healthy!"
  docker stop okp-mcp-test
  exit 0
elif [[ "$STATUS" = "unhealthy" ]]; then
  echo "Container healthcheck failed!"
  break
fi
sleep 2
done

echo "Container failed to become healthy. Logs:"
docker logs okp-mcp-test
docker stop okp-mcp-test
exit 1
