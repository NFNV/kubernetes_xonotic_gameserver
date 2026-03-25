resource "google_container_cluster" "primary" {
  name     = var.cluster_name
  location = var.zone

  network    = var.network_name
  subnetwork = var.subnetwork_name

  deletion_protection      = false
  remove_default_node_pool = true
  initial_node_count       = 1

  networking_mode = "VPC_NATIVE"

  ip_allocation_policy {}

  logging_service    = "logging.googleapis.com/kubernetes"
  monitoring_service = "monitoring.googleapis.com/kubernetes"

  release_channel {
    channel = "REGULAR"
  }

  resource_labels = local.common_labels

  depends_on = [google_project_service.required]
}

resource "google_container_node_pool" "primary" {
  name     = "${var.cluster_name}-pool"
  cluster  = google_container_cluster.primary.name
  location = var.zone

  node_count = var.node_count

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type = var.node_machine_type
    disk_size_gb = var.node_disk_size_gb
    disk_type    = var.node_disk_type
    image_type   = "COS_CONTAINERD"

    labels = local.common_labels
  }
}
