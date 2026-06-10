provider "google" {
  project = var.project_id
  region  = local.cluster_region
  zone    = local.cluster_zone
}
