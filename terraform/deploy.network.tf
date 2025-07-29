# not currently using network module from CCoE as old provider is being used there
# module "network" {
#   source = "git::https://mrisoftware@dev.azure.com/mrisoftware/CCoE/_git/terraform-azurerm-virtual-network?ref=v0.2.1"

#   name                = "vnet-${local.common_resource_name_infix}"
#   resource_group_name = azurerm_resource_group.main.name
#   location            = local.location
#   address_space       = var.disaster_recovery ? var.dr_address_space : var.address_space
#   tags                = local.tags

#   depends_on = [azurerm_network_security_group.core]

#   dns_servers = local.dns_servers

#   subnets = [
#     for subnet in local.subnets :
#     {
#       name                   = subnet.name
#       address_prefixes       = subnet.address_prefixes
#       service_endpoints      = subnet.service_endpoints
#       delegation             = subnet.delegation
#       # network_security_group = { name_or_id = azurerm_network_security_group.main.name }
#     }

#   ]

#   # Virtual Network Peers Configuration
#   virtual_network_peers = [{
#     identifier              = "vnp-${local.hub_network.name}"
#     virtual_network_id      = data.azurerm_virtual_network.this.id
#     allow_forwarded_traffic = true
#     use_remote_gateways     = false
#   }]
# }

# resource "azurerm_virtual_network_peering" "this" {
#   provider = azurerm.hub-network
#   # Virtual Network Peering Configuration
#   name                      = "vnp-${local.common_resource_name_infix}"
#   resource_group_name       = data.azurerm_virtual_network.this.resource_group_name
#   virtual_network_name      = data.azurerm_virtual_network.this.name
#   remote_virtual_network_id = module.network.virtual_network_id
# }



# Virtual Network and Subnets
resource "azurerm_virtual_network" "main" {
  name                = "vnet-${local.common_resource_name_infix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  address_space       = ["10.0.0.0/16"]
  tags                = var.tags
}

# Subnets
resource "azurerm_subnet" "agw" {
  name                 = "snet-${local.common_resource_name_infix}-agw"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.0.0/24"]
}

resource "azurerm_subnet" "backend" {
  name                 = "snet-${local.common_resource_name_infix}-app"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.1.0/24"]
}

resource "azurerm_subnet" "cae" {
  name                 = "snet-${local.common_resource_name_infix}-cae"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.2.0/23"]

  delegation {
    name = "dl-container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "private_endpoints" {
  name                 = "snet-${local.common_resource_name_infix}-pe"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.4.0/24"]
}
