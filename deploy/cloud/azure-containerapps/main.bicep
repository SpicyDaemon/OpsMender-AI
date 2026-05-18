// OpsMender on Azure Container Apps — Sprint 41 step 2.
//
// Deploys into an operator-provided resource group. Creates a Log Analytics
// workspace, a Container Apps managed environment, a user-assigned managed
// identity (granted Key Vault Secrets User on the operator's Key Vault), and
// the Container App itself with external ingress on port 8000.
//
// Inputs the operator must pre-create:
//   - Resource group         (az group create -n <rg> -l <location>)
//   - Azure Key Vault        with the three secrets referenced below
//   - (optional) ACR + image push, OR use the public GHCR image
//
// Per locked decision D-023 the OpsMender framework ships zero platform
// knowledge — this Bicep template is operator-facing infrastructure.

targetScope = 'resourceGroup'

// ─────────────────────────────────────────────────────────────────────────
// Identity
// ─────────────────────────────────────────────────────────────────────────

@description('Prefix for every resource the template creates.')
param name string = 'opsmender'

@description('Azure region. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Tags applied to every resource.')
param tags object = {
  Application: 'opsmender-ai'
  ManagedBy: 'bicep'
}

// ─────────────────────────────────────────────────────────────────────────
// Container
// ─────────────────────────────────────────────────────────────────────────

@description('Container image. Defaults to the public GHCR image published by the release workflow.')
param containerImage string = 'ghcr.io/shipitpirate/opsmender-ai:latest'

@description('Port the OpsMender container listens on. Override only if you have changed the Dockerfile EXPOSE.')
param containerPort int = 8000

@description('CPU cores per replica. ACA accepts fractional values like 0.5, 0.75, 1.0, 2.0.')
param containerCpu string = '1.0'

@description('Memory per replica. Must form a valid pair with cpu. Examples: 1Gi, 2Gi, 4Gi.')
param containerMemory string = '2Gi'

@description('Minimum number of replicas.')
@minValue(0)
@maxValue(25)
param minReplicas int = 1

@description('Maximum number of replicas. ACA autoscales on HTTP-concurrency between min and max.')
@minValue(1)
@maxValue(25)
param maxReplicas int = 3

@description('HTTP concurrency target per replica for the autoscaler.')
param httpConcurrency int = 50

// ─────────────────────────────────────────────────────────────────────────
// Secrets — operator pre-creates these in Key Vault. Pass the vault name +
// the secret names. The deployment grants the managed identity read access
// on the vault.
// ─────────────────────────────────────────────────────────────────────────

@description('Resource ID of the Key Vault holding OpsMender secrets. Format: /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.KeyVault/vaults/<vault>')
param keyVaultId string

@description('Name of the Key Vault secret holding OPSMENDER_JWT_SECRET. Generate the value with `openssl rand -hex 32`.')
param jwtSecretName string = 'opsmender-jwt-secret'

@description('Name of the Key Vault secret holding OPSMENDER_DATABASE_URL. Value must be `postgresql+asyncpg://user:password@host:port/db`.')
param databaseUrlSecretName string = 'opsmender-database-url'

@description('Map of LLM provider env-var names to Key Vault secret names. Example: { ANTHROPIC_API_KEY: \'opsmender-anthropic-key\' }. Provide at least one.')
param providerSecretNames object

// ─────────────────────────────────────────────────────────────────────────
// Optional private registry (ACR)
// ─────────────────────────────────────────────────────────────────────────

@description('Optional Azure Container Registry login server (e.g. opsmenderacr.azurecr.io). Leave empty when pulling from public GHCR.')
param acrServer string = ''

// ─────────────────────────────────────────────────────────────────────────
// Runtime config — env vars handed to the container.
// ─────────────────────────────────────────────────────────────────────────

@description('Non-secret env vars forwarded to the container.')
param extraEnvironment object = {
  OPSMENDER_TIER: '2'
  OPSMENDER_LOG_LEVEL: 'INFO'
}

@description('Log Analytics workspace retention in days.')
@minValue(30)
@maxValue(730)
param logRetentionDays int = 30

// ─────────────────────────────────────────────────────────────────────────
// Resources
// ─────────────────────────────────────────────────────────────────────────

var keyVaultName = last(split(keyVaultId, '/'))

resource logWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${name}-logs'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: logRetentionDays
    workspaceCapping: {
      dailyQuotaGb: -1
    }
  }
}

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${name}-id'
  location: location
  tags: tags
}

// Built-in role definition for "Key Vault Secrets User".
// https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles#key-vault-secrets-user
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource keyVaultRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, managedIdentity.id, keyVaultSecretsUserRoleId)
  properties: {
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${name}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logWorkspace.properties.customerId
        sharedKey: logWorkspace.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

// Build the secrets array referenced by the Container App.
//   - base secrets are JWT + DATABASE_URL (always present).
//   - provider secrets come from the providerSecretNames map (one or more).
// Each entry references the Key Vault secret by URI; the managed identity
// resolves it at startup so no secret material ever lands in template state.

// Constructed deterministically from the vault name so this expression
// is resolvable at deployment start (Bicep BCP182 — `for` bodies cannot
// reference runtime properties of `existing` resources).
var keyVaultUri = 'https://${keyVaultName}${az.environment().suffixes.keyvaultDns}/'

var baseSecrets = [
  {
    name: 'jwt-secret'
    keyVaultUrl: '${keyVaultUri}secrets/${jwtSecretName}'
    identity: managedIdentity.id
  }
  {
    name: 'database-url'
    keyVaultUrl: '${keyVaultUri}secrets/${databaseUrlSecretName}'
    identity: managedIdentity.id
  }
]

var providerSecrets = [
  for envVarName in items(providerSecretNames): {
    name: toLower(replace(envVarName.key, '_', '-'))
    keyVaultUrl: '${keyVaultUri}secrets/${envVarName.value}'
    identity: managedIdentity.id
  }
]

// Env vars for the container = plain values from extraEnvironment plus the
// three flavors of secret references.
var plainEnv = [
  for kvPair in items(extraEnvironment): {
    name: kvPair.key
    value: kvPair.value
  }
]

var baseSecretEnv = [
  {
    name: 'OPSMENDER_JWT_SECRET'
    secretRef: 'jwt-secret'
  }
  {
    name: 'OPSMENDER_DATABASE_URL'
    secretRef: 'database-url'
  }
]

var providerSecretEnv = [
  for envVarName in items(providerSecretNames): {
    name: envVarName.key
    secretRef: toLower(replace(envVarName.key, '_', '-'))
  }
]

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: containerPort
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: empty(acrServer) ? [] : [
        {
          server: acrServer
          identity: managedIdentity.id
        }
      ]
      secrets: concat(baseSecrets, providerSecrets)
    }
    template: {
      containers: [
        {
          name: name
          image: containerImage
          resources: {
            cpu: json(containerCpu)
            memory: containerMemory
          }
          env: concat(plainEnv, baseSecretEnv, providerSecretEnv)
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: containerPort
              }
              initialDelaySeconds: 30
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: containerPort
              }
              periodSeconds: 10
              timeoutSeconds: 5
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: string(httpConcurrency)
              }
            }
          }
        ]
      }
    }
  }
  dependsOn: [
    keyVaultRoleAssignment
  ]
}

// ─────────────────────────────────────────────────────────────────────────
// Outputs
// ─────────────────────────────────────────────────────────────────────────

@description('Default FQDN assigned by Azure Container Apps. ACA terminates TLS automatically on this hostname.')
output fqdn string = containerApp.properties.configuration.ingress.fqdn

@description('Full HTTPS URL operators can open in a browser.')
output dashboardUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'

@description('Container App resource ID. Use with `az containerapp update` for out-of-band redeploys.')
output containerAppId string = containerApp.id

@description('Container Apps Environment resource ID.')
output environmentId string = environment.id

@description('Managed identity principal ID. Attach additional KeyVault / ACR / data-plane roles here if your MCP servers need them.')
output managedIdentityPrincipalId string = managedIdentity.properties.principalId

@description('Log Analytics workspace name. Tail logs with `az containerapp logs show -n <app> -g <rg> --follow`.')
output logWorkspaceName string = logWorkspace.name
