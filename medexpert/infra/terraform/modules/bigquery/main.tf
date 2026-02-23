variable "project_id" { type = string }
variable "region" { type = string }

resource "google_bigquery_dataset" "medexpert" {
  dataset_id = "medexpert_analytics"
  project    = var.project_id
  location   = var.region

  labels = {
    app = "medexpert"
  }

  default_table_expiration_ms = null

  access {
    role          = "OWNER"
    special_group = "projectOwners"
  }
  access {
    role          = "READER"
    special_group = "projectReaders"
  }
}

resource "google_bigquery_table" "task_logs" {
  dataset_id = google_bigquery_dataset.medexpert.dataset_id
  table_id   = "task_logs"
  project    = var.project_id

  schema = jsonencode([
    { name = "task_id",    type = "STRING",    mode = "REQUIRED" },
    { name = "session_id", type = "STRING",    mode = "REQUIRED" },
    { name = "agent_name", type = "STRING",    mode = "NULLABLE" },
    { name = "step",       type = "STRING",    mode = "NULLABLE" },
    { name = "timestamp",  type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "payload",    type = "JSON",      mode = "NULLABLE" },
  ])

  labels = {
    app       = "medexpert"
    component = "analytics"
  }
}

resource "google_bigquery_table" "feedback" {
  dataset_id = google_bigquery_dataset.medexpert.dataset_id
  table_id   = "feedback"
  project    = var.project_id

  schema = jsonencode([
    { name = "feedback_id", type = "STRING",    mode = "REQUIRED" },
    { name = "task_id",     type = "STRING",    mode = "REQUIRED" },
    { name = "rating",      type = "INTEGER",   mode = "NULLABLE" },
    { name = "comment",     type = "STRING",    mode = "NULLABLE" },
    { name = "timestamp",   type = "TIMESTAMP", mode = "REQUIRED" },
  ])

  labels = {
    app = "medexpert"
  }
}

output "dataset_id" {
  value = google_bigquery_dataset.medexpert.dataset_id
}
