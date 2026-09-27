# HFWeatherFax OK5TVR

![Ikona HFWeatherFax OK5TVR](assets/hfweatherfax_ok5tvr.png)

**HFWeatherFax OK5TVR** je desktopový program pro Windows určený k dekódování analogového meteorologického HF radiofaxu / WEFAX. Umí zpracovat živý zvuk i nahrané audio soubory, zobrazit spektrum a waterfall, automaticky detekovat START / phasing / STOP, korigovat obraz a přímo ovládat přijímač nebo transceiver přes Hamlib CAT.

[English documentation](README.md)


### Režimy automatického příjmu

START/STOP a LPM jsou nezávislé. Výchozí nastavení je **Automatický START / STOP zapnutý**, **Automatické LPM vypnuté** a pevné **120 LPM**. Při vypnutém Auto LPM program zvolenou hodnotu LPM nepřepisuje. Pokud jsou obě automatiky vypnuté, příjem je plně ruční a dekódování začne ihned s vybraným LPM.

## Stažení

Pro běžné použití ve Windows stáhněte z části **Releases** poslední soubor **`HFWeatherFax_OK5TVR.exe`**. Oficiální Release je samostatné EXE vytvořené pomocí PyInstalleru; na cílovém počítači není nutné instalovat Python.

Ke každému tagovanému vydání se vytváří také `SHA256SUMS.txt`, podle kterého lze ověřit stažený EXE soubor.

> Program nepoužívá `rigctld`. CAT komunikuje přímo s knihovnou Hamlib přes `libhamlib-4.dll`. Oficiální Windows build potřebné 64bit DLL Hamlibu obsahuje.

## Česká a anglická verze

V jednom programu jsou dostupné oba jazyky:

- **Čeština**
- **English**

Jazyk se přepíná položkou **Jazyk / Language** v ovládacím panelu. Volba se uloží a při příštím spuštění obnoví. Při prvním spuštění se na českém systému zvolí čeština, na ostatních systémech angličtina.

## Hlavní funkce

- živý příjem ze zvukové karty a dekódování WAV/FLAC/OGG,
- dekódování FM subnosné BLACK 1500 Hz / WHITE 2300 Hz,
- IOC 288 / 576 a LPM 60 / 90 / 120 / 240,
- automatická detekce START 300 Hz a STOP 450 Hz,
- ochrana při zmeškaném STOP: nový START ukončí předchozí fax a začne nový obrázek,
- volitelná automatická detekce LPM; phasing a horizontální synchronizace fungují i s ručně zvoleným LPM,
- automatická kalibrace BLACK/WHITE,
- automatická i ruční korekce sklonu,
- automatická korekce začátku řádku / přetočení obrazu,
- živé FFT spektrum a waterfall se značkami START / PHASING / IMAGE / STOP,
- kompaktní rozhraní pro notebooky s kartami **Příjem / CAT / Stanice** a nastavitelnými dělicími panely,
- živé indikátory **úroveň audia / START 300 Hz / STOP 450 Hz / SYNC**,
- zapamatování vybrané zvukové karty i rozložení hlavního okna mezi spuštěními,
- automatické ukládání PNG,
- přímé CAT ovládání přes Hamlib bez `rigctld`,
- zapamatování cesty k Hamlibu a nastavení rádia/CAT mezi spuštěními,
- skutečný seznam modelů rádií načítaný z Hamlib DLL,
- databáze HF FAX stanic s filtrem země/služba/pásmo,
- oblíbené stanice a UTC rozvrhy,
- přenos frekvence stanice do CAT a **Naladit frekvenci přes CAT**; obě akce z knihovny mění pouze frekvenci a nemění modulaci ani šířku filtru.

## Databáze stanic

Vestavěný katalog vychází z NOAA/NWS **Worldwide Marine Radiofacsimile Broadcast Schedules** ze **7. 3. 2025**. Publikace sama upozorňuje, že celosvětové rozvrhy mohou být neúplné nebo zastaralé; program proto zachovává informaci o stavu a zdroji položek.

U přidělených frekvencí program podle pravidla publikace vypočítává USB nosnou jako přidělená frekvence minus 1,9 kHz, pokud záznam konkrétní stanice výslovně neuvádí, že jde již o frekvenci nosné.

## Spuštění ze zdrojového kódu

Doporučeno: 64bit Python 3.11 nebo 3.12 pro Windows.

```bat
install.bat
run.bat
```

Hamlib není pro samotné dekódování nutný. Pro CAT lze v programu vybrat existující 64bit Hamlib BIN adresář, případně vložit jeho DLL do `hamlib\bin`.

## Vytvoření EXE na vlastním počítači

Spusťte:

```bat
build_exe.bat
```

Skript automaticky:

1. vytvoří/použije `.venv`,
2. nainstaluje potřebné Python balíčky,
3. stáhne oficiální 64bit Hamlib 4.7.2, pokud ještě není připraven,
4. ověří SHA-256 staženého archivu,
5. vytvoří EXE podle `HFWeatherFax_OK5TVR.spec`.

Výsledek bude:

```text
dist\HFWeatherFax_OK5TVR.exe
```

EXE má vlastní ikonu OK5TVR a Windows metadata verze programu.

## Automatický build na GitHubu

V repozitáři je připraven workflow `.github/workflows/windows-release.yml`.

Pro vydání nové verze stačí například:

```bash
git tag v1.10.1
git push origin v1.10.1
```

GitHub Actions potom na Windows runneru:

1. sestaví `HFWeatherFax_OK5TVR.exe`,
2. vytvoří `SHA256SUMS.txt`,
3. uloží build jako Actions artifact,
4. vytvoří/připojí soubory do GitHub Release odpovídajícího tagu.

Workflow lze spustit také ručně z karty **Actions**. Ruční spuštění vytvoří artifact, ale bez tagu samo nevytvoří Release.

## Hamlib v oficiálním buildu

Build stahuje oficiální `hamlib-w64-4.7.2.zip` z GitHubu projektu Hamlib a před použitím kontroluje jeho zveřejněný SHA-256. Potřebná DLL se následně přibalí do PyInstaller EXE. Informace o knihovnách třetích stran jsou v `THIRD_PARTY_NOTICES.md`.

## Struktura repozitáře

```text
HFWeatherFax_OK5TVR.spec      definice PyInstaller buildu
main.py                       vstupní bod aplikace
hfweatherfax/                 zdrojový kód GUI a dekodéru
assets/                       ikona programu
hamlib/                       lokální/bundled Hamlib
scripts/download_hamlib.ps1   ověřené stažení Hamlibu
.github/workflows/            GitHub Actions build Release
requirements.txt              běhové Python závislosti
requirements-build.txt        build závislosti
CHANGELOG.md                  historie verzí
```

## Windows SmartScreen

Dokud nebude do buildu doplněn certifikát pro podepisování kódu, EXE nebude digitálně podepsané. Windows proto může u nově staženého Release zobrazit SmartScreen upozornění, i když souhlasí SHA-256. Podepisování lze později doplnit bez změny programu.

### CAT: ruční volba režimu a filtru

Při zapnutém **Číst po 1 s** se aktuální režim a šířka filtru rádia zobrazují ve stavovém řádku CAT, ale **nepřepisují** hodnoty zvolené v polích **Režim** a **Filtr**. Tlačítko **Načíst rádio** je načte z TRX záměrně. Volbu odešlete tlačítkem **Nastavit režim**.

