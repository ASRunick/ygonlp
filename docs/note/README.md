# YGONLP overview note assets

`assets/` contains the committed SVG and PNG figures used by the overview note. The analysis datasets themselves are not committed.

## Regenerate

Install the development dependencies, then run the script with local analysis metadata. The script resolves the JSON files from metadata and verifies their SHA-256 checksums before plotting; it does not contain manually entered analysis values.

```powershell
python -m pip install -e ".[dev]"
python scripts/generate_note_figures.py `
  --timeseries-metadata <path-to-timeseries-metadata.json> `
  --release-counts-metadata <path-to-release-counts-metadata.json> `
  --output docs\note\assets
```

Generate the analysis outputs first with the same measurement input and intended UTC cutoff. The script accepts only completed metadata, resolves the referenced JSON beside it, and verifies its SHA-256 checksum. A different source snapshot, cutoff, dependency version, or analysis parameter can legitimately produce different figures.

The command writes both `.svg` and `.png` for each figure:

- `text-length-character-count-trend`: annual mean `character_count` from the timeseries overall groups.
- `yearly-release-count`: annual overall `release_count`; a hatched final bar marks a partial cutoff year.
- `analysis-pipeline-overview`: workflow diagram for the analysis commands.
