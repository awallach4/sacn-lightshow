<script setup lang="ts">
import { useToast } from '@nuxt/ui/runtime/composables/useToast.js'
import { useSeoMeta } from '@unhead/vue'
import { ref, reactive, onMounted, onUnmounted } from 'vue'

const toast = useToast()

// Telemetry State
const cpu_usage = ref<number>(0)
const ram_usage = ref<number>(0)
const cpu_temp = ref<number>(0)
const isBackendConnected = ref<boolean>(true)

const node_status = ref<Record<string, boolean>>({
  'sACN Transmitter': false,
  'Audio Playback': false,
  'Show Control': false,
  Scheduler: false,
  'Web Controls': false,
})

const current_song = ref('')
const next_song = ref('')

// Button Loading States
const isMacroLoading = reactive<Record<string, boolean>>({
  start_show: false,
  prev_track: false,
  next_track: false,
  all_on: false,
  blackout: false,
  panic: false,
})

const isHostLoading = reactive<Record<string, boolean>>({
  reboot: false,
  shutdown: false,
})

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

    cpu_usage.value = data.cpu_usage
    ram_usage.value = data.ram_usage
    cpu_temp.value = data.cpu_temp
    current_song.value = data.current_song
    next_song.value = data.next_song

    if (data.node_status) {
      node_status.value = data.node_status
    }
    isBackendConnected.value = true
  } catch {
    // Quietly catch network drops to avoid flooding the console
    isBackendConnected.value = false
  }
}

// Trigger Show Macros with loading state & toast notifications
const sendMacro = async (action: string) => {
  isMacroLoading[action] = true
  try {
    const res = await fetch('/api/macro', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    })

    if (!res.ok) throw new Error(`Server returned status ${res.status}`)

    toast.add({
      title: 'Macro Sent',
      description: `Action '${action}' successfully dispatched.`,
      color: 'success',
    })
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
  } catch (err) {
    toast.add({
      title: 'Command Failed',
      description: `Could not execute '${action}'. Verify backend service.`,
      color: 'error',
    })
  } finally {
    isMacroLoading[action] = false
  }
}

// Trigger Host Power Actions
const executeHostAction = async (cmd: 'reboot' | 'shutdown') => {
  if (!confirm(`Are you sure you want to ${cmd} the Pi host?`)) return

  isHostLoading[cmd] = true
  try {
    const res = await fetch(`/api/host/${cmd}`, { method: 'POST' })
    if (!res.ok) throw new Error(`Server returned status ${res.status}`)

    toast.add({
      title: 'Host Control Initiated',
      description: `Pi is executing ${cmd}...`,
      color: 'warning',
    })
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
  } catch (err) {
    toast.add({
      title: 'Host Action Failed',
      description: `Could not trigger host ${cmd}.`,
      color: 'error',
    })
  } finally {
    isHostLoading[cmd] = false
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
  title: 'Dashboard',
  description: 'An overview of system status.',
})
</script>

<template>
  <UPage>
    <UPageHero title="Dashboard" :ui="{ title: 'mx-0! text-left' }" />

    <UPageSection :ui="{ container: 'pt-0!' }">
      <UPageGrid class="grid-cols-1 sm:grid-cols-2 lg:grid-cols-2">
        <!-- Show Status -->
        <UPageCard title="Show Status" variant="subtle">
          <UBadge
            v-if="!isBackendConnected"
            color="error"
            variant="soft"
            icon="i-lucide-octagon-alert"
          >
            Backend Offline (Retrying...)
          </UBadge>
          <div v-for="(value, key) in node_status" :key="key">
            <div
              class="flex justify-between items-center py-1 px-4 rounded-lg my-1"
              :class="isBackendConnected && value ? 'bg-green-800/80' : 'bg-red-800/80'"
            >
              <span>{{ key }}</span>
              <UIcon
                :name="
                  isBackendConnected && value ? 'i-lucide-circle-check' : 'i-lucide-octagon-alert'
                "
              />
            </div>
          </div>
        </UPageCard>

        <!-- Show Macros -->
        <UPageCard title="Show Macros" variant="subtle">
          <UButton
            class="justify-center"
            :loading="isMacroLoading.start_show"
            :disabled="current_song.length > 0"
            @click="sendMacro('start_show')"
          >
            Start Show
          </UButton>

          <div class="w-full flex justify-between gap-4">
            <UButton
              color="neutral"
              class="w-full justify-center"
              :loading="isMacroLoading.prev_track"
              :disabled="current_song.length < 1"
              @click="sendMacro('prev_track')"
            >
              Previous Track
            </UButton>
            <UButton
              color="neutral"
              class="w-full justify-center"
              :loading="isMacroLoading.next_track"
              :disabled="current_song.length < 1 || next_song.length < 1"
              @click="sendMacro('next_track')"
            >
              Next Track
            </UButton>
          </div>

          <UButton
            color="info"
            class="justify-center"
            :loading="isMacroLoading.all_on"
            @click="sendMacro('all_on')"
          >
            All ON
          </UButton>
          <UButton
            color="error"
            class="justify-center"
            :loading="isMacroLoading.blackout"
            @click="sendMacro('blackout')"
          >
            Blackout
          </UButton>

          <div class="flex flex-col gap-1 mt-2">
            <strong>Now Playing: {{ current_song || 'None' }}</strong>
            <strong>Next Track: {{ next_song || 'None' }}</strong>
          </div>
        </UPageCard>

        <!-- Host Status -->
        <UPageCard title="Host Status" variant="subtle">
          <UProgress :max="100" color="info" :model-value="cpu_usage" />
          CPU Usage: {{ cpu_usage }}%
          <UProgress :max="100" color="primary" :model-value="ram_usage" />
          RAM Usage: {{ ram_usage }}%
          <UProgress :max="100" color="error" :model-value="cpu_temp" />
          CPU Temp: {{ cpu_temp }} °C
        </UPageCard>

        <!-- Host Control -->
        <UPageCard title="Host Control" variant="subtle">
          <div class="flex flex-col justify-between gap-4">
            <UButton
              color="warning"
              class="justify-center"
              :loading="isMacroLoading.panic"
              @click="sendMacro('panic')"
            >
              PANIC MODE
            </UButton>
            <UButton
              color="neutral"
              class="justify-center"
              :loading="isHostLoading.reboot"
              @click="executeHostAction('reboot')"
            >
              Reboot Host
            </UButton>
            <UButton
              color="error"
              class="justify-center"
              :loading="isHostLoading.shutdown"
              @click="executeHostAction('shutdown')"
            >
              Shutdown Host
            </UButton>
          </div>
        </UPageCard>
      </UPageGrid>
    </UPageSection>
  </UPage>
</template>
