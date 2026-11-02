# Views

There are 4 pages in this application: Dashboard, Console, Playlists, and Settings, each described below.

## Dashboard

Monitor the status of each backend node, trigger show macros, monitor the host's health, and command restart or shutdown.

### Macros

- Start Show - Begin playback of the playlist set in main_config.json.
- Previous Track/Next Track - Immediately stop playback of the current track and start the Next/Previous track.
- All ON - Stop all show playback and set all patched channels to ON.
- Blackout - Stop all show playback and set all patched channels to OFF.

## Console

Manually control patched channels with this keypad interface! All output overrides show values until the controlled channel(s) are Sneaked.

### Keyboard Shortcuts

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

## Playlists

Upload audio and FSEQ files and assign them to a playlist. Also includes a JSON editor to edit playlist.json. Audio and FSEQ files must be uploaded together, and will be placed on the selected playlist(s).
