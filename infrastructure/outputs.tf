output "container_app_url" {
	value       = azurerm_container_app.app.latest_revision_fqdn
	description = "Public FQDN of the demo container app"
}

output "app_insights_connection_string" {
	value       = azurerm_application_insights.appinsights.connection_string
	sensitive   = true
	description = "App Insights connection string for telemetry ingestion"
}

output "azure_openai_endpoint" {
  value       = azurerm_cognitive_account.aoai.endpoint
  description = "Azure OpenAI endpoint URL"
}

output "azure_openai_key" {
  value       = azurerm_cognitive_account.aoai.primary_access_key
  sensitive   = true
  description = "Primary access key for Azure OpenAI account"
}

output "acr_login_server" {
  value       = azurerm_container_registry.acr.login_server
  description = "ACR login server URL"
}

output "acr_admin_username" {
  value       = azurerm_container_registry.acr.admin_username
  description = "ACR admin username"
  sensitive   = true
}

output "acr_admin_password" {
  value       = azurerm_container_registry.acr.admin_password
  description = "ACR admin password"
  sensitive   = true
}