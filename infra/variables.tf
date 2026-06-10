variable "project_id" {
  description = "Existing GCP project ID where the MVP infrastructure will be created."
  type        = string
}

variable "region" {
  description = "Compatibility override for the default server pool GCP region. Prefer server_pools for new configuration."
  type        = string
  default     = null
}

variable "zone" {
  description = "Compatibility override for the default server pool GCP zone. Prefer server_pools for new configuration."
  type        = string
  default     = null
}

variable "environment" {
  description = "Single environment name for this MVP."
  type        = string
  default     = "mvp"
}

variable "cluster_name" {
  description = "Compatibility override for the default server pool GKE cluster name. Prefer server_pools for new configuration."
  type        = string
  default     = null
}

variable "default_server_pool_id" {
  description = "Server pool entry that backs the current single-cluster deployment."
  type        = string
  default     = "south-america-default"
}

variable "server_pools" {
  description = "Configured game server pools. This MVP provisions only the default pool's GKE cluster."
  type = map(object({
    pool_id          = string
    display_name     = string
    region           = string
    gcp_region       = string
    gcp_zone         = string
    cluster_name     = string
    agones_namespace = string
    fleet_name       = string
    udp_port_range   = string
  }))
  default = {
    south-america-default = {
      pool_id          = "south-america-default"
      display_name     = "South America - Default"
      region           = "south-america"
      gcp_region       = "southamerica-west1"
      gcp_zone         = "southamerica-west1-a"
      cluster_name     = "xonotic-mvp"
      agones_namespace = "xonotic-agones"
      fleet_name       = "xonotic-fleet"
      udp_port_range   = "7000-7010"
    }
  }
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
