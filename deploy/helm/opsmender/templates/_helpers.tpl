{{/*
Expand the name of the chart.
*/}}
{{- define "opsmender.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a fully qualified app name.
*/}}
{{- define "opsmender.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "opsmender.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "opsmender.labels" -}}
helm.sh/chart: {{ include "opsmender.chart" . }}
{{ include "opsmender.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "opsmender.selectorLabels" -}}
app.kubernetes.io/name: {{ include "opsmender.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "opsmender.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "opsmender.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Name of the rendered (or referenced) app secret.
*/}}
{{- define "opsmender.secretName" -}}
{{- if .Values.existingSecret.name -}}
{{- .Values.existingSecret.name -}}
{{- else -}}
{{- printf "%s-secrets" (include "opsmender.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Name of the rendered config map.
*/}}
{{- define "opsmender.configMapName" -}}
{{- printf "%s-config" (include "opsmender.fullname" .) -}}
{{- end -}}

{{/*
Name of the PVC for /app/logs.
*/}}
{{- define "opsmender.pvcName" -}}
{{- if .Values.persistence.existingClaim -}}
{{- .Values.persistence.existingClaim -}}
{{- else -}}
{{- printf "%s-logs" (include "opsmender.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Postgres host (Bitnami subchart releases as <release>-postgresql).
*/}}
{{- define "opsmender.postgresql.host" -}}
{{- printf "%s-postgresql" .Release.Name -}}
{{- end -}}

{{/*
Built-in Postgres URL when the subchart is enabled.
*/}}
{{- define "opsmender.postgresql.url" -}}
{{- $u := .Values.postgresql.auth.username -}}
{{- $p := .Values.postgresql.auth.password -}}
{{- $d := .Values.postgresql.auth.database -}}
{{- printf "postgresql+asyncpg://%s:%s@%s:5432/%s" $u $p (include "opsmender.postgresql.host" .) $d -}}
{{- end -}}
