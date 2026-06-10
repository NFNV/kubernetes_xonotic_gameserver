locals {
  configured_default_server_pool = var.server_pools[var.default_server_pool_id]
  default_server_pool = merge(local.configured_default_server_pool, {
    gcp_region   = coalesce(var.region, local.configured_default_server_pool.gcp_region)
    gcp_zone     = coalesce(var.zone, local.configured_default_server_pool.gcp_zone)
    cluster_name = coalesce(var.cluster_name, local.configured_default_server_pool.cluster_name)
  })
  server_pools = merge(var.server_pools, {
    (var.default_server_pool_id) = local.default_server_pool
  })

  cluster_name         = local.default_server_pool.cluster_name
  cluster_region       = local.default_server_pool.gcp_region
  cluster_zone         = local.default_server_pool.gcp_zone
  fleet_udp_port_range = local.default_server_pool.udp_port_range

  common_labels = {
    environment = var.environment
    managed_by  = "terraform"
    workload    = "xonotic"
  }

  required_services = toset([
    "compute.googleapis.com",
    "container.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "serviceusage.googleapis.com",
  ])
}

resource "google_project_service" "required" {
  for_each = local.required_services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_firewall" "xonotic_agones_gameserver_udp" {
  name        = "${local.cluster_name}-agones-udp-26000"
  project     = var.project_id
  network     = var.network_name
  description = "Dev-cluster ingress for the single GameServer reference on UDP 26000."

  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]

  allow {
    protocol = "udp"
    ports    = ["26000"]
  }
}

resource "google_compute_firewall" "xonotic_agones_fleet_udp_dynamic" {
  name        = "${local.cluster_name}-agones-udp-${local.fleet_udp_port_range}"
  project     = var.project_id
  network     = var.network_name
  description = "Dev-cluster ingress for the Agones Fleet dynamic UDP port range ${local.fleet_udp_port_range}."

  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]

  allow {
    protocol = "udp"
    ports    = [local.fleet_udp_port_range]
  }
}
