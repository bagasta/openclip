# REA evidence to local implementation

Target studied: OpusClip public homepage, login, podcast demo, and publicly served JavaScript. This project independently implements a similar local workflow; it does not call OpusClip's private API or reuse its shipped application code.

The MCP REA record was rechecked during implementation using `get_evidence_bundle(detail="summary", filters.evidence_id=...)`; it reports complete-record retention.

| Observation | REA source | Implementation choice |
|---|---|---|
| Separate create-project preferences for curation and rendering | `ev_b119a01f497bd332e1ad689f8dad8c152c1be9160120e537631e6bebeb340029`, `3-n9krofnw5xu.js` | Persisted project JSON, job stages, local worker queue |
| Clip cards have scores, titles, durations and exports | `ev_cbf394227bfe55c62f96d19270d7c68a9107c9876ab7fe290c4902a4e2f1d082`, screenshot `ev_6a134b7e53bcc3a67bfed2c71f56f921dd4234fe431c0d4f734430dc2842a1fa` | Real rendered clip cards and explicitly labeled local editorial scores |
| `judgeResult` exposes score components | Same source capture, `3-u_w5-pfwq9i.js` | Independent transcript heuristics or optional Ollama candidate ranking; original weights are unknown |
| `editingScript.tracks` contains caption and keyframe tracks | Same source capture, `0a493cuyvzud2.js`, `246i1rgzu1sv7.js` | Local timeline JSON, ASS/SRT captions, sampled face crop |
| Rendering and exportable clips are distinct operations | Same source capture, `3h9iv30j745ma.js` | FFmpeg renders versioned artifacts; edits can render again without retranscription |

The prior full report and raw evidence are in the parent `outputs` directory. Models, backend topology, ranking weights, and renderer used by OpusClip remain unknown. Faster Whisper, OpenCV, FFmpeg, Python/FastAPI, and Ollama are implementation choices for this laptop, not conclusions about OpusClip's own stack.

Official implementation references consulted:

- https://github.com/SYSTRAN/faster-whisper (CPU int8 and word timestamps)
- https://github.com/yt-dlp/yt-dlp (video source import)
- https://help.opus.pro/api-reference/endpoints/create-project (observed public contract)
- https://help.opus.pro/docs/article/virality-score (product ranking explanation)
