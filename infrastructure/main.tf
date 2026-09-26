locals {
  rg_name = coalesce(var.resource_group_name, "${var.prefix}-rg")
}

resource "azurerm_resource_group" "rg" {
  name     = local.rg_name
  location = var.location
}

# Azure Container Registry
resource "azurerm_container_registry" "acr" {
  name                = "${replace(var.prefix, "-", "")}acr"
  resource_group_name = azurerm_resource_group.rg.name
  location            = var.location
  sku                 = "Basic"
  admin_enabled       = true
  tags = { application = "llm-observability" }
}

resource "azurerm_log_analytics_workspace" "law" {
  name                = "${var.prefix}-law"
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_application_insights" "appinsights" {
  name                = "${var.prefix}-ai"
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.law.id
}

resource "azurerm_container_app_environment" "cae" {
  name                       = "${var.prefix}-env"
  location                   = var.location
  resource_group_name        = azurerm_resource_group.rg.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.law.id
}

# Azure OpenAI (Cognitive Services) Account
resource "azurerm_cognitive_account" "aoai" {
  name                = "${var.prefix}-aoai"
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  kind                = "OpenAI"
  sku_name            = var.azure_openai_sku_name
  custom_subdomain_name = "${var.prefix}-aoai-${replace(var.location, " ", "")}" 
  tags = { application = "llm-observability" }
}

# Azure OpenAI Model Deployment
resource "azurerm_cognitive_deployment" "aoai_model" {
  name                 = var.azure_openai_deployment_name
  cognitive_account_id = azurerm_cognitive_account.aoai.id
  
  model {
    format  = "OpenAI"
    name    = var.azure_openai_model_name
    version = var.azure_openai_model_version
  }
  
  scale {
    type     = "Standard"
    capacity = 10
  }
}

resource "azurerm_container_app" "app" {
  name                         = "${var.prefix}-app"
  container_app_environment_id = azurerm_container_app_environment.cae.id
  resource_group_name          = azurerm_resource_group.rg.name

  revision_mode = "Single"

  registry {
    server               = azurerm_container_registry.acr.login_server
    username             = azurerm_container_registry.acr.admin_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = azurerm_container_registry.acr.admin_password
  }

  template {
    container {
      name   = "langgraph"
      image  = "${azurerm_container_registry.acr.login_server}/llm-observability-otel:latest"
      cpu    = 0.5
      memory = "1Gi"
      # Basic telemetry env vars
      env {
        name  = "SERVICE_NAME"
        value = "langgraph-multi-agent"
      }
      env {
        name  = "SERVICE_VERSION"
        value = "0.1.0"
      }
      env {
        name  = "APPINSIGHTS_CONNECTION_STRING"
        value = azurerm_application_insights.appinsights.connection_string
      }
      # Mandatory Azure OpenAI configuration
      env {
        name  = "AZURE_OPENAI_ENDPOINT"
        value = azurerm_cognitive_account.aoai.endpoint
      }
      env {
        name  = "AZURE_OPENAI_API_KEY"
        value = azurerm_cognitive_account.aoai.primary_access_key
      }
      env {
        name  = "AZURE_OPENAI_DEPLOYMENT"
        value = var.azure_openai_deployment_name
      }
      env {
        name  = "AZURE_OPENAI_API_VERSION"
        value = var.azure_openai_api_version
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  tags = {
    application = "llm-observability"
  }
}
