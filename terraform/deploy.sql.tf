
# SQL Server and Elastic Pool
resource "azurerm_mssql_server" "main" {
  name                = "sql-${local.common_resource_name_infix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  version             = "12.0"

  administrator_login          = "mriadmin"
  administrator_login_password = "ChangeMe123!" # Replace with secure password or preferably use a secret from Key Vault
  minimum_tls_version          = "1.2"
  tags                         = var.tags
}

resource "azurerm_mssql_elasticpool" "main" {
  name                = "sep-${local.common_resource_name_infix}s"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  server_name         = azurerm_mssql_server.main.name
  max_size_gb         = 50

  sku {
    name     = "StandardPool"
    tier     = "Standard"
    capacity = 50
  }

  per_database_settings {
    min_capacity = 0
    max_capacity = 10
  }

  tags = var.tags
}

resource "azurerm_mssql_database" "main" {
  name            = "sqldb-${local.common_resource_name_infix}-main"
  server_id       = azurerm_mssql_server.main.id
  elastic_pool_id = azurerm_mssql_elasticpool.main.id
  collation       = "SQL_Latin1_General_CP1_CI_AS"
  max_size_gb     = 2
  read_scale      = false
  zone_redundant  = false
  create_mode     = "Default"

  tags = var.tags
}

# Private Endpoints
resource "azurerm_private_endpoint" "sql" {
  name                = "pe-${local.common_resource_name_infix}-sql"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = var.tags

  private_service_connection {
    name                           = "sql-private-connection"
    private_connection_resource_id = azurerm_mssql_server.main.id
    subresource_names              = ["sqlServer"]
    is_manual_connection           = false
  }
}

# Private DNS Zones
resource "azurerm_private_dns_zone" "sql" {
  name                = "privatelink.database.windows.net"
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "sql" {
  name                  = "sqllink-${local.common_resource_name_infix}"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.sql.name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
  tags                  = var.tags
}

resource "azurerm_private_dns_a_record" "sql" {
  name                = azurerm_mssql_server.main.name
  zone_name           = azurerm_private_dns_zone.sql.name
  resource_group_name = azurerm_resource_group.main.name
  ttl                 = 300
  records             = [azurerm_private_endpoint.sql.private_service_connection.0.private_ip_address]
}
