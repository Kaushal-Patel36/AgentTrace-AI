# backend.hcl - External backend configuration
# Usage: terraform init -backend-config=backend.hcl

resource_group_name  = "tfstate-rg"
storage_account_name = "tfstate42983e21"
container_name       = "tfstate"
key                  = "llmobs.terraform.tfstate"
