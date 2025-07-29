# not currently using network module from CCoE as old provider is being used there
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++ #
#                      APPLICATION GATEWAY                     #

# module "appgw" {
#   source = "git::https://dev.azure.com/mrisoftware/CCoE/_git/terraform-azurerm-application-gateway?ref=v0.0.10"

#   name                = "agw-${local.common_resource_name_infix}"
#   resource_group_name = azurerm_resource_group.main.name
#   location            = azurerm_resource_group.main.location
#   subnet_id           = module.network.subnets[local.subnets[1].name]
#   zones               = local.zones
#   enable_http2        = true


#   # SKU & Autoscale
#   #
#   sku = {
#     name = "WAF_v2"
#     tier = "WAF_v2"
#   }

#   autoscale_configuration = {
#     min_capacity = 1
#     max_capacity = 10
#   }

#   waf_configuration = {
#     enabled          = true
#     firewall_mode    = "Detection"
#     rule_set_type    = "OWASP"
#     rule_set_version = "3.2"
#   }

#   identity = [{
#     type         = "UserAssigned"
#     identity_ids = [local.azurerm_user_assigned_identity-appgw.id]
#   }]

#   # Front end configuration
#   frontend_ip_configuration = [{
#     name = "public"
#     public_ip = {
#       sku               = "Standard"
#       allocation_method = "Static"
#       zones             = local.zones
#     }
#   }]

#   frontend_port = [
#     {
#       name = "http"
#       port = 80
#     },
#     {
#       name = "https"
#       port = 443
#     }
#   ]

#   # SSL Cert
#   #   ssl_certificate = [
#   #     {
#   #       name                = var.ssl_certificate.name
#   #       key_vault_secret_id = var.ssl_certificate.key_vault_secret_id
#   #     },
#   #   ]
#   http_listener = [
#     {
#       name                           = "l10_https"
#       frontend_ip_configuration_name = "public"
#       frontend_port_name             = "https"
#       protocol                       = "Https"
#       #   host_name                      = var.host_name
#       #   ssl_certificate_name           = var.ssl_certificate.name
#     }
#   ]
#   request_routing_rule = [{
#     name                       = "l10_https"
#     backend_address_pool_name  = "bp-nginx-router"
#     backend_http_settings_name = "default_http_settings"
#     http_listener_name         = "l10_https"
#     priority                   = 100
#     rule_type                  = "Basic"
#     rewrite_rule_set_name      = "l10_http_rewrite_set"
#   }]

#   rewrite_rule_set = [
#     {
#       name = "l10_http_rewrite_set"

#       rewrite_rule = [
#         {
#           name          = "hsts_rewrite_rule"
#           rule_sequence = 50

#           response_header_configuration = [
#             {
#               header_name  = "Strict-Transport-Security"
#               header_value = "max-age=2592000; includeSubDomains"
#             }
#           ]
#         }
#       ]
#     }
#   ]

#   backend_address_pool = [
#     {
#       name  = "bp-app"
#       fqdns = [format("%s.%s", substr("ca-${local.common_resource_name_infix}-app", 0, 32), azurerm_container_app_environment.main.default_domain)]
#     }
#   ]

#   backend_http_settings = [
#     {
#       name                                = "default_http_settings"
#       port                                = 443
#       protocol                            = "Https"
#       request_timeout                     = 3650
#       cookie_based_affinity               = "Disabled"
#       probe_name                          = "default_http_probe"
#       pick_host_name_from_backend_address = true
#     }
#   ]
# }

#                                                              #
#                     END APPLICATION GATEWAY                  #
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++ #


# Application Gateway
resource "azurerm_public_ip" "appgw" {
    name                = "pip-${local.common_resource_name_infix}-appgw"
    resource_group_name = azurerm_resource_group.main.name
    location            = azurerm_resource_group.main.location
    allocation_method   = "Static"
    sku                 = "Standard"
    zones               = local.zones
    tags                = var.tags
}

resource "azurerm_application_gateway" "main" {
    name                = "agw-${local.common_resource_name_infix}"
    resource_group_name = azurerm_resource_group.main.name
    location            = azurerm_resource_group.main.location
    enable_http2        = true
    tags                = var.tags

    sku {
        name     = "Standard_v2"
        tier     = "Standard_v2"
        capacity = 2
    }

    gateway_ip_configuration {
        name      = "gateway-ip-config"
        subnet_id = azurerm_subnet.agw.id
    }

    frontend_port {
        name = "http-port"
        port = 80
    }

    frontend_port {
        name = "https-port"
        port = 443
    }

    frontend_ip_configuration {
        name                 = "frontend-ip-config"
        public_ip_address_id = azurerm_public_ip.appgw.id
    }

    backend_address_pool {
        name  = "bp-app"
        fqdns = [format("%s.%s", substr(azurerm_container_app.main.name, 0, 32), azurerm_container_app_environment.main.default_domain)]
    }

    backend_http_settings {
        name                                = "default-http-settings"
        cookie_based_affinity               = "Disabled"
        port                                = 443
        protocol                            = "Https"
        request_timeout                     = 3650
        pick_host_name_from_backend_address = true
        probe_name                          = "default-http-probe"
    }

    # Check what endpoint exists and put it in path
    # probe {
    #     name                                      = "default-http-probe"
    #     protocol                                  = "Https"
    #     path                                      = "/"
    #     interval                                  = 30
    #     timeout                                   = 30
    #     unhealthy_threshold                       = 3
    #     pick_host_name_from_backend_http_settings = true
    # }

    http_listener {
        name                           = "l10-http-listener"
        frontend_ip_configuration_name = "frontend-ip-config"
        frontend_port_name             = "https-port"
        protocol                       = "Https"
    }

    request_routing_rule {
        name                       = "l10-routing-rule"
        rule_type                  = "Basic"
        http_listener_name         = "l10-http-listener"
        backend_address_pool_name  = "bp-app"
        backend_http_settings_name = "default-http-settings"
        priority                   = 100
    }

    rewrite_rule_set {
        name = "l10-http-rewrite-set"

        rewrite_rule {
            name          = "hsts-rewrite-rule"
            rule_sequence = 50

            response_header_configuration {
                header_name  = "Strict-Transport-Security"
                header_value = "max-age=2592000; includeSubDomains"
            }
        }
    }
}
