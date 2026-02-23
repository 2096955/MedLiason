variable "project_id" { type = string }
variable "region" { type = string }

resource "google_redis_instance" "medexpert" {
  name           = "medexpert-redis"
  tier           = "BASIC"
  memory_size_gb = 1
  region         = var.region
  project        = var.project_id

  redis_version = "REDIS_7_0"

  labels = {
    app         = "medexpert"
    component   = "memory-plane"
  }
}

output "redis_host" {
  value = google_redis_instance.medexpert.host
}

output "redis_port" {
  value = google_redis_instance.medexpert.port
}
