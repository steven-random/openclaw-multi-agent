PLUGIN_SRC  = plugins/db-tool
PLUGIN_DEST = $(HOME)/.openclaw/extensions/db-tool

.PHONY: help sync-plugin restart deploy status logs

help:
	@echo ""
	@echo "  make sync-plugin   Copy db-tool source → ~/.openclaw/extensions/db-tool"
	@echo "  make restart       Restart openclaw-gateway (hot reload config + plugins)"
	@echo "  make deploy        sync-plugin + restart (full update in one command)"
	@echo "  make status        Show service status"
	@echo "  make logs          Tail openclaw-gateway logs"
	@echo ""

## Copy updated plugin source files to OpenClaw's extension dir and reinstall deps.
## node_modules stays in the extension dir (not in git).
sync-plugin:
	cp $(PLUGIN_SRC)/index.ts              $(PLUGIN_DEST)/index.ts
	cp $(PLUGIN_SRC)/openclaw.plugin.json  $(PLUGIN_DEST)/openclaw.plugin.json
	cp $(PLUGIN_SRC)/package.json          $(PLUGIN_DEST)/package.json
	cp $(PLUGIN_SRC)/package-lock.json     $(PLUGIN_DEST)/package-lock.json
	cd $(PLUGIN_DEST) && npm install --ignore-scripts --silent
	@echo "✅  db-tool synced to $(PLUGIN_DEST)"

## Restart the gateway service (picks up config + plugin changes).
restart:
	systemctl --user restart openclaw-gateway
	@echo "✅  openclaw-gateway restarted"

## Full deploy: sync plugin files then restart gateway.
deploy: sync-plugin restart

## Show status of both services.
status:
	@systemctl --user status openclaw-gateway codex-proxy --no-pager -l | \
		grep -E '(●|Active|Main PID)'

## Tail live logs.
logs:
	journalctl --user -u openclaw-gateway -f
