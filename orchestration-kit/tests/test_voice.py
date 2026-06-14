"""Stead — Tier 5: the agent's voice THROUGH MARS. SUBJECT to PRD.md. Starts RED.
Offline-checkable: the Vocalizer records spoken lines instead of needing the robot on the
critical path, so 'it speaks' is gradeable without hardware."""
from consent_agent.voice import Vocalizer


def test_vocalizes_negotiation_and_consent_through_mars():
    v = Vocalizer(sink="mars")
    v.say("Ordering from Tony's, $28, on the card Mom shared.")
    v.say("Consent revoked — stopping.")
    assert len(v.spoken) == 2
    assert all(u["sink"] == "mars" for u in v.spoken)
    assert any("tony" in u["text"].lower() for u in v.spoken)
