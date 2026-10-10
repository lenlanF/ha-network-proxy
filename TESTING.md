# Verification

Local Windows checks:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\ruff.exe check addons custom_components tests tools
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/verify_ui.py
```

Put the official Mihomo v1.19.12 executable in `.tools/mihomo-windows-amd64-v1.exe`, or set `MIHOMO_BINARY`. Real-core tests use only local fixtures: authenticated HTTP CONNECT, verified TLS, authenticated SOCKS5, complete subscription policy, YAML extension, invalid-group rollback and global outlet selection. Browser QA checks Ingress prefixes, source switching, credential retention, YAML saves, sharing, address copying, mode controls and desktop/mobile overflow. Local screenshots contain only synthetic test settings.

The PR workflow builds native amd64/aarch64 images without publishing. It runs all tests and the following inside a disposable Linux container with no external network:

```sh
docker run --rm --privileged --network=none \
  -e NP_ISOLATED_TEST=1 -v "$PWD:/repo:ro" -w /repo \
  --entrypoint python network-proxy:test tools/verify_traffic.py
```

**Never run this script on HAOS or another live host.** The privilege belongs only to the throwaway CI container. The fixture creates a synthetic WAN server and two application namespaces, simulates bridge forwarding and NAT, and checks selected versus unselected apps, whole-host/apps TCP/UDP/DNS, IPv6, private bypass, direct/global modes, LAN sharing, and cleanup. DNS responses differ between the baseline server and Mihomo's upstream, proving actual redirection. Origin responses identify loopback proxy connections versus direct WAN connections.

Production release jobs must also pass this isolation test before publishing images. A CI success is evidence for the synthetic Linux topology, not a claim that a particular HAOS installation has been validated. Live acceptance should check Core and representative apps, management access, local devices, selected integration capture counters, UDP-capable upstream behavior and recovery after stop/restart. Use a maintenance window; no live routing switch is implied by a source build or PR.

0.4.1 regressions validate private error classification, timeout cleanup, retained source/cache, recovery status, no route activation, guarded integration runtime, no background auto-repair, rollback to the actual reject bootstrap, and successful manual repair with a real Mihomo core. Live HAOS startup is an additional acceptance step.

0.4.2 regressions cover escaped and literal emoji JSON subscriptions, invalid surrogate rejection, duplicate decoded keys, real-core save/reload with non-BMP node and group names, and UTF-8 persisted reload. The isolated startup check also boots the actual manager and core from an ASCII-escaped legacy snapshot containing emoji names on both architectures.
