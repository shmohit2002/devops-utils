# Container App Environment
resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.common_resource_name_infix}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  infrastructure_subnet_id   = azurerm_subnet.cae.id
  tags                       = var.tags
}

# Container App
resource "azurerm_container_app" "main" {
  name                         = "ca-${local.common_resource_name_infix}-app"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = var.tags

  template {
    container {
      name  = "app"
      image = "nginx:stable-alpine"

      cpu    = 0.25
      memory = "0.5Gi"
    }

    min_replicas = 1
    max_replicas = 10
  }

  ingress {
    external_enabled = true
    target_port      = 80
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}
