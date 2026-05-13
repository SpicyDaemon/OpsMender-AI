# Opsmender Helm Chart

Deploys the Opsmender AI (Opsmender) onto Kubernetes: app Deployment + Service, optional Ingress, persistent logs PVC, configurable Postgres (bundled Bitnami subchart) or external DB, and env/secret wiring for Opsmender config plus provider keys.

## Prerequisites

- Kubernetes 1.25+
- Helm 3.10+
- An ingress controller if `ingress.enabled=true`
- A `StorageClass` that supports `ReadWriteOnce` (for logs + bundled Postgres)

## Quick start (bundled Postgres)

```bash
helm dependency update ./deploy/helm/opsmender

helm install opsmender ./deploy/helm/opsmender \
  --namespace opsmender --create-namespace \
  --set secrets.OPSMENDER_JWT_SECRET=$(openssl rand -hex 32) \
  --set secrets.ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
```

Port-forward and open:

```bash
kubectl -n opsmender port-forward svc/opsmender 8000:8000
open http://localhost:8000
```

## Upgrade

```bash
helm upgrade opsmender ./deploy/helm/opsmender -n opsmender -f my-values.yaml
```

## External database

Disable the bundled Postgres and point at your own:

```bash
helm install opsmender ./deploy/helm/opsmender -n opsmender --create-namespace \
  -f ./deploy/helm/opsmender/values-external-db.yaml \
  --set externalDatabase.url='postgresql+asyncpg://user:pass@db.example.com:5432/opsmender' \
  --set secrets.OPSMENDER_JWT_SECRET=$(openssl rand -hex 32)
```

Or reference an existing Secret containing `OPSMENDER_DATABASE_URL`:

```yaml
postgresql:
  enabled: false
externalDatabase:
  existingSecret: opsmender-db
  existingSecretKey: OPSMENDER_DATABASE_URL
```

## Ingress + TLS

```yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: opsmender.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: opsmender-tls
      hosts: [opsmender.example.com]
```

## Configuration

| Key | Description | Default |
|-----|-------------|---------|
| `image.repository` | Container image | `ghcr.io/shipitpirate/ai-incident-manager` |
| `image.tag` | Image tag | `.Chart.AppVersion` |
| `replicaCount` | Replica count (ignored if `autoscaling.enabled`) | `1` |
| `service.type` | Service type | `ClusterIP` |
| `service.port` | Service port | `8000` |
| `ingress.enabled` | Create Ingress | `false` |
| `persistence.enabled` | PVC for `/app/logs` (audit JSONL fallback) | `true` |
| `persistence.size` | Logs PVC size | `5Gi` |
| `postgresql.enabled` | Deploy Bitnami Postgres subchart | `true` |
| `externalDatabase.url` | Async SQLAlchemy URL when subchart disabled | `""` |
| `externalDatabase.existingSecret` | Secret holding `OPSMENDER_DATABASE_URL` | `""` |
| `config.*` | Plain env vars (rendered into ConfigMap) | see `values.yaml` |
| `secrets.*` | Sensitive env vars (rendered into Secret) | see `values.yaml` |
| `existingSecret.name` | Use an existing Secret instead of `secrets.*` | `""` |
| `extraEnv` | Extra env vars passed to the container | `[]` |
| `envFrom` | Extra ConfigMap/Secret refs | `[]` |
| `probes.*` | Liveness/readiness probe tuning | enabled |
| `resources` | Container resource requests/limits | 250m/512Mi → 1/1Gi |
| `autoscaling.enabled` | Enable HPA on CPU | `false` |

`config.*` keys map 1:1 to Opsmender environment variables (see [.env.example](../../../.env.example)). Anything not in `config.*` or `secrets.*` can be added through `extraEnv` / `envFrom`.

## Required secrets

| Key | Notes |
|-----|-------|
| `OPSMENDER_JWT_SECRET` | **Always required.** `openssl rand -hex 32`. |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `AZURE_OPENAI_API_KEY` | Required only for the chosen `OPSMENDER_MODEL_PROVIDER`. |

To use a pre-existing Secret instead of rendering one, set `existingSecret.name=my-secret`. Its keys must match the env var names above.

## Lint / template / dry-run

```bash
helm lint ./deploy/helm/opsmender
helm template opsmender ./deploy/helm/opsmender --debug
helm install opsmender ./deploy/helm/opsmender --dry-run --debug
```

## Uninstall

```bash
helm uninstall opsmender -n opsmender
kubectl delete pvc -n opsmender -l app.kubernetes.io/instance=opsmender   # bundled Postgres + logs
```
