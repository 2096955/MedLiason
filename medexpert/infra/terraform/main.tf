terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
  backend "gcs" {
    bucket = "medexpert-tfstate"
    prefix = "terraform/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

module "iam" {
  source     = "./modules/iam"
  project_id = var.project_id
}

module "storage" {
  source     = "./modules/storage"
  project_id = var.project_id
  region     = var.region
}

module "memorystore" {
  source     = "./modules/memorystore"
  project_id = var.project_id
  region     = var.region
}

module "alloydb" {
  source     = "./modules/alloydb"
  project_id = var.project_id
  region     = var.region
}

module "bigquery" {
  source     = "./modules/bigquery"
  project_id = var.project_id
  region     = var.region
}

module "vertex_ai" {
  source     = "./modules/vertex_ai"
  project_id = var.project_id
  region     = var.region
}

module "cloud_run" {
  source              = "./modules/cloud_run"
  project_id          = var.project_id
  region              = var.region
  redis_host          = module.memorystore.redis_host
  redis_port          = module.memorystore.redis_port
  service_account     = module.iam.service_account_email
  artifact_bucket     = module.storage.artifact_bucket_name
}
