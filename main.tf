terraform {
  required_version = "~> 1.3.9"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      version = "4.57.0"
    }
  }
}

provider "aws" {
  profile = "dev"
  region  = "ap-southeast-2"
}

data "aws_vpc" "default_vpc" {
  default = true
}

output "default_vpc_id" {
  value = data.aws_vpc.default_vpc.id
}
