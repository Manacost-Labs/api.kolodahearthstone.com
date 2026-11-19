# VaryloUI (vendored)

Source copied from the VaryloUI registry (`r/<name>.json`, local release in
`/home/debian/varylo-ui-release`, equivalent to `npx varylo-cli add <name…>`).
Items: app-shell, sidebar, dropdown-menu, tooltip, command-palette, tabs,
segmented-control, select, pagination, dialog, drawer, badge, skeleton, button,
icon-button, spinner, text-input, empty-state, theme, styles, primitives, icons,
scatter-chart, bar-chart (with the shared `charts/*`; only `ScatterChart` is used so far, the meta view, #107).

Local changes: `styles/index.css` imports only the `vercel` theme (glass and the
other themes are not shipped). Do not edit other files by hand; re-copy them
from the registry instead. Lint and formatting skip this folder; typecheck does not.
