"""Invented mission themes and the adopted engineering dictionary."""

FAMILIES = (
    (
        "earth-observation",
        "compact coastal imaging",
        "multispectral tiles and seasonal mapping",
        80,
        300,
        550,
    ),
    (
        "communications-relay",
        "regional relay mesh",
        "store-and-forward channels and link availability",
        250,
        900,
        1800,
    ),
    (
        "science-probe",
        "remote ice spectroscopy",
        "spectral samples and long navigation arcs",
        600,
        1600,
        42000,
    ),
    (
        "technology-pathfinder",
        "modular avionics trial",
        "clock stability and experimental interfaces",
        120,
        450,
        430,
    ),
)
ELEMENTS = ("power subsystem", "structure", "payload")
PARAMETERS = (
    (
        "mass_dry",
        "Dry mass",
        "kg",
        "0",
        None,
        False,
        {"kg": "1", "g": "0.001", "t": "1000", "lb": "0.45359237"},
        ("dry mass", "masse sèche"),
    ),
    (
        "power_peak",
        "Peak power",
        "W",
        "0",
        None,
        False,
        {"W": "1", "kW": "1000", "mW": "0.001"},
        ("peak power", "puissance crête"),
    ),
    (
        "payload_volume",
        "Payload volume",
        "m3",
        "0",
        None,
        False,
        {"m3": "1", "L": "0.001", "cm3": "0.000001"},
        ("payload volume", "volume utile"),
    ),
    (
        "orbit_altitude",
        "Orbit altitude",
        "km",
        "0",
        None,
        False,
        {"km": "1", "m": "0.001"},
        ("orbit altitude", "altitude orbitale"),
    ),
    (
        "readiness",
        "Technology readiness",
        "level",
        "1",
        "9",
        True,
        {"level": "1"},
        ("readiness", "maturité"),
    ),
    (
        "design_life",
        "Design life",
        "year",
        "0",
        None,
        False,
        {"year": "1", "month": "0.08333333333333333333333333333"},
        ("design life", "durée de vie"),
    ),
)


def narrative(mission: str, theme: str, focus: str, seed: int) -> list[str]:
    """Describe a fictional engineering review with enough context for retrieval."""
    return [
        f"{mission} develops {theme}. The scenario is simulated and unclassified.",
        f"The baseline emphasizes {focus}. The payload and bus share a bounded interface envelope. "
        "The verification team checks allocations before changing the reference configuration. "
        "The engineering record distinguishes an allocated limit from a measured result.",
        "Power modes include acquisition, processing, safe hold and downlink. "
        "Each mode has its own "
        "duty cycle and thermal assumptions. The budget keeps a reserve for transient loads. "
        "Subsystem estimates are checked against the mission total rather than added twice.",
        "Interface reviews cover mechanical attachment, timing, data transport "
        "and fault isolation. "
        "A change to one interface is traced to the dependent verification case. Open findings "
        "remain visible until their evidence and closure criteria are recorded.",
        "The test campaign separates bench evidence, integration evidence "
        "and operational rehearsal. "
        "The plan records the configuration used for each case. A later configuration needs a new "
        "verification record; earlier evidence is available for comparison.",
        "Schedule reserves protect the integration sequence from late equipment changes. Cost "
        "estimates include qualification articles, test support and a contingency allowance. "
        "The review panel checks the assumptions before accepting the next lifecycle gate.",
        f"Scenario {seed} uses invented equipment, values and review outcomes. Source dates and "
        "lifecycle tags describe this fictional baseline only.",
    ]
