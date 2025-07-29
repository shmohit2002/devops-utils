# provider "azurerm" {
#   features {}
# }

# resource "azurerm_resource_group" "this" {
#   name     = format("rg-%s-%s", local.common_resource_name_infix, "application")
#   location = var.location
# }

# resource "azurerm_virtual_network" "this" {
#   name                = format("vnet-%s", local.common_resource_name_infix)
#   address_space       = ["10.0.0.0/16"]
#   location            = azurerm_resource_group.this.location
#   resource_group_name = azurerm_resource_group.this.name
# }

# resource "azurerm_subnet" "this" {
#   name                 = format("snet-%s", local.common_resource_name_infix)
#   resource_group_name  = azurerm_resource_group.this.name
#   virtual_network_name = azurerm_virtual_network.this.name
#   address_prefixes     = ["10.0.1.0/24"]
# }

# resource "azurerm_private_dns_zone" "this" {
#   name                = "privatelink.database.windows.net"
#   resource_group_name = azurerm_resource_group.this.name
# }

# resource "azurerm_private_dns_zone_virtual_network_link" "this" {
#   name                  = "this-link"
#   resource_group_name   = azurerm_resource_group.this.name
#   private_dns_zone_name = azurerm_private_dns_zone.this.name
#   virtual_network_id    = azurerm_virtual_network.this.id
# }

# resource "azurerm_sql_server" "this" {
#   name                         = "sql-${local.common_resource_name_infix}"
#   resource_group_name          = azurerm_resource_group.this.name
#   location                     = azurerm_resource_group.this.location
#   version                      = "12.0"
#   administrator_login          = var.sql_username
#   administrator_login_password = var.sql_password
# }

# resource "azurerm_sql_database" "this" {
#   name                = "master"
#   resource_group_name = azurerm_resource_group.this.name
#   location            = azurerm_resource_group.this.location
#   server_name         = azurerm_sql_server.this.name
# }

# resource "azurerm_private_endpoint" "this" {
#   name                = format("pe-%s", azurerm_sql_server.this.name)
#   location            = azurerm_resource_group.this.location
#   resource_group_name = azurerm_resource_group.this.name
#   subnet_id           = azurerm_subnet.this.id

#   private_service_connection {
#     name                           = format("pvtsc-sql-%s", azurerm_sql_server.this.name)
#     private_connection_resource_id = azurerm_sql_server.this.id
#     is_manual_connection           = false
#     subresource_names              = ["sqlServer"]
#   }
# }

# resource "azurerm_container_app_environment" "this" {
#   name                = "cae-${local.common_resource_name_infix}"
#   location            = azurerm_resource_group.this.location
#   resource_group_name = azurerm_resource_group.this.name
  
# }

# resource "azurerm_container_app" "this" {
#   name                         = ""
#   revision_mode                = "Single"
#   container_app_environment_id = azurerm_container_app_environment.this.id
#   resource_group_name          = azurerm_resource_group.this.name
#   location                     = azurerm_resource_group.this.location

#   template {
#     container {
#       name   = "this-container"
#       image  = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
#       cpu    = 0.5
#       memory = "1.0Gi"
#     }
#   }
# }
