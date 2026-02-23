variable "project_id" { type = string }

resource "google_service_account" "medexpert" {
  account_id   = "medexpert-sa"
  display_name = "MedExpert Service Account"
  project      = var.project_id
}

# Cloud Run invoker
resource "google_project_iam_member" "run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.medexpert.email}"
}

# Redis access
resource "google_project_iam_member" "redis_editor" {
  project = var.project_id
  role    = "roles/redis.editor"
  member  = "serviceAccount:${google_service_account.medexpert.email}"
}

# BigQuery writer
resource "google_project_iam_member" "bq_data_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.medexpert.email}"
}

# GCS artifact access
resource "google_project_iam_member" "storage_admin" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.medexpert.email}"
}

# Vertex AI user
resource "google_project_iam_member" "vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.medexpert.email}"
}

# AlloyDB client
resource "google_project_iam_member" "alloydb_client" {
  project = var.project_id
  role    = "roles/alloydb.client"
  member  = "serviceAccount:${google_service_account.medexpert.email}"
}

output "service_account_email" {
  value = google_service_account.medexpert.email
}
