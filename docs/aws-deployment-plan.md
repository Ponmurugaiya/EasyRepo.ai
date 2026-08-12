# EasyRepo AWS Deployment Plan

**Target:** Frontend → AWS Amplify Hosting | Backend → AWS ECS Fargate (API + Worker)  
**Database:** Supabase (existing) | **Cache:** Redis Cloud (existing)  
**Auth:** AWS Cognito (to be provisioned)  
**Cost:** ~$17–20/month — $130 remaining covers ~6–7 months  
**No ALB** — ECS task uses a public IP, exposed directly via API Gateway HTTP API

---

## Architecture Overview

```
User
 │
 ├─► Amplify Hosting (Next.js)
 │       NEXT_PUBLIC_API_URL → API Gateway HTTP API
 │
 └─► API Gateway HTTP API  (free tier — 1M calls/month)
         │  HTTP_PROXY integration → ECS task public IP:8000
         │
         └─► ECS Fargate — Single Task  (src.api.main:app + in-process worker)
                 │   ├─ FastAPI on port 8000  (handles all HTTP requests)
                 │   └─ Procrastinate worker  (runs inside same process,
                 │                             picks up ingestion jobs instantly)
                 │   assignPublicIp=ENABLED  ← no ALB, task is directly reachable
                 │
                 ├─► Supabase Postgres (DATABASE_URL — already external)
                 │       └─► pgvector, Procrastinate job tables, app schema
                 │
                 ├─► Redis Cloud (REDIS_URL — already external)
                 │       └─► Rate-limit storage for slowapi
                 │
                 └─► AWS Cognito User Pool
                         └─► Google OAuth IdP → frontend login flow
```

> **No ALB:** saves ~$16/month. The ECS task gets a public IP via `assignPublicIp=ENABLED`. API Gateway proxies requests to it over HTTP. When the task restarts (e.g. after a deploy), you update the API Gateway integration URL with the new IP — a one-line change. This is a dev/staging tradeoff: perfectly fine until you need zero-downtime deploys or horizontal scaling.

> **Scaling note:** Add an ALB later when you need zero-downtime rolling deploys or multiple task instances. Until then, a single Fargate task with a public IP is sufficient.

**Why ECS Fargate instead of Lambda for the backend:**
- The Procrastinate worker runs as a long-lived process inside the API (or as a separate process). Lambda has a 15-min max — repo ingestion (tree-sitter parsing + VoyageAI embeddings) can exceed this.
- The `/ask` endpoint already has a 2-minute timeout — fits within Lambda, but ECS avoids Lambda cold starts on AI inference calls.
- ECS is simpler to operate when the app already behaves like a standard HTTP server (uvicorn).

**Worker strategy — keep it in-process (single ECS task):**

The Procrastinate worker already runs inside the API process. `POST /repositories` writes a job row to Postgres, the in-process worker picks it up instantly, and the API returns `202 Accepted`. Splitting into a separate ECS worker service only makes sense when you need to scale API tasks and worker tasks independently — unnecessary at this usage level.

Running everything in one ECS task means:
- No second service to deploy or monitor
- Instant job pickup (no cold start delay for users)
- ~$4–6/month cheaper than a second Fargate task

If you ever need to scale workers independently, Procrastinate supports a standalone CLI worker (`procrastinate --app=src.jobs.queue.task_queue worker`) that can run as a separate ECS service alongside the API — no code changes needed.

---

## Phase 1 — Containerize the Backend

### 1.1 Create `platform/Dockerfile`

```dockerfile
FROM python:3.11-slim

# System deps for tree-sitter native binaries + psycopg2
RUN apt-get update && apt-get install -y \
    gcc g++ git curl \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency spec and install
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

# Copy application code
COPY . .

# Default: run the API server
# Override CMD for the worker service (see docker-compose / ECS task def)
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

> **Note:** The Windows-specific `WindowsSelectorEventLoopPolicy` in `run.py` is not needed in a Linux container. The `CMD` points directly to uvicorn, bypassing `run.py`.

### 1.2 Create `platform/.dockerignore`

```
.venv/
__pycache__/
*.pyc
*.pyo
.pytest_cache/
logs/
.env
*.egg-info/
```

### 1.3 Test locally

```bash
cd platform
docker build -t easyrepo-backend .
docker run --env-file ../.env -p 8000:8000 easyrepo-backend
```

---

## Phase 2 — Push Image to ECR

### 2.1 Create ECR repository

```bash
aws ecr create-repository --repository-name easyrepo-backend --region us-east-1
```

### 2.2 Authenticate and push

```bash
# Get login token
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin \
  <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com

# Tag and push
docker tag easyrepo-backend:latest \
  <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/easyrepo-backend:latest

docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/easyrepo-backend:latest
```

---

## Phase 3 — Deploy Backend to ECS Fargate

### 3.1 Create ECS Cluster

```bash
aws ecs create-cluster --cluster-name easyrepo --region us-east-1
```

### 3.2 Store secrets in AWS SSM Parameter Store

Store all sensitive env vars as SecureString parameters so they never appear in ECS task definitions in plain text.

```bash
# Repeat for each secret:
aws ssm put-parameter \
  --name "/easyrepo/DATABASE_URL" \
  --value "postgresql://postgres:PASSWORD@db.REF.supabase.co:5432/postgres" \
  --type SecureString \
  --region us-east-1

# Keys to store:
# /easyrepo/DATABASE_URL
# /easyrepo/REDIS_URL
# /easyrepo/GEMINI_API_KEY
# /easyrepo/GROQ_API_KEY
# /easyrepo/VOYAGE_API_KEY
# /easyrepo/CEREBRAS_API_KEY
# /easyrepo/OPENROUTER_API_KEY
# /easyrepo/COHERE_API_KEY
# /easyrepo/CLOUDFLARE_API_KEY
# /easyrepo/CLOUDFLARE_ACCOUNT_ID
```

### 3.3 ECS Task Definition — Single Service (API + Worker)

The worker runs inside the same container as the API — this is the default architecture. No separate worker service needed.

Create `infra/ecs-task-api.json`:

```json
{
  "family": "easyrepo-api",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/ecsTaskExecutionRole",
  "containerDefinitions": [
    {
      "name": "api",
      "image": "<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/easyrepo-backend:latest",
      "portMappings": [{ "containerPort": 8000, "protocol": "tcp" }],
      "command": ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
      "environment": [
        { "name": "AUTH_ENABLED",        "value": "true" },
        { "name": "CORS_ALLOWED_ORIGINS","value": "https://your-amplify-domain.amplifyapp.com" },
        { "name": "LOG_TO_FILE",         "value": "false" },
        { "name": "PIPELINE_LOG_LEVEL",  "value": "INFO" },
        { "name": "GROQ_MODEL",          "value": "llama-3.3-70b-versatile" },
        { "name": "VOYAGE_BATCH_SIZE",   "value": "4" },
        { "name": "VOYAGE_BATCH_DELAY_SECS", "value": "21" }
      ],
      "secrets": [
        { "name": "DATABASE_URL",         "valueFrom": "/easyrepo/DATABASE_URL" },
        { "name": "REDIS_URL",            "valueFrom": "/easyrepo/REDIS_URL" },
        { "name": "GEMINI_API_KEY",       "valueFrom": "/easyrepo/GEMINI_API_KEY" },
        { "name": "GROQ_API_KEY",         "valueFrom": "/easyrepo/GROQ_API_KEY" },
        { "name": "VOYAGE_API_KEY",       "valueFrom": "/easyrepo/VOYAGE_API_KEY" },
        { "name": "CEREBRAS_API_KEY",     "valueFrom": "/easyrepo/CEREBRAS_API_KEY" },
        { "name": "OPENROUTER_API_KEY",   "valueFrom": "/easyrepo/OPENROUTER_API_KEY" },
        { "name": "COHERE_API_KEY",       "valueFrom": "/easyrepo/COHERE_API_KEY" },
        { "name": "CLOUDFLARE_API_KEY",   "valueFrom": "/easyrepo/CLOUDFLARE_API_KEY" },
        { "name": "CLOUDFLARE_ACCOUNT_ID","valueFrom": "/easyrepo/CLOUDFLARE_ACCOUNT_ID" }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/easyrepo-api",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
        "interval": 30,
        "timeout": 10,
        "retries": 3
      }
    }
  ]
}
```

### 3.4 Create ECS Service (no ALB — public IP direct)

With `assignPublicIp=ENABLED` and no ALB, the task gets a public IP that API Gateway will proxy to. The security group must allow inbound TCP on port 8000 **from API Gateway's managed prefix list** (or from `0.0.0.0/0` for simplicity in dev — lock it down later).

```bash
# Create a security group for the ECS task
aws ec2 create-security-group \
  --group-name easyrepo-ecs-sg \
  --description "EasyRepo ECS task" \
  --vpc-id vpc-xxxxxxxx \
  --region us-east-1

# Allow inbound on port 8000 (restrict to API Gateway IPs in production)
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxx \
  --protocol tcp \
  --port 8000 \
  --cidr 0.0.0.0/0 \
  --region us-east-1

# Create the ECS service
aws ecs create-service \
  --cluster easyrepo \
  --service-name easyrepo-api \
  --task-definition easyrepo-api \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={
    subnets=[subnet-xxxxxxxx],
    securityGroups=[sg-xxxxxxxx],
    assignPublicIp=ENABLED
  }" \
  --region us-east-1
```

### 3.5 Get the Task's Public IP

After the service starts, retrieve the public IP — you'll need it for API Gateway:

```bash
# Get the task ARN
TASK_ARN=$(aws ecs list-tasks \
  --cluster easyrepo \
  --service-name easyrepo-api \
  --query 'taskArns[0]' \
  --output text \
  --region us-east-1)

# Get the ENI attachment
ENI_ID=$(aws ecs describe-tasks \
  --cluster easyrepo \
  --tasks $TASK_ARN \
  --query 'tasks[0].attachments[0].details[?name==`networkInterfaceId`].value' \
  --output text \
  --region us-east-1)

# Get the public IP
aws ec2 describe-network-interfaces \
  --network-interface-ids $ENI_ID \
  --query 'NetworkInterfaces[0].Association.PublicIp' \
  --output text \
  --region us-east-1
```

### 3.6 Create API Gateway HTTP API

API Gateway provides a stable HTTPS URL that proxies to the ECS task's public IP. This gives you HTTPS termination and a fixed URL even when the task IP changes.

```bash
# Create the HTTP API
aws apigatewayv2 create-api \
  --name easyrepo-api-gw \
  --protocol-type HTTP \
  --cors-configuration \
    AllowOrigins="https://your-amplify-domain.amplifyapp.com",\
    AllowMethods="GET,POST,PUT,DELETE,OPTIONS",\
    AllowHeaders="Content-Type,X-API-Key,Authorization" \
  --region us-east-1

# Create an HTTP_PROXY integration pointing to the ECS task
aws apigatewayv2 create-integration \
  --api-id <API_ID> \
  --integration-type HTTP_PROXY \
  --integration-method ANY \
  --integration-uri http://<ECS_PUBLIC_IP>:8000/{proxy} \
  --payload-format-version 1.0 \
  --region us-east-1

# Create a catch-all route
aws apigatewayv2 create-route \
  --api-id <API_ID> \
  --route-key "ANY /{proxy+}" \
  --target integrations/<INTEGRATION_ID> \
  --region us-east-1

# Deploy to $default stage (auto-deploy)
aws apigatewayv2 create-stage \
  --api-id <API_ID> \
  --stage-name '$default' \
  --auto-deploy \
  --region us-east-1
```

This gives you a stable URL like:  
`https://<api-id>.execute-api.us-east-1.amazonaws.com`

> **When the ECS task restarts** (redeploy, crash recovery), it gets a new public IP. Update the API Gateway integration URI:
> ```bash
> aws apigatewayv2 update-integration \
>   --api-id <API_ID> \
>   --integration-id <INTEGRATION_ID> \
>   --integration-uri http://<NEW_ECS_IP>:8000/{proxy} \
>   --region us-east-1
> ```
> This is the main operational tradeoff of skipping the ALB — worth it at $16/month savings.

---

## Phase 4 — Provision AWS Cognito

The frontend already has full Cognito support — it just needs the User Pool to exist.

### 4.1 Create User Pool

```bash
aws cognito-idp create-user-pool \
  --pool-name EasyRepoUsers \
  --auto-verified-attributes email \
  --region us-east-1
```

### 4.2 Create App Client (no secret — SPA)

```bash
aws cognito-idp create-user-pool-client \
  --user-pool-id us-east-1_XXXXXXXXX \
  --client-name easyrepo-frontend \
  --no-generate-secret \
  --allowed-o-auth-flows code \
  --allowed-o-auth-scopes openid email profile \
  --allowed-o-auth-flows-user-pool-client \
  --callback-urls '["https://your-amplify-domain.amplifyapp.com", "http://localhost:3000"]' \
  --logout-urls '["https://your-amplify-domain.amplifyapp.com", "http://localhost:3000"]' \
  --supported-identity-providers COGNITO Google \
  --region us-east-1
```

### 4.3 Configure Google Identity Provider

1. Go to [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Credentials → Create OAuth 2.0 Client ID.
2. Authorized redirect URI: `https://your-domain.auth.us-east-1.amazoncognito.com/oauth2/idpresponse`
3. In Cognito console → User Pool → Sign-in experience → Federated identity providers → Add Google.
4. Paste the Google Client ID and Secret.

### 4.4 Create Hosted UI Domain

```bash
aws cognito-idp create-user-pool-domain \
  --domain your-easyrepo-app \
  --user-pool-id us-east-1_XXXXXXXXX \
  --region us-east-1
```

This gives you: `https://your-easyrepo-app.auth.us-east-1.amazoncognito.com`

### 4.5 Update frontend env vars

```env
NEXT_PUBLIC_COGNITO_REGION=us-east-1
NEXT_PUBLIC_COGNITO_USER_POOL_ID=us-east-1_XXXXXXXXX
NEXT_PUBLIC_COGNITO_CLIENT_ID=<client-id-from-step-4.2>
NEXT_PUBLIC_COGNITO_DOMAIN=https://your-easyrepo-app.auth.us-east-1.amazoncognito.com
NEXT_PUBLIC_COGNITO_REDIRECT_URI=https://your-amplify-domain.amplifyapp.com
NEXT_PUBLIC_API_URL=https://<api-id>.execute-api.us-east-1.amazonaws.com
```

---

## Phase 5 — Deploy Frontend to Amplify Hosting

### 5.1 Create `frontend/amplify.yml`

```yaml
version: 1
frontend:
  phases:
    preBuild:
      commands:
        - npm ci
    build:
      commands:
        - npm run build
  artifacts:
    baseDirectory: .next
    files:
      - '**/*'
  cache:
    paths:
      - node_modules/**/*
      - .next/cache/**/*
```

### 5.2 Connect to Amplify Hosting

1. Go to AWS Amplify Console → New App → Host Web App.
2. Connect your Git repo (GitHub/CodeCommit).
3. Set build root directory to `frontend`.
4. Under **Environment Variables**, add all `NEXT_PUBLIC_*` vars (Cognito, API URL).
5. Deploy.

Amplify auto-detects Next.js and handles SSR if needed. Since the app is fully client-side rendered, it builds as a static export with zero SSR costs.

### 5.3 Custom domain (optional)

In Amplify Console → Domain management → Add domain → follow the Route 53 / external DNS flow.

---

## Phase 6 — Run Alembic Migrations

Before the first deploy, run migrations against Supabase from your local machine or a one-off ECS task:

```bash
cd platform
# Ensure DATABASE_URL is set in your shell
alembic upgrade head
```

This is safe to run multiple times (idempotent).

---

## Environment Variables Reference

### Backend (ECS — set via SSM + task definition)

| Variable | Value |
|---|---|
| `DATABASE_URL` | Your Supabase connection string (pooler port 6543 recommended) |
| `REDIS_URL` | Your Redis Cloud connection string |
| `GEMINI_API_KEY` | From `.env` |
| `GROQ_API_KEY` | From `.env` |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` |
| `VOYAGE_API_KEY` | From `.env` |
| `VOYAGE_BATCH_SIZE` | `4` (increase after upgrading VoyageAI plan) |
| `VOYAGE_BATCH_DELAY_SECS` | `21` |
| `CEREBRAS_API_KEY` | From `.env` |
| `OPENROUTER_API_KEY` | From `.env` |
| `COHERE_API_KEY` | From `.env` |
| `CLOUDFLARE_API_KEY` | From `.env` |
| `CLOUDFLARE_ACCOUNT_ID` | From `.env` |
| `AUTH_ENABLED` | `true` |
| `CORS_ALLOWED_ORIGINS` | Amplify app domain |
| `LOG_TO_FILE` | `false` (use CloudWatch) |
| `PIPELINE_LOG_LEVEL` | `INFO` |

### Frontend (Amplify environment variables)

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://<api-id>.execute-api.us-east-1.amazonaws.com` |
| `NEXT_PUBLIC_COGNITO_REGION` | `us-east-1` |
| `NEXT_PUBLIC_COGNITO_USER_POOL_ID` | From Phase 4 |
| `NEXT_PUBLIC_COGNITO_CLIENT_ID` | From Phase 4 |
| `NEXT_PUBLIC_COGNITO_DOMAIN` | Hosted UI domain |
| `NEXT_PUBLIC_COGNITO_REDIRECT_URI` | Amplify app URL |

---

## Deployment Order

```
1. Phase 1 — Build Docker image locally, test it works
2. Phase 2 — Push to ECR
3. Phase 4 — Create Cognito User Pool + Google IdP
4. Phase 6 — Run Alembic migrations (one-time)
5. Phase 3 — Deploy ECS service (steps 3.1–3.5), note the public IP
6.          — Create API Gateway HTTP API (step 3.6), note the invoke URL
7. Phase 5 — Deploy frontend to Amplify with final env vars (Cognito + API GW URL)
8.          — Smoke test end-to-end
```

---

## Cost Estimate (with $130 remaining)

| Service | Monthly Cost | Notes |
|---|---|---|
| ECS Fargate (1 task × 0.5 vCPU, 1 GB) | ~$15–18 | Main cost driver |
| API Gateway HTTP API | ~$0 | Free tier: 1M calls/month |
| ECR storage | ~$0.10 | ~1–2 GB image |
| Amplify Hosting | ~$0 | Free tier |
| Cognito | ~$0 | Free tier (50K MAUs) |
| CloudWatch Logs | ~$1–2 | Log ingestion + storage |
| Data transfer | ~$1 | Minimal at dev traffic |
| Supabase | Already paying | Not from AWS credits |
| Redis Cloud | Already paying | Not from AWS credits |
| **Total** | **~$17–20/month** | |

**$130 ÷ ~$18/month = ~7 months runway**

> Note: AWS free credits typically expire 6 months from account creation. If you're already past month 1 or 2, the expiry date may hit before the balance runs out — check your credit expiry date in the AWS Billing console.

### If you want to cut further

- **Stop the ECS task when not actively testing** (`aws ecs update-service --desired-count 0`) — drops to ~$2–3/month (ECR + CloudWatch only). Start it again when you need it (`--desired-count 1`).
- **Switch to `ap-south-1` (Mumbai) region** — Fargate pricing is ~20% cheaper than `us-east-1`.

---

## Known Considerations

1. **Task IP changes on restart:** Without an ALB, the ECS task's public IP changes every time it restarts. After a redeploy or crash recovery, update the API Gateway integration URI with the new IP (one CLI command — see step 3.6). This is the main operational cost of skipping the ALB.

2. **Supabase pooler:** Use port `6543` (Supavisor/PgBouncer) for the `DATABASE_URL` instead of port `5432` (direct). This handles ECS bursty connection patterns. Exception: Procrastinate requires a direct connection or a session-mode pooler — use the `Session` mode pooler (port `5432` with `pgbouncer=true`) or the direct connection for the worker.

3. **Tree-sitter binaries:** The `tree-sitter-python` and `tree-sitter-typescript` packages compile native extensions. The `gcc`/`g++` install in the Dockerfile handles this. Build the image on Linux (or use `docker buildx` with `--platform linux/amd64` on Windows).

4. **Procrastinate schema:** The first API startup calls `apply_schema_async()` — this is idempotent. Subsequent restarts skip it cleanly.

5. **`run.py` Windows workaround:** Not needed in the container. The Dockerfile `CMD` goes directly to `uvicorn`, bypassing `run.py` entirely.

6. **CORS:** Set `CORS_ALLOWED_ORIGINS` to the exact Amplify domain (e.g. `https://main.d1234abcd.amplifyapp.com`) before go-live. Avoid `*` in production.

7. **Scaling:** Ingestion jobs are durable in Postgres — if the worker ECS task restarts mid-job, Procrastinate retries it automatically (up to 3 attempts with 60s wait). Add an ALB + increase `desired-count` when you need horizontal scaling.
