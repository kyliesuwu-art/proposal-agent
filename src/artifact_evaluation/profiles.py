from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class Profile:
    name: str
    delivery: bool
    min_ppi_warning: int = 120
    min_ppi_error: int = 72
    min_font_pt: int = 10

PROFILES = {"internal-source": Profile("internal-source", False), "client-delivery": Profile("client-delivery", True)}

def get_profile(name: str) -> Profile:
    try: return PROFILES[name]
    except KeyError: raise ValueError(f"unknown profile: {name}") from None
