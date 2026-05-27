# OpsMender on AWS ECS Fargate

Reference Terraform recipe for deploying OpsMender as a Fargate service behind an Application Load Balancer. **Sprint 41 step 1** — the first of four cloud-specific recipes (ECS, Azure Container Apps, GCP Cloud Run, OCI Container Instances) layering on top of the canonical Docker image at `ghcr.io/spicydaemon/opsmender-ai`.

Per locked decision **D-023**, the OpsMender framework ships zero platform-specific knowledge. This recipe is operator-facing — it sets up the *surrounding* AWS infrastructure (cluster, task def, ALB, IAM roles, log group) that runs the standard image. Adapt and re-use as a starting point; don't expect to `terraform apply` it as-is for every environment.

## What this recipe creates

| Resource | Purpose |
|---|---|
| `aws_ecs_cluster` (Fargate) | Runs the OpsMender container. |
| `aws_ecs_task_definition` | Container spec (image, CPU/memory, env vars, secret references, health check, log driver). |
| `aws_ecs_service` | Keeps `desired_count` tasks running, attached to the target group. |
| `aws_lb` (Application LB) | Internet-facing (or internal) entry point on `:80` (or `:443` when TLS is configured). |
| `aws_lb_target_group` | Health-checks `/health` against each Fargate task on `:8000`. |
| `aws_lb_listener` × 1–2 | Plain `:80` listener, plus an HTTPS `:443` listener when `acm_certificate_arn` is set. HTTP → HTTPS redirect lands automatically when TLS is configured. |
| `aws_security_group` × 2 | One for the ALB (internet ingress on 80/443), one for the tasks (ALB-only ingress on 8000). |
| `aws_iam_role.execution` | Pulled by Fargate to fetch the image, write logs, and read every referenced secret. |
| `aws_iam_role.task` | Empty by default. Attach a policy here if your MCP servers need AWS API access from inside the container. |
| `aws_cloudwatch_log_group` | Container stdout/stderr with `log_retention_days` retention. |

**Not created by this recipe:** VPC, subnets, NAT gateway, Route 53 records, ACM certificates, RDS Postgres, secrets in Secrets Manager. All of those are inputs you supply.

## Prerequisites

- **Terraform** 1.6 or newer.
- **AWS CLI** authenticated against the target account (used by Terraform's AWS provider).
- An existing **VPC** with at least two private subnets (with NAT egress) and two public subnets in different AZs.
- A reachable **Postgres 16+** instance (RDS, Aurora, or self-managed). Its async SQLAlchemy URL (`postgresql+asyncpg://user:pass@host:5432/opsmender`) goes into a Secrets Manager secret.
- One pre-created Secrets Manager secret holding `OPSMENDER_JWT_SECRET`. Generate with `openssl rand -hex 32` and create it via the CLI or console.
- One Secrets Manager secret per LLM provider you intend to use (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `AZURE_OPENAI_API_KEY`). At least one is required.
- *(Optional)* An **ACM certificate** in the same region for the dashboard hostname. Without one, the ALB serves HTTP only — fine for testing, **never for production**.

## Quick start

```bash
cd deploy/cloud/aws-ecs

# 1. Copy the example tfvars file and fill it in.
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars

# 2. Initialize Terraform and review the plan.
terraform init
terraform plan -out=opsmender.tfplan

# 3. Apply.
terraform apply opsmender.tfplan

# 4. Wait for the service to stabilize (~2 min — first task pulls the image,
#    runs Alembic migrations, then passes the /health target-group check).
aws ecs wait services-stable \
  --cluster $(terraform output -raw cluster_name) \
  --services $(terraform output -raw service_name)

# 5. Hit the dashboard. The ALB DNS name comes out of Terraform.
ALB=$(terraform output -raw alb_dns_name)
curl -sS http://$ALB/health
# → {"status":"ok"}

open http://$ALB
# Click Register; the first user becomes admin.
```

## Verification recipe

After `terraform apply` completes:

```bash
# Confirm the ECS service has the right number of tasks running.
aws ecs describe-services \
  --cluster $(terraform output -raw cluster_name) \
  --services $(terraform output -raw service_name) \
  --query 'services[0].{desired:desiredCount,running:runningCount,pending:pendingCount}'

# Tail the live log stream while you exercise the dashboard.
aws logs tail $(terraform output -raw log_group_name) --follow

# Confirm the ALB target group has healthy targets.
TG_ARN=$(aws elbv2 describe-target-groups \
  --names opsmender-tg \
  --query 'TargetGroups[0].TargetGroupArn' \
  --output text)
aws elbv2 describe-target-health --target-group-arn $TG_ARN
```

## Common cutover patterns

### Pointing a custom hostname at the ALB

```bash
# Route 53 ALIAS record (preferred — no extra hop, no TTL cost).
aws route53 change-resource-record-sets --hosted-zone-id $ZONE \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "opsmender.example.com",
        "Type": "A",
        "AliasTarget": {
          "DNSName": "'$(terraform output -raw alb_dns_name)'",
          "HostedZoneId": "'$(terraform output -raw alb_zone_id)'",
          "EvaluateTargetHealth": true
        }
      }
    }]
  }'
```

Once DNS resolves, set `OPSMENDER_PUBLIC_URL=https://opsmender.example.com` in `extra_environment` (so deep-link buttons in Slack/Teams cards point at the right host) and re-apply.

### Rolling a new image tag

```bash
# Update var.container_image in terraform.tfvars, then:
terraform apply -auto-approve

# Or, for an out-of-band redeploy that re-uses the same task def family:
aws ecs update-service \
  --cluster $(terraform output -raw cluster_name) \
  --service $(terraform output -raw service_name) \
  --force-new-deployment
```

### Scaling out

```hcl
# In terraform.tfvars:
desired_count    = 4
container_cpu    = 2048
container_memory = 4096
```

For autoscaling, layer `aws_appautoscaling_target` + `aws_appautoscaling_policy` resources on top of `aws_ecs_service.this` — kept out of this baseline recipe so the surface stays small.

## Tear-down

```bash
terraform destroy
```

Terraform removes the cluster, service, ALB, target group, log group, IAM roles, and security groups. **It does not delete** your VPC, subnets, Secrets Manager secrets, or RDS instance — those were inputs, not module-managed resources.

## Architecture summary

```
        ┌─────────────────────────────────────────────────────────┐
        │  AWS account                                            │
        │                                                         │
        │   Internet                                              │
        │      │                                                  │
        │      ▼                                                  │
        │  ┌────────────────────┐                                 │
        │  │  ALB (public SGs)  │  :80 (and :443 when TLS set)    │
        │  └─────────┬──────────┘                                 │
        │            │ healthcheck /health                        │
        │            ▼                                            │
        │  ┌────────────────────────────────────────────┐         │
        │  │  Fargate tasks (private subnets, NAT egress) │       │
        │  │  Container :8000 — backend.api.app:create_app │      │
        │  │  Reads secrets via execution role at start    │      │
        │  └────┬─────────────┬───────────────┬────────────┘      │
        │       │             │               │                   │
        │       ▼             ▼               ▼                   │
        │  Secrets Mgr    CloudWatch      Postgres                │
        │  (3+ secrets)   (1 log group)   (BYO — RDS, Aurora,…)   │
        │                                                         │
        └─────────────────────────────────────────────────────────┘
```

## Related

- [Helm chart](../../helm/opsmender/) — Kubernetes deploy.
- [Docker compose](../../../docker/docker-compose.yml) — single-host deploy with bundled Postgres.
- [TASKS.md — Sprint 41](../../../docs/TASKS.md) — the broader cloud-recipes plan; Azure Container Apps, Cloud Run, and OCI Container Instances are tracked as follow-on sub-sprints.
