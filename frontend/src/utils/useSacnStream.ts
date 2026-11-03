import { watch, onMounted, onUnmounted } from 'vue'
import { useCommandParser } from './useCommandParser'
import { resolvePatchOutput, type PatchConfig } from '../utils/patchResolver'

export function useSacnStream(getPatchConfig: () => PatchConfig) {
  const { channelLevels, addressLevels } = useCommandParser()
  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setInterval> | null = null

  const connectWebSocket = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    ws = new WebSocket(`${protocol}//${host}/api/live-sacn`)

    ws.onopen = () => {
      console.log('[sACN Stream] Connected to backend transmitter')
    }

    ws.onclose = () => {
      // Automatic reconnect fallback
      reconnectTimer = setTimeout(connectWebSocket, 3000)
    }
  }

  // Stream payload to backend on channel/address changes
  const sendDmxFrame = () => {
    const patch = getPatchConfig()
    const dmxMap = resolvePatchOutput(patch, channelLevels.value, addressLevels.value)

    const payload = {
      priority: 150, // High priority override
      source: 'Command Console UI',
      frame: dmxMap, // e.g. { "1/1": 255, "1/2": 128, "2/5": 255 }
    }

    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload))
    } else {
      // HTTP POST Fallback if WebSocket is disconnected/offline
      fetch('/api/sacn/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).catch(() => {})
    }
  }

  // Watch for reactive changes in levels
  watch(
    [channelLevels, addressLevels],
    () => {
      sendDmxFrame()
    },
    { deep: true },
  )

  onMounted(() => {
    connectWebSocket()
  })

  onUnmounted(() => {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    if (ws) ws.close()
  })

  return { sendDmxFrame }
}
