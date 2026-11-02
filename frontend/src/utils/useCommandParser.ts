import { ref, readonly, type DeepReadonly, type Ref } from 'vue'

// --- STRICT TYPES ---

export type ChannelState = Record<number, number>
export type ManualState = Record<number, boolean>
export type AddressState = Record<string, number>

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
  | 'NEXT'
  | 'LAST'
  | 'ADDRESS'
  | 'CHAN_CHECK'
export type DigitKey = '0' | '1' | '2' | '3' | '4' | '5' | '6' | '7' | '8' | '9'

export type ConsoleKey = SyntaxKey | ControlKey | DigitKey | (string & {})

export interface HistorySnapshot {
  readonly channelLevels: Readonly<ChannelState>
  readonly addressLevels: Readonly<AddressState>
  readonly isManual: Readonly<ManualState>
  readonly selectedChannels: readonly number[]
  readonly selectedAddresses: readonly string[]
  readonly commandText: string
}

export interface UseCommandParserReturn {
  channelLevels: Ref<ChannelState>
  addressLevels: Ref<AddressState>
  isManual: Ref<ManualState>
  selectedChannels: Ref<number[]>
  selectedAddresses: Ref<string[]>
  commandText: Ref<string>
  commandError: Ref<boolean>
  isAddressMode: Ref<boolean>
  isChanCheckMode: Ref<boolean>
  chanCheckLevel: Ref<number>
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
const addressLevels = ref<AddressState>({})
const isManual = ref<ManualState>({})
const selectedChannels = ref<number[]>([])
const selectedAddresses = ref<string[]>([])
const commandText = ref<string>('')
const commandError = ref<boolean>(false)
const historyStack = ref<HistorySnapshot[]>([])
const isCommandCompleted = ref<boolean>(false)

const isAddressMode = ref<boolean>(false)
const isChanCheckMode = ref<boolean>(false)
const chanCheckLevel = ref<number>(100)
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
      addressLevels: { ...addressLevels.value },
      isManual: { ...isManual.value },
      selectedChannels: [...selectedChannels.value],
      selectedAddresses: [...selectedAddresses.value],
      commandText: commandText.value,
    }

    historyStack.value.push(snapshot)

    if (historyStack.value.length > 30) {
      historyStack.value.shift()
    }
  }

  const sneakSelectedAddresses = (targets?: string[]): void => {
    const addrsToSneak =
      targets ??
      (selectedAddresses.value.length > 0
        ? selectedAddresses.value
        : Object.keys(addressLevels.value))
    const nextAddressLevels: AddressState = { ...addressLevels.value }

    addrsToSneak.forEach((addr: string) => {
      delete nextAddressLevels[addr]
    })

    addressLevels.value = nextAddressLevels
    selectedAddresses.value = []
    isAddressMode.value = false
  }

  const exitChanCheckMode = (): void => {
    if (!isChanCheckMode.value) return
    isChanCheckMode.value = false

    if (selectedAddresses.value.length > 0 || Object.keys(addressLevels.value).length > 0) {
      sneakSelectedAddresses()
    }

    const nextLevels: ChannelState = { ...channelLevels.value }
    const nextManual: ManualState = { ...isManual.value }
    const allChannels = getChannelKeys(nextLevels)

    allChannels.forEach((c: number) => {
      nextLevels[c] = 0
      delete nextManual[c]
    })

    channelLevels.value = nextLevels
    isManual.value = nextManual
  }

  const parseChannelSelection = (expr: string): number[] => {
    const tokens: string[] = expr
      .trim()
      .split(/\s+/)
      .filter((t: string): boolean => t.length > 0)
    const selectionList: number[] = []
    let currentMode: '+' | '-' = '+'
    let i = 0

    const addChannel = (ch: number) => {
      const existingIdx = selectionList.indexOf(ch)
      if (existingIdx !== -1) selectionList.splice(existingIdx, 1)
      selectionList.push(ch)
    }

    const removeChannel = (ch: number) => {
      const existingIdx = selectionList.indexOf(ch)
      if (existingIdx !== -1) selectionList.splice(existingIdx, 1)
    }

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
            const step = num <= endNum ? 1 : -1
            for (let c = num; step > 0 ? c <= endNum : c >= endNum; c += step) {
              if (currentMode === '+') addChannel(c)
              else removeChannel(c)
            }
            i += 3
            continue
          }
        }

        if (currentMode === '+') addChannel(num)
        else removeChannel(num)
      }
      i++
    }

    return selectionList
  }

  const parseAddressSelection = (expr: string): string[] => {
    const cleanExpr = expr.toUpperCase().replace('ADDRESS', '').trim()
    const tokens: string[] = cleanExpr.split(/\s+/).filter((t: string): boolean => t.length > 0)
    const selectionList: string[] = []
    let currentMode: '+' | '-' = '+'
    let activeUniverse = 1
    let i = 0

    const parseAddressToken = (token: string): { universe: number; address: number } | null => {
      if (token.includes('/')) {
        const [uStr, aStr] = token.split('/')
        const u = parseInt(uStr ?? '', 10)
        const a = parseInt(aStr ?? '', 10)
        if (!isNaN(u) && !isNaN(a)) {
          activeUniverse = u
          return { universe: u, address: a }
        }
      } else {
        const a = parseInt(token, 10)
        if (!isNaN(a)) {
          return { universe: activeUniverse, address: a }
        }
      }
      return null
    }

    const addAddr = (u: number, a: number) => {
      const formatted = `${u}/${a}`
      const existingIdx = selectionList.indexOf(formatted)
      if (existingIdx !== -1) selectionList.splice(existingIdx, 1)
      selectionList.push(formatted)
    }

    const removeAddr = (u: number, a: number) => {
      const formatted = `${u}/${a}`
      const existingIdx = selectionList.indexOf(formatted)
      if (existingIdx !== -1) selectionList.splice(existingIdx, 1)
    }

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

      const startAddr = parseAddressToken(token)

      if (startAddr !== null) {
        const nextToken = tokens[i + 1]
        const rangeToken = tokens[i + 2]

        if (
          nextToken !== undefined &&
          rangeToken !== undefined &&
          nextToken.toUpperCase() === 'THRU'
        ) {
          const endAddr = parseAddressToken(rangeToken)

          if (endAddr !== null) {
            const targetUniverse = rangeToken.includes('/') ? endAddr.universe : startAddr.universe
            const step = startAddr.address <= endAddr.address ? 1 : -1

            for (
              let addrNum = startAddr.address;
              step > 0 ? addrNum <= endAddr.address : addrNum >= endAddr.address;
              addrNum += step
            ) {
              if (currentMode === '+') addAddr(targetUniverse, addrNum)
              else removeAddr(targetUniverse, addrNum)
            }

            activeUniverse = targetUniverse
            i += 3
            continue
          }
        }

        if (currentMode === '+') addAddr(startAddr.universe, startAddr.address)
        else removeAddr(startAddr.universe, startAddr.address)
      }

      i++
    }

    return selectionList
  }

  const formatSelectionString = (channels: number[]): string => {
    if (channels.length === 0) return ''
    return channels.join(' + ')
  }

  const formatAddressSelectionString = (addresses: string[]): string => {
    if (addresses.length === 0) return ''
    return 'ADDRESS ' + addresses.join(' + ')
  }

  type ParsedIntensity =
    | { type: 'absolute'; value: number }
    | { type: 'relative'; value: number }
    | { type: 'proportional'; factor: number }
    | { type: 'fan'; startValue: number; endValue: number }

  const parseIntensityLevel = (levelStr: string): ParsedIntensity | null => {
    const trimmed = levelStr.trim().toUpperCase()
    if (trimmed.length === 0) return null

    if (trimmed === 'FULL' || trimmed === 'FL') {
      return { type: 'absolute', value: 100 }
    }

    if (trimmed.startsWith('/')) {
      const numPart = trimmed.slice(1).trim()
      if (numPart.length === 0) return { type: 'proportional', factor: 1.0 }

      let divisor = 1.0

      if (numPart.includes('.')) {
        const parsedFloat = parseFloat(numPart)
        divisor = isNaN(parsedFloat) || parsedFloat === 0 ? 1.0 : Math.max(0.01, parsedFloat)
      } else if (/^[0-9]$/.test(numPart)) {
        divisor = parseInt(numPart, 10)
        if (divisor === 0) divisor = 1.0
      } else {
        const parsedInt = parseInt(numPart, 10)
        divisor = isNaN(parsedInt) || parsedInt === 0 ? 1.0 : Math.max(0.01, parsedInt)
      }

      return { type: 'proportional', factor: 1 / divisor }
    }

    if (trimmed.includes('THRU')) {
      const parts = trimmed.split('THRU').map((p) => p.trim())
      const startParsed = parseIntensityLevel(parts[0] ?? '')
      const endParsed = parseIntensityLevel(parts[1] ?? '')

      const startVal = startParsed && 'value' in startParsed ? startParsed.value : 0
      const endVal = endParsed && 'value' in endParsed ? endParsed.value : 100

      return { type: 'fan', startValue: startVal, endValue: endVal }
    }

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

    if (/^[0-9]$/.test(trimmed)) {
      return { type: 'absolute', value: parseInt(trimmed, 10) * 10 }
    }

    const parsedVal = parseInt(trimmed, 10)
    if (isNaN(parsedVal)) return null
    return { type: 'absolute', value: Math.min(100, Math.max(0, parsedVal)) }
  }

  const applyIntensityToValue = (
    currentVal: number,
    intensity: ParsedIntensity,
    indexInSelection: number,
    totalSelected: number,
  ): number => {
    switch (intensity.type) {
      case 'absolute':
        return intensity.value
      case 'relative':
        return Math.min(100, Math.max(0, currentVal + intensity.value))
      case 'proportional':
        return Math.min(100, Math.max(0, Math.round(currentVal * intensity.factor)))
      case 'fan': {
        if (totalSelected <= 1) return intensity.startValue
        const step = (intensity.endValue - intensity.startValue) / (totalSelected - 1)
        return Math.min(
          100,
          Math.max(0, Math.round(intensity.startValue + step * indexInSelection)),
        )
      }
    }
  }

  const executeChanCheckStep = (targetChan: number): void => {
    const nextLevels: ChannelState = { ...channelLevels.value }
    const nextManual: ManualState = { ...isManual.value }
    const allChannels = getChannelKeys(nextLevels)

    allChannels.forEach((c: number) => {
      if (c === targetChan) {
        nextLevels[c] = chanCheckLevel.value
        nextManual[c] = true
      } else {
        nextLevels[c] = 0
        delete nextManual[c]
      }
    })

    channelLevels.value = nextLevels
    isManual.value = nextManual
    selectedChannels.value = [targetChan]
    commandText.value = `${targetChan}`
  }

  const executeAddressCheckStep = (targetAddr: string): void => {
    const nextAddressLevels: AddressState = {}
    nextAddressLevels[targetAddr] = chanCheckLevel.value

    addressLevels.value = nextAddressLevels
    selectedAddresses.value = [targetAddr]
    isAddressMode.value = true
    commandText.value = `ADDRESS ${targetAddr}`
  }

  const triggerSyntaxError = () => {
    commandError.value = true
    commandText.value = 'Syntax Error'
    isCommandCompleted.value = true
  }

  const evaluateCommand = (rawCmd: string): void => {
    const cmd = rawCmd.trim()
    if (!cmd) {
      triggerSyntaxError()
      return
    }

    // --- ADDRESS MODE EVALUATION ---
    if (cmd.startsWith('ADDRESS') || isAddressMode.value) {
      if (cmd.includes('CHAN_CHECK')) {
        let selectionPart = ''
        let levelPart = ''

        if (cmd.includes('AT')) {
          const atParts = cmd.replace('ADDRESS', '').split('AT')
          selectionPart = atParts[0] ?? ''
          levelPart = (atParts[1] ?? '').split('CHAN_CHECK')[0] ?? ''
        } else {
          selectionPart = cmd.replace('ADDRESS', '').split('CHAN_CHECK')[0] ?? ''
        }

        const targets =
          selectionPart.trim().length > 0
            ? parseAddressSelection(selectionPart)
            : [...selectedAddresses.value]

        if (targets.length === 0) return triggerSyntaxError()
        const targetAddr = targets[0] ?? '1/1'

        const parsedIntensity = parseIntensityLevel(levelPart)
        chanCheckLevel.value =
          parsedIntensity && 'value' in parsedIntensity ? parsedIntensity.value : 100

        isAddressMode.value = true
        isChanCheckMode.value = true
        executeAddressCheckStep(targetAddr)
        commandText.value = `ADDRESS ${targetAddr} CHAN_CHECK`
        commandError.value = false
        isCommandCompleted.value = true
        return
      }

      if (cmd.includes('SNEAK')) {
        const parts = cmd.replace('ADDRESS', '').split('SNEAK')
        const selectionPart = parts[0] ?? ''
        const targets =
          selectionPart.trim().length > 0 ? parseAddressSelection(selectionPart) : undefined
        sneakSelectedAddresses(targets)
        commandText.value =
          selectionPart.trim().length > 0
            ? `ADDRESS ${selectionPart.trim()} SNEAK`
            : 'ADDRESS SNEAK'
        commandError.value = false
        isCommandCompleted.value = true
        return
      }

      const parts = cmd.replace('ADDRESS', '').trim().split('AT')
      const targetStr = parts[0] ?? ''
      const levelStr = parts[1] ?? ''

      const targets =
        targetStr.trim().length > 0
          ? parseAddressSelection(targetStr)
          : [...selectedAddresses.value]

      if (targets.length === 0) return triggerSyntaxError()

      const intensity = parseIntensityLevel(levelStr) ?? { type: 'absolute', value: 0 }
      const nextAddressLevels: AddressState = { ...addressLevels.value }

      targets.forEach((addr: string, idx: number) => {
        const levelVal = applyIntensityToValue(
          nextAddressLevels[addr] ?? 0,
          intensity,
          idx,
          targets.length,
        )
        if (levelVal > 0) nextAddressLevels[addr] = levelVal
        else delete nextAddressLevels[addr]
      })

      addressLevels.value = nextAddressLevels
      selectedAddresses.value = targets
      selectedChannels.value = []
      commandText.value = `${formatAddressSelectionString(targets)} AT ${levelStr.trim() || '0'}`
      commandError.value = false
      isAddressMode.value = true
      isCommandCompleted.value = true
      return
    }

    // --- CHANNEL CHECK SYNTAX EVALUATION ---
    if (cmd.includes('CHAN_CHECK')) {
      let selectionPart = ''
      let levelPart = ''

      if (cmd.includes('AT')) {
        const atParts = cmd.split('AT')
        selectionPart = atParts[0] ?? ''
        levelPart = (atParts[1] ?? '').split('CHAN_CHECK')[0] ?? ''
      } else {
        selectionPart = cmd.split('CHAN_CHECK')[0] ?? ''
      }

      const targets =
        selectionPart.trim().length > 0
          ? parseChannelSelection(selectionPart)
          : [...selectedChannels.value]

      if (targets.length === 0) return triggerSyntaxError()
      const targetChan = targets[0] ?? 1

      const parsedIntensity = parseIntensityLevel(levelPart)
      chanCheckLevel.value =
        parsedIntensity && 'value' in parsedIntensity ? parsedIntensity.value : 100

      isChanCheckMode.value = true
      executeChanCheckStep(targetChan)
      commandText.value = `${targetChan} CHAN_CHECK`
      commandError.value = false
      isCommandCompleted.value = true
      return
    }

    // --- COMBINED AT LEVEL REMDIM EVALUATION ---
    if (cmd.includes('REMDIM')) {
      const hasAt = cmd.includes('AT')
      let selectionPart = ''
      let levelPart = ''
      let remDimBackgroundPart = ''

      if (hasAt) {
        const atParts = cmd.split('AT')
        selectionPart = atParts[0] ?? ''
        const remainder = atParts[1] ?? ''
        const remDimParts = remainder.split('REMDIM')
        levelPart = remDimParts[0] ?? ''
        remDimBackgroundPart = remDimParts[1] ?? ''
      } else {
        const remDimParts = cmd.split('REMDIM')
        selectionPart = remDimParts[0] ?? ''
        remDimBackgroundPart = remDimParts[1] ?? ''
      }

      const activeTargets: number[] =
        selectionPart.trim().length > 0
          ? parseChannelSelection(selectionPart)
          : [...selectedChannels.value]

      if (activeTargets.length === 0) return triggerSyntaxError()

      const nextLevels: ChannelState = { ...channelLevels.value }
      const nextManual: ManualState = { ...isManual.value }
      const allChannels = getChannelKeys(nextLevels)

      const targetIntensity = parseIntensityLevel(levelPart)
      const backgroundIntensity = parseIntensityLevel(remDimBackgroundPart)
      const bgLevel =
        backgroundIntensity && 'value' in backgroundIntensity ? backgroundIntensity.value : 0

      allChannels.forEach((chan: number) => {
        if (activeTargets.includes(chan)) {
          if (targetIntensity !== null) {
            const idx = activeTargets.indexOf(chan)
            nextLevels[chan] = applyIntensityToValue(
              nextLevels[chan] ?? 0,
              targetIntensity,
              idx,
              activeTargets.length,
            )
            nextManual[chan] = true
          }
        } else {
          nextLevels[chan] = bgLevel
          nextManual[chan] = true
        }
      })

      channelLevels.value = nextLevels
      isManual.value = nextManual
      selectedChannels.value = activeTargets
      if (selectedAddresses.value.length > 0) sneakSelectedAddresses()
      commandText.value =
        levelPart.trim().length > 0
          ? `${formatSelectionString(activeTargets)} AT ${levelPart.trim()} REMDIM`
          : `${formatSelectionString(activeTargets)} REMDIM`
      commandError.value = false
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
      commandText.value =
        selectionPart.trim().length > 0 ? `${selectionPart.trim()} SNEAK` : 'SNEAK'
      commandError.value = false
      isCommandCompleted.value = true
      return
    }

    // --- STANDARD / RELATIVE / PROPORTIONAL / FANNING AT LEVEL EVALUATION ---
    if (cmd.includes('AT')) {
      const parts: string[] = cmd.split('AT')
      const selectionPart = parts[0] ?? ''
      const levelPart = parts[1] ?? ''

      const targetChannels: number[] =
        selectionPart.trim().length > 0
          ? parseChannelSelection(selectionPart)
          : [...selectedChannels.value]

      if (targetChannels.length === 0) return triggerSyntaxError()

      const intensity = parseIntensityLevel(levelPart) ?? { type: 'absolute', value: 0 }

      const nextLevels: ChannelState = { ...channelLevels.value }
      const nextManual: ManualState = { ...isManual.value }

      targetChannels.forEach((chan: number, idx: number) => {
        nextLevels[chan] = applyIntensityToValue(
          nextLevels[chan] ?? 0,
          intensity,
          idx,
          targetChannels.length,
        )
        nextManual[chan] = true
      })

      channelLevels.value = nextLevels
      isManual.value = nextManual
      selectedChannels.value = targetChannels
      if (selectedAddresses.value.length > 0) sneakSelectedAddresses()
      commandText.value = `${formatSelectionString(targetChannels)} AT ${levelPart.trim() || '0'}`
      commandError.value = false
      isCommandCompleted.value = true
      return
    }

    const parsedTargets = parseChannelSelection(cmd)
    if (parsedTargets.length > 0) {
      selectedChannels.value = parsedTargets
      if (selectedAddresses.value.length > 0) sneakSelectedAddresses()
      commandText.value = formatSelectionString(parsedTargets)
      commandError.value = false
      isCommandCompleted.value = true
    } else {
      triggerSyntaxError()
    }
  }

  const evaluateSelectionOnly = (cmdStr: string): void => {
    const cleanStr = cmdStr.trim()
    if (!cleanStr) {
      if (isAddressMode.value) selectedAddresses.value = []
      else selectedChannels.value = []
      return
    }

    if (cleanStr.startsWith('ADDRESS') || isAddressMode.value) {
      const selectionPart = cleanStr.replace('ADDRESS', '').split('AT')[0] ?? ''
      if (selectionPart.trim().length > 0) {
        selectedAddresses.value = parseAddressSelection(selectionPart)
        selectedChannels.value = []
      }
      return
    }

    let selectionPart = cleanStr
    if (cleanStr.includes('AT')) {
      selectionPart = cleanStr.split('AT')[0] ?? ''
    } else if (cleanStr.includes('SNEAK')) {
      selectionPart = cleanStr.split('SNEAK')[0] ?? ''
    } else if (cleanStr.includes('REMDIM')) {
      selectionPart = cleanStr.split('REMDIM')[0] ?? ''
    } else if (cleanStr.includes('CHAN_CHECK')) {
      selectionPart = cleanStr.split('CHAN_CHECK')[0] ?? ''
    }

    if (selectionPart.trim().length > 0) {
      selectedChannels.value = parseChannelSelection(selectionPart)
      if (selectedAddresses.value.length > 0) sneakSelectedAddresses()
    }
  }

  const handleKeyPress = (key: ConsoleKey): void => {
    if (commandError.value) {
      commandError.value = false
      commandText.value = ''
    }

    if (isChanCheckMode.value && key !== 'NEXT' && key !== 'LAST') {
      exitChanCheckMode()
    }

    // Helper to clear command line after completion, preserving existing selection if modifying
    const prepareCommandLineForNextInput = (isModifier: boolean) => {
      if (!isCommandCompleted.value) return

      if (isModifier) {
        const activePrefix = isAddressMode.value
          ? formatAddressSelectionString(selectedAddresses.value)
          : formatSelectionString(selectedChannels.value)
        commandText.value = activePrefix.length > 0 ? activePrefix : ''
      } else {
        commandText.value = ''
      }
      isCommandCompleted.value = false
    }

    const isSelectionModifierKey =
      key === 'AT' ||
      key === ' AT ' ||
      key === '+' ||
      key === ' + ' ||
      key === '-' ||
      key === ' - ' ||
      key === 'THRU' ||
      key === ' THRU ' ||
      key === '/' ||
      key === 'FULL' ||
      key === 'FULL_FULL' ||
      key === 'OUT' ||
      key === 'SNEAK' ||
      key === 'REMDIM' ||
      key === 'CHAN_CHECK'

    if (
      isAddressMode.value &&
      isCommandCompleted.value &&
      !isSelectionModifierKey &&
      key !== 'NEXT' &&
      key !== 'LAST' &&
      key !== 'ENTER'
    ) {
      sneakSelectedAddresses()
    }

    if (key === 'ENTER') {
      if (!isCommandCompleted.value && commandText.value.trim().length > 0) {
        saveHistorySnapshot()
        evaluateCommand(commandText.value)
      } else if (selectedAddresses.value.length > 0 && isAddressMode.value) {
        commandText.value = formatAddressSelectionString(selectedAddresses.value)
        commandError.value = false
        isCommandCompleted.value = true
      } else if (selectedChannels.value.length > 0) {
        commandText.value = formatSelectionString(selectedChannels.value)
        commandError.value = false
        isCommandCompleted.value = true
      }
      return
    }

    if (key === 'NEXT' || key === 'LAST') {
      if (isChanCheckMode.value) {
        saveHistorySnapshot()

        if (
          isAddressMode.value ||
          selectedAddresses.value.length > 0 ||
          Object.keys(addressLevels.value).length > 0
        ) {
          const activeKeys = Object.keys(addressLevels.value)
          const currentAddrStr = selectedAddresses.value[0] ?? activeKeys[0] ?? '1/1'
          let universe = 1
          let currentAddrNum = 1

          if (currentAddrStr.includes('/')) {
            const [uStr, aStr] = currentAddrStr.split('/')
            universe = parseInt(uStr ?? '1', 10)
            currentAddrNum = parseInt(aStr ?? '1', 10)
          } else {
            currentAddrNum = parseInt(currentAddrStr, 10)
          }

          let nextAddrNum = key === 'NEXT' ? currentAddrNum + 1 : currentAddrNum - 1
          if (nextAddrNum > 512) nextAddrNum = 1
          if (nextAddrNum < 1) nextAddrNum = 512

          executeAddressCheckStep(`${universe}/${nextAddrNum}`)
          commandText.value = `ADDRESS ${universe}/${nextAddrNum} CHAN_CHECK`
          commandError.value = false
          isCommandCompleted.value = true
          return
        }

        const currentChan = selectedChannels.value[0] ?? 1
        const allChannels = getChannelKeys(channelLevels.value)
        const maxChan = Math.max(...allChannels, 16)

        let nextChan = key === 'NEXT' ? currentChan + 1 : currentChan - 1
        if (nextChan > maxChan) nextChan = 1
        if (nextChan < 1) nextChan = maxChan

        executeChanCheckStep(nextChan)
        commandText.value = `${nextChan} CHAN_CHECK`
        commandError.value = false
        isCommandCompleted.value = true
        return
      }

      const currentChans =
        selectedChannels.value.length > 0
          ? selectedChannels.value
          : commandText.value.trim().length > 0
            ? parseChannelSelection(commandText.value)
            : [1]

      const refChan = key === 'NEXT' ? Math.max(...currentChans) : Math.min(...currentChans)

      const targetChan = key === 'NEXT' ? refChan + 1 : Math.max(1, refChan - 1)

      if (selectedAddresses.value.length > 0) sneakSelectedAddresses()
      selectedChannels.value = [targetChan]
      commandText.value = `${targetChan}`
      isAddressMode.value = false
      isCommandCompleted.value = false
      return
    }

    if (key === 'ADDRESS') {
      prepareCommandLineForNextInput(false)
      isAddressMode.value = true
      commandText.value = 'ADDRESS '
      isCommandCompleted.value = false
      return
    }

    if (key === 'HARD_CLEAR') {
      if (selectedAddresses.value.length > 0 || Object.keys(addressLevels.value).length > 0) {
        sneakSelectedAddresses()
      }
      commandText.value = ''
      selectedChannels.value = []
      selectedAddresses.value = []
      isAddressMode.value = false
      isCommandCompleted.value = false
      return
    }

    if (key === 'CLEAR') {
      if (
        selectedAddresses.value.length > 0 ||
        (isCommandCompleted.value && commandText.value.startsWith('ADDRESS'))
      ) {
        saveHistorySnapshot()
        sneakSelectedAddresses()
        commandText.value = ''
        selectedAddresses.value = []
        isAddressMode.value = false
        isCommandCompleted.value = false
        return
      }

      if (isCommandCompleted.value || commandText.value.length === 0) {
        commandText.value = ''
        selectedChannels.value = []
        selectedAddresses.value = []
        isAddressMode.value = false
        isCommandCompleted.value = false
        return
      }

      const trimmed = commandText.value.trimEnd()
      const lastSpaceIndex = trimmed.lastIndexOf(' ')

      if (lastSpaceIndex === -1) {
        commandText.value = ''
        selectedChannels.value = []
        selectedAddresses.value = []
        isAddressMode.value = false
      } else {
        commandText.value = commandText.value.substring(0, lastSpaceIndex + 1)
        if (!commandText.value.includes('ADDRESS')) {
          isAddressMode.value = false
        }
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
        addressLevels.value = { ...lastSnapshot.addressLevels }
        isManual.value = { ...lastSnapshot.isManual }
        selectedChannels.value = [...lastSnapshot.selectedChannels]
        selectedAddresses.value = [...lastSnapshot.selectedAddresses]
        commandText.value = lastSnapshot.commandText
        isAddressMode.value = commandText.value.startsWith('ADDRESS')
        isCommandCompleted.value = false
      }
      return
    }

    // Process completed command state before adding new key input
    if (isCommandCompleted.value) {
      prepareCommandLineForNextInput(isSelectionModifierKey)
    }

    const line = commandText.value

    if (key === 'CHAN_CHECK') {
      if (isAddressMode.value || selectedAddresses.value.length > 0 || line.startsWith('ADDRESS')) {
        commandText.value = line.length > 0 ? line + ' CHAN_CHECK ' : 'ADDRESS CHAN_CHECK '
        return
      }

      if (selectedAddresses.value.length > 0) sneakSelectedAddresses()
      isAddressMode.value = false
      selectedAddresses.value = []
      commandText.value = line.length > 0 ? line + ' CHAN_CHECK ' : 'CHAN_CHECK '
      return
    }

    if (key === ' - ' || key === '-') {
      if (line.endsWith('AT') || line.endsWith('AT ')) {
        commandText.value = line.trimEnd() + ' -'
      } else {
        commandText.value += line.length > 0 && !line.endsWith(' ') ? ' - ' : '- '
      }
      return
    }

    if (key === ' + ' || key === '+') {
      if (line.endsWith('AT') || line.endsWith('AT ')) {
        commandText.value = line.trimEnd() + ' +'
      } else {
        commandText.value += line.length > 0 && !line.endsWith(' ') ? ' + ' : '+ '
      }
      return
    }

    if (key === ' THRU ' || key === 'THRU') {
      commandText.value += line.length > 0 && !line.endsWith(' ') ? ' THRU ' : 'THRU '
      return
    }

    if (key === '/') {
      commandText.value += '/'
      return
    }

    if (key === 'FULL' || key === 'FULL_FULL') {
      const now = Date.now()
      const isDoubleTap = key === 'FULL_FULL' || now - lastFullClickTime < 400
      lastFullClickTime = now

      if (isDoubleTap) {
        saveHistorySnapshot()
        const targetStr =
          line.trim().length > 0
            ? line
            : isAddressMode.value
              ? formatAddressSelectionString(selectedAddresses.value)
              : formatSelectionString(selectedChannels.value)
        if (targetStr.length > 0) {
          evaluateCommand(`${targetStr} AT FULL`)
        }
        return
      } else {
        if (line.endsWith('THRU') || line.endsWith('THRU ')) {
          commandText.value = line.trimEnd() + ' FULL'
        } else if (line.endsWith('AT') || line.endsWith('AT ')) {
          commandText.value = line.trimEnd() + ' FULL'
        } else if (!line.includes('AT')) {
          commandText.value = line.length > 0 ? line + ' AT FULL' : 'AT FULL'
        }
        return
      }
    }

    if (key === ' AT ' || key === 'AT') {
      if (!line.includes('AT')) {
        commandText.value =
          line.length > 0 ? (line.endsWith(' ') ? `${line}AT ` : `${line} AT `) : 'AT '
      }
      return
    }

    if (key === 'OUT') {
      saveHistorySnapshot()
      const targetStr =
        line.trim().length > 0
          ? line
          : isAddressMode.value
            ? formatAddressSelectionString(selectedAddresses.value)
            : formatSelectionString(selectedChannels.value)
      if (targetStr.length > 0) {
        evaluateCommand(`${targetStr} AT 0`)
      }
      return
    }

    if (key === 'SNEAK') {
      commandText.value =
        line.length > 0 ? (line.endsWith(' ') ? `${line}SNEAK` : `${line} SNEAK`) : 'SNEAK'
      return
    }

    if (key === 'REMDIM') {
      commandText.value =
        line.length > 0 ? (line.endsWith(' ') ? `${line}REMDIM ` : `${line} REMDIM `) : 'REMDIM '
      return
    }

    if (key === 'SELECTLAST') {
      const lastSnapshot = historyStack.value[historyStack.value.length - 1]
      if (lastSnapshot !== undefined) {
        selectedChannels.value = [...lastSnapshot.selectedChannels]
        commandText.value = formatSelectionString(selectedChannels.value)
        isAddressMode.value = false
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
        isAddressMode.value = false
        isCommandCompleted.value = false
      }
      return
    }

    commandText.value += key
    evaluateSelectionOnly(commandText.value)
  }

  return {
    channelLevels,
    addressLevels,
    isManual,
    selectedChannels,
    selectedAddresses,
    commandText,
    commandError,
    isAddressMode,
    isChanCheckMode,
    chanCheckLevel,
    historyStack: readonly(historyStack),
    isCommandCompleted,
    handleKeyPress,
    evaluateCommand,
    saveHistorySnapshot,
  }
}
