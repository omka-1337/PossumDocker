SHELL := /bin/bash
COMPOSE := docker compose
# A terminal gets hidden password prompts; without one (scripts, CI) answers are read from stdin.
EXEC = $(COMPOSE) exec $$([ -t 0 ] || echo -T) panel
.DEFAULT_GOAL := help

.PHONY: help start stop update logs admin

help: ## Show the commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  make %-8s %s\n", $$1, $$2}'

start: .env token private ## Build and start the panel; asks for an administrator account on the first start
	$(COMPOSE) up -d --build --remove-orphans
	@$(MAKE) --no-print-directory wait
	@$(EXEC) python -m app.cli ensure-admin
	@echo "The panel is running at http://localhost:$$(grep '^POSSUM_PORT=' .env | cut -d= -f2)"

stop: ## Stop the panel (game servers keep running)
	$(COMPOSE) down

update: ## Pull the latest version from GitHub and restart the panel
	@git pull --ff-only || { echo "Can't update: this copy has local changes or has diverged from GitHub."; exit 1; }
	@$(MAKE) --no-print-directory token private
	$(COMPOSE) up -d --build --remove-orphans
	@$(MAKE) --no-print-directory wait

logs: ## Follow the panel's logs
	$(COMPOSE) logs -f panel

admin: ## Add an administrator or reset a forgotten password
	@$(EXEC) python -m app.cli create-admin

# Settings for this machine, written once. Edit .env to change the port or time zone.
.env:
	@{ \
		echo "POSSUM_PORT=8080"; \
		echo "POSSUM_UID=$$(id -u)"; \
		echo "POSSUM_GID=$$(id -g)"; \
		tz=$$(timedatectl show -p Timezone --value 2>/dev/null || readlink /etc/localtime | sed 's|.*/zoneinfo/||'); \
		echo "POSSUM_TIMEZONE=$${tz:-UTC}"; \
	} > .env
	@mkdir -p data
	@echo "Created .env:"; sed 's/^/  /' .env

# The secret the panel and the agent share. Added to .env once, also to an .env from an older version.
.PHONY: token
token: .env
	@grep -q '^POSSUM_AGENT_TOKEN=' .env || { \
		echo "POSSUM_AGENT_TOKEN=$$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" >> .env; \
		echo "Added an agent token to .env"; \
	}

# The agent token and the database (password hashes) are for this user only, not everyone on the machine.
.PHONY: private
private: .env
	@mkdir -p data
	@chmod 600 .env
	@chmod 700 data

.PHONY: wait
wait:
	@echo -n "Waiting for the panel"; \
	for i in $$(seq 1 60); do \
		status=$$(docker inspect -f '{{.State.Health.Status}}' $$($(COMPOSE) ps -q panel) 2>/dev/null); \
		if [ "$$status" = healthy ]; then echo " ready"; exit 0; fi; \
		echo -n "."; sleep 2; \
	done; \
	echo; echo "The panel didn't become healthy. See: make logs"; exit 1
