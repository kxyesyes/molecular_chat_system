from __future__ import annotations

import re
from collections.abc import Mapping

from src.agent.contracts import ScientificClaim


class UnsupportedClaimError(ValueError):
    pass


CLAIM_PATTERN = re.compile(r"\{\{claim:([A-Za-z0-9_-]+)\}\}")


def render_claim_template(
    template: str,
    claims: Mapping[str, ScientificClaim],
) -> str:
    def replace(match: re.Match[str]) -> str:
        claim_id = match.group(1)
        if claim_id not in claims:
            raise UnsupportedClaimError(f"Unknown claim id: {claim_id}")
        claim = claims[claim_id]
        suffix = f" {claim.unit}" if claim.unit else ""
        return f"{claim.value}{suffix}"

    rendered = CLAIM_PATTERN.sub(replace, template)
    if "{{claim:" in rendered:
        raise UnsupportedClaimError("Malformed claim placeholder")
    return rendered
