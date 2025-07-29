terraform {
  # backend "azurerm" {}

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "4.37.0"
    }
  }
}

provider "azurerm" {
  features {}
  skip_provider_registration = true
  subscription_id = "9125290c-f368-4e55-8694-d7912fb0f111"
}
