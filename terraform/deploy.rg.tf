# Resource Group
resource "azurerm_resource_group" "main" {
  name     = "rg-${local.common_resource_name_infix}"
  location = var.location
  tags     = var.tags
}
