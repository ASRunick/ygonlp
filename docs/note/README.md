# YGONLP overview note assets

`assets/` contains the committed SVG and PNG figures used by the overview note. The analysis datasets themselves are not committed.

## Regenerate

Install the development dependencies, then run the script with local analysis metadata. The script resolves the JSON files from metadata and verifies their SHA-256 checksums before plotting; it does not contain manually entered analysis values.

```powershell
python -m pip install -e ".[dev]"
python scripts/generate_note_figures.py `
  --timeseries-metadata C:\Users\Tadashi\data\ygonlp\dogfood-issue-23-20260717\timeseries\timeseries-390f66c645bd94e3.metadata.json `
  --release-counts-metadata C:\Users\Tadashi\data\ygonlp\note-release-counts\release-counts-0a6b7dc6bc43d135.metadata.json `
  --output docs\note\assets
```

The command writes both `.svg` and `.png` for each figure:

- `text-length-character-count-trend`: annual mean `character_count` from the timeseries overall groups.
- `yearly-release-count`: annual overall `release_count`; a hatched final bar marks a partial cutoff year.
- `analysis-pipeline-overview`: workflow diagram for the analysis commands.
