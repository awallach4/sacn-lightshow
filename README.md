# sACN Lightshow

A lightweight Remote Processing Unit (RPU) and gateway running on a Raspberry Pi 3. Built to bridge sACN (E1.31), audio reactivity, and local Matter light fixtures.

## Features

- **sACN / E1.31 Integration:** Native UDP multicast receiver with Highest Takes Precedence (HTP) merging and priority-based overrides.
- **Hardware Abstraction Layer:** Includes a Windows-compatible `RPi.GPIO` emulator with smooth, flicker-free ANSI ASCII bar-graph output for offline testing.
- **FastAPI Core:** Serves REST endpoints and WebSockets for local control surfaces (iOS Siri Shortcuts and web GUI).

## Project Structure

```text
lightshow-rpu/
├── pyproject.toml        # Single source of truth for dependencies and metadata
├── .gitignore            # Excludes venv, build artifacts, and caches
├── RPi/
│   ├── __init__.py
│   └── GPIO.py           # Mock GPIO module with ANSI ASCII console renderer
└── test_emulator.py      # Demo audio-sweep sweep script for testing offline
```
