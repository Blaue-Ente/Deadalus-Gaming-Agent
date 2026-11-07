Place `python.3.12.10.nupkg` here so `Daedalus-Setup.exe` can install
without downloading CPython at runtime.

`.\packaging\windows\build-setup.ps1` fetches it from nuget.org (official
PSF `python` package) and verifies the SHA-512 in `Get-BundledPython.ps1`.

Do not commit the nupkg.
