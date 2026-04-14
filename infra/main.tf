locals {
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
  name        = "${var.cluster_name}-agones-udp-26000"
  project     = var.project_id
  network     = var.network_name
  description = "Dev-cluster ingress for the first Agones Xonotic GameServer on UDP 26000."

  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]

  allow {
    protocol = "udp"
    ports    = ["26000"]
  }
}
