variable "location" {
  description = "The Azure location"
  type        = string
}
variable "datacenter" {
  description = "The datacenter code"
  type        = string
}
variable "environment" {
  description = "The environment name"
  type        = string
}
variable "sql_username" {
  description = "The SQL Server username"
  type        = string
  default     = "mriadmin"
}
variable "sql_password" {
  description = "The SQL Server password"
  type        = string
}
variable "tags" {
  type    = map(string)
  default = {}
}


variable "subnets" {
  description = "Subnet layout for use with the CCoE network module"
  type = list(
    object({
      name              = string
      address_prefixes  = list(string)
      service_endpoints = optional(list(string))
      network_security_group = optional(object({
        name_or_id = string
        attach     = optional(bool)
      }))

      delegation = optional(list(
        object({
          name = string
          service_delegation = list(
            object({
              name    = string
              actions = optional(list(string))
            })
          )
        })
      ))

      route_table = optional(list(
        object({
          route_table_id         = optional(string)
          name                   = optional(string)
          egress_gateway_address = optional(string)
          tags                   = optional(map(string))
          route = optional(list(
            object({
              name                   = string
              address_prefix         = string
              next_hop_type          = string
              next_hop_in_ip_address = optional(string)
            })
          ))
        })
      ))
    })
  )
  default = []
}
