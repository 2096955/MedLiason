variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "GCP region for all resources"
}

variable "environment" {
  type        = string
  default     = "dev"
  description = "Environment name (dev, staging, prod)"
}

variable "redis_tier" {
  type        = string
  default     = "BASIC"
  description = "Memorystore Redis tier (BASIC or STANDARD_HA)"
}

variable "redis_memory_gb" {
  type        = number
  default     = 1
  description = "Redis instance memory size in GB"
}

variable "alloydb_cpu_count" {
  type        = number
  default     = 2
  description = "AlloyDB instance CPU count"
}
