# Cognito user pool. Each user carries an immutable custom:tenant_id; tenant membership is
# assigned by administrators and cannot be changed by the user (not in write_attributes).
resource "aws_cognito_user_pool" "main" {
  name                     = local.name
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OPTIONAL"
  deletion_protection      = var.deletion_protection ? "ACTIVE" : "INACTIVE"

  software_token_mfa_configuration {
    enabled = true
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }

  schema {
    name                     = "tenant_id"
    attribute_data_type      = "String"
    mutable                  = false
    developer_only_attribute = false
    required                 = false
    string_attribute_constraints {
      min_length = 1
      max_length = 64
    }
  }
}

resource "aws_cognito_user_pool_client" "api" {
  name            = "${local.name}-api"
  user_pool_id    = aws_cognito_user_pool.main.id
  generate_secret = false
  explicit_auth_flows = concat(
    ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    var.enable_password_auth_flow ? ["ALLOW_USER_PASSWORD_AUTH"] : [],
  )
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
  id_token_validity             = 60
  access_token_validity         = 60
  refresh_token_validity        = 12
  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "hours"
  }
  read_attributes  = ["email", "email_verified", "custom:tenant_id"]
  write_attributes = ["email"] # users cannot set or change their tenant
}

resource "aws_cognito_user_group" "admin" {
  name         = "admin"
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "Tenant administrators: may share with any group, reprocess and delete documents"
}
