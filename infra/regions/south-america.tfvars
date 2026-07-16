default_server_pool_id = "south-america-default"

server_pools = {
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

environment       = "mvp"
node_machine_type = "e2-standard-2"
node_disk_size_gb = 100
node_disk_type    = "pd-standard"
node_count        = 1
