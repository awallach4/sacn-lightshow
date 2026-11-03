<script setup lang="ts">
import { ref, computed, watch, nextTick, useTemplateRef } from 'vue'
import { useToast } from '@nuxt/ui/runtime/composables/useToast.js'

const props = defineProps<{
  title?: string
  endpoint: string
}>()

const open = defineModel<boolean>('open', { default: false })

const emit = defineEmits<{
  (e: 'saved'): void
}>()

const toast = useToast()

const jsonContent = ref<string>('')
const isSavingJson = ref(false)

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const textareaRef = ref<any>(null)
const lineNumbersRef = useTemplateRef<HTMLDivElement>('lineNumbersRef')

// Calculate line count for editor gutter
const lineCount = computed(() => {
  if (!jsonContent.value) return 1
  return jsonContent.value.split('\n').length
})

// Sync line number scroll with textarea scroll
const handleScroll = (e: Event) => {
  const target = e.target as HTMLTextAreaElement
  if (lineNumbersRef.value) {
    lineNumbersRef.value.scrollTop = target.scrollTop
  }
}

// Fetch raw JSON configuration from server
const loadConfig = async () => {
  try {
    const res = await fetch(props.endpoint)
    if (!res.ok) return
    const data = await res.json()
    jsonContent.value = data.content
  } catch {
    toast.add({ title: 'Error', description: 'Failed to fetch JSON file.', color: 'error' })
  }
}

// Save JSON Handler with Loading State
const saveJson = async () => {
  isSavingJson.value = true
  try {
    const res = await fetch(props.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: jsonContent.value }),
    })

    if (!res.ok) {
      const errData = await res.json()
      throw new Error(errData.error || 'Failed to save configuration')
    }

    toast.add({
      title: 'Saved',
      description: 'JSON configuration updated successfully.',
      color: 'success',
    })
    open.value = false
    emit('saved')
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
    const parsed = JSON.parse(jsonContent.value)
    jsonContent.value = JSON.stringify(parsed, null, 2)
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

const handleTabKey = (e: KeyboardEvent) => {
  if (e.key === 'Tab') {
    e.preventDefault()
    e.stopPropagation()

    const target = e.target as HTMLTextAreaElement
    const start = target.selectionStart
    const end = target.selectionEnd

    jsonContent.value =
      jsonContent.value.substring(0, start) + '  ' + jsonContent.value.substring(end)

    nextTick(() => {
      target.focus()
      target.setSelectionRange(start + 2, start + 2)
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

// Watch Modal State: Attach handlers on open, re-fetch fresh JSON on close/cancel
watch(open, (isOpen) => {
  if (isOpen) {
    loadConfig()
    attachHandlers()
  }
})
</script>

<template>
  <UModal
    v-model:open="open"
    :title="title || 'Edit JSON'"
    fullscreen
    scrollable
    :dismissible="false"
    :close="{ color: 'error' }"
  >
    <template #body>
      <div
        class="h-[80vh] w-full flex bg-neutral-950 border border-neutral-800 rounded-lg overflow-hidden font-mono text-sm"
      >
        <!-- Line Number Gutter -->
        <div
          ref="lineNumbersRef"
          class="w-12 shrink-0 py-4 select-none text-right pr-3 bg-neutral-900 text-neutral-600 border-r border-neutral-800 overflow-hidden leading-relaxed text-xs sm:text-sm"
        >
          <div v-for="n in lineCount" :key="n">{{ n }}</div>
        </div>

        <!-- Nuxt UI Textarea -->
        <div class="grow h-full relative">
          <UTextarea
            ref="textareaRef"
            v-model="jsonContent"
            class="w-full h-full"
            :ui="{
              root: 'w-full h-full flex flex-col',
              base: 'h-full resize-none font-mono text-xs sm:text-sm p-4 leading-relaxed bg-transparent text-neutral-100 border-0 focus:ring-0',
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
</template>
