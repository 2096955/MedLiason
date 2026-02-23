variable "project_id" { type = string }
variable "region" { type = string }

# Enable Vertex AI API
resource "google_project_service" "vertex_ai" {
  project = var.project_id
  service = "aiplatform.googleapis.com"

  disable_on_destroy = false
}

# RAG corpus for session history (optional)
resource "google_vertex_ai_feature_online_store" "medexpert" {
  name     = "medexpert-features"
  project  = var.project_id
  region   = var.region

  optimized {}

  labels = {
    app = "medexpert"
  }

  depends_on = [google_project_service.vertex_ai]
}
