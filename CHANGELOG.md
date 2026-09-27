# Changelog

## 1.10.4 - 2026-09-27

- Added a dedicated **Start image decoding** / **Spustit dekódování obrazu** button.
- The manual image start immediately begins raster capture at the currently selected LPM (120 LPM by default), bypassing a missed START/phasing lock.
- If live reception is not running yet, the manual image-start button starts the selected audio input first.
- Automatic START/STOP can remain enabled: after a manual image start, normal automatic STOP detection still finishes and optionally saves the image.
- Existing decoded lines are preserved when manually resuming capture; use Clear image first when a new raster is desired.
- Added a detector-side forced RECEIVING state with a short restart guard to avoid immediately mistaking the current signal for a new START.

## 1.10.3 - 2026-09-27

- Added a compact **live transceiver display** to the CAT/radio controls.
- The display shows the actual CAT frequency, current modulation/mode, signal strength and CAT connection state.
- CAT connection uses a simple green/red status LED as requested.
- Added Hamlib `RIG_LEVEL_STRENGTH` reading for an S-meter; unsupported radios show `S --` without breaking CAT polling.
- S-meter values are presented as familiar S-units (S0–S9 / S9+ dB) from Hamlib's calibrated dB-relative-to-S9 value.
- The live display is independent from the editable frequency/mode/filter command controls, so monitoring never overwrites prepared operator settings.

## 1.10.2 - 2026-09-27

- Automatic STOP now explicitly **finishes the current image capture** instead of only returning the detector to WAIT_START.
- Live audio, spectrum and START detector continue running after STOP so the program can wait for the next fax without appending lines to the completed image.
- Added an independent raster-capture gate: START/phasing pauses image writing, phasing LOCK enables it, and STOP disables it immediately.
- Prevented the final timer cycle from overwriting the STOP status with a stale "receiving" message.
- The completed fax remains visible and can be saved while the receiver waits for the next START.

## 1.10.1 - 2026-09-27

- Split receive automation into independent **Automatic START / STOP** and **Automatic LPM** controls.
- Automatic LPM is now disabled by default; the primary fixed/manual line rate is **120 LPM**.
- With Auto START/STOP enabled and Auto LPM disabled, START/STOP remains automatic while phasing is evaluated only at the operator-selected fixed LPM; the software does not change it.
- With both options disabled, reception is fully manual and starts decoding immediately at the selected LPM.
- With Auto LPM enabled but Auto START/STOP disabled, the program can acquire LPM from phasing while START/STOP remains manual.
- START/STOP/SYNC diagnostics continue to run even in manual mode.
- Receive automation choices and the manually selected LPM are remembered between program starts.

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
