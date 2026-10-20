# Friction Log — Amazon Build-Ship-Shape (Alexa+ track)

## Entry 01: Collision at the Register
Date: Contest window | System: zerobeacon MCP server | Track: Alexa+

### Friction observed
Tool-name collision across blocks. System map showed 296 of 699 distinct tool names colliding — same name living in block after block with different behavior. An agent calling a bare name instead of a full address will eventually check out the wrong cart: wrong tier, wrong variant, wrong size, wrong color — and never know it happened.

### Impact
In commerce, a misrouted call is not cosmetic. It is a failed payment, a wrong-tier charge, a dispute with no proof. Same failure class as identical-looking ARC tasks: collision only shows when it fires.

### Turn / fix
Stopped fighting collisions, anchored them. Nine controlled collisions at the core, by design. Every tool addressed by full block path /api/{block}/{nn}/{tool} — choosing the block chooses the tier/variant. Demo chain cart_checkout → delivery_final_proof → dispute_resolve (fed by dispute_evidence) runs as one unbroken, proof-carrying chain.

### Payoff / generalization
Same machinery runs legal, notary, wills and trusts. The commerce block's proof chain, pointed at documents and estates. Verified, trackable, end to end. Ecommerce of the future.

### Evidence
Demo video beats 3–4 (live MCP screen recordings, V2 tool family, block-anchored paths visible on screen).
