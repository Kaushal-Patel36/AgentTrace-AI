variable "prefix" {
	type        = string
	default     = "llmobs"
	description = "Name prefix for non-production telemetry demo resources"
}

variable "location" {
	type        = string
	default     = "eastus"
	description = "Azure region"
}

variable "container_image" {
	type        = string
	default     = "ghcr.io/your-org/llm-observability-otel:latest"
	description = "Container image hosting the LangGraph demo"
}

variable "resource_group_name" {
	type        = string
	default     = ""
	description = "Optional existing RG name; blank creates one with prefix"
}

variable "azure_openai_deployment_name" {
	type        = string
	default     = "gpt-4o-mini"
	description = "Azure OpenAI model deployment name used by the app"
}

variable "azure_openai_model_name" {
	type        = string
	default     = "gpt-4o-mini"
	description = "Azure OpenAI base model name (e.g., gpt-4o-mini, gpt-4o, gpt-35-turbo)"
}

variable "azure_openai_model_version" {
	type        = string
	default     = "2024-07-18"
	description = "Azure OpenAI model version"
}

variable "azure_openai_api_version" {
	type        = string
	default     = "2024-08-01-preview"
	description = "API version for Azure OpenAI service calls"
}

variable "azure_openai_sku_name" {
	type        = string
	default     = "S0"
	description = "SKU tier (used only if enable_azure_openai=true)"
}
