# Container Apps Environment
resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.common_resource_name_infix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}
