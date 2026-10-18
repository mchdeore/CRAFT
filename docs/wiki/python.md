# Python workspace

## 1. Session

`WorkspaceManager` opens the managed image, receives bounded CSV data and executes Python in a persistent kernel session. Variables remain available across cells in that session. `WorkspaceManager` lists or reads managed files and exports a bundle when it closes the session.

The transport owns container control. The image uses a non-root account, a read-only filesystem, a managed temporary workspace, bounded resources and no external network. Credentials and the application database are held by the controller.

## 2. Analysis

`craft_analysis` receives explicit tables, units and options. It supplies exact parameter comparisons, a chronological cost forecast, seeded risk simulations, sensitivity calculations and local charts. Results identify their method and input. Forecasts and simulations are derived analysis.

The chart builder returns a specification inside its result. Pass that specification to the renderer.

```python
from craft_analysis.core import chart_spec, render_chart

result = chart_spec([{"day": 1, "value": 2}, {"day": 2, "value": 3}], "day", "value")
chart = render_chart(result["spec"], "png")
```

## 3. Files and closure

Files use safe relative names. `WorkspaceManager` refuses traversal, links, oversized files and an oversized close bundle. It collects and verifies files before shutdown. A failed export leaves the session available for individual retrieval. A crashed session loses kernel variables; execution is not replayed automatically.

See [components](components.md), [runtime](runtime.md), [datacube](../spec/08-engineering-datacube.md) and [development](development.md).
