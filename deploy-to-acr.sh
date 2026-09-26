#!/bin/bash
# deploy-to-acr.sh - Build and push container to ACR, then update Container App

set -e

cd "$(dirname "$0")"

echo "Getting ACR credentials from Terraform outputs..."
cd infrastructure
ACR_LOGIN_SERVER=$(terraform output -raw acr_login_server)
ACR_USERNAME=$(terraform output -raw acr_admin_username)
ACR_PASSWORD=$(terraform output -raw acr_admin_password)
cd ..

echo "ACR Login Server: $ACR_LOGIN_SERVER"
echo ""

# Login to ACR
echo "Logging in to ACR..."
echo "$ACR_PASSWORD" | docker login "$ACR_LOGIN_SERVER" -u "$ACR_USERNAME" --password-stdin

# Build image
echo "Building Docker image..."
docker build -t llm-observability-otel:latest .

# Tag for ACR
echo "Tagging image for ACR..."
docker tag llm-observability-otel:latest "$ACR_LOGIN_SERVER/llm-observability-otel:latest"

# Push to ACR
echo "Pushing image to ACR..."
docker push "$ACR_LOGIN_SERVER/llm-observability-otel:latest"

echo ""
echo "✓ Image pushed successfully to $ACR_LOGIN_SERVER/llm-observability-otel:latest"
echo ""
echo "Updating Container App..."
cd infrastructure
terraform apply -auto-approve

echo ""
echo "✓ Deployment complete!"
echo ""
echo "Container App URL:"
terraform output container_app_url
