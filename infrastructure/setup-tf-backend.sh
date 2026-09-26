#!/bin/bash
# setup-tf-backend.sh - Creates Azure Storage backend for Terraform state

set -e

RESOURCE_GROUP="${TF_STATE_RG:-tfstate-rg}"
STORAGE_ACCOUNT="${TF_STATE_SA:-tfstate$(openssl rand -hex 4)}"
CONTAINER_NAME="${TF_STATE_CONTAINER:-tfstate}"
LOCATION="${TF_STATE_LOCATION:-eastus}"

echo "Creating Terraform state backend..."
echo "  Resource Group: $RESOURCE_GROUP"
echo "  Storage Account: $STORAGE_ACCOUNT"
echo "  Container: $CONTAINER_NAME"
echo ""

# Create resource group
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --output none

# Create storage account with Azure AD auth only (key-based auth disabled)
az storage account create \
  --name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku Standard_LRS \
  --encryption-services blob \
  --allow-blob-public-access false \
  --allow-shared-key-access false \
  --output none

# Get current user's object ID
CURRENT_USER=$(az ad signed-in-user show --query id -o tsv)

# Assign Storage Blob Data Contributor role to current user
az role assignment create \
  --role "Storage Blob Data Contributor" \
  --assignee "$CURRENT_USER" \
  --scope "/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$STORAGE_ACCOUNT" \
  --output none

echo "Waiting for role assignment to propagate..."
sleep 10

# Create blob container
az storage container create \
  --name "$CONTAINER_NAME" \
  --account-name "$STORAGE_ACCOUNT" \
  --auth-mode login \
  --output none

echo ""
echo "✓ Backend created successfully!"
echo ""
echo "Update infrastructure/backend.hcl with:"
echo "  resource_group_name  = \"$RESOURCE_GROUP\""
echo "  storage_account_name = \"$STORAGE_ACCOUNT\""
echo "  container_name       = \"$CONTAINER_NAME\""
echo "  key                  = \"llmobs.terraform.tfstate\""
echo "  use_azuread_auth     = true"
echo ""
echo "Then run: cd infrastructure && terraform init -reconfigure -backend-config=backend.hcl"
