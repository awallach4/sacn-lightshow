<script setup lang="ts">
import JSONEditor from '@/components/JSONEditor.vue'
import { useSeoMeta } from '@unhead/vue'
import { onMounted, onUnmounted, ref } from 'vue'

const editing_main = ref<boolean>(false)
const editing_patch = ref<boolean>(false)
const editing_schedule = ref<boolean>(false)

const isBackendConnected = ref<boolean>(true)
const system_state = ref<string>('')

let pollInterval: ReturnType<typeof setInterval> | null = null

// Poll backend telemetry quietly
const fetchTelemetry = async () => {
  try {
    const res = await fetch('/api/telemetry')
    if (!res.ok) {
      isBackendConnected.value = false
      return
    }
    const data = await res.json()

    system_state.value = data.system_state
    isBackendConnected.value = true
  } catch {
    // Quietly catch network drops to avoid flooding the console
    isBackendConnected.value = false
  }
}

onMounted(() => {
  fetchTelemetry()
  pollInterval = setInterval(fetchTelemetry, 1000)
})

onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval)
})

useSeoMeta({
  title: 'Settings',
  description: 'Configure show settings and sACN patch files with this editor.',
})
</script>

<template>
  <UPage>
    <UPageHero
      title="Settings"
      description="Edit the main config file and the sACN patch using these editors."
      :ui="{ title: 'mx-0! text-left', description: 'mx-0! text-left' }"
    />
    <div class="flex flex-wrap gap-4 justify-center">
      <UButton
        label="Edit main_config.json"
        :disabled="system_state !== 'OFF' || isBackendConnected === false"
        @click="editing_main = true"
      />
      <UButton
        label="Edit patch.json"
        :disabled="system_state !== 'OFF' || isBackendConnected === false"
        @click="editing_patch = true"
      />
      <UButton
        label="Edit schedule.json"
        :disabled="system_state !== 'OFF' || isBackendConnected === false"
        @click="editing_schedule = true"
      />
    </div>
    <JSONEditor
      v-model:open="editing_main"
      title="Edit main_config.json"
      endpoint="/api/config/main_config"
    />
    <JSONEditor v-model:open="editing_patch" title="Edit patch.json" endpoint="/api/config/patch" />
    <JSONEditor
      v-model:open="editing_schedule"
      title="Edit schedule.json"
      endpoint="/api/config/schedule"
    />
  </UPage>
</template>
