default_server_pool_id = "europe-default"

server_pools = {
  europe-default = {
    pool_id          = "europe-default"
    display_name     = "Europe - Default"
    region           = "europe"
    gcp_region       = "europe-west1"
    gcp_zone         = "europe-west1-b"
    cluster_name     = "xonotic-eu"
    agones_namespace = "xonotic-agones"
    fleet_name       = "xonotic-fleet"
    udp_port_range   = "7000-7010"
  }
}

environment       = "mvp"
node_machine_type = "e2-medium"
node_disk_size_gb = 100
node_disk_type    = "pd-standard"
node_count        = 1
