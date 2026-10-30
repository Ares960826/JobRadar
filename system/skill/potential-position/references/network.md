# Network and proxy limits

Some mainland recruitment sites fail from the default route. A proxy may be tried only when the
user allows it, and only for the specific site or request that is actually blocked.

- Never enable a global VPN, app-wide takeover, system default-route change or shell-wide proxy
  environment variables. The user and the agent can both lose connectivity.
- A proxy app's "smart mode" is not a domain allow-list. First confirm a local proxy endpoint or
  domain rule that can be limited to one request or to the isolated browser; everything else
  keeps its original route.
- If you cannot confirm that scoping, stop proxy attempts and report the gap. Never experiment
  with the agent's own model connection.
- Undo any temporary route afterwards. Record site, method, time and outcome, never secrets.
- A browser succeeding through the proxy does not prove CLI or cloud search tools use the same
  path.

A desktop proxy client whose per-domain scoping you cannot verify is not an automatic
collection proxy.
