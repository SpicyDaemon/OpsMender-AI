## Network Security Group attached to the Container Instance's VNIC.
##
## OCI distinguishes between Security Lists (subnet-level) and NSGs
## (VNIC-level). NSGs are the modern choice: rules attach to the
## workload, not the subnet, so multiple workloads in the same subnet
## can have different rule sets.

resource "oci_core_network_security_group" "this" {
  compartment_id = var.compartment_id
  vcn_id         = var.vcn_id
  display_name   = "${var.name}-nsg"
  freeform_tags  = var.freeform_tags
}

# Ingress on the container port from operator-supplied CIDRs.
resource "oci_core_network_security_group_security_rule" "ingress" {
  for_each = toset(var.allowed_ingress_cidrs)

  network_security_group_id = oci_core_network_security_group.this.id
  direction                 = "INGRESS"
  protocol                  = "6" # TCP
  source                    = each.value
  source_type               = "CIDR_BLOCK"
  description               = "OpsMender ingress on ${var.container_port} from ${each.value}"

  tcp_options {
    destination_port_range {
      min = var.container_port
      max = var.container_port
    }
  }
}

# Egress to anywhere — the container needs to reach GHCR (image pull),
# the LLM provider, Postgres, and operator MCP servers.
resource "oci_core_network_security_group_security_rule" "egress" {
  network_security_group_id = oci_core_network_security_group.this.id
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
  description               = "Egress to GHCR, LLM provider, Postgres, MCP servers"
}
