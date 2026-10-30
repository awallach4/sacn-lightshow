<script setup lang="ts">
import { RouterView } from 'vue-router'
import { navLinks } from '@/utils/links'
import { useColorMode } from '@vueuse/core'
import { computed } from 'vue'
import AppHeader from './components/AppHeader.vue'
import { useHead, useSeoMeta } from '@unhead/vue'

const colorMode = useColorMode()

const color = computed(() => (colorMode.value === 'dark' ? '#020618' : 'white'))

useHead({
  meta: [
    { charset: 'utf-8' },
    { name: 'viewport', content: 'width=device-width, initial-scale=1' },
    { key: 'theme-color', name: 'theme-color', content: color },
  ],
  link: [{ rel: 'icon', href: '/favicon.ico' }],
  htmlAttrs: {
    lang: 'en',
  },
})

useSeoMeta({
  titleTemplate: '%s - Lightshow Controls',
})
</script>

<template>
  <UApp>
    <div>
      <UContainer class="sm:border-x border-default pt-10">
        <AppHeader :links="navLinks" />
        <UMain class="relative">
          <RouterView v-slot="{ Component }">
            <tranition name="fade">
              <component :is="Component" />
            </tranition>
          </RouterView>
        </UMain>
      </UContainer>
    </div>
  </UApp>
</template>

<style scoped>
.page-enter-active,
.page-leave-active {
  transition: all 0.4s;
}
.page-enter-from,
.page-leave-to {
  opacity: 0;
  filter: blur(1rem);
}
</style>
