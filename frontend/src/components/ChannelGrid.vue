<template>
  <div class="tombstone-grid">
    <div
      v-for="(val, chan) in channelLevels"
      :key="chan"
      :class="[
        'tombstone',
        {
          manual: isManual[Number(chan)],
          selected: selectedChannels.includes(Number(chan)),
        },
      ]"
      @click="onTombstoneClick(Number(chan))"
    >
      <div class="chan-hdr">
        <span class="chan-num">{{ chan }}</span>
      </div>
      <div class="chan-val">
        {{ val === 100 ? 'FL' : val === 0 && isManual[Number(chan)] ? '0' : val === 0 ? '' : val }}
      </div>
    </div>
    <!-- Ghost Address Tombstones -->
    <div
      v-for="ghost in ghostAddresses"
      :key="`addr-${ghost.address}`"
      class="tombstone ghost-tombstone"
      :class="{ selected: ghost.isSelected }"
    >
      <div class="addr-label-container" :data-len="ghost.address.length">
        <span class="addr-label">A {{ ghost.address }}</span>
      </div>
      <div class="chan-val">{{ ghost.level === 100 ? 'FL' : ghost.level }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useCommandParser } from '@/utils/useCommandParser'
import { computed } from 'vue'

const {
  channelLevels,
  selectedChannels,
  selectedAddresses,
  addressLevels,
  isManual,
  handleKeyPress,
  commandText,
} = useCommandParser()

const onTombstoneClick = (chan: number) => {
  const line = commandText.value.trim()
  const chanStr = chan.toString()

  // 1. If command line is empty or previous command finished, start fresh selection
  if (line.length === 0) {
    appendDigits(chanStr)
    handleKeyPress('ENTER')
    return
  }

  // 2. If line ends in an operator (+, -, THRU, AT), append channel and commit
  if (line.endsWith('+') || line.endsWith('-') || line.endsWith('THRU') || line.endsWith('AT')) {
    appendDigits(chanStr)
    handleKeyPress('ENTER')
    return
  }

  // 3. Otherwise toggle channel into current selection list (+ or -)
  const isAlreadySelected = selectedChannels.value.includes(chan)

  if (isAlreadySelected) {
    handleKeyPress(' - ')
    appendDigits(chanStr)
  } else {
    handleKeyPress(' + ')
    appendDigits(chanStr)
  }

  handleKeyPress('ENTER')
}

const appendDigits = (digits: string) => {
  for (const char of digits) {
    handleKeyPress(char)
  }
}

const ghostAddresses = computed(() => {
  return Object.entries(addressLevels.value).map(([addr, level]) => ({
    address: addr,
    level,
    isSelected: selectedAddresses.value.includes(addr),
  }))
})
</script>

<style scoped>
.tombstone-grid {
  display: grid;
  /* Auto-fill desktop width with tiles that are at least 80px wide */
  grid-template-columns: repeat(auto-fill, minmax(64px, 1fr));
  grid-auto-rows: 72px;
  gap: 6px;
  padding: 10px;
  background: #121215;
  border: 1px solid #27272a;
  border-radius: 6px;
  width: 100%;
  height: auto;
  box-sizing: border-box;
}

.tombstone {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  height: 72px;
  background: #18181b;
  border: 1.5px solid #27272a;
  border-radius: 4px;
  padding: 4px 6px;
  box-sizing: border-box;
}

.ghost-tombstone {
  border: 1px dashed #e67e22;
  background-color: rgba(230, 126, 34, 0.12);
  container-type: inline-size;
}

.ghost-tombstone.selected {
  border-style: solid;
  border-color: #f39c12;
  box-shadow: 0 0 8px rgba(243, 156, 18, 0.5);
}

.tombstone.selected {
  border-color: #3b82f6;
  box-shadow: 0 0 6px rgba(59, 130, 246, 0.4);
}

.chan-num {
  font-size: 0.75rem;
  font-weight: 700;
  color: #a1a1aa;
}

.addr-label-container {
  width: 100%;
  text-align: center;
  overflow: hidden;
}

.addr-label {
  color: #e67e22;
  font-weight: 700;
  font-family: monospace;
  font-size: 1rem; /* Default max size matching .chan-number */
  white-space: nowrap;
  line-height: 1;
}

/* 1 to 4 chars (e.g. "1", "1/1", "12/1"): Full size */
.addr-label-container[data-len='1'] .addr-label,
.addr-label-container[data-len='2'] .addr-label,
.addr-label-container[data-len='3'] .addr-label,
.addr-label-container[data-len='4'] .addr-label {
  font-size: 0.75rem;
}

/* 5 to 6 chars (e.g. "11/12", "1/100"): Slightly smaller */
.addr-label-container[data-len='5'] .addr-label,
.addr-label-container[data-len='6'] .addr-label {
  font-size: 0.65rem;
}

/* 7+ chars (e.g. "11/512", "12/512"): Smallest fit */
.addr-label-container[data-len='7'] .addr-label,
.addr-label-container[data-len='8'] .addr-label,
.addr-label-container[data-len='9'] .addr-label {
  font-size: 0.6rem;
}

.chan-val {
  font-size: 1.15rem;
  font-weight: 800;
  text-align: center;
  color: #52525b;
  line-height: 1;
  margin-bottom: 12px;
}

.tombstone.manual .chan-val {
  color: #ff0000;
}

.ghost-tombstone .chan-val {
  color: #ff0000;
}

/* Mobile adjustment (< 640px) */
@media (max-width: 640px) {
  .tombstone-grid {
    grid-template-columns: repeat(auto-fill, minmax(70px, 1fr));
    max-height: 35vh;
    overflow-y: auto;
  }
}
</style>
