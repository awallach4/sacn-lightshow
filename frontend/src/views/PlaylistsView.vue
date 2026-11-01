<script setup lang="ts">
import type { FormError } from '@nuxt/ui'
import { useToast } from '@nuxt/ui/runtime/composables/useToast.js'
import { useSeoMeta } from '@unhead/vue'
import { reactive, ref, computed, watch, nextTick, onMounted, useTemplateRef } from 'vue'

const state = reactive<{
  audio: File | File[] | null
  lights: File | File[] | null
  playlist: string[] | string
}>({
  audio: null,
  lights: null,
  playlist: [],
})

const editing = ref(false)
const isUploading = ref(false)
const isSavingJson = ref(false)

const json_content = ref<string>('')
const playlists = ref<string[]>([])
const form = useTemplateRef('form')

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const textareaRef = ref<any>(null)
const lineNumbersRef = ref<HTMLDivElement | null>(null)

// Calculate line count for editor gutter
const lineCount = computed(() => {
  if (!json_content.value) return 1
  return json_content.value.split('\n').length
})

// Sync line number scroll with textarea scroll
const handleScroll = (e: Event) => {
  const target = e.target as HTMLTextAreaElement
  if (lineNumbersRef.value) {
    lineNumbersRef.value.scrollTop = target.scrollTop
  }
}

// Reset form helper
const resetForm = () => {
  state.audio = null
  state.lights = null
  state.playlist = []
}

type Schema = typeof state

const validate = (state: Partial<Schema>): FormError[] => {
  const errors = []
  if (!state.audio || (Array.isArray(state.audio) && state.audio.length === 0)) {
    errors.push({ name: 'audio', message: 'Audio File Required' })
  }
  if (!state.lights || (Array.isArray(state.lights) && state.lights.length === 0)) {
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
    const res = await fetch('/api/playlist/config')
    if (!res.ok) return
    const data = await res.json()

    json_content.value = data.content

    const parsed = JSON.parse(data.content)
    if (parsed.playlists) {
      playlists.value = Object.keys(parsed.playlists)
    }
  } catch {
    // Quiet handling when dev server is offline
  }
}

// Form Submit Handler with Loading State
const onSubmit = async () => {
  isUploading.value = true

  const formData = new FormData()
  const rawAudio = Array.isArray(state.audio) ? state.audio[0] : state.audio
  const rawLights = Array.isArray(state.lights) ? state.lights[0] : state.lights
  const selectedPLs = Array.isArray(state.playlist) ? state.playlist : [state.playlist]

  if (rawAudio) formData.append('audio', rawAudio)
  if (rawLights) formData.append('lights', rawLights)
  formData.append('playlists', JSON.stringify(selectedPLs))

  try {
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

// Save JSON Handler with Loading State
const saveJson = async () => {
  isSavingJson.value = true
  try {
    const res = await fetch('/api/playlist/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: json_content.value }),
    })

    if (!res.ok) {
      const errData = await res.json()
      throw new Error(errData.error || 'Failed to save playlist')
    }

    toast.add({
      title: 'Saved',
      description: 'playlist.json updated successfully.',
      color: 'success',
    })
    editing.value = false
    await loadPlaylistConfig()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } catch (err: any) {
    toast.add({
      title: 'Save Error',
      description: err.message || 'Invalid JSON syntax.',
      color: 'error',
    })
  } finally {
    isSavingJson.value = false
  }
}

// Pretty-print JSON helper
const formatJson = () => {
  try {
    const parsed = JSON.parse(json_content.value)
    json_content.value = JSON.stringify(parsed, null, 2)
    toast.add({
      title: 'JSON Validated',
      description: 'JSON formatted and syntax correct!',
      color: 'success',
    })
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } catch (err: any) {
    toast.add({
      title: 'Invalid JSON',
      description: err.message,
      color: 'error',
    })
  }
}

// Native Tab Interceptor & Scroll Sync
const attachHandlers = () => {
  nextTick(() => {
    const el =
      textareaRef.value?.$el?.querySelector('textarea') ||
      textareaRef.value?.$el ||
      textareaRef.value
    if (!el) return

    el.removeEventListener('keydown', handleTabKey)
    el.addEventListener('keydown', handleTabKey)

    el.removeEventListener('scroll', handleScroll)
    el.addEventListener('scroll', handleScroll)
  })
}

const handleTabKey = (e: KeyboardEvent) => {
  if (e.key === 'Tab') {
    e.preventDefault()
    e.stopPropagation()

    const target = e.target as HTMLTextAreaElement
    const start = target.selectionStart
    const end = target.selectionEnd

    json_content.value =
      json_content.value.substring(0, start) + '  ' + json_content.value.substring(end)

    nextTick(() => {
      target.focus()
      target.setSelectionRange(start + 2, start + 2)
    })
  }
}

// Watch Modal State: Attach handlers on open, re-fetch fresh JSON on close/cancel
watch(editing, (isOpen) => {
  if (isOpen) {
    attachHandlers()
  } else {
    // Re-fetch config to revert any unsaved text modifications
    loadPlaylistConfig()
  }
})

onMounted(() => {
  loadPlaylistConfig()
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
      @submit="onSubmit"
      @error="onError"
    >
      <UFormField name="audio">
        <UFileUpload
          v-model="state.audio"
          accept="audio/*"
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
          :disabled="isUploading"
        >
          Submit
        </UButton>

        <UButton
          color="error"
          class="text-neutral-900"
          :disabled="isUploading"
          @click="(form?.clear(), resetForm())"
        >
          Clear
        </UButton>

        <UButton label="Edit playlist.json" color="neutral" @click="editing = true" />
      </div>

      <!-- JSON Modal Editor -->
      <UModal
        v-model:open="editing"
        title="Edit playlist.json"
        fullscreen
        scrollable
        :dismissible="false"
        :close="{ color: 'error' }"
      >
        <template #body>
          <div
            class="h-[80vh] w-full flex bg-neutral-50 dark:bg-neutral-950 border border-neutral-200 dark:border-neutral-800 rounded-lg overflow-hidden font-mono text-sm"
          >
            <!-- Line Number Gutter -->
            <div
              ref="lineNumbersRef"
              class="w-12 shrink-0 py-4 select-none text-right pr-3 bg-neutral-100 dark:bg-neutral-900 text-neutral-400 dark:text-neutral-600 border-r border-neutral-200 dark:border-neutral-800 overflow-hidden leading-relaxed text-xs sm:text-sm"
            >
              <div v-for="n in lineCount" :key="n">{{ n }}</div>
            </div>

            <!-- Nuxt UI Textarea -->
            <div class="grow h-full relative">
              <UTextarea
                ref="textareaRef"
                v-model="json_content"
                class="w-full h-full"
                :ui="{
                  root: 'w-full h-full flex flex-col',
                  base: 'h-full resize-none font-mono text-xs sm:text-sm p-4 leading-relaxed bg-transparent text-neutral-900 dark:text-neutral-100 border-0 focus:ring-0',
                }"
                placeholder="Edit raw JSON configuration..."
                autofocus
              />
            </div>
          </div>
        </template>

        <template #actions>
          <UButton
            color="neutral"
            variant="subtle"
            class="ml-auto mr-4"
            :disabled="isSavingJson"
            @click="formatJson"
          >
            Format JSON
          </UButton>

          <!-- Save Button with Loading State -->
          <UButton class="mr-10" :loading="isSavingJson" :disabled="isSavingJson" @click="saveJson">
            Save
          </UButton>
        </template>
      </UModal>
    </UForm>
  </UPage>
</template>
