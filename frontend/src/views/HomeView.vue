<script setup lang="ts">
import TheWelcome from '@/components/TheWelcome.vue'
import { useSeoMeta } from '@unhead/vue'

useSeoMeta({
  title: 'Dashboard',
  description: 'An overview of system status.',
})
</script>

<template>
  <UPage>
    <TheWelcome />
  </UPage>
</template>
