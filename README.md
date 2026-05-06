# dreaming

> Three-phase memory consolidation system for AI agents. Parses daily conversations, extracts memory fragments via LLM, validates over time, and syncs to long-term memory.

## Overview

A self-contained skill for the [WPS Lingxi Claw](https://ai.wps.cn) AI agent platform. Install by copying the `dreaming/` directory into your Claw skills folder.

## Installation

Copy the entire `dreaming/` folder to your WPS Lingxi Claw skills directory:

```
<USER_HOME>/AppData/Roaming/WPS 灵犀/serverdir/skills/dreaming/
```

## Dependencies

No other skills required.

## Environment Variables

All sensitive configuration is managed via environment variables. Copy `.env.example` (if provided) to `.env` and fill in your values:

- `MEMORY_DIR`
- `WPS_SKILLS_DIR`

## Usage

Trigger this skill by mentioning its capabilities in your conversation with the AI agent. See `SKILL.md` for detailed usage instructions and workflow documentation.

## File Structure

```
dreaming/
├── SKILL.md           # Skill documentation and usage guide
└── scripts/           # Python scripts
├── scripts/__init__.py
├── scripts/dreaming\__init__.py
├── scripts/dreaming\cluster.py
├── scripts/dreaming\extract.py
├── scripts/dreaming\main.py
├── scripts/dreaming\memory_writer.py
├── scripts/dreaming\parser.py
├── scripts/dreaming\sleep.py
├── scripts/dreaming\storage.py
├── scripts/wps_sync\__init__.py
├── scripts/wps_sync\client.py
└── scripts/wps_sync\sync.py

## License

[MIT License](LICENSE)
