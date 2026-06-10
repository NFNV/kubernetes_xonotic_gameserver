output "project_id" {
  description = "GCP project ID used for this deployment."
  value       = var.project_id
}

output "cluster_name" {
  description = "Name of the GKE cluster."
  value       = google_container_cluster.primary.name
}

output "cluster_location" {
  description = "Cluster location."
  value       = google_container_cluster.primary.location
}

output "cluster_endpoint" {
  description = "GKE API server endpoint."
  value       = google_container_cluster.primary.endpoint
}

output "node_pool_name" {
  description = "Name of the primary node pool."
  value       = google_container_node_pool.primary.name
}

output "default_server_pool_id" {
  description = "Server pool ID used by this single-cluster deployment."
  value       = var.default_server_pool_id
}

output "default_server_pool" {
  description = "Resolved server pool metadata for the current GKE/Agones deployment."
  value       = local.default_server_pool
}

output "server_pools" {
  description = "Configured server pool metadata. Only the default pool is provisioned in this phase."
  value       = local.server_pools
}

output "get_credentials_command" {
  description = "Convenience command for fetching kubeconfig credentials."
  value       = "gcloud container clusters get-credentials ${google_container_cluster.primary.name} --zone ${google_container_cluster.primary.location} --project ${var.project_id}"
}
