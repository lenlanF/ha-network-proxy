# Architecture

The host-network application owns the proxy runtime. Supervisor options supply only the controller secret. The private `/data/sources.json` stores webpage settings; `/data/subscription.json` stores a URL hash, fetch timestamp and complete subscription document. Permissions default to 0600. Nothing from these private files is packaged in the repository or release bundle.

Subscription saves fetch bounded YAML/JSON, apply recursive mapping overrides and replacement lists, then validate a generated configuration with the actual Mihomo binary. Runtime fields are managed locally, and provider cache paths cannot escape the data directory. A failed fetch, merge, validation or routing update restores previous settings and runtime; errors returned to the webpage use fixed codes rather than exception text containing tokens.

The source selector chooses subscription policy or LAN upstream. Subscription rules reference the original groups, and the application adds one reserved outlet group for global mode and simple overrides. Global mode explicitly selects this managed group instead of Mihomo's default DIRECT. No flat subscription node list is exposed by the manager.

The manager listens on loopback 9091 and the controller on loopback 9090. Ingress 8099 requires the Supervisor peer 172.30.32.2; JSON mutations require a request marker. The integration runtime endpoint containing local outlet credentials is bearer-authenticated and unavailable through Ingress. The public settings endpoint redacts both upstream and outlet passwords.

Whole-HAOS mode uses Mihomo TUN auto-route/auto-redirect with a dedicated route index and outbound socket mark. Private destinations bypass capture. Application-owned DNS chains redirect host and Supervisor bridge DNS into Mihomo, excluding marked upstream DNS sockets. The DNS listener is restricted to loopback and the Supervisor bridge by firewall rules. IPv6 is enabled for whole-host capture.

Partial application mode uses TUN without automatic host routing. Each selected bridge application's current IPv4 address gets an exact source rule in table 20227; private routes throw back to the main table. Named FORWARD chains allow selected container traffic across the TUN despite Docker's forwarding policy. DNS redirection is limited to selected source addresses. App addresses are reconciled every 15 seconds. Host-network applications share Core's source network identity and cannot be selected individually.

Routing resources are journaled before modification and cleaned on switches, failed setup, shutdown and startup. Cleanup deletes only the application's named chains, exact source rules and dedicated table; it never flushes the host's general route or firewall tables. Mihomo owns its auto-route resources.

The companion integration reports config-entry names/domains to the manager and polls centralized routing settings every 10 seconds. Selected config-entry-context aiohttp requests use the loopback HTTP outlet and its current credentials. Explicit proxy arguments and local destinations are preserved. Failed proxied requests do not silently retry direct. Unloading restores the hook. This does not capture other HTTP clients or tasks which lost their entry context.

Supervisor reads use the default role: `/supervisor/info`, per-app `/addons/{slug}/info` and `/network/info`. Only whitelisted names, state, host-network flags and addresses are retained. No app options, Docker API, host PID namespace or Supervisor manager/admin privileges are needed.
