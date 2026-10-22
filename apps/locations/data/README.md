# Zone boundaries

`cqzones.geojson` and `ituzones.geojson` are the CQ and ITU zone boundaries
from https://github.com/HB9HIL/hamradio-zones-geojson (commit
091b813f35c899af194d028d3841fc10b16fbd45), MIT licensed, see `LICENSE-zones`.
They are the same files Wavelog uses.

They are used unchanged by `apps.locations.zones`. Some polygons run past
±180° longitude to cross the antimeridian, and nothing covers the poles
beyond about 85°.
