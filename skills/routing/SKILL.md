---
name: routing
description: Plan a personal driving route from explicitly supplied coordinates.
---

Use route_status and route_current first. This first slice accepts longitude/latitude coordinates only; it does not resolve addresses, acquire device location, or infer a current position. Confirm the supplied origin and endpoint or return-home convention with the user. Do not guess coordinates for an address.

Each edit and solve must carry expected_revision from the latest state. On stale_revision read again and reconsider the edit; never silently overwrite. Stops have stable IDs. Keep visited history, fixed next and precedence locks. Use route_set_round_trip for return-home routes, or route_set_end for an explicit endpoint; these are mutually exclusive.

Locations persist in the active profile's plugin data. Explain that route_solve sends exact coordinates to the configured OSRM server for driving matrices, snaps and road geometry. Public OSRM is a development default, not guaranteed availability. Missing engine: ask the user to configure a plain executable binary path; there is no downloader or installer here.

Show best-found itinerary and estimated driving only, not proven optimal, live traffic, arrival times or navigation. Errors and unreachable stops must be reported, not dropped. After solving emit ::route{} to show the current route in Desktop. The embedded map is a read-only schematic geographic road geometry, not a full slippy map. After edits the solution is pending; solve again. Mark visited only after the user confirms the visit; current origin moves to that supplied stop, distinct from stable home where available.
