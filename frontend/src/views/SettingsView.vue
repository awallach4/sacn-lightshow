<script setup lang="ts">
import JSONEditor from '@/components/JSONEditor.vue'
import { useSeoMeta } from '@unhead/vue'
import { ref } from 'vue'

const editing_main = ref<boolean>(false)
const editing_patch = ref<boolean>(false)

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
    <div class="flex gap-4 justify-center">
      <UButton label="Edit main_config.json" @click="editing_main = true" />
      <UButton label="Edit patch.json" @click="editing_patch = true" />
    </div>
    <JSONEditor
      v-model:open="editing_main"
      title="Edit main_config.json"
      endpoint="/api/config/main_config"
    />
    <JSONEditor v-model:open="editing_patch" title="Edit patch.json" endpoint="/api/config/patch" />
  </UPage>
</template>
