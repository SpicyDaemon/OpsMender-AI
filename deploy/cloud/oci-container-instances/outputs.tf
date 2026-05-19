## The container instance resource doesn't surface its VNIC's IP
## directly — read it back via a `data "oci_core_vnic"` lookup keyed by
## the VNIC OCID that the resource does expose.

data "oci_core_vnic" "primary" {
  vnic_id = oci_container_instances_container_instance.this.vnics[0].vnic_id
}

output "container_instance_id" {
  description = "OCID of the Container Instance. Use with `oci container-instances container-instance get` for status and `oci container-instances container-instance restart` for out-of-band restarts."
  value       = oci_container_instances_container_instance.this.id
}

output "public_ip" {
  description = "Public IP assigned to the Container Instance's VNIC. Empty when `assign_public_ip = false`."
  value       = try(data.oci_core_vnic.primary.public_ip_address, "")
}

output "private_ip" {
  description = "Private IP on the Container Instance's VNIC. Useful when fronting with an OCI Network Load Balancer."
  value       = try(data.oci_core_vnic.primary.private_ip_address, "")
}

output "dashboard_url" {
  description = "Quick HTTP URL pointing at the Container Instance. For production use, layer an NLB + TLS-terminating Load Balancer in front of the instance."
  value       = var.assign_public_ip ? "http://${try(data.oci_core_vnic.primary.public_ip_address, "")}:${var.container_port}" : ""
}

output "nsg_id" {
  description = "OCID of the Network Security Group attached to the instance's VNIC. Attach to additional VNICs or workloads if you scale out."
  value       = oci_core_network_security_group.this.id
}

output "log_group_id" {
  description = "OCID of the OCI Logging log group. Use with the OCI Console or `oci logging-search search-logs` to query container output."
  value       = oci_logging_log_group.this.id
}
