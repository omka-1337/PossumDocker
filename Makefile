# Kept for installs from before ./possum: `make update` on them still works. Use ./possum instead.
# (0.2.x ran `make token private up wait` after pulling; all of it is part of ./possum restart now.)
.PHONY: start stop update logs admin token private up wait

start stop update logs admin:
	@./possum $@

token private wait:
	@:

up:
	@./possum restart
