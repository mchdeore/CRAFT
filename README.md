# CRAFT

CRAFT answers questions about project documents with cited sources and maintains an engineering datacube. Everything about what it is and how it works is in the wiki: **[mchdeore.github.io/CRAFT](https://mchdeore.github.io/CRAFT/)** (source in [`.public/wiki/`](.public/wiki/README.md)).

```bash
cp .env.example .env  # set the four database passwords and the Foundry endpoint and key
scripts/check         # quick checks; scripts/check full for milestones
```

CRAFT runs on the local Kubernetes cluster: [deploy/README.md](deploy/README.md) brings it up on `localhost:8000`.

Layout: `app/` backend (with the API contract in `app/contract/`, which the separate `craft-frontend` repository compiles against), `workspace/` the JupyterLab workspace image, `.public/wiki/` the wiki. Setup, checks and conventions: [development](.public/wiki/development.md). Only the maintainer merges pull requests.

License: all rights reserved. Any use beyond viewing on GitHub needs the maintainer's written permission; see [LICENSE](LICENSE).
