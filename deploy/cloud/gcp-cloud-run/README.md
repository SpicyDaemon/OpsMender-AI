# OpsMender on GCP Cloud Run

Reference YAML recipe for deploying OpsMender as a Cloud Run service with Secret Manager environment references, a dedicated service account, and a Cloud SQL connector. **Sprint 41 step 3** - sibling of the AWS ECS and Azure Container Apps recipes, still layered on top of the standard image at `ghcr.io/spicydaemon/opsmender-ai`.

Per locked decision **D-023**, OpsMender ships zero platform-specific runtime knowledge. This recipe configures the surrounding Google Cloud resources that run the container: service identity, IAM grants, secrets, Cloud SQL connectivity, and Cloud Run service settings.

## What this recipe creates

The checked-in `service.yaml` creates or updates one Cloud Run service:

| Resource | Purpose |
|---|---|
| `serving.knative.dev/v1 Service` | Runs the OpsMender container on Cloud Run. |
| `run.googleapis.com/cloudsql-instances` annotation | Attaches the Cloud SQL connector so `/cloudsql/PROJECT:REGION:INSTANCE` is available in the container. |
| `serviceAccountName` | Runs the container as a dedicated service account, not the default Compute Engine service account. |
| Secret Manager `secretKeyRef` env vars | Supplies `OPSMENDER_DATABASE_URL`, `OPSMENDER_JWT_SECRET`, and one or more provider keys at revision startup. |
| Startup, liveness, readiness probes | Health-check `/health` on container port 8000. |
| Scaling annotations | Keeps one warm instance by default and caps autoscaling at three instances. |

**Not created by this recipe:** the Google Cloud project, enabled APIs, service account, IAM bindings, Secret Manager secrets, Cloud SQL instance, custom domains. Those are operator-provided prerequisites.

## Prerequisites

- **Google Cloud CLI** authenticated against the target project.
- Enabled APIs: Cloud Run, Cloud SQL Admin, Secret Manager, IAM, Service Usage.
- A reachable **Cloud SQL for PostgreSQL 16+** instance.
- A dedicated Cloud Run service account, for example `opsmender-run@PROJECT_ID.iam.gserviceaccount.com`.
- Three Secret Manager secrets:
  - `opsmender-jwt-secret`
  - `opsmender-database-url`
  - at least one provider key, for example `opsmender-anthropic-key`

Official Google references used for this recipe:

- Cloud Run service YAML maps to `serving.knative.dev/v1` Service objects and supports labels/annotations such as region, ingress, min/max scale, and Cloud SQL instance attachments.
- Cloud Run can expose Secret Manager secrets as environment variables; the service identity needs `roles/secretmanager.secretAccessor`.
- Cloud Run service identity is set through `spec.template.spec.serviceAccountName`.
- Cloud SQL for PostgreSQL from Cloud Run uses the Cloud SQL connector and requires the service account to have `roles/cloudsql.client`.

## Quick start

```bash
cd deploy/cloud/gcp-cloud-run

export PROJECT_ID=your-project-id
export REGION=us-central1
export SERVICE=opsmender
export CLOUD_SQL_INSTANCE=opsmender-postgres
export SERVICE_ACCOUNT=opsmender-run@$PROJECT_ID.iam.gserviceaccount.com

gcloud config set project "$PROJECT_ID"

# 1. Enable required APIs.
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  serviceusage.googleapis.com

# 2. Create the dedicated service account.
gcloud iam service-accounts create opsmender-run \
  --display-name "OpsMender Cloud Run service identity"

# 3. Grant the minimum runtime roles.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:$SERVICE_ACCOUNT" \
  --role roles/cloudsql.client

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:$SERVICE_ACCOUNT" \
  --role roles/secretmanager.secretAccessor

# 4. Create secrets. Store the full async SQLAlchemy URL used by OpsMender.
# For the Cloud SQL Unix socket path, use your instance connection name:
#   PROJECT_ID:REGION:CLOUD_SQL_INSTANCE
gcloud secrets create opsmender-jwt-secret --replication-policy automatic
printf '%s' "$(openssl rand -hex 32)" | \
  gcloud secrets versions add opsmender-jwt-secret --data-file=-

gcloud secrets create opsmender-database-url --replication-policy automatic
printf '%s' \
  "postgresql+asyncpg://opsmender:PASSWORD@/opsmender?host=/cloudsql/$PROJECT_ID:$REGION:$CLOUD_SQL_INSTANCE" | \
  gcloud secrets versions add opsmender-database-url --data-file=-

gcloud secrets create opsmender-anthropic-key --replication-policy automatic
printf '%s' "$ANTHROPIC_API_KEY" | \
  gcloud secrets versions add opsmender-anthropic-key --data-file=-

# 5. Prepare a local service manifest with project-specific values.
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
INSTANCE_CONNECTION_NAME="$PROJECT_ID:$REGION:$CLOUD_SQL_INSTANCE"

cp service.yaml service.local.yaml
perl -0pi -e "s/PROJECT_NUMBER/$PROJECT_NUMBER/g; s/PROJECT_ID/$PROJECT_ID/g; s/REGION/$REGION/g; s/CLOUD_SQL_INSTANCE/$CLOUD_SQL_INSTANCE/g" service.local.yaml

# 6. Deploy the service from YAML.
gcloud run services replace service.local.yaml --region "$REGION"

# 7. Allow public HTTPS access. For private deployments, skip this and wire IAM/IAP.
gcloud run services add-iam-policy-binding "$SERVICE" \
  --region "$REGION" \
  --member allUsers \
  --role roles/run.invoker
```

Cloud Run terminates HTTPS automatically on the default `*.run.app` URL. After the service is healthy, add `OPSMENDER_PUBLIC_URL=<status.url>` to `service.local.yaml` and replace the service again so Slack/Teams deep links point at the right host.

## Verification recipe

```bash
# Show the public URL.
URL=$(gcloud run services describe opsmender \
  --region "$REGION" \
  --format='value(status.url)')
echo "$URL"

# Confirm the latest revision is ready.
gcloud run services describe opsmender \
  --region "$REGION" \
  --format='table(status.latestReadyRevisionName,status.conditions[0].type,status.conditions[0].status)'

# Tail application logs while the first request runs migrations.
gcloud run services logs tail opsmender --region "$REGION"

# Hit the health endpoint.
curl -sS "$URL/health"
# -> {"status":"ok"}

# Register the first admin.
curl -sS -X POST "$URL/auth/register" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","email":"admin@example.com","password":"securepass123","role":"admin"}'
```

## Common cutover patterns

### Custom domain

```bash
gcloud run domain-mappings create \
  --service opsmender \
  --domain opsmender.example.com \
  --region "$REGION"

gcloud run domain-mappings describe opsmender.example.com \
  --region "$REGION"
```

Add the DNS records returned by the second command, then set `OPSMENDER_PUBLIC_URL=https://opsmender.example.com` in `service.local.yaml` and replace the service again.

### Rolling a new image tag

```bash
# Edit the image in service.local.yaml, then:
gcloud run services replace service.local.yaml --region "$REGION"

# Or roll the image directly:
gcloud run services update opsmender \
  --region "$REGION" \
  --image ghcr.io/spicydaemon/opsmender-ai:v1.0.1
```

### Mirroring to Artifact Registry

Public GHCR works for the baseline path. If your org blocks external registries, mirror the image into Artifact Registry and change `containers[0].image`:

```bash
gcloud artifacts repositories create opsmender \
  --repository-format docker \
  --location "$REGION"

gcloud auth configure-docker "$REGION-docker.pkg.dev"

docker pull ghcr.io/spicydaemon/opsmender-ai:latest
docker tag ghcr.io/spicydaemon/opsmender-ai:latest \
  "$REGION-docker.pkg.dev/$PROJECT_ID/opsmender/opsmender-ai:latest"
docker push "$REGION-docker.pkg.dev/$PROJECT_ID/opsmender/opsmender-ai:latest"
```

### Scaling

For small teams, `minScale=1`, `maxScale=3`, `containerConcurrency=80`, and `1 CPU / 1 GiB` are conservative defaults. Increase `maxScale` only after checking Cloud SQL connection limits; each Cloud Run instance can open database connections.

## Tear-down

```bash
gcloud run services delete opsmender --region "$REGION"

# Optional cleanup if these resources are not shared.
gcloud secrets delete opsmender-jwt-secret
gcloud secrets delete opsmender-database-url
gcloud secrets delete opsmender-anthropic-key
gcloud iam service-accounts delete "$SERVICE_ACCOUNT"
```

Deleting the Cloud Run service does **not** delete your Cloud SQL instance or secrets. Delete them deliberately only when no other environment uses them.

## Architecture summary

```
        +----------------------------------------------------------+
        |  Google Cloud project                                    |
        |                                                          |
        |   Internet                                               |
        |      |                                                   |
        |      v                                                   |
        |   Cloud Run HTTPS ingress (*.run.app or custom domain)   |
        |      |                                                   |
        |      v                                                   |
        |   +-----------------------------------------------+      |
        |   |  Cloud Run service - port 8000                |      |
        |   |  Dedicated service account                    |      |
        |   |  Secret Manager env refs at revision startup  |      |
        |   |  Cloud SQL connector mounted at /cloudsql     |      |
        |   +---------+----------------+--------------------+      |
        |             |                |                           |
        |             v                v                           |
        |      Secret Manager     Cloud SQL for PostgreSQL         |
        |      (3+ secrets)       (BYO instance)                   |
        |                                                          |
        +----------------------------------------------------------+
```

## Related

- [AWS ECS recipe](../aws-ecs/) - sibling deployment recipe for AWS Fargate.
- [Azure Container Apps recipe](../azure-containerapps/) - sibling serverless-container recipe on Azure.
- [Helm chart](../../helm/opsmender/) - Kubernetes (including GKE).
- [Docker compose](../../../docker/docker-compose.yml) - single-host deploy with bundled Postgres.
- [TASKS.md - Sprint 41](../../../docs/TASKS.md) - broader cloud-recipes plan; OCI Container Instances remains.
