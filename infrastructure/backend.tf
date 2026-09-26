terraform {
  # Uncomment backend block to use remote state in Azure Storage
  # backend "azurerm" {
  #   # Backend config can be provided via:
  #   # 1. Command line: terraform init -backend-config="key=value"
  #   # 2. File: terraform init -backend-config=backend.hcl
  #   # 3. Environment variables: ARM_STORAGE_ACCOUNT_NAME, etc.
  #   
  #   # For storage accounts with key-based auth disabled, add:
  #   # use_azuread_auth = true
  #   
  #   # Uncomment and populate for team/production use:
  #   # resource_group_name  = "tfstate-rg"
  #   # storage_account_name = "tfstate<uniqueid>"
  #   # container_name       = "tfstate"
  #   # key                  = "llmobs.terraform.tfstate"
  #   # use_azuread_auth     = true
  # }
}
