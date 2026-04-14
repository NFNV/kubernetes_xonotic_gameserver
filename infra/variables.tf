variable "project_id" {
  description = "Existing GCP project ID where the MVP infrastructure will be created."
  type        = string
}

variable "region" {
  description = "Primary GCP region for the project."
  type        = string
  default     = "southamerica-west1"
}

variable "zone" {
  description = "Zone for the zonal GKE cluster."
  type        = string
  default     = "southamerica-west1-a"
}

variable "environment" {
  description = "Single environment name for this MVP."
  type        = string
  default     = "mvp"
}

variable "cluster_name" {
  description = "Name of the GKE cluster."
  type        = string
  default     = "xonotic-mvp"
}

variable "network_name" {
  description = "Existing VPC network name to use for the cluster."
  type        = string
  default     = "default"
}

variable "subnetwork_name" {
  description = "Existing subnetwork name to use for the cluster."
  type        = string
  default     = "default"
}

variable "node_machine_type" {
  description = "Machine type for the single MVP node pool."
  type        = string
  default     = "e2-medium"
}

variable "node_disk_size_gb" {
  description = "Boot disk size in GB for GKE nodes."
  type        = number
  default     = 100
}

variable "node_disk_type" {
  description = "Boot disk type for GKE nodes."
  type        = string
  default     = "pd-standard"
}

variable "node_count" {
  description = "Number of nodes in the MVP node pool."
  type        = number
  default     = 1
}
