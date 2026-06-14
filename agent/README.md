# Stead — care-companion agent

A voice agent that acts on Ruby's behalf **only inside scoped, revocable, owner-held consent**, built
on the **Claude Agent SDK** (Claude API + tool runner, `claude-opus-4-8`, adaptive thinking).

It's the runtime layer on top of the consent core in [`../orchestration-kit`](../orchestration-kit):
- **`order_food`** → calls the Bolo-backed `ConsentEngine` → real **HALT on overstep** (wrong vendor /
  over cap / after revoke), not a prompt rule.
- **`speak_through_mars`** → the agent vocalizes the order + consent moments so the room hears them.
- **`care_handoff`** → the 20-second PCA shift summary from `CareLog`.

## Run

```bash
pip install -r agent/requirements.txt
export ANTHROPIC_API_KEY=...          # your $500 of credits
python agent/stead_agent.py "Ruby wants a pizza from Tony's, about $28"
```

Try it overstepping to see the consent gate fire:

```bash
python agent/stead_agent.py "Ruby wants $45 of sushi from Sushi Palace"   # HALTS — outside the grant
```

## Make it actually call the pizza place (real telephony)

`order_food` places a **real outbound call via Vapi** and orders by voice; **ElevenLabs** is the voice.
Without keys it runs in clearly-marked **MOCK** mode (prints what it would dial and say), so the flow
works offline. Set in `agent/.env`:

```
VAPI_API_KEY=...
VAPI_PHONE_NUMBER_ID=...        # your Vapi caller-ID number
STEAD_TARGET_PHONE=+1...        # the pizza place (or pass vendor_phone to the tool)
# Voice — pick one:
STEAD_VAPI_ASSISTANT_ID=...     # a Vapi dashboard assistant (voice+model configured there) — recommended
STEAD_VOICE_ID=...              # OR an ElevenLabs voiceId (add ELEVENLABS_API_KEY in Vapi → Provider Credentials)
```

One-time, for a cloned voice: **vapi.ai → Settings → Provider Credentials → ElevenLabs**.

The call fires **only inside Mom's live `order:food` grant** (right vendor, within cap) — overstep HALTS
before dialing. Matching r3: in production the **voice ID comes from a Bolo voice grant**, so revoking
the grant revokes the voice.

## Real Bolo (live, revocable grants)

By default the engine uses a deterministic **in-memory** backend so the demo runs offline on a hotspot.
For real grants, inject a live Bolo client:

```python
consent = ConsentEngine(owner="mom", bolo=bolo_mcp_client)  # @bolospot/mcp adapter
```

`anthropic[mcp]` ships the MCP helpers (`anthropic.lib.tools.mcp`) to connect `@bolospot/mcp` and wire
its `create_grant` / `check_access` / `revoke_grant` tools.

## Stack (from the Claude API reference)

- **Surface:** Claude API + tool runner (`client.beta.messages.tool_runner`) — we host the loop so the
  agent can drive MARS, TTS, and Bolo itself.
- **Model:** `claude-opus-4-8`, `thinking: {type: "adaptive"}`.
- **Consent is the authority plane:** every action goes through the Bolo-backed `ConsentEngine`.
