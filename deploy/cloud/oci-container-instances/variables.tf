## ─────────────────────────────────────────────────────────────────────────
## Identity
## ─────────────────────────────────────────────────────────────────────────

variable "name" {
  description = "Prefix for every resource the module creates."
  type        = string
  default     = "opsmender"
}

variable "freeform_tags" {
  description = "Freeform tags applied to every taggable resource."
  type        = map(string)
  default = {
    "Application" = "opsmender-ai"
    "ManagedBy"   = "terraform"
  }
}

variable "compartment_id" {
  description = "OCID of the compartment OpsMender deploys into. The module creates the Container Instance, NSG, and log resources here."
  type        = string
}

variable "availability_domain" {
  description = "Availability domain for the Container Instance (e.g. `XYZ:US-ASHBURN-AD-1`). List with `oci iam availability-domain list --compartment-id <tenancy>`."
  type        = string
}

## ─────────────────────────────────────────────────────────────────────────
## Networking — operator-provided. The module never creates a VCN.
## ─────────────────────────────────────────────────────────────────────────

variable "vcn_id" {
  description = "OCID of the VCN that contains `subnet_id`. The module attaches its NSG to this VCN."
  type        = string
}

variable "subnet_id" {
  description = "OCID of the subnet the Container Instance's VNIC attaches to. For a public-IP instance this must be a public subnet."
  type        = string
}

variable "assign_public_ip" {
  description = "Whether the Container Instance gets a public IP. Set to false when fronting with an OCI Network Load Balancer (operator-provisioned, out of scope for this baseline recipe)."
  type        = bool
  default     = true
}

variable "allowed_ingress_cidrs" {
  description = "CIDR blocks allowed to hit the Container Instance on the ingress port. Tighten this for production."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

## ─────────────────────────────────────────────────────────────────────────
## Container
## ─────────────────────────────────────────────────────────────────────────

variable "container_image" {
  description = "Container image to run. Defaults to the public GHCR image published by the release workflow. For private OCIR images, pre-create an auth token and set `image_pull_secret_id` (out of scope for this baseline recipe — OCI Container Instances supports image pull secrets via `image_pull_secrets[].secret_type=BASIC` referencing a Vault secret)."
  type        = string
  default     = "ghcr.io/spicydaemon/opsmender-ai:latest"
}

variable "container_port" {
  description = "Port the OpsMender container listens on. Override only if you have changed the Dockerfile EXPOSE."
  type        = number
  default     = 8000
}

variable "shape" {
  description = "Flexible Compute shape backing the Container Instance. `CI.Standard.E4.Flex` (AMD) and `CI.Standard.E5.Flex` (AMD Genoa) are the common production picks."
  type        = string
  default     = "CI.Standard.E4.Flex"
}

variable "shape_ocpus" {
  description = "OCPUs allocated to the instance. One OCPU ≈ 2 vCPU."
  type        = number
  default     = 1
}

variable "shape_memory_in_gbs" {
  description = "Memory in GiB allocated to the instance."
  type        = number
  default     = 8
}

variable "container_memory_limit_in_gbs" {
  description = "Memory cap for the OpsMender container (must be ≤ `shape_memory_in_gbs`)."
  type        = number
  default     = 4
}

variable "container_vcpus_limit" {
  description = "vCPU cap for the OpsMender container (must be ≤ `shape_ocpus * 2`)."
  type        = number
  default     = 2
}

variable "container_restart_policy" {
  description = "OCI Container Instance restart policy: ALWAYS / NEVER / ON_FAILURE."
  type        = string
  default     = "ALWAYS"

  validation {
    condition     = contains(["ALWAYS", "NEVER", "ON_FAILURE"], var.container_restart_policy)
    error_message = "container_restart_policy must be one of: ALWAYS, NEVER, ON_FAILURE."
  }
}

## ─────────────────────────────────────────────────────────────────────────
## Secrets — operator pre-creates these in OCI Vault. Pass the secret
## OCIDs. The module fetches each at apply time and injects as plain env
## vars on the Container Instance. **Secrets land in Terraform state** —
## use an encrypted remote backend (OCI Object Storage with KMS, or
## Terraform Cloud).
## ─────────────────────────────────────────────────────────────────────────

variable "jwt_secret_ocid" {
  description = "OCID of the Vault secret holding OPSMENDER_JWT_SECRET. Generate the value with `openssl rand -hex 32` and store via `oci vault secret create-base64`."
  type        = string
}

variable "database_url_secret_ocid" {
  description = "OCID of the Vault secret holding OPSMENDER_DATABASE_URL. Value must be `postgresql+asyncpg://user:password@host:port/db`."
  type        = string
}

variable "provider_secret_ocids" {
  description = "Map of LLM provider env-var names to Vault secret OCIDs. Provide at least one. Example: `{ ANTHROPIC_API_KEY = ocid1.vaultsecret.oc1... }`."
  type        = map(string)
  default     = {}

  validation {
    condition     = length(var.provider_secret_ocids) > 0
    error_message = "At least one provider secret is required. Supply ANTHROPIC_API_KEY, OPENAI_API_KEY, or AZURE_OPENAI_API_KEY in `provider_secret_ocids`."
  }
}

## ─────────────────────────────────────────────────────────────────────────
## Runtime config — non-secret env vars handed to the container.
## ─────────────────────────────────────────────────────────────────────────

variable "extra_environment" {
  description = "Non-secret env vars forwarded to the container. Use this for OPSMENDER_TIER, OPSMENDER_LOG_LEVEL, OPSMENDER_PUBLIC_URL, etc."
  type        = map(string)
  default = {
    OPSMENDER_TIER      = "2"
    OPSMENDER_LOG_LEVEL = "INFO"
  }
}

variable "log_retention_duration" {
  description = "OCI Logging retention duration. Allowed values: 30, 60, 90, 120, 180."
  type        = number
  default     = 30
}
