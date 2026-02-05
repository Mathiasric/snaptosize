# SnapToSize Runner Service

Skeleton FastAPI service for background ZIP generation. Auth-only for now (no R2/Pillow).

## Deploy to Fly.io

```bash
cd services/runner

# Create app (first time only)
fly apps create snaptosize-runner

# Set secret
fly secrets set RUNNER_TOKEN=<your-secret-token>

# Deploy
fly deploy
```

## Test

```bash
# Health check (no auth)
curl https://snaptosize-runner.fly.dev/health

# Generate without token -> 401
curl -X POST https://snaptosize-runner.fly.dev/generate

# Generate with wrong token -> 403
curl -X POST https://snaptosize-runner.fly.dev/generate \
  -H "Authorization: Bearer wrong-token"

# Generate with valid token -> 200
curl -X POST https://snaptosize-runner.fly.dev/generate \
  -H "Authorization: Bearer <your-RUNNER_TOKEN>"
```
