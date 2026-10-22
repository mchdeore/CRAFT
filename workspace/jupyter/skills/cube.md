# Cube questions

Use this skill to answer mission and parameter questions from the engineering datacube.

1. Resolve names and units first: `craft.cube.missions("sim-01")`, `craft.cube.parameters()`.
2. Filter missions: `craft.cube.find(("orbit_altitude", "le", 600, "km"), ("power_peak", "ge", 400, "W"))`.
   A range condition is `("mass_dry", "between", 100, 150, "kg")`. Unknown values never match.
3. One mission on a date: `craft.cube.mission("msn-sim-01", at="2026-03-15")`; `gaps` lists parameters without a value.
4. Side by side: `craft.cube.compare(["msn-sim-01", "msn-sim-02"], ["mass_dry", "power_peak"]).df`.
5. Evidence for one value: `craft.cube.evidence(value_id)` gives the quote, source number, unit, conversion and citation status.
6. Similar missions: `craft.cube.similar("msn-sim-01")` and `craft.cube.explain("msn-sim-01", "msn-sim-04")`.

Report withdrawn or changed citations. Never fill a gap or average two values.
