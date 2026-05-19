## OCI Container Instance + Logging.
##
## Container Instances run a single container per instance (or a small
## sidecar set). There is no native autoscaling — scale by deploying
## additional instances behind an OCI Network Load Balancer (operator-
## provisioned, out of scope for this baseline recipe).
##
## Secrets handling: this recipe fetches Vault secret values at apply
## time via `data "oci_secrets_secretbundle"` and injects them as plain
## environment variables on the container. **Secret material lands in
## Terraform state** — use an encrypted remote backend (OCI Object
## Storage with KMS, or Terraform Cloud) for production deployments.
##
## A more secure alternative would be to mount Vault credentials via
## Instance Principals and pull at runtime, but OpsMender doesn't have
## native OCI Vault integration so that wiring would require a custom
## entrypoint.

# ─────────────────────────────────────────────────────────────────────────
# Resolve every Vault secret at apply time.
# ─────────────────────────────────────────────────────────────────────────

data "oci_secrets_secretbundle" "jwt_secret" {
  secret_id = var.jwt_secret_ocid
  stage     = "CURRENT"
}

data "oci_secrets_secretbundle" "database_url" {
  secret_id = var.database_url_secret_ocid
  stage     = "CURRENT"
}

data "oci_secrets_secretbundle" "providers" {
  for_each = var.provider_secret_ocids

  secret_id = each.value
  stage     = "CURRENT"
}

locals {
  # The bundle returns base64-encoded content; decode to the plain value
  # the container expects in its env.
  jwt_secret_value   = base64decode(data.oci_secrets_secretbundle.jwt_secret.secret_bundle_content[0].content)
  database_url_value = base64decode(data.oci_secrets_secretbundle.database_url.secret_bundle_content[0].content)

  provider_secret_values = {
    for env_name, bundle in data.oci_secrets_secretbundle.providers :
    env_name => base64decode(bundle.secret_bundle_content[0].content)
  }

  base_secret_env = [
    {
      name  = "OPSMENDER_JWT_SECRET"
      value = local.jwt_secret_value
    },
    {
      name  = "OPSMENDER_DATABASE_URL"
      value = local.database_url_value
    },
  ]

  provider_secret_env = [
    for env_name, value in local.provider_secret_values : {
      name  = env_name
      value = value
    }
  ]

  plain_env = [
    for k, v in var.extra_environment : {
      name  = k
      value = v
    }
  ]

  environment_variables = merge(
    var.extra_environment,
    {
      OPSMENDER_JWT_SECRET   = local.jwt_secret_value
      OPSMENDER_DATABASE_URL = local.database_url_value
    },
    local.provider_secret_values,
  )
}

# ─────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────

resource "oci_logging_log_group" "this" {
  compartment_id = var.compartment_id
  display_name   = "${var.name}-logs"
  freeform_tags  = var.freeform_tags
}

# ─────────────────────────────────────────────────────────────────────────
# Container Instance
# ─────────────────────────────────────────────────────────────────────────

resource "oci_container_instances_container_instance" "this" {
  compartment_id      = var.compartment_id
  availability_domain = var.availability_domain
  display_name        = var.name
  freeform_tags       = var.freeform_tags

  shape = var.shape
  shape_config {
    ocpus         = var.shape_ocpus
    memory_in_gbs = var.shape_memory_in_gbs
  }

  container_restart_policy = var.container_restart_policy

  containers {
    image_url             = var.container_image
    display_name          = var.name
    environment_variables = local.environment_variables

    resource_config {
      memory_limit_in_gbs = var.container_memory_limit_in_gbs
      vcpus_limit         = var.container_vcpus_limit
    }

    health_checks {
      health_check_type   = "HTTP"
      port                = var.container_port
      path                = "/health"
      interval_in_seconds = 30
      timeout_in_seconds  = 5
      failure_threshold   = 3
      success_threshold   = 1
      name                = "health"
    }
  }

  vnics {
    subnet_id              = var.subnet_id
    is_public_ip_assigned  = var.assign_public_ip
    nsg_ids                = [oci_core_network_security_group.this.id]
    skip_source_dest_check = false
    display_name           = "${var.name}-vnic"
  }
}
