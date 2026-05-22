# Tournament Map/Mode Verification

This project keeps tournament map/mode selection conservative. A mode is exposed in the normal frontend selector only after the existing allocation path can:

1. allocate a warm Agones `GameServer`
2. apply the requested mode and map with whitelisted RCON
3. confirm the live server with `getstatus`
4. release the disposable verification assignment

Do not promote a map/mode pair based only on upstream map metadata.

## RCON Sequence

Xonotic gametype definitions use these short aliases, and the current backend applies them with the same sequence for every mode:

| Mode | Label | RCON sequence |
| --- | --- | --- |
| `dm` | Deathmatch | `gametype dm`, then `changelevel <map>` |
| `tdm` | Team Deathmatch | `gametype tdm`, then `changelevel <map>` |
| `ctf` | Capture The Flag | `gametype ctf`, then `changelevel <map>` |
| `duel` | Duel | `gametype duel`, then `changelevel <map>` |
| `ca` | Clan Arena | `gametype ca`, then `changelevel <map>` |
| `dom` | Domination | `gametype dom`, then `changelevel <map>` |
| `kh` | Key Hunt | `gametype kh`, then `changelevel <map>` |

Verification passes only when `getstatus` reports both the requested `map` and `game_mode`.

## Current Verified Matrix

These are the only combinations selectable in normal match creation:

| Mode | Verified maps |
| --- | --- |
| `dm` | `xoylent`, `stormkeep`, `solarium` |
| `tdm` | `stormkeep` |

## Experimental Candidates

These candidates come from upstream Xonotic mapinfo metadata and must still be verified against the deployed server image before promotion.

| Mode | Candidate maps |
| --- | --- |
| `dm` | `drain`, `darkzone`, `runningman`, `warfare` |
| `tdm` | `xoylent`, `solarium`, `darkzone`, `implosion`, `runningman`, `silentsiege` |
| `ctf` | `catharsis`, `courtfun`, `dance`, `go`, `implosion`, `runningmanctf`, `space-elevator`, `vorix` |
| `duel` | `darkzone`, `fuse`, `stormkeep`, `warfare`, `xoylent` |
| `ca` | `darkzone`, `implosion`, `runningman`, `solarium`, `stormkeep`, `xoylent` |
| `dom` | `afterslime`, `geoplanetary`, `glowplant`, `implosion`, `runningmanctf`, `stormkeep` |
| `kh` | `implosion`, `runningman`, `runningmanctf`, `solarium`, `stormkeep` |

## Verification Flow

Port-forward the backend:

```bash
kubectl port-forward -n xonotic-allocator-backend service/xonotic-allocator-backend 18080:8080
```

For already verified pairs, run:

```bash
scripts/verify-tournament-map-mode.sh dm xoylent
```

For experimental pairs, temporarily enable backend probe mode and redeploy the backend first:

```bash
kubectl set env deployment/xonotic-allocator-backend \
  -n xonotic-allocator-backend \
  XONOTIC_ENABLE_EXPERIMENTAL_GAME_CONFIG=1
kubectl rollout status deployment/xonotic-allocator-backend -n xonotic-allocator-backend
```

Then run a candidate probe:

```bash
scripts/verify-tournament-map-mode.sh --experimental ctf implosion
```

The script creates disposable tournament records, allocates a server, checks the allocation configuration result, and releases the server unless `--keep-server` is supplied.

When finished, disable probe mode again:

```bash
kubectl set env deployment/xonotic-allocator-backend \
  -n xonotic-allocator-backend \
  XONOTIC_ENABLE_EXPERIMENTAL_GAME_CONFIG-
kubectl rollout status deployment/xonotic-allocator-backend -n xonotic-allocator-backend
```

## Promotion Rule

Promote a pair only after the script prints `VERIFIED <mode>/<map>` against the deployed image. Promotion means:

1. move the map from `experimental_maps` to `verified_maps` in `GAME_CONFIG_OPTIONS`
2. set the mode `selectable: true` only when it has at least one verified map intended for normal operators
3. update this document and the public docs
4. rebuild/redeploy backend and frontend

Never add arbitrary map/mode input to the normal frontend selector.

## Source Notes

- Gametype aliases come from upstream Xonotic gametype registrations in [`xonotic-data.pk3dir`](https://gitlab.com/xonotic/xonotic-data.pk3dir/-/tree/master/qcsrc/common/gametypes/gametype), where each mode defines its short name such as `dm`, `tdm`, `ctf`, `duel`, `ca`, `dom`, and `kh`.
- Candidate maps come from upstream Xonotic mapinfo files in [`xonotic-maps.pk3dir`](https://gitlab.com/xonotic/xonotic-maps.pk3dir/-/tree/master/maps), plus `drain` because the current server entrypoint still treats it as part of the runtime startup map pool. The deployed server image is still the authority; mapinfo only decides what is worth probing.
