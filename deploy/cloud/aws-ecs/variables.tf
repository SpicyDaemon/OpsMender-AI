## ─────────────────────────────────────────────────────────────────────────
## Identity
## ─────────────────────────────────────────────────────────────────────────

variable "name" {
  description = "Prefix for every resource the module creates (cluster, service, ALB, log group, IAM roles)."
  type        = string
  default     = "opsmender"
}

variable "tags" {
  description = "Tags applied to every taggable resource."
  type        = map(string)
  default = {
    "Application" = "opsmender-ai"
    "ManagedBy"   = "terraform"
  }
}

## ─────────────────────────────────────────────────────────────────────────
## Networking — operator-provided. The module never creates a VPC.
## ─────────────────────────────────────────────────────────────────────────

variable "vpc_id" {
  description = "VPC the service runs in. Must contain `private_subnet_ids` and `public_subnet_ids`."
  type        = string
}

variable "private_subnet_ids" {
  description = "Subnets the Fargate tasks attach to. Need a NAT gateway egress route so the tasks can pull from GHCR and reach the LLM provider."
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "Subnets the internet-facing ALB attaches to (one per AZ, minimum two)."
  type        = list(string)
}

variable "alb_internal" {
  description = "Set to true to make the ALB internal-only (no internet access). Default exposes the dashboard to the internet."
  type        = bool
  default     = false
}

variable "allowed_ingress_cidrs" {
  description = "CIDR blocks allowed to hit the ALB on 80/443. Tighten this for production deployments."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

## ─────────────────────────────────────────────────────────────────────────
## Container
## ─────────────────────────────────────────────────────────────────────────

variable "container_image" {
  description = "Container image to run. Defaults to the public GHCR image published by the release workflow."
  type        = string
  default     = "ghcr.io/shipitpirate/opsmender-ai:latest"
}

variable "container_cpu" {
  description = "Fargate task CPU units. 1024 = 1 vCPU. Allowed pairs are documented at https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html#task_size."
  type        = number
  default     = 1024
}

variable "container_memory" {
  description = "Fargate task memory (MiB). Must form a valid pair with `container_cpu`."
  type        = number
  default     = 2048
}

variable "desired_count" {
  description = "Number of Fargate tasks the service should keep running."
  type        = number
  default     = 1
}

variable "container_port" {
  description = "Port the OpsMender container listens on. Override only if you've changed the Dockerfile's EXPOSE."
  type        = number
  default     = 8000
}

variable "assign_public_ip" {
  description = "Whether Fargate tasks get a public IP. Leave false when the tasks live in private subnets behind a NAT gateway (recommended). Set to true only for VPCs without a NAT (e.g. dev/test)."
  type        = bool
  default     = false
}

## ─────────────────────────────────────────────────────────────────────────
## Secrets — pre-create in Secrets Manager and pass the ARNs.
## ─────────────────────────────────────────────────────────────────────────

variable "jwt_secret_arn" {
  description = "Secrets Manager ARN for OPSMENDER_JWT_SECRET. Create with `aws secretsmanager create-secret --name opsmender/jwt-secret --secret-string $(openssl rand -hex 32)`."
  type        = string
}

variable "database_url_secret_arn" {
  description = "Secrets Manager ARN for OPSMENDER_DATABASE_URL. Value must be `postgresql+asyncpg://user:password@host:port/db`."
  type        = string
}

variable "provider_secret_arns" {
  description = "Map of LLM provider env-var names → Secrets Manager ARNs. Provide at least one. Example: `{ ANTHROPIC_API_KEY = arn:aws:secretsmanager:... }`."
  type        = map(string)
  default     = {}

  validation {
    condition     = length(var.provider_secret_arns) > 0
    error_message = "At least one provider secret is required. Supply ANTHROPIC_API_KEY, OPENAI_API_KEY, or AZURE_OPENAI_API_KEY in `provider_secret_arns`."
  }
}

## ─────────────────────────────────────────────────────────────────────────
## TLS — optional. If unset, ALB listens on :80 only (HTTP).
## ─────────────────────────────────────────────────────────────────────────

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for the ALB HTTPS listener. When set, the ALB redirects :80 → :443 and serves traffic on :443. When empty, the ALB listens on :80 only (operator must layer their own TLS — e.g. CloudFront, API Gateway)."
  type        = string
  default     = ""
}

## ─────────────────────────────────────────────────────────────────────────
## Runtime config — env vars handed to the container.
## ─────────────────────────────────────────────────────────────────────────

variable "extra_environment" {
  description = "Non-secret env vars forwarded to the container. Use this for OPSMENDER_TIER, OPSMENDER_LOG_LEVEL, OPSMENDER_PUBLIC_URL, OLLAMA_BASE_URL, etc."
  type        = map(string)
  default = {
    OPSMENDER_TIER      = "2"
    OPSMENDER_LOG_LEVEL = "INFO"
  }
}

variable "log_retention_days" {
  description = "CloudWatch log group retention in days."
  type        = number
  default     = 30
}
