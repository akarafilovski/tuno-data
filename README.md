# tuno-data

Radio catalog for the TUNO app, one JSON file per country, served by GitHub Pages:

- `radio/<CC>.json` (ISO country code, for example `radio/HR.json`): `{"stations":[...],"minSupportedVersion":1}`, the same shape as TUNO's `StationsResponse`.
- `radio/index.json`: the countries available, with station counts and the time of the last change.
- `state/health.json`: failure counters, so a station is removed only after three failed daily runs in a row.

`build_radio.py` builds the files from the radio-browser.info directory. It keeps only stations the directory marks as working, removes duplicates by name and stream URL, and probes every stream itself. A GitHub Action runs it every day at 03:17 UTC and commits the result.

Run it locally: `pip install requests` then `python build_radio.py HR RS SI` (or no arguments for every country).

The station data comes from the community directory at radio-browser.info. Station names and logos belong to the stations.
