# Third-party notices

HFWeatherFax OK5TVR uses third-party Python packages listed in `requirements.txt`.
Official Windows builds also bundle the 64-bit **Hamlib** shared library for direct CAT control.

## Hamlib

- Project: Hamlib — ham radio control library
- Upstream: https://github.com/Hamlib/Hamlib
- Bundled by the official Windows build: Hamlib 4.7.2 x64
- Hamlib is distributed under its upstream open-source license terms (including LGPL/GPL components as documented by the Hamlib project).

The build script downloads the official `hamlib-w64-4.7.2.zip` release asset and verifies its published SHA-256 before bundling the DLLs.
