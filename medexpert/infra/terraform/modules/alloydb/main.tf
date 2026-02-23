variable "project_id" { type = string }
variable "region" { type = string }
variable "alloydb_password" {
  type        = string
  sensitive   = true
  description = "Password for the AlloyDB initial user. Must be provided via tfvars or environment variable."
}

resource "google_alloydb_cluster" "medexpert" {
  cluster_id = "medexpert-cluster"
  location   = var.region
  project    = var.project_id

  initial_user {
    user     = "medexpert"
    password = var.alloydb_password
  }

  labels = {
    app = "medexpert"
  }
}

resource "google_alloydb_instance" "primary" {
  cluster       = google_alloydb_cluster.medexpert.name
  instance_id   = "medexpert-primary"
  instance_type = "PRIMARY"

  machine_config {
    cpu_count = 2
  }

  labels = {
    app       = "medexpert"
    component = "session-store"
  }
}

output "connection_uri" {
  value     = google_alloydb_instance.primary.ip_address
  sensitive = true
}
