# Data source to access the configuration of the AzureRM provider
data "azurerm_client_config" "current" {}

# Create Azure Key Vault
resource "azurerm_key_vault" "kv" {
  name                        = "kv-${local.common_resource_name_infix}"
  location                    = azurerm_resource_group.main.location
  resource_group_name         = azurerm_resource_group.main.name
  enabled_for_disk_encryption = true
  tenant_id                   = data.azurerm_client_config.current.tenant_id
  soft_delete_retention_days  = 7
  purge_protection_enabled    = false

  sku_name = "standard"

  access_policy {
    tenant_id = data.azurerm_client_config.current.tenant_id
    object_id = data.azurerm_client_config.current.object_id

    key_permissions = [
      "Get", "List", "Create", "Delete", "Update",
    ]

    secret_permissions = [
      "Get", "List", "Set", "Delete",
    ]

    certificate_permissions = [
      "Get", "List", "Create", "Delete",
    ]
  }
}


# Generate a random password
resource "random_password" "sql" {
  length           = 16
  special          = true
  override_special = "!@#$%&*()-_=+[]{}<>:?"
  keepers = {
    # Only recreate when this value changes
    version = "1"
  }
}

# Create a secret in the Key Vault with the random password
resource "azurerm_key_vault_secret" "password" {
  name         = "sql-${local.common_resource_name_infix}-password"
  value        = random_password.sql.result
  key_vault_id = azurerm_key_vault.kv.id
}
