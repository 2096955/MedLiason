output "redis_host" {
  value       = module.memorystore.redis_host
  description = "Memorystore Redis host"
}

output "redis_port" {
  value       = module.memorystore.redis_port
  description = "Memorystore Redis port"
}

output "artifact_bucket" {
  value       = module.storage.artifact_bucket_name
  description = "GCS bucket for artifacts"
}

output "cloud_run_urls" {
  value       = module.cloud_run.service_urls
  description = "Cloud Run service URLs"
}

output "bigquery_dataset" {
  value       = module.bigquery.dataset_id
  description = "BigQuery dataset ID for analytics"
}

output "service_account" {
  value       = module.iam.service_account_email
  description = "Service account email"
}
