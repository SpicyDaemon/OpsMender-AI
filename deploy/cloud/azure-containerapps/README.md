# OpsMender on Azure Container Apps

Reference Bicep recipe for deploying OpsMender as a serverless Container App with external HTTPS ingress and HTTP-concurrency autoscaling. **Sprint 41 step 2** — sibling of the AWS ECS recipe at [../aws-ecs/](../aws-ecs/), sharing the same locked-decision shape (VPC/network handled by the platform, Postgres BYO, secrets pulled from the cloud's secret store, framework still ships zero platform knowledge per **D-023**).

If you want a one-line summary: **`az group create`, pre-create a Key Vault with three secrets, fill in `main.bicepparam`, run one `az deployment group create`, open the FQDN.**

## What this recipe creates

| Resource | Purpose |
|---|---|
| `Microsoft.OperationalInsights/workspaces` | Log Analytics workspace receiving container stdout/stderr. |
| `Microsoft.ManagedIdentity/userAssignedIdentities` | User-assigned identity used for Key Vault reads (and ACR pull when configured). |
| `Microsoft.Authorization/roleAssignments` | Grants the managed identity **Key Vault Secrets User** on the operator-supplied vault. |
| `Microsoft.App/managedEnvironments` | Container Apps Environment (Consumption workload profile) bound to the Log Analytics workspace above. |
| `Microsoft.App/containerApps` | The OpsMender workload. External ingress on `containerPort` (8000), TLS handled automatically by ACA on `*.azurecontainerapps.io`, HTTP-concurrency autoscaling between `minReplicas` and `maxReplicas`. |

**Not created by this recipe:** the resource group, the Key Vault, the Key Vault secrets, Azure DB for PostgreSQL, custom DNS records, custom domain bindings. All of those are inputs you pre-create.

## Prerequisites

- **Azure CLI** authenticated against the target subscription. `az login` + `az account set --subscription <sub>`.
- **Bicep CLI** (bundled with recent `az` releases, or install standalone with `brew install bicep`).
- A **resource group** in the region you want to deploy to (`az group create -n opsmender-prod -l eastus`).
- An **Azure Key Vault** with RBAC authorization enabled, holding:
  - `opsmender-jwt-secret` — `openssl rand -hex 32` value.
  - `opsmender-database-url` — `postgresql+asyncpg://user:pass@host:5432/opsmender`.
  - At least one provider key (e.g. `opsmender-anthropic-key`).
- A reachable **Postgres 16+** instance. Azure DB for PostgreSQL Flexible Server is the natural choice; provision separately.

## Quick start

```bash
cd deploy/cloud/azure-containerapps

# 1. Pre-create the resource group (or re-use an existing one).
az group create --name opsmender-prod --location eastus

# 2. Pre-create the Key Vault and three secrets.
az keyvault create -n opsmender-prod -g opsmender-prod -l eastus \
  --enable-rbac-authorization true

az keyvault secret set --vault-name opsmender-prod \
  --name opsmender-jwt-secret --value "$(openssl rand -hex 32)"

az keyvault secret set --vault-name opsmender-prod \
  --name opsmender-database-url \
  --value 'postgresql+asyncpg://opsmender:PASSWORD@host:5432/opsmender'

az keyvault secret set --vault-name opsmender-prod \
  --name opsmender-anthropic-key --value "$ANTHROPIC_API_KEY"

# 3. Copy + edit the parameter file.
cp main.bicepparam main.bicepparam.local
$EDITOR main.bicepparam.local
# Update: keyVaultId, providerSecretNames, anything else.

# 4. Lint + deploy.
az bicep build --file main.bicep

az deployment group create \
  --resource-group opsmender-prod \
  --template-file main.bicep \
  --parameters main.bicepparam.local

# 5. Get the FQDN and open it.
FQDN=$(az containerapp show -g opsmender-prod -n opsmender \
  --query 'properties.configuration.ingress.fqdn' -o tsv)
echo "https://$FQDN"
open "https://$FQDN"
```

ACA terminates TLS automatically on the default `*.azurecontainerapps.io` FQDN — no certificate provisioning needed for the baseline path.

## Verification recipe

```bash
# Confirm the Container App has the expected number of healthy replicas.
az containerapp replica list -g opsmender-prod -n opsmender -o table

# Tail the live log stream.
az containerapp logs show -g opsmender-prod -n opsmender --follow

# Confirm the process and database-backed application are ready.
FQDN=$(az containerapp show -g opsmender-prod -n opsmender \
  --query 'properties.configuration.ingress.fqdn' -o tsv)
curl -sS "https://$FQDN/health/live"
curl -sS "https://$FQDN/health/ready"
# -> {"status":"ready","database":"ok","migrations":"current"}

# Register the first admin (auto-admin per the existing /auth/register logic).
curl -sS -X POST "https://$FQDN/auth/register" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","email":"admin@example.com","password":"securepass123","role":"admin"}'
```

## Common cutover patterns

### Custom domain + managed cert

ACA's free *managed certificate* binds a custom hostname to the Container App with no extra IaC. Provision it via the CLI after the deployment is healthy:

```bash
az containerapp hostname add -g opsmender-prod -n opsmender \
  --hostname opsmender.example.com
# Returns a CNAME validation token. Add the CNAME at your DNS provider, then:
az containerapp hostname bind -g opsmender-prod -n opsmender \
  --hostname opsmender.example.com --environment opsmender-env
# Once the hostname is bound, enable the free managed cert:
az containerapp hostname bind -g opsmender-prod -n opsmender \
  --hostname opsmender.example.com --environment opsmender-env \
  --validation-method CNAME
```

After DNS propagates and the cert is issued, set `OPSMENDER_PUBLIC_URL=https://opsmender.example.com` in `extraEnvironment` and re-deploy so the Slack/Teams deep-link buttons in page cards point at the right host.

### Rolling a new image tag

```bash
# Option A — change containerImage in main.bicepparam.local and re-deploy:
az deployment group create -g opsmender-prod \
  --template-file main.bicep --parameters main.bicepparam.local

# Option B — out-of-band image roll (faster, skips full template):
az containerapp update -g opsmender-prod -n opsmender \
  --image ghcr.io/spicydaemon/opsmender-ai:v1.0.1
```

### Pulling from a private ACR

```bash
# Grant the managed identity AcrPull on the registry.
az role assignment create \
  --role AcrPull \
  --assignee $(az containerapp show -g opsmender-prod -n opsmender \
    --query 'identity.userAssignedIdentities | values(@) | [0].principalId' -o tsv) \
  --scope $(az acr show -n opsmenderacr --query id -o tsv)

# Then set acrServer in main.bicepparam.local and redeploy.
```

## Tear-down

```bash
az group delete -n opsmender-prod --yes --no-wait
```

`az group delete` removes the Container App, the managed environment, the Log Analytics workspace, the managed identity, and the role assignment. It does **not** delete your Key Vault or Postgres instance unless those live in the same resource group (and you almost certainly want them in a separate, longer-lived resource group anyway).

## Architecture summary

```
        ┌──────────────────────────────────────────────────────────────┐
        │  Azure subscription / resource group                         │
        │                                                              │
        │     Internet                                                 │
        │       │                                                      │
        │       ▼                                                      │
        │   ACA-managed L7 ingress (TLS terminated on *.acaio)         │
        │       │                                                      │
        │       ▼                                                      │
        │   ┌─────────────────────────────────────────┐                │
        │   │  Container App  —  port 8000             │               │
        │   │  Replicas autoscale on HTTP concurrency  │               │
        │   │  User-assigned identity reads KV at start │              │
        │   └────┬─────────────────┬──────────────────┬──┘             │
        │        │                 │                  │                │
        │        ▼                 ▼                  ▼                │
        │   Key Vault         Log Analytics       Postgres (BYO)       │
        │   (3+ secrets)      (logs + metrics)    (Azure DB for PG,    │
        │                                          self-managed, …)    │
        │                                                              │
        └──────────────────────────────────────────────────────────────┘
```

## Related

- [AWS ECS recipe](../aws-ecs/) — sibling deployment recipe for AWS Fargate.
- [Helm chart](../../helm/opsmender/) — Kubernetes (any flavor, including AKS).
- [Docker compose](../../../docker/docker-compose.yml) — single-host deploy with bundled Postgres.
- [TASKS.md — Sprint 41](../../../docs/TASKS.md) — broader cloud-recipes plan; GCP Cloud Run + OCI Container Instances tracked as the remaining sub-sprints.
