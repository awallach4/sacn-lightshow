# sACN Lightshow

A lightweight lightshow system intended to run on a Raspberry Pi. Supports a web dashboard, scheduler, and multiple playlists and controls lights over sACN (E1.31).

## Features

- **sACN / E1.31 Integration:** Native UDP multicast receiver with Highest Takes Precedence (HTP) merging and priority-based overrides.
- **Flask + gevent server** Serves REST endpoints and WebSockets for local control surfaces (hosted Vue app)
- **Playlist-Centered** Designed around creating playlists and playing them back in order.
- **Supports xLights** Sequence files for lights can be created in xLights or any other program that exports .fseq files.
- **Web App** Includes a Vue app with a system dashboard, JSON editors for playlist, sACN patch, schedule, and system settings files.
- **Remote Console** The Vue app includes a simple console designed to be used as an RFU (Remote Focus Unit) using industry-standard syntax.
- **Modular Design** The system consists of individual nodes, allowing parts of it to run on separate machines.
- **Compatible with Windows** While intended to run on Raspberry Pi OS, this system will run on Windows except for FM broadcast and GPIO output

## Project Structure

```text
sacn-lightshow/
├── config/
│   ├── main_config.json          # System settings
│   ├── patch.json                # sACN patch, grouped by universe
│   ├── playlist.json             # Playlist definitions
│   └── schedule.json             # Date/Time stamps to run commands
├── frontend/
│   ├── dist/                     # Production frontend directory
│   ├── public/                   # Icon and manifest files
│   └── src/                      # Vue, CSS, and Typescript frontend files
├── logs/                         # Output logs from each node
├── showfiles/
│   ├── music/                    # Audio files for playback
│   └── sequences/                # xLights .fseq files for playback
├── src/
│   ├── sacn/
│       ├── sacn_listener.py      # Listens for data on every universe in patch.json and outputs to GPIO pins
│       └── sacn_transmitter.py   # Transmits data from a .fseq file over sACN
│   ├── web/
│       └── app.py                # Hosts the Vue app and responds to API calls
│   ├── audio_playback.py         # Plays an audio file over speakers or FM broadcast via GPIO4
│   ├── logger_setup.py           # Helper to initialize the logger for all nodes
│   ├── main.py                   # Entry-point for all nodes, also handles macros and keep-alive for each node
│   ├── scheduler.py              # Reads schedule.json and sends the scheduled macros at the listed dates/times
│   └── show_control.py           # Manages the sACN transmitter and audio playback nodes, including synchronization and playlists
├── .gitignore                    # Excludes venv, build artifacts, caches, logs, showfiles, and configs
├── LICENSE                       # This project is licensed under the MIT License
├── README.md
└── requirements.txt              # Run this with pip to install dependencies
```

## Installation

Clone the repository and use pip to install dependencies:

```
cd sacn-lightshow
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create/edit config/main_config.json and config/patch.json

Start the system: `python3 src/main.py`

Access the Vue app at http://localhost:5000 or http://[raspberry-pi-ip]:5000

## Main Config

The `main_config.json` file contains system settings, including the paths to other config files. **It is CRITICAL that you add this file BEFORE starting the system for the first time.** Here's an example of this file:

```
{
  "system": {
    "show_name": "Holiday Lightshow",
    "playlist_name": "Winter 2026",
    "log_level": "INFO",
    "broadcast": {
      "fm_enabled": true,
      "fm_frequency": 90.3,
      "rds_ps_name": "HOLIDAY",
      "rds_rt_default": "Lightshow",
      "fifo_path": "/tmp/fm_audio.fifo"    # Path to persistent FIFO file
    },
    "sync": {
      "lead_time_s": 1.0,        # How long to wait after services are ready before starting the track
      "ready_timeout_s": 60,     # How long to wait maximum before lights/sound are ready to run
      "end_grace_s": 15          # How long to let lights run past the end of sound before terminating
    },
    "network": {
      "sacn_universe": 11,  # Starting universe
      "priority_normal": 100,
      "priority_panic": 200,
      "multicast": true
    },
    "paths": {
      "playlist_config": "config/playlist.json",
      "patch_config": "config/patch.json",
      "schedule_config": "config/schedule.json"
    }
  }
}
```

## Patch

Patch controls the relationship between sACN universe/address pairs, channel numbers, and GPIO pins. See an example below, devices are nested inside universes, and parameters are nested inside devices. For more information on patching and a good overview of how a lighting system works, check out this great article by ETC: https://blog.etcconnect.com/stage-lighting-design-part-7

```
{
  "patch": {
    "11": {
      "devices": {
        "1": {                      # Channel number for the web console
          "type": "GPIO",           # GPIO, matter (unimplemented), or DMX (only to show in the web console)
          "label": "Relay 1",
          "pin": 17,                # BCM Pin number
          "parameters": [
            {
              "address": 1,
              "param_type": "intensity",
              "nondim": true,
              "default_level": 0,
              "label": "Relay 1"
            }
          ]
        },
        "2": {
          "type": "GPIO",
          "label": "Relay 2",
          "pin": 18,
          "parameters": [
            {
              "address": 2,
              "param_type": "intensity",
              "nondim": true,
              "default_level": 0,
              "label": "Relay 2"
            }
          ]
        },
        "3": {
          "type": "GPIO",
          "label": "Relay 3",
          "pin": 27,
          "parameters": [
            {
              "address": 3,
              "param_type": "intensity",
              "nondim": true,
              "default_level": 0,
              "label": "Relay 3"
            }
          ]
        },
        "4": {
          "type": "GPIO",
          "label": "Relay 4",
          "pin": 22,
          "parameters": [
            {
              "address": 4,
              "param_type": "intensity",
              "nondim": true,
              "default_level": 0,
              "label": "Relay 4"
            }
          ]
        },
        "5": {
          "type": "GPIO",
          "label": "Relay 5",
          "pin": 23,
          "parameters": [
            {
              "address": 5,
              "param_type": "intensity",
              "nondim": true,
              "default_level": 0,
              "label": "Relay 5"
            }
          ]
        },
        "6": {
          "type": "GPIO",
          "label": "Relay 6",
          "pin": 24,
          "parameters": [
            {
              "address": 6,
              "param_type": "intensity",
              "nondim": true,
              "default_level": 0,
              "label": "Relay 6"
            }
          ]
        },
        "7": {
          "type": "GPIO",
          "label": "Relay 7",
          "pin": 25,
          "parameters": [
            {
              "address": 7,
              "param_type": "intensity",
              "nondim": true,
              "default_level": 0,
              "label": "Relay 7"
            }
          ]
        },
        "8": {
          "type": "GPIO",
          "label": "Relay 8",
          "pin": 16,
          "parameters": [
            {
              "address": 8,
              "param_type": "intensity",
              "nondim": true,
              "default_level": 0,
              "label": "Relay 8"
            }
          ]
        }
      }
    }
  }
}
```

## Vue App

There are 4 pages in this application: Dashboard, Console, Playlists, and Settings, each described below.

### Dashboard

Monitor the status of each backend node, trigger show macros, monitor the host's health, and command restart or shutdown.

#### Macros

- Start Show - Begin playback of the playlist set in main_config.json.
- Previous Track/Next Track - Immediately stop playback of the current track and start the Next/Previous track.
- All ON - Stop all show playback and set all patched channels to ON.
- Blackout - Stop all show playback and set all patched channels to OFF.

### Console

Manually control patched channels with this keypad interface! The console outputs at priority 50, so any show output will override it. It is intended as a Remote Focus Unit (RFU).

#### Keyboard Shortcuts

- 0-9 - 0-9 on keyboard or numeric keypad
- / - / on keyboard or numeric keypad, use for Universe/Address notation or proportional levels
- . - . on keyboard or numeric keypad
- Clear - Backspace or Esc - Clear the previous keyword, or clear a terminated command
- Hard Clear - Shift + Backspace or Esc - Clear the command line
- \+ - + or = on keyboard or numeric keypad
- \- - - on keyboard or numeric keypad
- Thru - t or T on keyboard
- At - a or A on keyboard
- Full - f or F on keyboard, double tap Full to instantly set the selection to Full
- Out - o or O on keyboard, instantly set selection to 0
- Rem Dim - h or H on keyboard, set all unselected channels to the specified value
- Sneak - n or N on keyboard, release selection (or all if not specified) to background value
- Home - Unimplemented
- Select Last - Ctrl + L or Ctrl + l, reselect previous selection
- Select Active - Ctrl + A or Ctrl + a, select all channels that are set above 0
- Enter - Enter or Return, execute the command
- Undo - Ctrl + Z or Ctrl + z, undo the last command (works for all commands this session)
- Chan Check - Alt + 5, enter channel check mode (e.g. 1 At Full Chan Check Enter), clears on any key besides Next or Last
- Last - Up Arrow, select the channel one before the selection, or go back 1 in Chan Check mode
- Next - Down Arrow, select the channel one after the selection, or go forward 1 in Chan Check mode
- Address - Alt + A or Alt + a, control a DMX address instead of a channel (e.g. Address 2/1 At Full), sneaks on Out, Clear, or new selection

### Playlists

Upload audio and FSEQ files and assign them to a playlist. Also includes a JSON editor to edit playlist.json. Audio and FSEQ files must be uploaded together, and will be placed on the selected playlist(s). This can only be done when the show is not running.

### Settings

Edit patch.json, schedule.json, and main_config.json. These files can only be edited while the show is not running.

## Building the Vue App

A built version of the Vue App is provided, but to rebuild it, run:

```
cd frontend
npm install
npm run build
```

## Playlists

This is a playlist-based system, so everything related to the show is defined in playlist.json. Each track requires an audio file (.wav, .mp3, .aac, .aif, .ogg, or .flac) and a sequence file (.fseq). Other configureable properties include:

```
"playlist_name": [
  {
    "title": ""                 # The title to display in the Vue app
    "button_hold_s": 0.0        # How long to hold the final frame of the sequence (the 'button' cue)
    "post_delay_s": 0.0         # How long to wait before playing the next track
    "audio_offset_s": 0.0       # How much to shift FM audio vs lights (+ is later)
    "speaker_offset_s": 0.0     # How much to shift speaker audio vs lights (+ is later)
    "light_speed": 1.0          # Speed multiplier for sequence playback
    "rds": ""                   # Description text to display via RDS
  }
]
```

## Schedule

The schedule is a list of times each day to run a command. This can be the name of a playlist, 'ON', or 'OFF'. Here's an example schedule, keep in mind all times are in your local timezone and are formatted as 24-hour time.

```
{
  "schedule": {
    "sunday": {
      "1700": "Winter 2026",
      "1800": "Winter 2026",
      "1900": "Winter 2026",
      "2000": "Winter 2026",
      "2100": "ON",
      "2230": "OFF"
    },
    "monday": {
      "1700": "Winter 2026",
      "1800": "Winter 2026",
      "1900": "Winter 2026",
      "2000": "Winter 2026",
      "2100": "ON",
      "2230": "OFF"
    },
    "tuesday": {
      "1700": "Winter 2026",
      "1800": "Winter 2026",
      "1900": "Winter 2026",
      "2000": "Winter 2026",
      "2100": "ON",
      "2230": "OFF"
    },
    "wednesday": {
      "1700": "Winter 2026",
      "1800": "Winter 2026",
      "1900": "Winter 2026",
      "2000": "Winter 2026",
      "2100": "ON",
      "2230": "OFF"
    },
    "thursday": {
      "1700": "Winter 2026",
      "1800": "Winter 2026",
      "1900": "Winter 2026",
      "2000": "Winter 2026",
      "2100": "ON",
      "2230": "OFF"
    },
    "friday": {
      "1700": "Winter 2026",
      "1800": "Winter 2026",
      "1900": "Winter 2026",
      "2000": "Winter 2026",
      "2100": "ON",
      "2230": "OFF"
    },
    "saturday": {
      "1700": "Winter 2026",
      "1800": "Winter 2026",
      "1900": "Winter 2026",
      "2000": "Winter 2026",
      "2100": "ON",
      "2230": "OFF"
    }
  }
}
```

## Disclaimer

This project was built with the help of artificial intelligence. Per the license, I take no responsibility for any malfunctions, incidents, or your decisions about how to use this project. **Please do NOT mess with AC mains voltage unless you know what you are doing (especially when it comes to dimming)!**
