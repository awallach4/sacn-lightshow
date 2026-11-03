export interface Parameter {
  address: number // Offset within the universe
  param_type: string // e.g. "intensity", "red", "green", "blue"
  nondim: boolean
  default_level: number
  label: string
}

export interface Device {
  type: string
  label: string
  device_id?: string
  pin?: number
  parameters: Parameter[]
}

export interface UniversePatch {
  devices: Record<string, Device>
}

export interface PatchConfig {
  patch: Record<string, UniversePatch>
}

// Flat DMX Target output structure
export interface ResolvedAddress {
  universe: number
  address: number
  nondim: boolean
  paramType: string
}

/**
 * Resolves a Channel or Address reference to absolute Universe/DMX Offsets using patch.json
 */
export function resolvePatchOutput(
  patchData: PatchConfig,
  channelLevels: Record<number, number>,
  addressLevels: Record<string, number>,
): Record<string, number> {
  const outputFrame: Record<string, number> = {} // Format: "universe/address": dmxValue (0-255)

  // 1. Process explicit Address Overrides ("universe/address")
  Object.entries(addressLevels).forEach(([addrKey, pctLevel]) => {
    const dmxVal = Math.round((pctLevel / 100) * 255)
    outputFrame[addrKey] = dmxVal
  })

  // 2. Process Channel Mappings based on Patch Schema
  // Channels correspond to Device IDs across Universes
  Object.entries(patchData.patch || {}).forEach(([universeStr, universeData]) => {
    const universe = parseInt(universeStr, 10)
    if (isNaN(universe) || !universeData.devices) return

    Object.entries(universeData.devices).forEach(([deviceIdStr, device]) => {
      const channelNum = parseInt(deviceIdStr, 10)
      const pctLevel = channelLevels[channelNum]

      // If this channel has an active level set
      if (pctLevel !== undefined) {
        device.parameters.forEach((param) => {
          const dmxKey = `${universe}/${param.address}`

          // Respect Non-Dim logic (Full On at >0%, else 0)
          let finalDmxVal = 0
          if (param.nondim) {
            finalDmxVal = pctLevel > 0 ? 255 : 0
          } else if (param.param_type === 'intensity') {
            finalDmxVal = Math.round((pctLevel / 100) * 255)
          } else {
            // Default color/attribute handling scaled by intensity
            finalDmxVal = Math.round((param.default_level * pctLevel) / 100)
          }

          // Address level overrides channel level if both exist
          if (!(dmxKey in addressLevels)) {
            outputFrame[dmxKey] = finalDmxVal
          }
        })
      }
    })
  })

  return outputFrame
}
