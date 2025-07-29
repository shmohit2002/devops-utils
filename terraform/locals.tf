locals {
  product                    = "mril10n"
  common_resource_name_infix = "${var.datacenter}${local.product}${var.environment}"
  regional_zones = {
    eastus = ["1", "2", "3"]
  }
  zones = local.regional_zones[var.location]

  tags = merge(var.tags, {
    "BuiltBy"            = "Terraform",
    "BuildRepositoryURL" = "https://github.com/MRI-Software/mri-l10n-service/",
    "Region"             = "NA",
    "Product"            = local.product,
    "BillingBudget"      = "IT",
    "BillingDepartment"  = "Product Development",
    "BillingProduct"     = "PMX",
    "BillingRegion"      = "NA",
    "DataCenter"         = var.datacenter,
    "Environment"        = var.environment,
    "WorkloadName"       = local.product
  })
}
