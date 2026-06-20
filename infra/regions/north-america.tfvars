default_server_pool_id = "north-america-default"

server_pools = {
  north-america-default = {
    pool_id          = "north-america-default"
    display_name     = "North America - Default"
    region           = "north-america"
    gcp_region       = "us-central1"
    gcp_zone         = "us-central1-a"
    cluster_name     = "xonotic-na"
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
