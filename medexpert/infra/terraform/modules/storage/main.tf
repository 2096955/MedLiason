variable "project_id" { type = string }
variable "region" { type = string }

resource "google_storage_bucket" "artifacts" {
  name          = "${var.project_id}-medexpert-artifacts"
  location      = var.region
  project       = var.project_id
  force_destroy = false

  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 90  # Delete artifacts older than 90 days
    }
  }

  labels = {
    app       = "medexpert"
    component = "artifact-store"
  }
}

output "artifact_bucket_name" {
  value = google_storage_bucket.artifacts.name
}
