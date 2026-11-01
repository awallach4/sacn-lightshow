<template>
  <UPage>
    <div class="console-root">
      <!-- Top Display: Tombstones -->
      <main class="console-body mt-12">
        <ChannelGrid />
        <section class="top-bar">
          <div class="status-indicator">LIVE></div>
          <div class="command-preview">{{ commandText }}</div>
        </section>
        <ConsoleKeypad />
      </main>
    </div>
  </UPage>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { useCommandParser } from '@/utils/useCommandParser'
import { useSeoMeta } from '@unhead/vue'

const { commandText, handleKeyPress } = useCommandParser()

// Physical Keyboard Shortcuts for Bench Testing
const onKeyDown = (e: KeyboardEvent) => {
  const isCmdOrCtrl = e.ctrlKey || e.metaKey

  if (isCmdOrCtrl) {
    const key = e.key.toLowerCase()

    if (key === 'a' || e.code === 'KeyA') {
      e.preventDefault()
      e.stopPropagation()
      handleKeyPress('SELECTACTIVE')
    } else if (key === 'l' || e.code === 'KeyL') {
      e.preventDefault()
      e.stopPropagation()
      handleKeyPress('SELECTLAST')
    } else if (key === 'z' || e.code === 'KeyZ') {
      e.preventDefault()
      e.stopPropagation()
      handleKeyPress('UNDO')
    }

    return
  }

  if (e.altKey) {
    if (e.key === '5') {
      e.preventDefault()
      e.stopPropagation()
      handleKeyPress('CHAN_CHECK')
    } else if (e.key === 'a') {
      e.preventDefault()
      e.stopPropagation()
      handleKeyPress('ADDRESS')
    }

    return
  }

  if (e.key >= '0' && e.key <= '9') {
    handleKeyPress(e.key)
  } else if (e.key === 'Enter') {
    handleKeyPress('ENTER')
  } else if (e.key === 'Backspace' || e.key === 'Escape') {
    if (e.shiftKey) {
      handleKeyPress('HARD_CLEAR')
    } else {
      handleKeyPress('CLEAR')
    }
  } else if (e.key === 't' || e.key === 'T') {
    handleKeyPress(' THRU ')
  } else if (e.key === 'a' || e.key === 'A') {
    handleKeyPress(' AT ')
  } else if (e.key === 'f' || e.key === 'F') {
    handleKeyPress('FULL')
  } else if (e.key === 'o' || e.key === 'O') {
    handleKeyPress('OUT')
  } else if (e.key === 'h' || e.key === 'H') {
    handleKeyPress('REMDIM')
  } else if (e.key === '=' || e.key === '+') {
    handleKeyPress('+')
  } else if (e.key === '-' || e.key === '_') {
    handleKeyPress('-')
  } else if (e.key === '/') {
    e.preventDefault()
    e.stopPropagation()
    handleKeyPress('/')
  } else if (e.key === '.') {
    handleKeyPress('.')
  } else if (e.key === 'n' || e.key === 'N') {
    handleKeyPress('SNEAK')
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    e.stopPropagation()
    handleKeyPress('LAST')
  } else if (e.key === 'ArrowDown') {
    e.preventDefault()
    e.stopPropagation()
    handleKeyPress('NEXT')
  }
}

onMounted(() => window.addEventListener('keydown', onKeyDown))
onUnmounted(() => window.removeEventListener('keydown', onKeyDown))

useSeoMeta({
  title: 'Console',
  description: 'Manually control channels with this keypad console.',
})
</script>

<style>
html,
body {
  background: #09090b;
  margin: 0;
  padding: 0;
  min-height: 100vh;
  font-family:
    system-ui,
    -apple-system,
    sans-serif;
  user-select: none;
  -webkit-user-select: none;
}

.console-root {
  display: flex;
  flex-direction: column;
  padding: 16px;
  width: 100%;
  max-width: 1200px; /* Generous max width for large monitors */
  margin: 0 auto;
  box-sizing: border-box;
}

.top-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  background: #18181b;
  border: 1px solid #27272a;
  border-radius: 6px;
  padding: 10px 16px;
  font-family: monospace;
  width: 100%;
  box-sizing: border-box;
}

.status-indicator {
  color: #eab308;
  font-weight: 700;
  font-size: 1rem;
}

.command-preview {
  color: #f4f4f5;
  font-size: 1rem;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Stacked Single-Column Console Stack */
.console-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  width: 100%;
  align-items: center;
}

.display-section {
  width: 100%; /* Full width display monitor */
}

.control-section {
  display: flex;
  justify-content: center;
  width: 100%;
}

/* Mobile Viewport Adaptation (< 640px) */
@media (max-width: 640px) {
  html,
  body {
    touch-action: manipulation;
    overflow: hidden;
    position: fixed;
    width: 100%;
    height: 100%;
  }

  .console-root {
    height: 100dvh;
    padding: 8px;
  }

  .console-body {
    height: 100%;
    justify-content: flex-start;
    gap: 16px;
  }
}
</style>
