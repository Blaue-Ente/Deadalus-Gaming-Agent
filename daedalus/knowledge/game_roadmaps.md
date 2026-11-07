# Game roadmaps and architecture canon

Daedalus does not vendor these trees into the package. Compact notes live here for RAG. Full READMEs and wiki pages are opened on demand with `game_canon` (`list` → `study` → `open` or `chunk`). Clone stays under `$DAEDALUS_DATA/canon/{id}/`. Never copy assets, trademarks, or book text into a Playground game.

Owner star list (five study repos): https://github.com/stars/Blaue-Ente/lists/дедал/

## milo-path — https://github.com/miloyip/game-programmer

Milo Yip's study path: math, physics, computer graphics, systems. Allowlisted files: `readme.md`, `isbn.csv` (titles and ISBNs only). Do not fetch the multi-megabyte PDF/PNG posters. Pair a formula from the path with `math3d`.

## game-program-books — refused

https://github.com/kurong00/GameProgramBooks is a dump of copyrighted ebooks collected against that path. `clone`, `open`, `chunk`, and `search` refuse. Buy the books named in `isbn.csv`. For named gameplay patterns use `game-programming-patterns`, not a PDF ingest.

## cis-roadmap — https://github.com/CIS-Team/Game-Development-Roadmap-2024

Modern game-dev checklist (algorithms, Unity-oriented reading, architecture pointers). `open` `README.md` then `chunk` by heading. Do not paste the whole README into the harness.

## awesome-engine — https://github.com/stevinz/awesome-game-engine-dev

Awesome list for people (and agents) who build or read engine internals: core, audio, geometry, graphics. `README.md` is large and truncates at 80k on `open`. Then `chunk`. Follow a link and write original code; do not scrape the list into a slug.

## engine-arch-samples — https://github.com/Azurelol/GameEngineArchitectureSamples

Small C++ samples (events, ECS-ish layout, factory). `chunk` splits `class`/`struct` and prefixes `// Context: Event System Pattern` (or similar) so RAG chunks stay labelled. Study SampleEventSystem headers before inventing a singleton `EventManager`. Keep the tree in the canon jail.

## unity-chop — https://github.com/UnityTechnologies/open-project-1

Chop Chop is a large Unity project. Value for Daedalus is the **wiki**, not `UOP1_Project`. Open `wiki/Game-architecture-overview.md`, `wiki/Event-system.md`, `wiki/State-Machine.md` (fetched from the GitHub wiki, not the git tree). Steal Event Channels and SO-driven FSMs; never copy Chop Chop art or scripts. This cloud has no Unity Editor — scaffold is not Play. `large`: do not clone the full repo into a Playground slug.

## tinyrenderer and stb (graphics / zero-overhead C)

Already in canon as `tinyrenderer` and `stb`. Raster math: barycentric, z-buffer, textures — verify projection with `math3d`. stb: single-file C, caller-owned buffers. Do not drop either tree into a game as the product.

## How to feed RAG

1. Markdown: `game_canon` `open` the allowlisted README/wiki, or `chunk` to split on headings.
2. C++ samples: `chunk` id `engine-arch-samples` (class/struct + Context comment). Do not vendor files into `daedalus/knowledge/`.
3. Always `study` first so `do_not_copy` is in the payload.
