# Changelog

## 1.10.0 - 2026-09-27

- Redesigned the main window for small notebook displays: controls are grouped into **Receive / CAT / Stations** tabs instead of three tall columns.
- Added horizontal and vertical splitters so the sidebar, decoded fax and live diagnostics can be resized interactively; layout and active tab are remembered.
- The selected sound input device is now saved by device name (with index fallback) and restored after restart or device-list refresh.
- Added always-visible live **Audio / START 300 Hz / STOP 450 Hz / SYNC** meters.
- Moved phasing and H-sync confidence indicators into the diagnostics panel.
- Kept the live FFT spectrum and waterfall visible below the decoded image and made them responsive to reduced window height.
- Audio level is now measured consistently for both live input and WAV/audio-file playback.

## 1.9.3 - 2026-09-27

- Renamed the station action to **Tune frequency via CAT** / **Naladit frekvenci přes CAT** so its behavior is explicit.
- The action now contains its own frequency-only path and never sends modulation or filter bandwidth.
- Double-clicking a station follows the same frequency-only CAT behavior.

## 1.9.2 - 2026-09-27

- Station-library actions now transfer/tune **frequency only**.
- Selecting or tuning a station no longer forces USB modulation.
- Selecting or tuning a station no longer changes the CAT filter bandwidth.
- The operator-selected modulation and filter remain unchanged until explicitly sent with the CAT controls.

## 1.9.1 - 2026-09-27

- CAT polling no longer overwrites the operator-selected modulation/mode.
- CAT polling no longer overwrites the operator-selected filter bandwidth.
- The actual rig mode and bandwidth are still shown in CAT status while polling.
- The **Read rig** button explicitly loads frequency, mode and filter values from the transceiver.
- Mode/filter selections remain stable after sending frequency or mode commands.

## 1.9.0 - 2026-09-27

- Added detection of a **new 300 Hz APT START while already receiving**. If a preceding STOP was missed, the current fax is finalized and the decoder switches back to phasing for the next image.
- Improved APT STOP robustness with a 4 kHz detection stream, short + long confidence windows and slightly more tolerant confirmation thresholds.
- Added a short post-lock guard so one START cannot be detected twice.
- Auto-save deduplication now tracks the current image generation, avoiding a duplicate save when STOP is followed by the next START.
- Hamlib path and CAT rig settings now persist across program restarts: model, COM port, baud rate, VFO, mode, filter width, last frequency and polling option.
- Added CZ/EN status messages for restored CAT settings and mid-reception START recovery.

## 1.8.0 - 2026-09-27

- Added **HFWeatherFax OK5TVR** branding in the window title, application metadata and Windows EXE name.
- Added a custom multi-resolution Windows application icon.
- Added runtime **Čeština / English** language switching with persistent language preference.
- Localized the main decoder controls, station browser, CAT controls, dialogs and operational status messages.
- Refactored station band/sort and detection-sensitivity selectors to use language-independent internal values.
- Added a PyInstaller spec for reproducible Windows builds.
- Official builds now download and SHA-256 verify Hamlib 4.7.2 x64 and bundle its DLLs for direct CAT control.
- Added GitHub Actions workflow for tagged Windows Releases.
- Added SHA-256 checksum generation for published EXE files.
- Added GitHub housekeeping files (`.gitignore`, `.gitattributes`) and third-party notices.

## 1.7

- Station catalog rebuilt from NOAA Worldwide Marine Radiofacsimile Broadcast Schedules, 7 Mar 2025.
- Added country/service/band filters, UTC schedules, favorites and CAT frequency handling.
