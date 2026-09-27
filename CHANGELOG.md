# Changelog

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
