{{- define "craft.image" -}}
image: {{ printf "%s:%s" .Values.image.repository .Values.image.tag | quote }}
imagePullPolicy: {{ .Values.image.pullPolicy }}
{{- end }}

{{/* The app's settings, as compose passes them: listed keys from the Secret, then fixed ones. */}}
{{- define "craft.env" -}}
env:
{{- range .Values.settings }}
  - name: {{ . }}
    valueFrom:
      secretKeyRef: { name: {{ $.Values.secret }}, key: {{ . }}, optional: true }
{{- end }}
  - { name: CRAFT_PROVIDER_ROOT, value: /provider }
  - { name: CRAFT_DATABASE_HOST, value: {{ .Release.Name }}-postgres }
  - { name: CRAFT_DATABASE_PORT, value: "5432" }
{{- end }}

{{- define "craft.containerSecurity" -}}
securityContext:
  runAsNonRoot: true
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: [ALL] }
{{- end }}

{{/* The read-only provider files and the container's own writable /tmp. */}}
{{- define "craft.appMounts" -}}
volumeMounts:
  - { name: provider, mountPath: /provider, readOnly: true }
  - { name: tmp-{{ . }}, mountPath: /tmp }
{{- end }}

{{- define "craft.provider" -}}
- name: provider
{{- if .Values.provider.hostPath }}
  hostPath: { path: {{ .Values.provider.hostPath | quote }}, type: Directory }
{{- else }}
  emptyDir: {}
{{- end }}
{{- end }}

{{/* A worker container; the heartbeat `craft worker --check` reads is in its own /tmp. */}}
{{- define "craft.worker" -}}
{{- $w := .worker }}
- name: {{ $w.name }}
  {{- include "craft.image" .root | nindent 2 }}
  command: [craft, worker]
  {{- include "craft.env" .root | nindent 2 }}
    - { name: CRAFT_WORKER_QUEUES, value: {{ $w.queues | quote }} }
    {{- range $queue, $jobs := $w.concurrency }}
    - { name: CRAFT_WORKER_CONCURRENCY_{{ upper $queue }}, value: {{ $jobs | quote }} }
    {{- end }}
  {{- include "craft.appMounts" $w.name | nindent 2 }}
  # The check starts the craft CLI, which takes several seconds, so the timeouts are compose's.
  startupProbe:
    exec: { command: [craft, worker, --check] }
    periodSeconds: 10
    failureThreshold: 18
    timeoutSeconds: 30
  livenessProbe:
    exec: { command: [craft, worker, --check] }
    periodSeconds: 30
    failureThreshold: 3
    timeoutSeconds: 30
  resources:
    limits: { memory: {{ $w.memory }} }
    requests: { memory: {{ .root.Values.memoryRequest }} }
  {{- include "craft.containerSecurity" .root | nindent 2 }}
{{- end }}
