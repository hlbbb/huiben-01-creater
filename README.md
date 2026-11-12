# huiben-01-creater

Codex skill for turning a picture-book script into a narrated short video.

It covers:

- multi-role voice casting and semantic-block TTS
- character-region H3 action generation
- motion-mask compositing back onto the original picture-book page
- page timing, full-page text overlay, audio/video assembly, and acceptance checks

## Files

- `SKILL.md` — workflow, guardrails, and acceptance gates
- `scripts/` — reusable TTS, shot fitting, character animation, compositing, and verification scripts

## Install

Copy this folder into your Codex skills directory, for example:

```text
~/.codex/skills/huiben-01-creater
```

or:

```text
~/.agents/skills/huiben-01-creater
```

The scripts are designed for a local Windows setup with ComfyUI, IndexTTS-2.5, FFmpeg, and PyAV available. Review the absolute paths in `SKILL.md` and adapt them to your machine before running.
