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

output "get_credentials_command" {
  description = "Convenience command for fetching kubeconfig credentials."
  value       = "gcloud container clusters get-credentials ${google_container_cluster.primary.name} --zone ${google_container_cluster.primary.location} --project ${var.project_id}"
}
