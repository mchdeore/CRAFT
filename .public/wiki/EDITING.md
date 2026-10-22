# Editing the wiki

The wiki is the Markdown in `.public/wiki/` on `main`. When a merge changes it, the `Wiki` workflow (`.github/workflows/wiki.yml`) republishes it to [mchdeore.github.io/CRAFT](https://mchdeore.github.io/CRAFT/) within a few minutes; nothing else runs in GitHub. Pages are updated by hand, when asked, in a normal pull request.

## How to update

1. Change the page in a branch. Keep one topic per page; add a new page only for a new topic, and list it on the [home page](README.md).
2. Write what CRAFT does now. Future work goes in the [roadmap](roadmap.md) under Deferred or Vision, not on topic pages.
3. If a decision explains current behavior, add at most one sentence saying why, on the page it affects.
4. Open a pull request. After it merges, check the page on the site.

## Formatting

- Plain GitHub Markdown only: headings, lists, tables, code blocks, links and images. No HTML, and never two opening curly braces in a row or a curly brace followed by a percent sign: the site generator (Jekyll) reads those as template tags and the page fails to publish.
- One `#` title per page, then `##` sections and `###` subsections. No numbers in headings.
- Link to another page with its file name, such as `[roadmap](roadmap.md)` or `[access](datacube.md#access)`. Anchors are the heading in lower case, punctuation removed, spaces turned into hyphens.
- Link to code with a full GitHub address, such as `https://github.com/mchdeore/CRAFT/blob/main/app/contract/types.ts`, because the site only contains `.public/wiki/`.
- Never copy what the code already states. Link to it instead: contract types (`app/contract/`), versions (`pyproject.toml`, `uv.lock`, `web/package.json`), settings (`.env.example`).
- Images go in `.public/wiki/images/` as SVG, with the Mermaid source beside them (`.mmd`).
- Short sentences, present tense, no private paths, names or credentials.

## Contract tests read this wiki

`app/tests/test_contract.py` checks three tables in [api-contract.md](api-contract.md) against the code: the built routes (`### 5.1 Built`), the agent tools (`## 6. Agent tools`) and the response examples (`## 15. Examples`). Keep those headings and table formats, and run `scripts/check` after editing them.
