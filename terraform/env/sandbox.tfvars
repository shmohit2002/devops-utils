datacenter   = "va05"
location     = "eastus"
environment  = "sndbx"
sql_password = "P@ssw0rd1234" # soon will be replaced with a secret
subnets = [
  {
    name              = "snet-va05mril10sndbx-pe"
    address_prefixes  = ["10.0.0.0/26"] #0-63 (64)
    service_endpoints = ["Microsoft.Sql", "Microsoft.KeyVault", "Microsoft.Web"]
  },
  {
    name              = "snet-va05mril10sndbx-agw"
    address_prefixes  = ["10.0.0.64/27"] #64-95 (32)
    # service_endpoints = ["Microsoft.KeyVault"] #Add if the ssl cert comes in picture
  },
  {
    name             = "snet-va05mril10sndbx-cae"
    address_prefixes = ["10.0.0.96/27"] #96-127 (32)
    delegation = [
      {
        name = "delegation"
        service_delegation = [
          {
            name    = "Microsoft.App/environments"
            actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
          }
        ]
      }
    ]
  },
]
