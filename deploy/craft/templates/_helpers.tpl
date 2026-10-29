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

{{/* A worker container; the heartbeat its probes read is in its own /tmp. */}}
{{- define "craft.worker" -}}
{{- $w := .worker }}
- name: {{ $w.name }}
  {{- include "craft.image" .root | nindent 2 }}
  command: [craft, worker]
  {{- include "craft.env" .root | nindent 2 }}
    - { name: CRAFT_WORKER_QUEUES, value: {{ $w.queues | quote }} }
    {{- if has "interactive" (splitList "," $w.queues) }}
    - { name: CRAFT_NOTEBOOK_IMAGE, value: {{ .root.Values.notebookRuns.image | quote }} }
    {{- end }}
    {{- range $queue, $jobs := $w.concurrency }}
    - { name: CRAFT_WORKER_CONCURRENCY_{{ upper $queue }}, value: {{ $jobs | quote }} }
    {{- end }}
  {{- include "craft.appMounts" $w.name | nindent 2 }}
  # The heartbeat check runs by path on a bare interpreter, never the craft CLI: importing the
  # app takes 10 s or more on a busy machine, and exec probes outlive their timeout and pile up
  # (app/infrastructure/heartbeat.py).
  startupProbe:
    exec: { command: [python, -I, -S, /app/app/infrastructure/heartbeat.py] }
    periodSeconds: 10
    failureThreshold: 18
    timeoutSeconds: 10
  livenessProbe:
    exec: { command: [python, -I, -S, /app/app/infrastructure/heartbeat.py] }
    periodSeconds: 30
    failureThreshold: 3
    timeoutSeconds: 10
  resources:
    limits: { memory: {{ $w.memory }} }
    requests: { memory: {{ .root.Values.memoryRequest }} }
  {{- include "craft.containerSecurity" .root | nindent 2 }}
{{- end }}
