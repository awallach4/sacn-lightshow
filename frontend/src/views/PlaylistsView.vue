<script setup lang="ts">
import JSONEditor from '@/components/JSONEditor.vue'
import type { FormError } from '@nuxt/ui'
import { useToast } from '@nuxt/ui/runtime/composables/useToast.js'
import { useSeoMeta } from '@unhead/vue'
import { reactive, ref, onMounted, useTemplateRef, onUnmounted } from 'vue'

const state = reactive<{
  audio: File | null
  lights: File | null
  playlist: string[]
}>({
  audio: null,
  lights: null,
  playlist: [],
})

const editing = ref(false)
const isUploading = ref(false)

const playlists = ref<string[]>([])
const form = useTemplateRef('form')

const isBackendConnected = ref<boolean>(true)
const system_state = ref<string>('')

// Reset form helper
const resetForm = () => {
  state.audio = null
  state.lights = null
  state.playlist = []
}

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

type Schema = typeof state

const validate = (state: Partial<Schema>): FormError[] => {
  const errors = []
  if (!state.audio) {
    errors.push({ name: 'audio', message: 'Audio File Required' })
  }
  if (!state.lights) {
    errors.push({ name: 'lights', message: 'FSEQ File Required' })
  }
  if (!state.playlist || (Array.isArray(state.playlist) && state.playlist.length === 0)) {
    errors.push({ name: 'playlist', message: 'Playlist Selection Required' })
  }
  return errors
}

const toast = useToast()

// Fetch raw JSON configuration from server
const loadPlaylistConfig = async () => {
  try {
    const res = await fetch('/api/config/playlist')
    if (!res.ok) return
    const data = await res.json()

    const parsed = JSON.parse(data.content)
    if (parsed.playlists) {
      playlists.value = Object.keys(parsed.playlists)
    }
  } catch {
    // Quiet handling when dev server is offline
    toast.add({ title: 'Error', description: 'Failed to fetch playlist.json', color: 'error' })
  }
}

// Form Submit Handler with Loading State
const onSubmit = async () => {
  isUploading.value = true

  if (system_state.value !== 'OFF') {
    toast.add({
      title: 'Error',
      description: 'Cannot upload files while show is running.',
      color: 'error',
    })
    return
  }

  const formData = new FormData()
  const validAudioExtensions = ['.mp3', '.wav', '.aac', '.aif', '.ogg', '.flac']
  const validLightsExtensions = ['.fseq']
  const audioExt = state.audio?.name.slice(state.audio.name.lastIndexOf('.')).toLowerCase() || ''
  const lightsExt = state.lights?.name.slice(state.lights.name.lastIndexOf('.')).toLowerCase() || ''

  const selectedPLs = Array.isArray(state.playlist) ? state.playlist : [state.playlist]

  if (state.audio) formData.append('audio', state.audio)
  if (state.lights) formData.append('lights', state.lights)
  formData.append('playlists', JSON.stringify(selectedPLs))

  try {
    if (!validAudioExtensions.includes(audioExt)) {
      throw new Error(
        '[Audio] Please upload a valid audio file (.wav, .mp3, .aac, .aif, .ogg, .flac)',
      )
    }

    if (!validLightsExtensions.includes(lightsExt)) {
      throw new Error('[Lights] Please upload a valid .fseq file')
    }
    if (state.audio && state.audio.size > 150 * 1024 * 1024) {
      throw new Error('[Audio] Max file size is 150 MB')
    }
    if (state.lights && state.lights.size > 5 * 1024 * 1024) {
      throw new Error('[Lights] Max file size is 5 MB')
    }

    const res = await fetch('/api/playlist/upload', {
      method: 'POST',
      body: formData,
    })

    if (!res.ok) {
      const errData = await res.json()
      throw new Error(errData.error || 'Upload failed')
    }

    toast.add({
      title: 'Success',
      description: 'Files uploaded and added to playlists!',
      color: 'success',
    })
    resetForm()
    await loadPlaylistConfig()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } catch (err: any) {
    toast.add({
      title: 'Upload Error',
      description: err.message || 'An error occurred while uploading files',
      color: 'error',
    })
  } finally {
    isUploading.value = false
  }
}

const onError = async () => {
  toast.add({
    title: 'Validation Error',
    description: 'Please select an audio file, an .fseq file, and at least one playlist.',
    color: 'error',
  })
}

onMounted(() => {
  fetchTelemetry()
  loadPlaylistConfig()
  pollInterval = setInterval(fetchTelemetry, 1000)
})

onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval)
})

useSeoMeta({
  title: 'Playlist Editor',
  description: 'Upload music and sequence files and customize playlists.',
})
</script>

<template>
  <UPage>
    <UPageHero title="Playlist Editor" :ui="{ title: 'mx-0! text-left' }" />

    <UForm
      ref="form"
      :validate="validate"
      :state="state"
      class="space-y-4 max-w-lg mx-auto mb-24"
      :disabled="system_state !== 'OFF' || isBackendConnected === false"
      @submit="onSubmit"
      @error="onError"
    >
      <UFormField name="audio">
        <UFileUpload
          v-model="state.audio"
          accept="audio/mp3,audio/wav,audio/ogg,audio/aac,audio/aif,audio/flac"
          label="Drop Music File Here"
          description="MP3 or WAV"
          icon="i-lucide-music"
          size="md"
          layout="list"
        />
      </UFormField>

      <UFormField name="lights">
        <UFileUpload
          v-model="state.lights"
          accept=".fseq"
          label="Drop Sequence File Here"
          description="xLights FSEQ File"
          icon="i-lucide-spotlight"
          size="md"
          layout="list"
        />
      </UFormField>

      <UFormField name="playlist">
        <USelect
          v-model="state.playlist"
          :items="playlists"
          multiple
          placeholder="Select Playlist"
          class="w-full"
        />
      </UFormField>

      <div class="my-4 flex flex-row gap-4">
        <!-- Submit Button with Loading State -->
        <UButton
          type="submit"
          class="text-neutral-900"
          :loading="isUploading"
          :disabled="isUploading || system_state !== 'OFF' || isBackendConnected === false"
        >
          Submit
        </UButton>

        <UButton
          color="error"
          class="text-neutral-900"
          :disabled="isUploading || system_state !== 'OFF' || isBackendConnected === false"
          @click="(form?.clear(), resetForm())"
        >
          Clear
        </UButton>

        <UButton
          label="Edit playlist.json"
          color="neutral"
          :disabled="system_state !== 'OFF' || isBackendConnected === false"
          @click="editing = true"
        />
      </div>

      <JSONEditor
        v-model:open="editing"
        title="Edit playlist.json"
        endpoint="/api/config/playlist"
        @saved="loadPlaylistConfig"
      />
    </UForm>
  </UPage>
</template>
