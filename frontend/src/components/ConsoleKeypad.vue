<template>
  <div class="keypad-wrapper">
    <div class="keypad">
      <button
        v-for="btn in buttons"
        :key="btn.label"
        :class="['key-btn', btn.type]"
        @click="onButtonClick($event, btn.value)"
      >
        {{ btn.label }}
      </button>
    </div>

    <!-- Undo Action Rail -->
    <div class="side-controls">
      <button
        class="key-btn undo"
        :disabled="historyStack.length === 0"
        @click="handleKeyPress('UNDO')"
      >
        Undo
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useCommandParser } from '@/utils/useCommandParser'

const { handleKeyPress, historyStack } = useCommandParser()

const onButtonClick = (e: MouseEvent, value: string) => {
  if (value === 'CLEAR' && e.shiftKey) {
    handleKeyPress('HARD_CLEAR')
  } else {
    handleKeyPress(value)
  }
}

const buttons = [
  { label: '+', value: ' + ', type: 'syntax' },
  { label: 'Thru', value: ' THRU ', type: 'syntax' },
  { label: '-', value: ' - ', type: 'syntax' },
  { label: '/', value: '/', type: 'syntax' },
  { label: 'Sneak', value: 'SNEAK', type: 'action' },

  { label: '7', value: '7', type: 'num' },
  { label: '8', value: '8', type: 'num' },
  { label: '9', value: '9', type: 'num' },
  { label: 'Rem Dim', value: 'REMDIM', type: 'syntax' },
  { label: 'Home', value: 'HOME', type: 'syntax' },

  { label: '4', value: '4', type: 'num' },
  { label: '5', value: '5', type: 'num' },
  { label: '6', value: '6', type: 'num' },
  { label: 'Out', value: 'OUT', type: 'action' },
  { label: 'Select Last', value: 'SELECTLAST', type: 'action' },

  { label: '1', value: '1', type: 'num' },
  { label: '2', value: '2', type: 'num' },
  { label: '3', value: '3', type: 'num' },
  { label: 'Full', value: 'FULL', type: 'action' },
  { label: 'Select Active', value: 'SELECTACTIVE', type: 'action' },

  { label: 'Clear', value: 'CLEAR', type: 'clear' },
  { label: '0', value: '0', type: 'num' },
  { label: '.', value: '.', type: 'num' },
  { label: 'At', value: ' AT ', type: 'syntax' },
  { label: 'Enter', value: 'ENTER', type: 'enter' },
]
</script>

<style scoped>
/* Exact Desktop Layout (Preserved) */
.keypad-wrapper {
  display: flex;
  gap: 10px;
}

.keypad {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
  background: #18181b;
  padding: 16px;
  border-radius: 8px;
  width: 380px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
}

.side-controls {
  display: flex;
  flex-direction: column;
}

.key-btn {
  height: 54px;
  border: 1px solid #27272a;
  border-radius: 6px;
  font-weight: 700;
  font-size: 0.95rem;
  font-family: monospace;
  cursor: pointer;
  background: #27272a;
  color: #f4f4f5;
  transition: all 0.05s ease;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
  touch-action: manipulation;
}

.key-btn:active {
  transform: translateY(1px);
  background: #3f3f46;
}

.key-btn.syntax {
  background: #1e3a8a;
  color: #93c5fd;
  border-color: #1d4ed8;
}

.key-btn.action {
  background: #78350f;
  color: #fde047;
  border-color: #b45309;
}

.key-btn.enter {
  background: #14532d;
  color: #86efac;
  border-color: #15803d;
}

.key-btn.clear {
  background: #7f1d1d;
  color: #fca5a5;
  border-color: #b91c1c;
}

.key-btn.undo {
  background: #581c87;
  color: #e9d5ff;
  border-color: #7e22ce;
  width: 72px;
}

.key-btn.undo:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

/* Mobile Overrides ONLY */
@media (max-width: 640px) {
  .keypad-wrapper {
    width: 100%;
    gap: 6px;
  }
  .keypad {
    width: auto;
    flex: 1;
    padding: 8px;
    gap: 6px;
  }
  .key-btn {
    height: 48px;
    font-size: 0.8rem;
  }
  .key-btn.undo {
    width: 56px;
  }
}
</style>
