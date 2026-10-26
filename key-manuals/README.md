# Key manuals

Each judging lane's manual is delivered with its key. It is not
published in this repository.

```
GET https://api.zerobeacon.ai/api/lane-manual
X-API-Key: zbk_…
```

- The key is the lane selector. There is no lane parameter: a lane's
  key returns that lane's manual and no other.
- Only judging-lane keys at the enterprise tier receive a manual. Other
  keys, including paid customer keys, are refused with `403
  not_a_lane_key`.
- Access ends when the key expires. An expired key is refused with
  `403 key_expired`.
- A missing or unknown key is refused with `403 key_required` or
  `403 invalid_key`.
- If a lane's manual has not been provisioned in the running
  deployment, the endpoint answers `503 manual_not_provisioned`.

The response is the manual as `text/markdown`.

Manual revision 2026-10-25: the PayPal and Amazon lane manuals
now carry a shared section ("Two paths, one harness") — each
lane's integration written for both audiences, engineering and
plain terms, with a compare-and-contrast of the two paths.

Amazon manual revision 2026-10-25 (v3): the trust harness at
full treatment, a dispute-resolution adaptation section, and
the cleaned shared core.

PayPal manual revision 2026-10-26 (v2.1): the MCP tools line
now states the eleven tools precisely (three each for Apathy,
Proven and Anchor, two for Receipts; the composed flow is
REST-only), and the composed-flow description states plainly
that a refusal is recorded in the receipt chain too.
