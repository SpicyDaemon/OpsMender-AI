// Copy to main.bicepparam.local (gitignored) and fill in your values.
// Then deploy with:
//   az deployment group create \
//     --resource-group <your-rg> \
//     --template-file main.bicep \
//     --parameters main.bicepparam.local

using './main.bicep'

// Identity ─────────────────────────────────────────────────────────────────
param name = 'opsmender'

param tags = {
  Application: 'opsmender-ai'
  Environment: 'prod'
  ManagedBy: 'bicep'
}

// Container ────────────────────────────────────────────────────────────────
// Pin in production:
// param containerImage = 'ghcr.io/shipitpirate/opsmender-ai:v1.0.0'
// param containerCpu    = '1.0'
// param containerMemory = '2Gi'
// param minReplicas     = 1
// param maxReplicas     = 3

// Key Vault ───────────────────────────────────────────────────────────────
// Pre-create the vault and its three secrets:
//
//   az keyvault create -n opsmender-prod -g <rg> -l <location> \
//     --enable-rbac-authorization true
//
//   az keyvault secret set --vault-name opsmender-prod --name opsmender-jwt-secret \
//     --value "$(openssl rand -hex 32)"
//
//   az keyvault secret set --vault-name opsmender-prod --name opsmender-database-url \
//     --value 'postgresql+asyncpg://opsmender:PASSWORD@host:5432/opsmender'
//
//   az keyvault secret set --vault-name opsmender-prod --name opsmender-anthropic-key \
//     --value "$ANTHROPIC_API_KEY"
//
param keyVaultId = '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/opsmender-shared/providers/Microsoft.KeyVault/vaults/opsmender-prod'

// param jwtSecretName        = 'opsmender-jwt-secret'
// param databaseUrlSecretName = 'opsmender-database-url'

param providerSecretNames = {
  ANTHROPIC_API_KEY: 'opsmender-anthropic-key'
  // OPENAI_API_KEY:      'opsmender-openai-key'
  // AZURE_OPENAI_API_KEY: 'opsmender-azure-openai-key'
}

// Optional ACR (leave empty when pulling from public GHCR) ────────────────
// param acrServer = 'opsmenderacr.azurecr.io'

// Runtime config ──────────────────────────────────────────────────────────
param extraEnvironment = {
  OPSMENDER_TIER: '2'
  OPSMENDER_LOG_LEVEL: 'INFO'
  // OPSMENDER_PUBLIC_URL: 'https://opsmender-example.azurecontainerapps.io'
  // OPSMENDER_CORS_ORIGINS: 'https://opsmender.example.com'
}

// param logRetentionDays = 30
