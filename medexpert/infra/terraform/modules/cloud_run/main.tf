variable "project_id" { type = string }
variable "region" { type = string }
variable "redis_host" { type = string }
variable "redis_port" { type = number }
variable "service_account" { type = string }
variable "artifact_bucket" { type = string }

locals {
  agents = [
    "orchestrator",
    "literature-specialist",
    "clinical-trials-specialist",
    "drug-specialist",
    "regulatory-specialist",
    "epidemiology-specialist",
    "genomics-specialist",
    "environmental-specialist",
    "provider-intel-specialist",
    "verifier",
    "reviser",
    "triage-intake",
    "triage-orchestrator",
  ]
  mcp_servers = {
    "pubmed"         = 9001
    "clinicaltrials"  = 9002
    "openfda"        = 9003
    "cdc"            = 9004
    "regulatory"     = 9005
    "seer"           = 9006
    "genomic"        = 9007
    "environmental"  = 9008
    "census-sdoh"    = 9009
    "provider-intel" = 9010
    "knowledge-graph" = 9011
    "societies"      = 9012
    "vigibase"       = 9013
  }
}

resource "google_cloud_run_v2_service" "agent" {
  for_each = toset(local.agents)

  name     = "medexpert-${each.key}"
  location = var.region

  template {
    service_account = var.service_account

    containers {
      image = "gcr.io/${var.project_id}/medexpert-agent:latest"

      env {
        name  = "REDIS_URL"
        value = "redis://${var.redis_host}:${var.redis_port}/0"
      }
      env {
        name  = "NAMESPACE"
        value = "medexpert"
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      startup_probe {
        http_get { path = "/health" }
        failure_threshold = 30
        period_seconds    = 10
      }
      liveness_probe {
        http_get { path = "/health" }
        period_seconds = 30
      }
    }

    scaling {
      min_instance_count = 1
      max_instance_count = 3
    }
  }
}

resource "google_cloud_run_v2_service" "mcp" {
  for_each = local.mcp_servers

  name     = "medexpert-mcp-${each.key}"
  location = var.region

  template {
    service_account = var.service_account

    containers {
      image = "gcr.io/${var.project_id}/medexpert-mcp:latest"
      args  = ["mcp_servers.${replace(each.key, "-", "_")}.server"]

      env {
        name  = "MCP_PORT"
        value = tostring(each.value)
      }

      ports {
        container_port = each.value
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
  }
}

resource "google_cloud_run_v2_service" "gateway" {
  name     = "medexpert-gateway"
  location = var.region

  template {
    service_account = var.service_account

    containers {
      image = "gcr.io/${var.project_id}/medexpert-agent:latest"
      args  = ["configs/gateways/webui.yaml"]

      env {
        name  = "REDIS_URL"
        value = "redis://${var.redis_host}:${var.redis_port}/0"
      }
      env {
        name  = "NAMESPACE"
        value = "medexpert"
      }
      env {
        name  = "FASTAPI_HOST"
        value = "0.0.0.0"
      }
      env {
        name  = "FASTAPI_PORT"
        value = "8000"
      }

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      startup_probe {
        http_get { path = "/health" }
        failure_threshold = 30
        period_seconds    = 10
      }
      liveness_probe {
        http_get { path = "/health" }
        period_seconds = 30
      }
    }

    scaling {
      min_instance_count = 1
      max_instance_count = 3
    }
  }
}

output "gateway_url" {
  value = google_cloud_run_v2_service.gateway.uri
}

output "service_urls" {
  value = merge(
    { for k, v in google_cloud_run_v2_service.agent : k => v.uri },
    { for k, v in google_cloud_run_v2_service.mcp : "mcp-${k}" => v.uri },
  )
}
