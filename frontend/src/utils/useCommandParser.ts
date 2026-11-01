import { ref, readonly, type DeepReadonly, type Ref } from 'vue'

// --- STRICT TYPES ---

export type ChannelState = Record<number, number>
export type ManualState = Record<number, boolean>

export type SyntaxKey = ' + ' | ' - ' | '-' | ' THRU ' | ' AT ' | '/' | '.'
export type ControlKey =
  | 'ENTER'
  | 'CLEAR'
  | 'HARD_CLEAR'
  | 'UNDO'
  | 'FULL_FULL'
  | 'FULL'
  | 'OUT'
  | 'SNEAK'
  | 'REMDIM'
  | 'HOME'
  | 'SELECTLAST'
  | 'SELECTACTIVE'
export type DigitKey = '0' | '1' | '2' | '3' | '4' | '5' | '6' | '7' | '8' | '9'

export type ConsoleKey = SyntaxKey | ControlKey | DigitKey | (string & {})

export interface HistorySnapshot {
  readonly channelLevels: Readonly<ChannelState>
  readonly isManual: Readonly<ManualState>
  readonly selectedChannels: readonly number[]
  readonly commandText: string
}

export interface UseCommandParserReturn {
  channelLevels: Ref<ChannelState>
  isManual: Ref<ManualState>
  selectedChannels: Ref<number[]>
  commandText: Ref<string>
  historyStack: DeepReadonly<Ref<HistorySnapshot[]>>
  isCommandCompleted: Ref<boolean>
  handleKeyPress: (key: ConsoleKey) => void
  evaluateCommand: (rawCmd: string) => void
  saveHistorySnapshot: () => void
}

// --- GLOBAL REACTIVE STATE ---

const INITIAL_CHANNELS: ChannelState = {
  1: 0,
  2: 0,
  3: 0,
  4: 0,
  5: 0,
  6: 0,
  7: 0,
  8: 0,
  9: 0,
  10: 0,
  11: 0,
  12: 0,
  13: 0,
  14: 0,
  15: 0,
  16: 0,
}

const channelLevels = ref<ChannelState>({ ...INITIAL_CHANNELS })
const isManual = ref<ManualState>({})
const selectedChannels = ref<number[]>([])
const commandText = ref<string>('')
const historyStack = ref<HistorySnapshot[]>([])
const isCommandCompleted = ref<boolean>(false)
let lastFullClickTime = 0

// --- COMPOSABLE ---

export function useCommandParser(): UseCommandParserReturn {
  const getChannelKeys = (map: ChannelState): number[] => {
    return Object.keys(map)
      .map((k: string) => parseInt(k, 10))
      .filter((k: number): k is number => !isNaN(k))
  }

  const saveHistorySnapshot = (): void => {
    const snapshot: HistorySnapshot = {
      channelLevels: { ...channelLevels.value },
      isManual: { ...isManual.value },
      selectedChannels: [...selectedChannels.value],
      commandText: commandText.value,
    }

    historyStack.value.push(snapshot)

    if (historyStack.value.length > 30) {
      historyStack.value.shift()
    }
  }

  const parseChannelSelection = (expr: string): number[] => {
    const tokens: string[] = expr
      .trim()
      .split(/\s+/)
      .filter((t: string): boolean => t.length > 0)
    const currentSet = new Set<number>()
    let currentMode: '+' | '-' = '+'
    let i = 0

    while (i < tokens.length) {
      const token = tokens[i]
      if (token === undefined) break

      if (token === '+') {
        currentMode = '+'
        i++
        continue
      }
      if (token === '-') {
        currentMode = '-'
        i++
        continue
      }

      const num = parseInt(token, 10)
      if (!isNaN(num)) {
        const nextToken = tokens[i + 1]
        const rangeToken = tokens[i + 2]

        if (
          nextToken !== undefined &&
          rangeToken !== undefined &&
          nextToken.toUpperCase() === 'THRU'
        ) {
          const endNum = parseInt(rangeToken, 10)
          if (!isNaN(endNum)) {
            const start = Math.min(num, endNum)
            const end = Math.max(num, endNum)
            for (let c = start; c <= end; c++) {
              if (currentMode === '+') currentSet.add(c)
              else currentSet.delete(c)
            }
            i += 3
            continue
          }
        }

        if (currentMode === '+') currentSet.add(num)
        else currentSet.delete(num)
      }
      i++
    }

    return Array.from(currentSet).sort((a: number, b: number) => a - b)
  }

  const formatSelectionString = (channels: number[]): string => {
    if (channels.length === 0) return ''
    return channels.join(' + ')
  }

  /** Parsed intensity result specifying absolute vs relative mode */
  interface ParsedIntensity {
    type: 'absolute' | 'relative'
    value: number
  }

  /** Helper to parse intensity level strings with decade rule and relative +/- support */
  const parseIntensityLevel = (levelStr: string): ParsedIntensity | null => {
    const trimmed = levelStr.trim().toUpperCase()
    if (trimmed.length === 0) return null

    if (trimmed === 'FULL' || trimmed === 'FL') {
      return { type: 'absolute', value: 100 }
    }

    // Relative level offset detection: AT + 10, AT - 20, AT + 1, AT - 5
    if (trimmed.startsWith('+') || trimmed.startsWith('-')) {
      const sign = trimmed.startsWith('+') ? 1 : -1
      const numPart = trimmed.slice(1).trim()

      if (numPart.length === 0) return null

      let delta = 0
      if (/^[0-9]$/.test(numPart)) {
        delta = parseInt(numPart, 10) * 10
      } else {
        const parsed = parseInt(numPart, 10)
        delta = isNaN(parsed) ? 0 : parsed
      }

      return { type: 'relative', value: sign * delta }
    }

    // Single digit decade rule: AT 5 -> 50%
    if (/^[0-9]$/.test(trimmed)) {
      return { type: 'absolute', value: parseInt(trimmed, 10) * 10 }
    }

    // Explicit two/three digit absolute level: AT 05 -> 5%, AT 75 -> 75%
    const parsedVal = parseInt(trimmed, 10)
    if (isNaN(parsedVal)) return null
    return { type: 'absolute', value: Math.min(100, Math.max(0, parsedVal)) }
  }

  const evaluateCommand = (rawCmd: string): void => {
    const cmd = rawCmd.trim()
    if (!cmd) return

    // --- COMBINED AT LEVEL REMDIM / REMDIM EVALUATION ---
    if (cmd.includes('REMDIM')) {
      const hasAt = cmd.includes('AT')
      let selectionPart = ''
      let levelPart = ''

      if (hasAt) {
        const atParts = cmd.split('AT')
        selectionPart = atParts[0] ?? ''
        const remainder = atParts[1] ?? ''
        levelPart = remainder.split('REMDIM')[0] ?? ''
      } else {
        selectionPart = cmd.split('REMDIM')[0] ?? ''
      }

      const activeTargets: number[] =
        selectionPart.trim().length > 0
          ? parseChannelSelection(selectionPart)
          : [...selectedChannels.value]

      if (activeTargets.length > 0) {
        const nextLevels: ChannelState = { ...channelLevels.value }
        const nextManual: ManualState = { ...isManual.value }
        const allChannels = getChannelKeys(nextLevels)
        const intensity = parseIntensityLevel(levelPart)

        allChannels.forEach((chan: number) => {
          if (activeTargets.includes(chan)) {
            if (intensity !== null) {
              if (intensity.type === 'relative') {
                const current = nextLevels[chan] ?? 0
                nextLevels[chan] = Math.min(100, Math.max(0, current + intensity.value))
              } else {
                nextLevels[chan] = intensity.value
              }
              nextManual[chan] = true
            }
          } else {
            nextLevels[chan] = 0
            nextManual[chan] = true
          }
        })

        channelLevels.value = nextLevels
        isManual.value = nextManual
        selectedChannels.value = activeTargets
        commandText.value = formatSelectionString(activeTargets)
      }
      isCommandCompleted.value = true
      return
    }

    // --- SNEAK EVALUATION ---
    if (cmd.includes('SNEAK')) {
      const parts = cmd.split('SNEAK')
      const selectionPart = parts[0] ?? ''
      let targets: number[] = []

      if (selectionPart.trim().length > 0) {
        targets = parseChannelSelection(selectionPart)
      } else if (selectedChannels.value.length > 0) {
        targets = [...selectedChannels.value]
      } else {
        targets = getChannelKeys(channelLevels.value)
      }

      const nextLevels: ChannelState = { ...channelLevels.value }
      const nextManual: ManualState = { ...isManual.value }

      targets.forEach((chan: number) => {
        nextLevels[chan] = 0
        delete nextManual[chan]
      })

      channelLevels.value = nextLevels
      isManual.value = nextManual
      selectedChannels.value =
        selectionPart.trim().length > 0 || selectedChannels.value.length > 0 ? targets : []
      commandText.value = formatSelectionString(selectedChannels.value)
      isCommandCompleted.value = true
      return
    }

    // --- STANDARD / RELATIVE AT LEVEL EVALUATION ---
    if (cmd.includes('AT')) {
      const parts: string[] = cmd.split('AT')
      const selectionPart = parts[0] ?? ''
      const levelPart = parts[1] ?? ''

      const targetChannels: number[] =
        selectionPart.trim().length > 0
          ? parseChannelSelection(selectionPart)
          : [...selectedChannels.value]

      const intensity = parseIntensityLevel(levelPart) ?? { type: 'absolute', value: 0 }

      if (targetChannels.length > 0) {
        const nextLevels: ChannelState = { ...channelLevels.value }
        const nextManual: ManualState = { ...isManual.value }

        targetChannels.forEach((chan: number) => {
          if (intensity.type === 'relative') {
            const current = nextLevels[chan] ?? 0
            nextLevels[chan] = Math.min(100, Math.max(0, current + intensity.value))
          } else {
            nextLevels[chan] = intensity.value
          }
          nextManual[chan] = true
        })

        channelLevels.value = nextLevels
        isManual.value = nextManual
        selectedChannels.value = targetChannels
        commandText.value = formatSelectionString(targetChannels)
      }
      isCommandCompleted.value = true
      return
    }

    const targets: number[] = parseChannelSelection(cmd)
    if (targets.length > 0) {
      selectedChannels.value = targets
      commandText.value = formatSelectionString(targets)
    }
    isCommandCompleted.value = true
  }

  const evaluateSelectionOnly = (cmdStr: string): void => {
    const cleanStr = cmdStr.trim()
    if (!cleanStr) {
      selectedChannels.value = []
      return
    }

    let selectionPart = cleanStr
    if (cleanStr.includes('AT')) {
      selectionPart = cleanStr.split('AT')[0] ?? ''
    } else if (cleanStr.includes('SNEAK')) {
      selectionPart = cleanStr.split('SNEAK')[0] ?? ''
    } else if (cleanStr.includes('REMDIM')) {
      selectionPart = cleanStr.split('REMDIM')[0] ?? ''
    }

    if (selectionPart.trim().length > 0) {
      const targets: number[] = parseChannelSelection(selectionPart)
      selectedChannels.value = targets
    }
  }

  const handleKeyPress = (key: ConsoleKey): void => {
    const line = commandText.value

    if (key === 'HARD_CLEAR') {
      commandText.value = ''
      selectedChannels.value = []
      isCommandCompleted.value = false
      return
    }

    if (key === 'CLEAR') {
      if (isCommandCompleted.value || line.length === 0) {
        commandText.value = ''
        selectedChannels.value = []
        isCommandCompleted.value = false
        return
      }

      const trimmed = line.trimEnd()
      const lastSpaceIndex = trimmed.lastIndexOf(' ')

      if (lastSpaceIndex === -1) {
        commandText.value = ''
        selectedChannels.value = []
      } else {
        commandText.value = line.substring(0, lastSpaceIndex + 1)
        evaluateSelectionOnly(commandText.value)
      }

      isCommandCompleted.value = false
      return
    }

    if (key === 'UNDO') {
      const lastSnapshot = historyStack.value[historyStack.value.length - 1]
      if (lastSnapshot !== undefined) {
        historyStack.value = historyStack.value.slice(0, -1)
        channelLevels.value = { ...lastSnapshot.channelLevels }
        isManual.value = { ...lastSnapshot.isManual }
        selectedChannels.value = [...lastSnapshot.selectedChannels]
        commandText.value = lastSnapshot.commandText
        isCommandCompleted.value = false
      }
      return
    }

    if (key === 'ENTER') {
      if (line.trim().length > 0) {
        saveHistorySnapshot()
        evaluateCommand(line)
      }
      return
    }

    // --- SMART MINUS OPERATOR (RANGE VS RELATIVE INTENSITY) ---
    if (key === ' - ' || key === '-') {
      if (isCommandCompleted.value) {
        commandText.value = formatSelectionString(selectedChannels.value) + ' - '
        isCommandCompleted.value = false
      } else if (line.endsWith('AT') || line.endsWith('AT ')) {
        // Appends relative minus to AT clause: "1 AT -"
        commandText.value = line.trimEnd() + ' -'
      } else {
        commandText.value += ' - '
      }
      return
    }

    // --- SMART PLUS OPERATOR (RANGE VS RELATIVE INTENSITY) ---
    if (key === ' + ' || key === '+') {
      if (isCommandCompleted.value) {
        commandText.value = formatSelectionString(selectedChannels.value) + ' + '
        isCommandCompleted.value = false
      } else if (line.endsWith('AT') || line.endsWith('AT ')) {
        // Appends relative plus to AT clause: "1 AT +"
        commandText.value = line.trimEnd() + ' +'
      } else {
        commandText.value += ' + '
      }
      return
    }

    if (key === 'FULL' || key === 'FULL_FULL') {
      const now = Date.now()
      const isDoubleTap = key === 'FULL_FULL' || now - lastFullClickTime < 400
      lastFullClickTime = now

      if (isDoubleTap) {
        saveHistorySnapshot()
        const targetStr =
          line.trim().length > 0 ? line : formatSelectionString(selectedChannels.value)
        if (targetStr.length > 0) {
          evaluateCommand(`${targetStr} AT FULL`)
        }
        return
      } else {
        if (isCommandCompleted.value) {
          commandText.value = formatSelectionString(selectedChannels.value) + ' AT FULL'
          isCommandCompleted.value = false
        } else if (line.endsWith('AT') || line.endsWith('AT ')) {
          commandText.value = line.trimEnd() + ' FULL'
        } else if (!line.includes('AT')) {
          commandText.value = line.length > 0 ? line + ' AT FULL' : 'AT FULL'
        }
        return
      }
    }

    if (key === ' AT ' || key === 'AT') {
      if (isCommandCompleted.value) {
        commandText.value = formatSelectionString(selectedChannels.value) + ' AT '
        isCommandCompleted.value = false
      } else if (!line.includes('AT')) {
        commandText.value = line.length > 0 ? line + ' AT ' : 'AT '
      }
      return
    }

    if (key === 'OUT') {
      saveHistorySnapshot()
      const targetStr =
        line.trim().length > 0 ? line : formatSelectionString(selectedChannels.value)
      if (targetStr.length > 0) {
        evaluateCommand(`${targetStr} AT 0`)
      }
      return
    }

    if (key === 'SNEAK') {
      if (isCommandCompleted.value) {
        commandText.value = formatSelectionString(selectedChannels.value) + ' SNEAK'
        isCommandCompleted.value = false
      } else {
        commandText.value = line.length > 0 ? line + ' SNEAK' : 'SNEAK'
      }
      return
    }

    if (key === 'REMDIM') {
      if (isCommandCompleted.value) {
        commandText.value = formatSelectionString(selectedChannels.value) + ' REMDIM'
        isCommandCompleted.value = false
      } else {
        commandText.value = line.length > 0 ? line + ' REMDIM' : 'REMDIM'
      }
      return
    }

    if (key === 'SELECTLAST') {
      const lastSnapshot = historyStack.value[historyStack.value.length - 1]
      if (lastSnapshot !== undefined) {
        selectedChannels.value = [...lastSnapshot.selectedChannels]
        commandText.value = formatSelectionString(selectedChannels.value)
        isCommandCompleted.value = false
      }
      return
    }

    if (key === 'SELECTACTIVE') {
      const allChannels = getChannelKeys(channelLevels.value)
      const activeChans: number[] = allChannels.filter(
        (chan: number) => (channelLevels.value[chan] ?? 0) > 0,
      )

      if (activeChans.length > 0) {
        selectedChannels.value = activeChans
        commandText.value = formatSelectionString(activeChans)
        isCommandCompleted.value = false
      }
      return
    }

    if (isCommandCompleted.value) {
      if (/^[0-9]/.test(key)) {
        commandText.value = key
        isCommandCompleted.value = false
        evaluateSelectionOnly(key)
        return
      } else {
        isCommandCompleted.value = false
      }
    }

    commandText.value += key
    evaluateSelectionOnly(commandText.value)
  }

  return {
    channelLevels,
    isManual,
    selectedChannels,
    commandText,
    historyStack: readonly(historyStack),
    isCommandCompleted,
    handleKeyPress,
    evaluateCommand,
    saveHistorySnapshot,
  }
}
