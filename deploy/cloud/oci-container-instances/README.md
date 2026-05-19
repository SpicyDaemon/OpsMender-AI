# OpsMender on Oracle Cloud Infrastructure — Container Instances

Reference Terraform recipe for deploying OpsMender as an OCI Container Instance with a public IP (or, optionally, fronted by an operator-provisioned Network Load Balancer). **Sprint 41 step 4** — closes the multi-cloud deployment recipes round (AWS ECS, Azure Container Apps, GCP Cloud Run, OCI Container Instances) layered on top of `ghcr.io/shipitpirate/opsmender-ai`.

Per locked decision **D-023**, the OpsMender framework ships zero platform-specific knowledge. This recipe is operator-facing — it sets up the surrounding OCI resources (Container Instance, NSG, log group) that run the canonical Docker image.

## What this recipe creates

| Resource | Purpose |
|---|---|
| `oci_container_instances_container_instance` | The OpsMender workload. One container per instance with a flexible shape (default `CI.Standard.E4.Flex`, 1 OCPU / 8 GB), `/health` HTTP health check, restart policy `ALWAYS`. |
| `oci_core_network_security_group` | NSG attached to the instance's VNIC. Ingress on the container port from operator-supplied CIDRs; egress anywhere. |
| `oci_logging_log_group` | OCI Logging log group with operator-controlled retention. Container stdout/stderr flows here when you also configure a service log on the log group (covered as a follow-on `oci logging log create` recipe in the verification section). |

**Not created by this recipe:** the compartment, the VCN, the subnet, the Vault, the Vault secrets, the Postgres database, an OCI Network Load Balancer, DNS records. All of those are inputs you pre-create.

## Secrets caveat

> **Secrets land in Terraform state.** This recipe fetches each Vault secret's value at apply time via `data "oci_secrets_secretbundle"` and injects it as a plain environment variable on the Container Instance. That value is persisted in Terraform state. Always use an encrypted remote backend (OCI Object Storage with KMS encryption, or Terraform Cloud) for production — never commit state files, and never run `terraform apply` against a stateless local backend with real secrets.
>
> A more secure alternative would be to mount Instance Principals on the Container Instance and pull from Vault at startup, but that requires a custom container entrypoint that OpsMender doesn't ship today. Filed as a future tightening if operators ask for it.

## Prerequisites

- **Terraform** 1.6 or newer.
- **OCI CLI** authenticated against the target tenancy. `oci setup config` produces `~/.oci/config`; Terraform's OCI provider reads it automatically.
- An existing **compartment**, **VCN**, and **subnet**. For a public-IP instance the subnet must be public (have an Internet Gateway in its route table). For a private instance, a NAT gateway is required so the container can pull from GHCR and reach the LLM provider.
- An existing **OCI Vault** with three secrets:
  - `opsmender-jwt-secret` — `openssl rand -hex 32`.
  - `opsmender-database-url` — `postgresql+asyncpg://user:pass@host:5432/opsmender`.
  - At least one provider key (e.g. `opsmender-anthropic-key`).
- A reachable **Postgres 16+** instance (OCI Database for PostgreSQL or self-managed).
- An **availability domain** name in the chosen region. List with:
  ```bash
  oci iam availability-domain list --compartment-id <tenancy-ocid>
  ```

## Quick start

```bash
cd deploy/cloud/oci-container-instances

# 1. Copy and fill in the example tfvars file.
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars

# 2. Initialize Terraform and review the plan.
terraform init
terraform plan -out=opsmender.tfplan

# 3. Apply.
terraform apply opsmender.tfplan

# 4. Wait for the Container Instance to reach ACTIVE state (~1-2 min).
INSTANCE_ID=$(terraform output -raw container_instance_id)
while [ "$(oci container-instances container-instance get \
  --container-instance-id "$INSTANCE_ID" \
  --query 'data."lifecycle-state"' --raw-output)" != "ACTIVE" ]; do
  echo "waiting for instance to come up..."
  sleep 10
done

# 5. Hit the dashboard.
URL=$(terraform output -raw dashboard_url)
curl -sS "$URL/health"
# → {"status":"ok"}

open "$URL"
# Click Register; the first user becomes admin.
```

## Verification recipe

```bash
# Confirm the Container Instance is ACTIVE and the embedded container is RUNNING.
INSTANCE_ID=$(terraform output -raw container_instance_id)
oci container-instances container-instance get \
  --container-instance-id "$INSTANCE_ID" \
  --query 'data.{state:"lifecycle-state",containers:containers}' \
  --output table

# Hit /health from the public IP (or via your NLB if you provisioned one).
URL=$(terraform output -raw dashboard_url)
curl -sS "$URL/health"

# Wire container stdout to the OCI Logging log group this recipe created.
# (One-time, post-apply — could be folded into the recipe but kept out
# to keep the baseline small.)
LOG_GROUP_ID=$(terraform output -raw log_group_id)
oci logging log create \
  --log-group-id "$LOG_GROUP_ID" \
  --display-name "${TERRAFORM_NAME:-opsmender}-stdout" \
  --log-type CUSTOM \
  --is-enabled true \
  --retention-duration 30
```

## Common cutover patterns

### Fronting the instance with a Network Load Balancer (recommended for production)

Set `assign_public_ip = false` and `allowed_ingress_cidrs = ["10.0.0.0/16"]` (or the VCN CIDR). Then provision an OCI Network Load Balancer in a public subnet and add the Container Instance's private IP (`terraform output private_ip`) as a backend on port 8000. NLB documentation: <https://docs.oracle.com/en-us/iaas/Content/NetworkLoadBalancer/home.htm>.

For TLS, layer an OCI Load Balancer (Layer 7) in front of the NLB, or terminate TLS at an upstream CDN.

### Rolling a new image tag

```bash
# Option A — change container_image in terraform.tfvars and re-apply.
terraform apply -auto-approve

# Option B — recreate the container in-place (faster).
oci container-instances container-instance restart \
  --container-instance-id $(terraform output -raw container_instance_id)
```

### Pulling from a private Oracle Container Registry (OCIR)

OCI Container Instances accept image pull credentials via `image_pull_secrets`, referencing a Vault secret in the form `{"username": "<tenancy>/<user>", "password": "<auth-token>"}`. This recipe leaves it out of the baseline because the default image lives on public GHCR. Add the block to the `containers {}` body in `main.tf` when you need it.

### Scaling out

OCI Container Instances run as single instances — there is no native autoscaling. The production pattern is:

1. Deploy N instances by running this Terraform module N times (one workspace per instance), or wrap the module call in `for_each` over a list of names.
2. Front them with an OCI Network Load Balancer.
3. Use OCI Monitoring + Alarms to scale manually based on traffic.

## Tear-down

```bash
terraform destroy
```

Terraform removes the Container Instance, the NSG, and the log group. It does **not** delete your VCN, subnet, Vault, Vault secrets, or Postgres database — those were inputs, not module-managed resources.

## Architecture summary

```
        ┌──────────────────────────────────────────────────────────────┐
        │  OCI tenancy / compartment                                   │
        │                                                              │
        │     Internet                                                 │
        │       │                                                      │
        │       ▼                                                      │
        │   ┌──────────────────────────────────┐                       │
        │   │  Public subnet (operator VCN)    │                       │
        │   │   └── Container Instance VNIC    │                       │
        │   │        (NSG attached: :8000)     │                       │
        │   └─────────────┬────────────────────┘                       │
        │                 │                                            │
        │                 ▼                                            │
        │   ┌──────────────────────────────────┐                       │
        │   │  Container Instance               │                      │
        │   │  CI.Standard.E4.Flex (1 OCPU/8 G) │                      │
        │   │  Image: ghcr.io/.../opsmender-ai  │                      │
        │   │  HTTP health-check /health        │                      │
        │   └────┬─────────────┬────────────────┘                      │
        │        │             │                                       │
        │        ▼             ▼                                       │
        │   OCI Vault     OCI Logging       Postgres (BYO)             │
        │   (3+ secrets)  (log group)        OCI Database for          │
        │                                    PostgreSQL / self-mgd     │
        └──────────────────────────────────────────────────────────────┘
```

## Related

- [AWS ECS recipe](../aws-ecs/) — Fargate via Terraform.
- [Azure Container Apps recipe](../azure-containerapps/) — Bicep template.
- [GCP Cloud Run recipe](../gcp-cloud-run/) — `service.yaml`.
- [Helm chart](../../helm/opsmender/) — any flavor of Kubernetes including OCI's OKE.
- [TASKS.md — Sprint 41](../../../docs/TASKS.md) — the broader cloud-recipes plan.
